"""LiveKit access tokens. Role -> grants is the single place that decides who can publish / be seen.

Observers are the security-critical case: subscribe-only, hidden from other participants,
no data channel, no metadata updates. Enforced by the token, never by UI hiding.
"""
import json
import time

import jwt

from .config import settings

GRANTS = {
    "moderator": dict(canPublish=True, canSubscribe=True, canPublishData=True, hidden=False),
    "participant": dict(canPublish=True, canSubscribe=True, canPublishData=False, hidden=False),
    "observer": dict(canPublish=False, canSubscribe=True, canPublishData=False, hidden=True, canUpdateOwnMetadata=False),
}


def build_room_token(*, identity: str, name: str, room: str, role: str, ttl_min: int | None = None, seat: int | None = None) -> str:
    if role not in GRANTS:
        raise ValueError(role)
    now = int(time.time())
    video = {"room": room, "roomJoin": True, **GRANTS[role]}
    if role == "observer":
        video["canPublishSources"] = []
    meta = {"role": role}
    if seat is not None:
        meta["seat"] = seat  # focus-group seat number; the browser translates the label, the real name never goes in
    claims = {
        "iss": settings.livekit_api_key,
        "sub": identity,
        "name": name,
        "nbf": now - 5,
        "exp": now + 60 * (ttl_min or settings.room_token_ttl_min),
        "video": video,
        "metadata": json.dumps(meta, separators=(",", ":")),
    }
    return jwt.encode(claims, settings.livekit_api_secret, algorithm="HS256")


def verify_webhook(auth_header: str, body: bytes) -> bool:
    """LiveKit signs webhooks with a JWT carrying the sha256 of the raw body."""
    import base64
    import hashlib

    try:
        claims = jwt.decode(auth_header, settings.livekit_api_secret, algorithms=["HS256"], issuer=settings.livekit_api_key)
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        return claims.get("sha256") == digest
    except jwt.PyJWTError:
        return False


def http_base(url: str | None = None) -> str:
    u = url or settings.livekit_url or ""
    return u.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def delete_room(room: str) -> bool:
    """Kick everyone out of a LiveKit room (interview ended). Failure is not fatal: tokens for an ended interview are refused anyway."""
    if not settings.livekit_url:
        return False
    import httpx

    now = int(time.time())
    tok = jwt.encode({"iss": settings.livekit_api_key, "nbf": now - 5, "exp": now + 60, "video": {"roomCreate": True, "roomAdmin": True, "room": room}},
                     settings.livekit_api_secret, algorithm="HS256")
    try:
        r = httpx.post(http_base() + "/twirp/livekit.RoomService/DeleteRoom", json={"room": room}, headers={"Authorization": "Bearer " + tok}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def url_for_client(host_header: str | None) -> str | None:
    """If LIVEKIT_URL points at localhost but the page was opened via a LAN address, give the browser that address instead."""
    u = settings.livekit_url
    if not u or not host_header:
        return u
    import re

    m = re.match(r"^(wss?://)(localhost|127\.0\.0\.1)(:\d+)?(/.*)?$", u)
    if not m:
        return u
    host = host_header.split(":")[0]
    return f"{m.group(1)}{host}{m.group(3) or ''}{m.group(4) or ''}" if host not in ("localhost", "127.0.0.1") else u


def revoke_publishing(room: str) -> None:
    """Take the right to speak / show video / share the screen away from every staff member in the room (the AI got the interview back).

    Best effort: a failure is logged without a body; the staff page also stops its own tracks, and a new token is view-only again.
    """
    if not settings.livekit_url:
        return
    import logging

    import httpx

    now = int(time.time())
    tok = jwt.encode({"iss": settings.livekit_api_key, "nbf": now - 5, "exp": now + 60, "video": {"roomAdmin": True, "room": room}}, settings.livekit_api_secret, algorithm="HS256")
    hdr = {"Authorization": "Bearer " + tok}
    try:
        listed = httpx.post(http_base() + "/twirp/livekit.RoomService/ListParticipants", json={"room": room}, headers=hdr, timeout=5).json()
        for p in listed.get("participants", []):
            if str(p.get("identity", "")).startswith("staff:"):
                httpx.post(http_base() + "/twirp/livekit.RoomService/UpdateParticipant", json={"room": room, "identity": p["identity"], "permission": {"can_subscribe": True, "can_publish": False, "can_publish_data": False, "hidden": True}},
                           headers=hdr, timeout=5)
    except (httpx.HTTPError, ValueError):
        logging.getLogger(__name__).warning("could not revoke staff publishing in a room", exc_info=True)
