"""Highlight reels (集锦): an ordered set of a project's clips with a title and a short note, that can be shared as one link.

A reel adds no new video: it points at clips that already exist, and the shared page plays each clip through the same watermarked, time-limited,
non-downloadable cut that a single shared clip uses. A reel link has its own expiry and watermark and can be revoked on its own.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Clip, Evidence, Insight, Interview, Participant, Recording, Reel, ReelItem
from .security import sign_media

MAX_CLIPS = 30
MAX_TITLE = 200
MAX_NOTE = 1000


class ReelError(ValueError):
    """The reel cannot be saved as asked; the message is plain Chinese for the researcher."""


def project_clips(db: Session, project_id: str) -> list[dict]:
    """Every clip of the project's interviews that is still there (not purged), with its interview, and the approved insight it came from (if any)."""
    out = []
    rows = db.execute(select(Clip, Interview).join(Interview, Interview.id == Clip.interview_id)
                      .where(Interview.project_id == project_id, Interview.purged_at.is_(None)).order_by(Interview.created_at, Clip.start_ms)).all()
    code = {p.id: f"P-{n + 1:03d}" for n, p in enumerate(db.scalars(select(Participant).where(Participant.project_id == project_id).order_by(Participant.created_at)))}
    for c, itv in rows:
        ev = db.get(Evidence, c.evidence_id) if c.evidence_id else None
        ins = db.get(Insight, ev.insight_id) if ev else None
        approved = ins is not None and ins.status == "approved"
        out.append({"id": c.id, "title": c.title, "interview_id": itv.id, "interview": itv.title, "who": code.get(itv.participant_id, "?") if itv.kind != "group" else "座谈会",
                    "start_ms": c.start_ms, "end_ms": c.end_ms, "theme": ins.theme if approved else None, "quote": ev.quote if approved and ev else None})
    return out


def check_items(db: Session, project_id: str, clip_ids: list[str]) -> list[str]:
    """The clip ids in the chosen order, each of them a clip of this project (once).

    Raises:
        ReelError: none chosen, too many, or a clip that is not in the project any more.
    """
    ids = list(dict.fromkeys(clip_ids))
    if not ids:
        raise ReelError("请至少选一个片段")
    if len(ids) > MAX_CLIPS:
        raise ReelError(f"一个集锦最多 {MAX_CLIPS} 个片段")
    have = {c["id"] for c in project_clips(db, project_id)}
    if any(i not in have for i in ids):
        raise ReelError("有片段不在这个项目里，或已被清除")
    return ids


def set_items(db: Session, reel: Reel, clip_ids: list[str]) -> None:
    """Replace the reel's clips with these, in this order (the caller commits)."""
    db.query(ReelItem).filter(ReelItem.reel_id == reel.id).delete()
    db.flush()
    for n, cid in enumerate(clip_ids):
        db.add(ReelItem(reel_id=reel.id, clip_id=cid, position=n))
    db.flush()


def items_of(db: Session, reel_id: str) -> list[Clip]:
    """The reel's clips in order, leaving out any that were purged since."""
    rows = db.execute(select(Clip, ReelItem).join(ReelItem, ReelItem.clip_id == Clip.id).where(ReelItem.reel_id == reel_id).order_by(ReelItem.position)).all()
    alive = {i.id for i in db.scalars(select(Interview).where(Interview.id.in_({c.interview_id for c, _ in rows}), Interview.purged_at.is_(None)))} if rows else set()
    return [c for c, _ in rows if c.interview_id in alive]


def staff_items(db: Session, reel: Reel) -> list[dict]:
    by = {c["id"]: c for c in project_clips(db, reel.project_id)}
    return [by[c.id] for c in items_of(db, reel.id) if c.id in by]


def public_items(db: Session, reel: Reel, watermark: str) -> list[dict]:
    """What the shared page gets: for each clip its title, the approved theme and quote (only when a researcher approved the insight), and a signed, watermarked,
    short-lived address of the cut. Clips whose recording is not ready have no playback."""
    out = []
    for c in items_of(db, reel.id):
        rec = db.scalar(select(Recording).where(Recording.interview_id == c.interview_id, Recording.status == "ready"))
        ev = db.get(Evidence, c.evidence_id) if c.evidence_id else None
        ins = db.get(Insight, ev.insight_id) if ev else None
        ok = ins is not None and ins.status == "approved"
        out.append({"title": c.title, "theme": ins.theme if ok else None, "quote": ev.quote if ok and ev else None,
                    "playback": {"url": "/api/public/media?t=" + sign_media(rec.storage_key, c.start_ms, c.end_ms, watermark=watermark), "start_ms": c.start_ms, "end_ms": c.end_ms,
                                 "expires_in": settings.media_url_ttl_s, "download": False} if rec else None})
    return out
