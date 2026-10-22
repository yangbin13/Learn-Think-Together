from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

from .db import now, uid

COOKIE_NAME = "study_session"


def hash_password(password: str):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str):
    try:
        _, salt, expected = encoded.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user):
    return {key: user[key] for key in ("id", "username", "display_name")}


def current_user(request: Request):
    # Same-origin mutations prevent browser CSRF; Vite's development origin is explicit.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            host = urlsplit(origin).netloc
            allowed = {request.headers.get("host", ""), "localhost:5173", "127.0.0.1:5173"}
            if host not in allowed:
                raise HTTPException(403, "请求来源不受信任")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "请先登录")
    user = request.app.state.db.one(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.expires_at>?", (token_hash(token), now())
    )
    if not user:
        raise HTTPException(401, "登录已过期，请重新登录")
    return public_user(user)


def create_session(request: Request, response: Response, user_id: str):
    token = secrets.token_urlsafe(32)
    settings = request.app.state.settings
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    request.app.state.db.execute("INSERT INTO auth_sessions VALUES (?,?,?)", (token_hash(token), user_id, expires.isoformat()))
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=settings.session_days * 86400, path="/")


def make_user(db, username, password, display_name):
    user = {"id": uid(), "username": username, "display_name": display_name}
    db.execute("INSERT INTO users VALUES (?,?,?,?,?)", (
        user["id"], username, display_name, hash_password(password), now()))
    return user

