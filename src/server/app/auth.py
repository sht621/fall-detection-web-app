"""Session-cookie authentication for the monitor UI."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException, Request, Response, status

SESSION_COOKIE_NAME = "fall_monitor_session"
CSRF_HEADER_NAME = "x-csrf-token"


@dataclass(frozen=True)
class AuthUser:
    username: str
    csrf_token: str


def auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def session_cookie_secure() -> bool:
    return os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}


def session_ttl_seconds() -> int:
    return int(os.getenv("SESSION_TTL_SECONDS", "28800"))


def monitor_username() -> str:
    username = os.getenv("MONITOR_USERNAME")
    if not username:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="monitor username is not configured")
    return username


def session_secret() -> str:
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32 or secret.startswith("change-me"):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="SESSION_SECRET is not configured")
    return secret


def password_hash() -> str:
    value = os.getenv("MONITOR_PASSWORD_HASH", "")
    if not value:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="monitor password hash is not configured")
    return value


def make_password_hash(password: str, iterations: int = 260_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256:{}:{}:{}".format(
        iterations,
        _b64encode(salt),
        _b64encode(digest),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split(":", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def authenticate(username: str, password: str) -> AuthUser:
    if auth_disabled():
        return AuthUser(username="development-monitor", csrf_token=secrets.token_urlsafe(32))
    if not hmac.compare_digest(username, monitor_username()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")
    if not verify_password(password, password_hash()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")
    return AuthUser(username=username, csrf_token=secrets.token_urlsafe(32))


def create_session_cookie(response: Response, user: AuthUser) -> None:
    payload = {
        "username": user.username,
        "csrf": user.csrf_token,
        "exp": int(time.time()) + session_ttl_seconds(),
        "nonce": secrets.token_urlsafe(16),
    }
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _sign_payload(payload),
        max_age=session_ttl_seconds(),
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def get_current_user(request: Request) -> AuthUser:
    if auth_disabled():
        return AuthUser(username="development-monitor", csrf_token="development-csrf-disabled")

    session_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")

    payload = _verify_signed_payload(session_value)
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")

    username = str(payload.get("username", ""))
    csrf_token = str(payload.get("csrf", ""))
    if not username or not csrf_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return AuthUser(username=username, csrf_token=csrf_token)


def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    if auth_disabled():
        return
    user = get_current_user(request)
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, user.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")


def _sign_payload(payload: dict[str, Any]) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def _verify_signed_payload(value: str) -> dict[str, Any]:
    try:
        body, signature = value.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session") from exc

    expected = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64encode(expected), signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")

    try:
        decoded = _b64decode(body).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return payload


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
