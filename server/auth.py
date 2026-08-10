import hmac
import logging
import os
import secrets
import time

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("auth")

COOKIE = "qa_session"
SESSION_TTL = 12 * 3600
# Endpoints reachable without a session, so the login page can load and probe.
PUBLIC_PATHS = {"/login", "/login.html", "/styles.css", "/api/login", "/api/auth-status"}

_sessions: dict[str, float] = {}


def password() -> str | None:
    """Auth is active only when APP_PASSWORD is set, so local use stays frictionless
    while a tunnelled instance is always protected."""
    return os.environ.get("APP_PASSWORD") or None


def enabled() -> bool:
    return password() is not None


def _prune():
    now = time.time()
    for token, expiry in list(_sessions.items()):
        if expiry < now:
            del _sessions[token]


def create_session() -> str:
    _prune()
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL
    return token


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if expiry < time.time():
        del _sessions[token]
        return False
    return True


def check_password(candidate: str) -> bool:
    expected = password()
    if not expected:
        return False
    # Constant-time compare so the tunnel cannot be brute-forced by timing.
    return hmac.compare_digest(candidate.encode(), expected.encode())


async def middleware(request: Request, call_next):
    if not enabled():
        return await call_next(request)

    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    if valid_session(request.cookies.get(COOKIE)):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    return Response(
        status_code=302, headers={"Location": "/login.html"}
    )


def login(response: Response, candidate: str) -> dict:
    if not check_password(candidate):
        # Blunt throttle: enough to make online guessing impractical.
        time.sleep(1.0)
        raise HTTPException(401, "Incorrect password")

    token = create_session()
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL, path="/"
    )
    return {"authenticated": True}
