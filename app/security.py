import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"s1${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def create_access_token(user_id: str, org_id: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.token_ttl_min)
    return jwt.encode({"sub": user_id, "org": org_id, "role": role, "exp": exp}, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def new_secret_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ---- signed, short-lived media URLs (clip playback) ----
def sign_media(storage_key: str, start_ms: int, end_ms: int, ttl_s: int | None = None, watermark: str = "") -> str:
    payload = {"k": storage_key, "s": start_ms, "e": end_ms, "w": watermark, "x": int(time.time()) + (ttl_s or settings.media_url_ttl_s)}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_media(token: str) -> dict | None:
    try:
        body, sig = token.split(".")
        good = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return payload if payload["x"] >= time.time() else None
    except Exception:
        return None
