"""Password check and signed management sessions shared by both UIs."""

import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException

import setting

COOKIE = "__Host-orbit_session"
LOCAL_COOKIE = "orbit_session"
SESSION_AGE = 5 * 24 * 60 * 60
_LOCAL_SECRET = secrets.token_bytes(32)


def password(*, cloud: bool) -> str:
    value = os.environ.get("LOGIN_PASSWORD", "") if cloud else setting.LOGIN_PASSWORD
    if not isinstance(value, str) or not value:
        location = "共有シークレット" if cloud else "setting.py"
        raise HTTPException(503, f"{location} に LOGIN_PASSWORD を設定してください。")
    return value


def check_password(candidate: str, *, cloud: bool) -> bool:
    expected = password(cloud=cloud)
    return len(candidate) <= 512 and hmac.compare_digest(candidate.encode(), expected.encode())


def session_key(*, cloud: bool) -> bytes:
    secret = os.environ.get("SESSION_COOKIE_SECRET", "") if cloud else _LOCAL_SECRET
    if cloud and len(secret) < 32:
        raise HTTPException(503, "SESSION_COOKIE_SECRET を設定してください。")
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(secret_bytes, b"orbit-session-v1\0" + password(cloud=cloud).encode(), hashlib.sha256).digest()


def new_session(key: bytes) -> str:
    payload = f"{int(time.time()) + SESSION_AGE}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_session(value: str | None, key: bytes) -> bool:
    if not value or len(value) > 256:
        return False
    try:
        expiry, nonce, signature = value.split(".")
        expires_at = int(expiry)
        if not nonce or not (time.time() < expires_at <= time.time() + SESSION_AGE):
            return False
        expected = hmac.new(key, f"{expiry}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def csrf_token(session: str, key: bytes) -> str:
    return hmac.new(key, b"csrf\0" + session.encode(), hashlib.sha256).hexdigest()
