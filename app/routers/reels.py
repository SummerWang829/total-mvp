"""Highlight reels: ordered sets of clips, shared as one link (staff of the project), and the public page's data. Logic in app/reels.py."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import reels
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, client_ip, require_roles
from ..models import Reel, ReelItem, ReelShare, utcnow
from ..security import new_secret_token, token_hash
from .projects import get_project

router = APIRouter(prefix="/api", tags=["reels"])


class ReelIn(BaseModel):
    title: str = Field(min_length=1, max_length=reels.MAX_TITLE)
    note: str = Field(default="", max_length=reels.MAX_NOTE)
    clip_ids: list[str] = Field(min_length=1, max_length=reels.MAX_CLIPS)


class ShareIn(BaseModel):
    expires_hours: int = Field(default=72, ge=1, le=720)
    watermark: str | None = Field(default=None, max_length=200)


def _reel(db: Session, ctx: Ctx, rid: str) -> Reel:
    r = db.get(Reel, rid)
    if not r or r.org_id != ctx.org_id:
        raise HTTPException(404, "集锦不存在")
    get_project(db, ctx, r.project_id)          # 404 for a project the person is not on
    return r


def reel_out(db: Session, r: Reel, detail: bool = False) -> dict:
    out = {"id": r.id, "title": r.title, "note": r.note, "created_at": r.created_at.isoformat(), "clips": len(reels.items_of(db, r.id))}
    if detail:
        out["items"] = reels.staff_items(db, r)
        out["shares"] = [{"id": s.id, "expires_at": s.expires_at.isoformat(), "revoked": s.revoked_at is not None, "views": s.views, "watermark": s.watermark}
                         for s in db.scalars(select(ReelShare).where(ReelShare.reel_id == r.id).order_by(ReelShare.created_at))]
    return out


@router.get("/projects/{pid}/clips")
def list_clips(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """All clips of the project (to choose from when building a reel)."""
    proj = get_project(db, ctx, pid)
    return reels.project_clips(db, proj.id)


@router.get("/projects/{pid}/reels")
def list_reels(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    return [reel_out(db, r) for r in db.scalars(select(Reel).where(Reel.project_id == proj.id).order_by(Reel.created_at.desc()))]


@router.post("/projects/{pid}/reels", status_code=201)
def create_reel(pid: str, body: ReelIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    try:
        ids = reels.check_items(db, proj.id, body.clip_ids)
    except reels.ReelError as e:
        raise HTTPException(422, str(e)) from e
    r = Reel(org_id=ctx.org_id, project_id=proj.id, title=body.title.strip(), note=body.note.strip(), created_by=ctx.user.id)
    db.add(r)
    db.flush()
    reels.set_items(db, r, ids)
    audit(db, ctx.org_id, ctx.actor, "reel.create", r.id, clips=len(ids))
    db.commit()
    return reel_out(db, r, detail=True)


@router.get("/reels/{rid}")
def get_reel(rid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return reel_out(db, _reel(db, ctx, rid), detail=True)


@router.put("/reels/{rid}")
def edit_reel(rid: str, body: ReelIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Change the title, note and the clips with their order."""
    r = _reel(db, ctx, rid)
    try:
        ids = reels.check_items(db, r.project_id, body.clip_ids)
    except reels.ReelError as e:
        raise HTTPException(422, str(e)) from e
    r.title, r.note = body.title.strip(), body.note.strip()
    reels.set_items(db, r, ids)
    audit(db, ctx.org_id, ctx.actor, "reel.edit", r.id, clips=len(ids))
    db.commit()
    return reel_out(db, r, detail=True)


@router.delete("/reels/{rid}", status_code=204)
def delete_reel(rid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Delete the reel and revoke every link to it (the clips themselves stay)."""
    r = _reel(db, ctx, rid)
    now = utcnow()
    for s in db.scalars(select(ReelShare).where(ReelShare.reel_id == r.id)):
        s.revoked_at = s.revoked_at or now
    db.query(ReelItem).filter(ReelItem.reel_id == r.id).delete()
    audit(db, ctx.org_id, ctx.actor, "reel.delete", r.id)
    db.delete(r)
    db.commit()


@router.post("/reels/{rid}/share", status_code=201)
def share_reel(rid: str, body: ShareIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One link to the whole reel (shown once). Every clip plays watermarked, short-lived and without a download button."""
    r = _reel(db, ctx, rid)
    if not reels.items_of(db, r.id):
        raise HTTPException(409, "集锦里没有可分享的片段")
    token = new_secret_token()
    s = ReelShare(org_id=ctx.org_id, reel_id=r.id, token_hash=token_hash(token), watermark=body.watermark or f"星桐 · {ctx.user.email}",
                  expires_at=utcnow() + timedelta(hours=body.expires_hours), created_by=ctx.user.id)
    db.add(s)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "reel.share", s.id, reel=r.id, hours=body.expires_hours)
    db.commit()
    return {"id": s.id, "path": f"/reel.html?t={token}", "expires_at": s.expires_at.isoformat(), "watermark": s.watermark}


@router.delete("/reel-shares/{sid}")
def revoke_reel_share(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    s = db.get(ReelShare, sid)
    if not s or s.org_id != ctx.org_id:
        raise HTTPException(404, "分享链接不存在")
    _reel(db, ctx, s.reel_id)
    s.revoked_at = s.revoked_at or utcnow()
    audit(db, ctx.org_id, ctx.actor, "reel.share_revoke", s.id)
    db.commit()
    return {"revoked": True}


# ---------- the public page ----------
@router.get("/public/reel/{token}")
def view_reel(token: str, request: Request, db: Session = Depends(get_db)):
    """The shared reel: title, note, and the clips with signed, watermarked playback addresses. A wrong, expired or revoked link all answer 404."""
    s = db.scalar(select(ReelShare).where(ReelShare.token_hash == token_hash(token)))
    if not s or s.revoked_at or s.expires_at < utcnow():
        raise HTTPException(404, "链接无效或已过期")
    reel = db.get(Reel, s.reel_id)
    if not reel:
        raise HTTPException(404, "链接无效或已过期")
    s.views += 1
    audit(db, s.org_id, f"share:{s.id}", "reel.view", reel.id, ip=client_ip(request))
    db.commit()
    return {"title": reel.title, "note": reel.note, "watermark": s.watermark, "expires_at": s.expires_at.isoformat(), "items": reels.public_items(db, reel, s.watermark)}
