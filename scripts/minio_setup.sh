#!/usr/bin/env bash
# Provision the ASCRAS object store.
#
# Idempotent: safe to re-run. Everything here is namespaced under one bucket so
# a single credential and a single lifecycle policy cover recordings, derived
# artefacts and database backups.
#
#   recordings/<call-id>/original.<ext>   what the client uploaded, never mutated
#   derived/<call-id>/audio.wav           16 kHz mono the pipeline actually reads
#   derived/<call-id>/waveform.json       cached render data for the player
#   backups/db/<timestamp>.sql.gz         Postgres dumps
#   backups/legacy/<timestamp>.db.gz      the old SQLite database
#
# Recordings are versioned: a re-upload or a bad overwrite is recoverable, which
# matters because these are the client's only copy of a call in many cases.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a

MC="${MC_BIN:-$HOME/.local/bin/mc}"
ALIAS="${MINIO_ALIAS:-ascras}"
BUCKET="${MINIO_BUCKET:-qa-stt}"
ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"

if [ ! -x "$MC" ]; then
  echo "MinIO client not found at $MC"
  echo "  curl -sSL -o ~/.local/bin/mc https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x ~/.local/bin/mc"
  exit 1
fi

"$MC" alias set "$ALIAS" "$ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
echo "connected to $ENDPOINT"

"$MC" mb --ignore-existing "$ALIAS/$BUCKET" >/dev/null
echo "bucket $BUCKET ready"

# Private by default. These are customer call recordings — nothing here is ever
# world-readable, and the portal serves them through signed URLs from NestJS.
"$MC" anonymous set none "$ALIAS/$BUCKET" >/dev/null 2>&1 || true
echo "access: private"

"$MC" version enable "$ALIAS/$BUCKET" >/dev/null 2>&1 && echo "versioning: enabled" \
  || echo "versioning: unavailable on this deployment (single-node without erasure coding)"

# Backups rotate; recordings do not. Keeping 90 days of dumps is enough to
# recover from a corruption noticed late, without growing without bound.
LIFECYCLE="$(mktemp)"
cat > "$LIFECYCLE" <<'JSON'
{
  "Rules": [
    {
      "ID": "expire-db-backups",
      "Status": "Enabled",
      "Filter": { "Prefix": "backups/" },
      "Expiration": { "Days": 90 }
    },
    {
      "ID": "expire-old-recording-versions",
      "Status": "Enabled",
      "Filter": { "Prefix": "recordings/" },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 30 }
    }
  ]
}
JSON
"$MC" ilm import "$ALIAS/$BUCKET" < "$LIFECYCLE" >/dev/null 2>&1 \
  && echo "lifecycle: backups expire after 90d, superseded recording versions after 30d" \
  || echo "lifecycle: could not apply (non-fatal)"
rm -f "$LIFECYCLE"

echo
"$MC" ls "$ALIAS/$BUCKET" 2>/dev/null || true
echo "done"
