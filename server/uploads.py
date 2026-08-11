"""Audio ingestion: validate, store, and register an uploaded recording.

Kept separate from the HTTP layer so the rules live in one place and can be
exercised without a server. Everything here is fail-closed — a recording is
registered in the database only after it is safely on disk and proven decodable,
so a rejected or interrupted upload never leaves a half-built call behind.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import unicodedata

import db

logger = logging.getLogger("uploads")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(SERVER_DIR, "uploads")
TMP_DIR = os.path.join(UPLOAD_DIR, ".incoming")

ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".aac"}

# Limits. A 4-hour call at 8 kHz mono MP3 is roughly 60 MB, so 500 MB is
# generous for real recordings while still bounding a runaway upload.
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
MAX_DURATION = float(os.environ.get("MAX_DURATION_MIN", "240")) * 60
MIN_DURATION = float(os.environ.get("MIN_DURATION_SEC", "1"))
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "50"))
# Refuse to accept uploads that would leave the disk nearly full — the pipeline
# still needs room for the converted wav and the waveform cache.
MIN_FREE_BYTES = int(os.environ.get("MIN_FREE_MB", "2048")) * 1024 * 1024

CHUNK = 1024 * 1024


class UploadError(Exception):
    """Rejection with an HTTP status and a message safe to show a user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def limits() -> dict:
    return {
        "max_bytes": MAX_BYTES,
        "max_upload_mb": MAX_BYTES // (1024 * 1024),
        "max_duration_sec": MAX_DURATION,
        "min_duration_sec": MIN_DURATION,
        "allowed_extensions": sorted(ALLOWED_EXT),
        "max_queue": MAX_QUEUE,
    }


def safe_filename(name: str | None) -> str:
    """Keep something human-readable for display without trusting it on disk.

    Stored files are always named by call id, so this only guards what is shown
    in the UI and written to the database.
    """
    # Some clients send a full Windows path; os.path.basename does not treat "\"
    # as a separator on Linux, so normalise it first.
    name = (name or "").replace("\\", "/")
    name = os.path.basename(name).strip()
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.lstrip(".") or "recording"
    return name[:200]


def check_extension(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        raise UploadError(415, "File has no extension, so its format cannot be determined.")
    if ext not in ALLOWED_EXT:
        raise UploadError(
            415,
            f"Unsupported format '{ext}'. Accepted: {', '.join(sorted(ALLOWED_EXT))}.",
        )
    return ext


def check_capacity(queue_depth: int):
    if queue_depth >= MAX_QUEUE:
        raise UploadError(503, f"Processing queue is full ({queue_depth} waiting). Try shortly.")

    free = shutil.disk_usage(SERVER_DIR).free
    if free < MIN_FREE_BYTES:
        logger.error(f"Refusing upload: only {free / 1e9:.1f} GB free")
        raise UploadError(507, "Server is low on disk space. Contact an administrator.")


def probe(path: str) -> float:
    """Confirm the bytes really are decodable audio and return the duration.

    Extension checks are trivially spoofed; this is the authoritative test and
    also catches truncated or corrupt recordings before they reach the GPU.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,duration:format=duration",
             "-select_streams", "a:0", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise UploadError(422, "Could not read the audio within a reasonable time.")

    if result.returncode != 0:
        raise UploadError(422, "File is not readable as audio, or is corrupt.")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        raise UploadError(422, "File is not readable as audio.")

    streams = data.get("streams") or []
    if not streams or streams[0].get("codec_type") != "audio":
        raise UploadError(422, "No audio track found in this file.")

    duration = streams[0].get("duration") or (data.get("format") or {}).get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        # Some containers omit duration in the header; the pipeline measures it
        # again after conversion, so this is not fatal.
        logger.warning(f"No duration reported for {path}; accepting anyway.")
        return 0.0

    if duration < MIN_DURATION:
        raise UploadError(422, f"Recording is too short ({duration:.1f}s).")
    if duration > MAX_DURATION:
        raise UploadError(
            413,
            f"Recording is {duration / 60:.0f} minutes; the limit is "
            f"{MAX_DURATION / 60:.0f} minutes.",
        )
    return duration


async def stream_to_disk(upload, dest: str) -> tuple[int, str]:
    """Write the upload in chunks, enforcing the size cap as it arrives.

    Streaming matters: reading the whole body first would let a single large
    request exhaust memory before any limit could be applied.
    """
    digest = hashlib.sha256()
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await upload.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise UploadError(
                    413,
                    f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB limit.",
                )
            digest.update(chunk)
            out.write(chunk)

    if total == 0:
        raise UploadError(422, "File is empty.")
    return total, digest.hexdigest()


async def ingest(upload, queue_depth: int) -> dict:
    """Validate and store an upload. Returns a dict describing the call.

    Raises UploadError for anything a caller should be told about; the temporary
    file is removed on every failure path.
    """
    filename = safe_filename(getattr(upload, "filename", None))
    ext = check_extension(filename)
    check_capacity(queue_depth)

    os.makedirs(TMP_DIR, exist_ok=True)
    call_id = None
    tmp_path = os.path.join(TMP_DIR, f"{os.urandom(8).hex()}{ext}")

    try:
        size, sha256 = await stream_to_disk(upload, tmp_path)

        existing = _find_duplicate(sha256)
        if existing:
            logger.info(f"Duplicate of {existing['id']} ({filename}); not reprocessing.")
            return {
                "call_id": existing["id"],
                "status": existing["status"],
                "duplicate": True,
                "filename": existing["filename"],
            }

        duration = probe(tmp_path)

        call_id = db.new_call_id()
        dest = os.path.join(UPLOAD_DIR, f"{call_id}{ext}")
        os.replace(tmp_path, dest)

        db.create_call_with_id(
            call_id, filename, dest, duration=duration or None,
            size_bytes=size, sha256=sha256,
        )
        logger.info(f"Accepted {filename} as {call_id} ({size / 1e6:.1f} MB, {duration:.0f}s)")
        return {
            "call_id": call_id,
            "status": "queued",
            "duplicate": False,
            "filename": filename,
            "size_bytes": size,
            "duration": duration,
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _find_duplicate(sha256: str) -> dict | None:
    existing = db.find_by_hash(sha256)
    if not existing:
        return None
    # A row whose audio has since been deleted must not be returned as a hit.
    if not existing.get("audio_path") or not os.path.exists(existing["audio_path"]):
        return None
    return existing


def cleanup_incoming():
    """Remove partial uploads abandoned by a crash or a dropped connection."""
    if not os.path.isdir(TMP_DIR):
        return
    removed = 0
    for name in os.listdir(TMP_DIR):
        try:
            os.remove(os.path.join(TMP_DIR, name))
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"Cleared {removed} incomplete upload(s).")
