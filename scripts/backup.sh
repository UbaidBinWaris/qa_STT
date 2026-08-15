#!/usr/bin/env bash
# Back up ASCRAS state to MinIO.
#
# Two databases exist during the transition and both are backed up:
#   - Postgres, which the NestJS backend owns
#   - the legacy SQLite file, which the Python worker still uses and which must
#     keep working untouched until the migration is finished
#
# Run from cron:  0 * * * * /data/github/qa_STT/scripts/backup.sh >> /var/log/ascras-backup.log 2>&1
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a

MC="${MC_BIN:-$HOME/.local/bin/mc}"
ALIAS="${MINIO_ALIAS:-ascras}"
BUCKET="${MINIO_BUCKET:-qa-stt}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

"$MC" alias set "$ALIAS" "${MINIO_ENDPOINT:-http://localhost:9000}" \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

# --- legacy SQLite -----------------------------------------------------------
# .backup rather than cp: the pipeline may be mid-write, and copying a WAL
# database under load yields a file that restores to a corrupted state.
SQLITE="$REPO/server/database/calls.db"
if [ -f "$SQLITE" ]; then
  # The sqlite3 CLI is not installed here, but Python's module exposes the same
  # online backup API — which is the part that matters, since it takes a
  # consistent snapshot of a WAL database that is being written to.
  "$REPO/server/venv/bin/python" - "$SQLITE" "$TMP/calls.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
    s.backup(d)
PY
  gzip -9 "$TMP/calls.db"
  "$MC" cp --quiet "$TMP/calls.db.gz" "$ALIAS/$BUCKET/backups/legacy/$STAMP.db.gz"
  echo "[$STAMP] legacy sqlite -> backups/legacy/$STAMP.db.gz ($(du -h "$TMP/calls.db.gz" | cut -f1))"
fi

# --- postgres ----------------------------------------------------------------
if [ -n "${DATABASE_URL:-}" ] && command -v pg_dump >/dev/null 2>&1; then
  # Kept as a guard rather than a fix: DATABASE_URL is now plain, but if anyone
  # re-adds a Prisma-only parameter like "?schema=public", libpq would refuse the
  # whole URI and backups would silently stop. Stripping the query string costs
  # nothing and keeps that failure from ever being silent again.
  PG_URL="${DATABASE_URL%%\?*}"
  if pg_dump "$PG_URL" 2>/dev/null | gzip -9 > "$TMP/pg.sql.gz"; then
    if [ -s "$TMP/pg.sql.gz" ]; then
      "$MC" cp --quiet "$TMP/pg.sql.gz" "$ALIAS/$BUCKET/backups/db/$STAMP.sql.gz"
      echo "[$STAMP] postgres -> backups/db/$STAMP.sql.gz ($(du -h "$TMP/pg.sql.gz" | cut -f1))"
    else
      echo "[$STAMP] postgres dump was empty — skipped"
    fi
  else
    echo "[$STAMP] postgres unreachable — skipped (legacy backup still succeeded)"
  fi
else
  echo "[$STAMP] postgres not configured or pg_dump missing — skipped"
fi

echo "[$STAMP] retained backups:"
"$MC" ls --recursive "$ALIAS/$BUCKET/backups/" 2>/dev/null | tail -5 || true
