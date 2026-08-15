#!/usr/bin/env bash
# Move existing call recordings into MinIO, keyed by call id.
#
# The legacy Python worker keeps reading from server/uploads on local disk — this
# copies rather than moves, so the old system carries on working untouched while
# the new stack starts reading from object storage. Nothing is deleted here.
#
#   server/uploads/<id>.<ext>  ->  recordings/<id>/original.<ext>
#   server/outputs/<id>.wav    ->  derived/<id>/audio.wav
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO/.env" ] && set -a && . "$REPO/.env" && set +a

MC="${MC_BIN:-$HOME/.local/bin/mc}"
ALIAS="${MINIO_ALIAS:-ascras}"
BUCKET="${MINIO_BUCKET:-qa-stt}"

"$MC" alias set "$ALIAS" "${MINIO_ENDPOINT:-http://localhost:9000}" \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

uploaded=0
for f in "$REPO"/server/uploads/*.*; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  id="${base%%.*}"
  ext="${base##*.}"
  "$MC" cp --quiet "$f" "$ALIAS/$BUCKET/recordings/$id/original.$ext"
  uploaded=$((uploaded + 1))
done
echo "recordings uploaded: $uploaded"

derived=0
for f in "$REPO"/server/outputs/*.wav; do
  [ -e "$f" ] || continue
  id="$(basename "$f" .wav)"
  "$MC" cp --quiet "$f" "$ALIAS/$BUCKET/derived/$id/audio.wav"
  derived=$((derived + 1))
done
for f in "$REPO"/server/outputs/*_waveform.json; do
  [ -e "$f" ] || continue
  id="$(basename "$f" _waveform.json)"
  "$MC" cp --quiet "$f" "$ALIAS/$BUCKET/derived/$id/waveform.json"
  derived=$((derived + 1))
done
echo "derived artefacts uploaded: $derived"

echo
echo "bucket usage:"
"$MC" du "$ALIAS/$BUCKET" 2>/dev/null || true
echo
echo "Local files are untouched — the legacy worker keeps reading from disk."
