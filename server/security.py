"""Transport and request-level hardening applied to every response.

Split from auth.py, which handles *who* you are; this module handles what the
browser is allowed to do once it has the page.
"""
import logging
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("security")

# Google Fonts is the only third party the pages use. Everything executable must
# come from this origin, which is what makes the removal of inline handlers
# worthwhile: a stored-XSS payload has nowhere to run.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)

# Uploads stream through their own size check; everything else is small JSON and
# has no business being large.
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_KB", "64")) * 1024
UPLOAD_PATHS = ("/api/calls",)


def _is_secure(request: Request) -> bool:
    """True when the browser reached us over HTTPS, including via a tunnel."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


async def headers_middleware(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if _is_secure(request):
        headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # Recordings and transcripts must never be cached by an intermediary. HTML is
    # included so a signed-out browser cannot resurrect the dashboard from cache.
    path = request.url.path
    if path.startswith("/api/") or path.endswith((".html", "/")) or path == "":
        headers["Cache-Control"] = "no-store"
    else:
        # Static assets may be cached, but must be revalidated: a stale app.js
        # after a deploy is both a support burden and a way to keep running code
        # that a security fix has already replaced.
        headers.setdefault("Cache-Control", "no-cache")
    return response


async def body_limit_middleware(request: Request, call_next):
    """Reject oversized non-upload bodies before they are parsed."""
    if not request.url.path.startswith(UPLOAD_PATHS):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


class RateLimiter:
    """Fixed-window limiter keyed by client address.

    Deliberately in-process: this guards a single-node deployment, and a shared
    store would add a dependency without changing the threat it addresses.
    """

    def __init__(self, max_attempts: int, window: float, lockout: float):
        self.max_attempts = max_attempts
        self.window = window
        self.lockout = lockout
        self._hits: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}

    def _prune(self, now: float):
        for key in [k for k, until in self._locked.items() if until < now]:
            del self._locked[key]
        if len(self._hits) > 10_000:  # bound memory against spoofed sources
            self._hits.clear()

    def retry_after(self, key: str) -> float:
        """Seconds the caller must wait, or 0 if allowed."""
        now = time.time()
        self._prune(now)
        until = self._locked.get(key)
        return max(0.0, until - now) if until else 0.0

    def record_failure(self, key: str) -> float:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        hits.append(now)
        self._hits[key] = hits
        if len(hits) >= self.max_attempts:
            self._locked[key] = now + self.lockout
            self._hits.pop(key, None)
            logger.warning(f"Locked out {key} after {len(hits)} failed logins")
            return self.lockout
        return 0.0

    def reset(self, key: str):
        self._hits.pop(key, None)
        self._locked.pop(key, None)


login_limiter = RateLimiter(
    max_attempts=int(os.environ.get("LOGIN_MAX_ATTEMPTS", "8")),
    window=float(os.environ.get("LOGIN_WINDOW_SEC", "300")),
    lockout=float(os.environ.get("LOGIN_LOCKOUT_SEC", "900")),
)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting and the audit log.

    X-Forwarded-For is only believed when the connection itself arrives from
    loopback — meaning a tunnel or reverse proxy on this machine put it there —
    or when TRUST_PROXY says an upstream proxy is in front. A request straight
    off the network cannot forge its way into a different rate-limit bucket.

    This matters behind a tunnel: cloudflared and ngrok connect over loopback, so
    without it every remote user collapses into one identity and a single
    password-guesser would lock out the entire team.
    """
    peer = request.client.host if request.client else "unknown"
    if os.environ.get("TRUST_PROXY") or peer in LOOPBACK:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer
