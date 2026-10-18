"""Public-opinion monitoring (staff of the project): sources, mentions, summary, take-downs. The logic is in app/listening/."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..listening import service
from ..listening.connectors import KINDS, ConfigError, validate_config
from ..models import ListeningSource, Mention, Project

router = APIRouter(prefix="/api", tags=["listening"])
MAX_SOURCES = 30
MAX_PAGE = 100


class SourceIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=200)
    config: dict = Field(default_factory=dict)
    interval_min: int = Field(default=360, ge=15, le=10080)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict | None = None
    enabled: bool | None = None
    interval_min: int | None = Field(default=None, ge=15, le=10080)


class TakeDown(BaseModel):
    url: str = Field(default="", max_length=2000)
    author: str = Field(default="", max_length=200)


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    return proj


def _source(db: Session, ctx: Ctx, sid: str) -> ListeningSource:
    s = db.get(ListeningSource, sid)
    if not s or s.org_id != ctx.org_id:
        raise HTTPException(404, "来源不存在")
    ensure_project(db, ctx, s.project_id, "来源不存在")
    return s


def source_out(s: ListeningSource, mentions: int = 0) -> dict:
    return {"id": s.id, "kind": s.kind, "name": s.name, "config": s.config or {}, "enabled": s.enabled, "interval_min": s.interval_min, "mentions": mentions,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None, "last_status": s.last_status, "last_error": s.last_error, "last_count": s.last_count}


@router.get("/projects/{pid}/listening")
def overview(pid: str, days: int = Query(30, ge=1, le=365), ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    counts = dict(db.execute(select(Mention.source_id, func.count()).where(Mention.project_id == pid).group_by(Mention.source_id)).all())
    rows = db.scalars(select(ListeningSource).where(ListeningSource.project_id == pid).order_by(ListeningSource.created_at)).all()
    return {"kinds": list(KINDS), "sources": [source_out(s, counts.get(s.id, 0)) for s in rows], "summary": service.summary(db, pid, days)}


@router.post("/projects/{pid}/listening/sources", status_code=201)
def add_source(pid: str, body: SourceIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    if (db.scalar(select(func.count()).select_from(ListeningSource).where(ListeningSource.project_id == pid)) or 0) >= MAX_SOURCES:
        raise HTTPException(409, f"一个项目最多 {MAX_SOURCES} 个来源")
    try:
        cfg = validate_config(body.kind, body.config)
    except ConfigError as e:
        raise HTTPException(422, str(e)) from e
    s = ListeningSource(org_id=ctx.org_id, project_id=pid, kind=body.kind, name=body.name.strip(), config=cfg, interval_min=body.interval_min, created_by=ctx.user.id)
    db.add(s)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "listening.source_add", s.id, kind=s.kind)
    db.commit()
    return source_out(s)


@router.patch("/listening/sources/{sid}")
def patch_source(sid: str, body: SourcePatch, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    s = _source(db, ctx, sid)
    if body.config is not None:
        try:
            s.config = validate_config(s.kind, body.config)
        except ConfigError as e:
            raise HTTPException(422, str(e)) from e
    for k in ("name", "enabled", "interval_min"):
        if (v := getattr(body, k)) is not None:
            setattr(s, k, v.strip() if isinstance(v, str) else v)
    audit(db, ctx.org_id, ctx.actor, "listening.source_edit", s.id)
    db.commit()
    return source_out(s)


@router.delete("/listening/sources/{sid}", status_code=204)
def delete_source(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    s = _source(db, ctx, sid)
    db.query(Mention).filter(Mention.source_id == s.id).delete()
    audit(db, ctx.org_id, ctx.actor, "listening.source_delete", s.id)
    db.delete(s)
    db.commit()


@router.post("/listening/sources/{sid}/run")
def run_now(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Collect now (a few seconds) and tag what is new; the result says how many are new or why it failed."""
    s = _source(db, ctx, sid)
    result = service.run_source(db, s)
    db.commit()
    if result["ok"]:
        service.enrich_pending(db, limit=service.BATCH * 5)
        db.commit()
    audit(db, ctx.org_id, ctx.actor, "listening.run", s.id, ok=result["ok"], new=result["new"])
    db.commit()
    return result | {"source": source_out(s)}


@router.get("/projects/{pid}/listening/mentions")
def mentions(pid: str, sentiment: str = "", topic: str = "", lang: str = "", q: str = Query("", max_length=200), source_id: str = "", days: int = Query(30, ge=1, le=365),
             limit: int = Query(30, ge=1, le=MAX_PAGE), offset: int = Query(0, ge=0), ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    since = service.utcnow() - service.timedelta(days=days)
    stamp = func.coalesce(Mention.posted_at, Mention.fetched_at)
    cond = [Mention.project_id == pid, stamp >= since]
    if sentiment in service.SENTIMENTS:
        cond.append(Mention.sentiment == sentiment)
    if lang:
        cond.append(Mention.lang == lang)
    if source_id:
        cond.append(Mention.source_id == source_id)
    if q.strip():
        cond.append(Mention.text.contains(q.strip()))
    rows = db.scalars(select(Mention).where(*cond).order_by(stamp.desc()).limit(500)).all()
    if topic:
        rows = [m for m in rows if topic in (m.topics or [])]
    return {"total": len(rows), "mentions": [service.mention_out(m) for m in rows[offset:offset + limit]]}


@router.post("/projects/{pid}/listening/takedown")
def takedown(pid: str, body: TakeDown, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Remove everything from a link and/or an author (hash or name) and never collect it again."""
    _project(db, ctx, pid)
    if not (body.url.strip() or body.author.strip()):
        raise HTTPException(422, "请填写链接或作者")
    removed = service.take_down(db, ctx.org_id, pid, ctx.user.id, body.url, body.author)
    audit(db, ctx.org_id, ctx.actor, "listening.takedown", pid, removed=removed)
    db.commit()
    return {"removed": removed}


@router.delete("/listening/mentions/{mid}", status_code=204)
def delete_mention(mid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Remove one mention (and never collect that link again)."""
    m = db.get(Mention, mid)
    if not m or m.org_id != ctx.org_id:
        raise HTTPException(404, "内容不存在")
    ensure_project(db, ctx, m.project_id, "内容不存在")
    service.take_down(db, ctx.org_id, m.project_id, ctx.user.id, url=m.url)
    audit(db, ctx.org_id, ctx.actor, "listening.mention_delete", m.id)
    db.commit()
