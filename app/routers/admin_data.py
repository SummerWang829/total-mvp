"""Data retention for admins: when each project's personal data will be deleted, how much there is, keeping it longer on purpose, and deleting it now.

Rules (decided 2026-10-10): personal data is deleted 180 days after the project is closed; insight conclusions stay. An admin can hold the deletion back
(never forward: only the system's clock or an admin's explicit 'delete now' deletes) and can delete a closed project's personal data at once, after typing
the project's name. Everything here is written to the audit trail.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import Ctx, require_roles
from ..models import Interview, Mention, Participant, Project, RepoDocument, utcnow
from ..retention import anonymize_participant, purge_date, purge_interview, purge_project_extras

router = APIRouter(prefix="/api/admin/data", tags=["admin-data"])
MAX_HOLD_DAYS = 730


class HoldBody(BaseModel):
    days: int = Field(ge=1, le=MAX_HOLD_DAYS)


class PurgeBody(BaseModel):
    confirm: str = Field(max_length=200)


def _row(db: Session, p: Project) -> dict:
    pd = purge_date(p)
    interviews = db.scalar(select(func.count()).select_from(Interview).where(Interview.project_id == p.id, Interview.purged_at.is_(None), Interview.ended_at.is_not(None))) or 0
    people = db.scalar(select(func.count()).select_from(Participant).where(Participant.project_id == p.id, Participant.anonymized_at.is_(None))) or 0
    posts = db.scalar(select(func.count()).select_from(Mention).where(Mention.project_id == p.id)) or 0
    docs = db.scalar(select(func.count()).select_from(RepoDocument).where(RepoDocument.project_id == p.id)) or 0
    return {"id": p.id, "name": p.name, "closed_at": p.closed_at.isoformat() if p.closed_at else None, "purge_at": pd.isoformat() if pd else None,
            "days_left": max(0, (pd - utcnow()).days) if pd else None, "hold_until": p.purge_hold_until.isoformat() if p.purge_hold_until else None,
            "holds": {"interviews": interviews, "participants": people, "mentions": posts, "documents": docs}}


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    return p


@router.get("")
def overview(ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = [_row(db, p) for p in db.scalars(select(Project).where(Project.org_id == ctx.org_id))]
    rows.sort(key=lambda r: (r["purge_at"] is None, r["purge_at"] or "", r["name"]))
    return {"retention_days": settings.retention_days, "backstop_days": settings.retention_backstop_days, "projects": rows}


@router.post("/{pid}/hold")
def hold(pid: str, body: HoldBody, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Keep this project's personal data for `days` more days than it would have been kept (counted from the later of its deletion date and today)."""
    p = _project(db, ctx, pid)
    if not p.closed_at:
        raise HTTPException(409, "项目还没有结项，没有删除日期可以延后")
    base = max(purge_date(p), utcnow())
    p.purge_hold_until = base + timedelta(days=body.days)
    audit(db, ctx.org_id, ctx.actor, "retention.hold", p.id, days=body.days, until=p.purge_hold_until.date().isoformat())
    db.commit()
    return _row(db, p)


@router.delete("/{pid}/hold")
def release_hold(pid: str, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    p.purge_hold_until = None
    audit(db, ctx.org_id, ctx.actor, "retention.hold_release", p.id)
    db.commit()
    return _row(db, p)


@router.post("/{pid}/purge")
def purge_now(pid: str, body: PurgeBody, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Delete a closed project's personal data at once (recordings, transcripts, quotes, clips, chats, people, collected posts, imported documents); insight conclusions stay.

    The project's name must be typed exactly: this cannot be undone."""
    p = _project(db, ctx, pid)
    if not p.closed_at:
        raise HTTPException(409, "只能立即清除已结项的项目，请先结项")
    if body.confirm.strip() != p.name.strip():
        raise HTTPException(422, "项目名称对不上，没有删除")
    n = 0
    for itv in db.scalars(select(Interview).where(Interview.project_id == p.id, Interview.purged_at.is_(None))).all():
        people = [s.participant for s in itv.group_participants] if itv.kind == "group" else [itv.participant]
        purge_interview(db, itv, "admin-request", ctx.actor)
        for person in people:
            if person.anonymized_at is None:
                anonymize_participant(db, person)
        n += 1
    extras = purge_project_extras(db, p, ctx.actor, "admin-request")
    audit(db, ctx.org_id, ctx.actor, "retention.purge_now", p.id, interviews=n, **extras)
    db.commit()
    return {"purged_interviews": n, **extras}
