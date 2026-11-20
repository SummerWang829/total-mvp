"""Material files uploaded into the outline (用户 2026-11-20 第 3 轮: 概念卡 pictures / PDF and 视频片段 files, uploaded straight into the item).

The files live on this server under MEDIA_DIR/outline/<project>/ (never in the database); the item's config lists them in showing order:
config["media"] = [{"id", "name", "type": image|pdf|video, "size"}], config["order"] = "seq" (in this order) | "random" (rotated per participant).
A browser opens a file only through a short-lived signed address (an <img>/<video> cannot send the login header); the address names the file
and its project and expires after an hour. File names are never logged. The files go with the project's personal data when it is cleaned up.
"""
import hashlib
import hmac
import logging
import os
import shutil
import time
import uuid

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Project
from . import service as svc

log = logging.getLogger(__name__)

KINDS = {"concept": ("image", "pdf"), "video": ("video",)}
MAX_BYTES = {"image": 15 * 1024 * 1024, "pdf": 15 * 1024 * 1024, "video": 95 * 1024 * 1024}   # the gateway in front of the site takes at most 100MB in one request
MAX_FILES = 12
TTL_S = 3600
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "application/pdf": "pdf", "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}
MIME = {v: k for k, v in EXT.items()}


class MediaError(svc.OutlineError):
    """The file cannot be taken (wrong kind, too big, unknown item)."""


def sniff(data: bytes) -> str:
    """The real type of the file from its first bytes (the name and the browser's word are not trusted); "" when it is none we take."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if data[4:8] == b"ftyp":
        return "video/quicktime" if data[8:10] == b"qt" else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return ""


def _dir(proj: Project) -> str:
    return os.path.join(settings.media_dir, "outline", proj.id)


def path_of(proj: Project, mid: str) -> str:
    """The file on disk (mid is checked to be our own id form, so it cannot walk out of the folder)."""
    import re
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp|pdf|mp4|webm|mov)", mid or ""):
        raise MediaError("没有这个文件", 404)
    return os.path.join(_dir(proj), mid)


def _sig(pid: str, mid: str, exp: int) -> str:
    return hmac.new(settings.secret_key.encode(), f"outline-media|{pid}|{mid}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def url_of(pid: str, mid: str, now: float | None = None) -> str:
    """A signed address valid for an hour."""
    exp = int((now or time.time()) + TTL_S)
    return f"/api/outline-media/{pid}/{mid}?e={exp}&s={_sig(pid, mid, exp)}"


def check(pid: str, mid: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(sig or "", _sig(pid, mid, exp))


def with_urls(config: dict, pid: str) -> list[dict]:
    """The item's files as the page needs them (with addresses)."""
    return [{**m, "url": url_of(pid, m["id"])} for m in (config or {}).get("media") or []]


def add(db: Session, proj: Project, item_uid: str, filename: str, data: bytes, mode: str) -> None:
    """Keep one uploaded file on the item: `add` puts it after the others, `replace` takes the others away (and their files).

    Raises:
        MediaError: the item is not a 概念卡 / 视频片段, the file is of another kind, empty or too big, or there are already MAX_FILES.
    """
    o, v, _ = svc_state(db, proj)
    it = next((i for i in svc.items_of(db, v.id) if i.item_uid == item_uid), None)
    if it is None:
        raise MediaError("没有这一条", 404)
    if it.kind not in KINDS:
        raise MediaError("只有概念卡和视频片段可以上传素材")
    mime = sniff(data)
    kind = "image" if mime.startswith("image/") else "pdf" if mime == "application/pdf" else "video" if mime.startswith("video/") else ""
    if kind not in KINDS[it.kind]:
        raise MediaError("概念卡只能传图片（PNG、JPG、WEBP）或 PDF" if it.kind == "concept" else "视频片段只能传 MP4、WEBM 或 MOV", 415)
    if len(data) > MAX_BYTES[kind]:
        raise MediaError(f"文件太大：{'视频请控制在 95MB' if kind == 'video' else '请控制在 15MB'}以内", 413)
    cfg = dict(it.config or {})
    have = list(cfg.get("media") or [])
    if mode == "replace":
        for m in have:
            _remove(proj, m["id"])
        have = []
    if len(have) >= MAX_FILES:
        raise MediaError(f"一处最多放 {MAX_FILES} 个素材")
    mid = f"{uuid.uuid4().hex}.{EXT[mime]}"
    os.makedirs(_dir(proj), exist_ok=True)
    with open(path_of(proj, mid), "wb") as fh:
        fh.write(data)
    have.append({"id": mid, "name": os.path.basename(filename or "")[:80] or mid, "type": kind, "size": len(data)})
    cfg["media"] = have
    cfg.setdefault("order", "seq")
    svc.update_item(db, v, item_uid, config=cfg)


def arrange(db: Session, proj: Project, item_uid: str, ids: list[str], order: str) -> None:
    """Set the showing order of the item's files (ids in the new order; a missing id is removed with its file) and seq / random.

    Raises:
        MediaError: unknown item or an order other than seq / random.
    """
    if order not in ("seq", "random"):
        raise MediaError("出示方式只能是按顺序或随机轮换")
    o, v, _ = svc_state(db, proj)
    it = next((i for i in svc.items_of(db, v.id) if i.item_uid == item_uid), None)
    if it is None:
        raise MediaError("没有这一条", 404)
    cfg = dict(it.config or {})
    have = {m["id"]: m for m in cfg.get("media") or []}
    keep = [have[i] for i in ids if i in have]
    for mid in set(have) - {m["id"] for m in keep}:
        _remove(proj, mid)
    cfg["media"], cfg["order"] = keep, order
    svc.update_item(db, v, item_uid, config=cfg)


def _remove(proj: Project, mid: str) -> None:
    try:
        os.remove(path_of(proj, mid))
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("outline media: a file could not be removed (%s)", type(e).__name__)


def purge(project_id: str) -> None:
    """Remove all of a project's material files (the retention clean-up calls this)."""
    shutil.rmtree(os.path.join(settings.media_dir, "outline", project_id), ignore_errors=True)


def svc_state(db: Session, proj: Project):
    from . import session as sess
    return sess.ensure(db, proj)
