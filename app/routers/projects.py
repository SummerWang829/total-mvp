from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, current_ctx, require_roles
from ..models import Interview, InterviewObserver, Participant, Project
from ..models import utcnow
from ..retention import erase_participant, purge_date

router = APIRouter(prefix="/api", tags=["projects"])


def _lang(v: str | None) -> str | None:
    if v and v not in settings.languages:
        raise ValueError("language must be one of " + ", ".join(settings.languages))
    return v


class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brief: str = ""
    market: str = Field(default="SG", max_length=2)
    language: str = Field(default="en", max_length=10)
    _l = field_validator("language")(_lang)


class NewParticipant(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = ""
    country: str = Field(default="", max_length=2)
    language: str = Field(default="", max_length=10)
    _l = field_validator("language")(_lang)


def project_out(p: Project) -> dict:
    pd = purge_date(p)
    return {"id": p.id, "name": p.name, "brief": p.brief, "market": p.market, "language": p.language, "created_at": p.created_at.isoformat(),
            "closed_at": p.closed_at.isoformat() if p.closed_at else None, "purge_at": pd.isoformat() if pd else None,
            "retention_days": settings.retention_days}


def participant_out(p: Participant) -> dict:
    return {"id": p.id, "name": p.name, "email": p.email, "country": p.country, "language": p.language,
            "screening": p.screening, "anonymized": p.anonymized_at is not None}


def get_project(db: Session, ctx: Ctx, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    return p


@router.post("/projects", status_code=201)
def create_project(body: NewProject, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = Project(org_id=ctx.org_id, created_by=ctx.user.id, **body.model_dump(), )
    p.market = p.market.upper()
    db.add(p)
    db.flush()  # assigns the id, so the audit row points at this project
    audit(db, ctx.org_id, ctx.actor, "project.create", p.id)
    db.commit()
    return project_out(p)


@router.get("/projects")
def list_projects(ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    q = select(Project).where(Project.org_id == ctx.org_id).order_by(Project.created_at.desc())
    if ctx.user.role not in STAFF:  # moderators / observers only see projects they are assigned to
        mine = select(Interview.project_id).where(Interview.org_id == ctx.org_id)
        if ctx.user.role == "moderator":
            mine = mine.where(Interview.moderator_id == ctx.user.id)
        else:
            mine = mine.where(Interview.id.in_(select(InterviewObserver.interview_id).where(InterviewObserver.user_id == ctx.user.id)))
        q = q.where(Project.id.in_(mine))
        return [project_out(p) for p in db.scalars(q)]
    from .overview import project_summary  # staff project cards: participants, sessions, next session
    return [{**project_out(p), "summary": project_summary(db, p)} for p in db.scalars(q)]


@router.get("/projects/{pid}")
def project_detail(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return project_out(get_project(db, ctx, pid))


@router.post("/projects/{pid}/close")
def close_project(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Closing starts the 180-day retention clock. Personal data is deleted when it runs out."""
    proj = get_project(db, ctx, pid)
    if db.scalar(select(Interview.id).where(Interview.project_id == proj.id, Interview.status == "live")):
        raise HTTPException(409, "还有进行中的访谈，请先结束")
    if not proj.closed_at:
        proj.closed_at = utcnow()
        audit(db, ctx.org_id, ctx.actor, "project.close", proj.id)
        db.commit()
    return project_out(proj)


@router.post("/projects/{pid}/reopen")
def reopen_project(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    if proj.closed_at:
        proj.closed_at = None
        audit(db, ctx.org_id, ctx.actor, "project.reopen", proj.id)
        db.commit()
    return project_out(proj)


@router.post("/projects/{pid}/participants", status_code=201)
def add_participant(pid: str, body: NewParticipant, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    country = body.country.upper()
    screening = "eligible" if (not country or settings.region_open(country)) else "blocked_region"  # empty: declared at consent
    p = Participant(org_id=ctx.org_id, project_id=proj.id, name=body.name, email=body.email, country=country,
                    language=body.language, screening=screening)
    db.add(p)
    db.flush()  # assigns the id, so the audit row points at this participant
    audit(db, ctx.org_id, ctx.actor, "participant.create", p.id, screening=screening)
    db.commit()
    return participant_out(p)


@router.get("/projects/{pid}/participants")
def list_participants(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    return [participant_out(p) for p in db.scalars(select(Participant).where(Participant.project_id == proj.id).order_by(Participant.created_at))]


@router.delete("/participants/{part_id}/data")
def erase(part_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = db.get(Participant, part_id)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "受访者不存在")
    return {"purged_interviews": erase_participant(db, p, ctx.actor)}
