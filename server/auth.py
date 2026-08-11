import hmac
import logging
import os
import secrets
import time

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

import security

logger = logging.getLogger("auth")

COOKIE = "qa_session"
SESSION_TTL = 12 * 3600
# Endpoints reachable without a session, so the login page can load and probe.
PUBLIC_PATHS = {
    "/login", "/login.html", "/login.js", "/styles.css",
    "/api/login", "/api/auth-status",
}

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


MAX_SESSIONS = 500


def create_session() -> str:
    _prune()
    # Bound memory: repeated logins must not grow this map without limit.
    if len(_sessions) >= MAX_SESSIONS:
        oldest = sorted(_sessions, key=_sessions.get)[: len(_sessions) - MAX_SESSIONS + 1]
        for token in oldest:
            del _sessions[token]
        logger.warning(f"Session table full; evicted {len(oldest)} oldest session(s)")
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

    # Only redirect real page loads. Redirecting a subresource (a script or
    # stylesheet) hands the browser HTML where it expects JavaScript, which
    # fails with a confusing MIME-type error instead of a clean sign-in.
    dest = request.headers.get("sec-fetch-dest")
    is_document = dest == "document" if dest else "text/html" in request.headers.get("accept", "")
    if not is_document:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    return Response(status_code=302, headers={"Location": "/login.html"})


def login(response: Response, candidate: str, request: Request) -> dict:
    key = security.client_key(request)

    wait = security.login_limiter.retry_after(key)
    if wait:
        logger.warning(f"AUDIT login blocked (locked out) from {key}")
        raise HTTPException(
            429,
            f"Too many failed attempts. Try again in {int(wait / 60) + 1} minute(s).",
            headers={"Retry-After": str(int(wait))},
        )

    if not check_password(candidate):
        lockout = security.login_limiter.record_failure(key)
        logger.warning(f"AUDIT login failed from {key}")
        time.sleep(0.5)
        if lockout:
            raise HTTPException(
                429,
                f"Too many failed attempts. Try again in {int(lockout / 60)} minute(s).",
                headers={"Retry-After": str(int(lockout))},
            )
        raise HTTPException(401, "Incorrect password")

    security.login_limiter.reset(key)
    token = create_session()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        # Strict keeps the cookie off every cross-site request, which removes
        # CSRF as a concern for the state-changing endpoints.
        samesite="strict",
        secure=security._is_secure(request),
        max_age=SESSION_TTL,
        path="/",
    )
    logger.info(f"AUDIT login success from {key}")
    return {"authenticated": True}


def destroy_session(token: str | None):
    if token:
        _sessions.pop(token, None)
