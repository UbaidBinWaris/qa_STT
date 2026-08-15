"""Machine-facing API for the NestJS backend.

The GPU worker holds no database credentials by design. NestJS stores the
recording in MinIO, hands over an object key and a callback URL, and this module
pulls the audio, runs the existing pipeline unchanged, and reports progress back.
NestJS remains the only writer to Postgres.

Kept separate from app.py's browser-facing routes: these endpoints authenticate
with a shared secret rather than a session, because the caller is a machine.
Nothing here touches the legacy dashboard, which keeps working exactly as before.
"""
import hmac
import logging
import os
import threading

import requests

logger = logging.getLogger("worker_api")

WORKER_SECRET = os.environ.get("WORKER_CALLBACK_SECRET", "")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_USER = os.environ.get("MINIO_ROOT_USER", "")
MINIO_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "qa-stt")

_minio = None
_minio_lock = threading.Lock()


def storage():
    """Lazily built so the server still starts when MinIO is down."""
    global _minio
    with _minio_lock:
        if _minio is None:
            from minio import Minio
            from urllib.parse import urlparse

            parsed = urlparse(MINIO_ENDPOINT)
            _minio = Minio(
                parsed.netloc,
                access_key=MINIO_USER,
                secret_key=MINIO_PASSWORD,
                secure=parsed.scheme == "https",
            )
    return _minio


def secret_ok(provided: str | None) -> bool:
    if not WORKER_SECRET:
        return True  # not configured: local development
    # Constant-time so the secret cannot be recovered by timing.
    return bool(provided) and hmac.compare_digest(provided, WORKER_SECRET)


def report(callback_url: str, payload: dict):
    """Tell NestJS where a job has got to. Never raises into the pipeline: a
    failed status update must not fail the transcription itself."""
    if not callback_url:
        return
    try:
        requests.post(
            callback_url,
            json=payload,
            headers={"x-worker-secret": WORKER_SECRET} if WORKER_SECRET else {},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"progress callback failed for {payload.get('callId')}: {e}")


def fetch_object(key: str, dest: str):
    storage().fget_object(MINIO_BUCKET, key, dest)
    return dest


def put_object(key: str, path: str, content_type: str = "application/octet-stream"):
    storage().fput_object(MINIO_BUCKET, key, path, content_type=content_type)
    return key
