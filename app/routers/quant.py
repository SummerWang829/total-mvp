"""Quantitative results (app/quant.py): the keyed ingest API for an outside survey platform, and the project's own page (keys, imported results)."""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import quant, ratelimit, recruit
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles
from ..models import IngestKey, Project, QuantSurvey, RecruitSettings, utcnow
from .projects import _can_manage, get_project

router = APIRouter(prefix="/api", tags=["quant"])
INGEST_PER_MINUTE = (120, 60)
MAX_KEYS = 10


class KeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CsvBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    csv: str = Field(min_length=1, max_length=2_000_000)
    respondents: int = Field(default=0, ge=0, le=10_000_000)


def survey_out(s: QuantSurvey) -> dict:
    return {"id": s.id, "external_id": s.external_id, "title": s.title, "respondents": s.respondents, "questions": s.questions or [], "source": s.source,
            "updated_at": s.updated_at.isoformat()}


# ---------- the project's page ----------
@router.get("/projects/{pid}/quant")
def overview(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    keys = db.scalars(select(IngestKey).where(IngestKey.project_id == proj.id).order_by(IngestKey.created_at)).all()
    surveys = db.scalars(select(QuantSurvey).where(QuantSurvey.project_id == proj.id).order_by(QuantSurvey.updated_at.desc())).all()
    return {"can_manage": _can_manage(db, ctx, proj.id),
            "keys": [{"id": k.id, "name": k.name, "prefix": k.prefix, "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                      "revoked": k.revoked_at is not None} for k in keys], "surveys": [survey_out(s) for s in surveys]}


@router.post("/projects/{pid}/quant/keys", status_code=201)
def make_key(pid: str, body: KeyBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A key for an outside survey platform (shown once; stored as a hash). Project owners and admins only."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能创建密钥")
    if len(db.scalars(select(IngestKey.id).where(IngestKey.project_id == proj.id, IngestKey.revoked_at.is_(None))).all()) >= MAX_KEYS:
        raise HTTPException(409, f"一个项目最多 {MAX_KEYS} 个有效密钥，请先撤销不用的")
    key, digest, prefix = quant.new_key()
    row = IngestKey(org_id=ctx.org_id, project_id=proj.id, name=body.name.strip(), key_hash=digest, prefix=prefix, created_by=ctx.user.id)
    db.add(row)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "quant.key_create", row.id)
    db.commit()
    return {"id": row.id, "name": row.name, "prefix": prefix, "key": key}


@router.delete("/projects/{pid}/quant/keys/{kid}", status_code=204)
def revoke_key(pid: str, kid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能撤销密钥")
    row = db.get(IngestKey, kid)
    if not row or row.project_id != proj.id:
        raise HTTPException(404, "密钥不存在")
    row.revoked_at = utcnow()
    audit(db, ctx.org_id, ctx.actor, "quant.key_revoke", row.id)
    db.commit()


@router.post("/projects/{pid}/quant/surveys/import", status_code=201)
def import_results(pid: str, body: CsvBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Add (or replace, by title) a survey's results from a spreadsheet: columns question, option, count."""
    proj = get_project(db, ctx, pid)
    try:
        qs = quant.parse_results_csv(body.csv)
        row, created = quant.upsert_survey(db, ctx.org_id, proj.id, "csv:" + body.title.strip().lower()[:90], body.title, body.respondents, qs, "csv")
    except quant.QuantError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "quant.import", row.id, created=created, questions=len(qs))
    db.commit()
    return survey_out(row)


@router.delete("/quant/surveys/{sid}", status_code=204)
def delete_survey(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    row = db.get(QuantSurvey, sid)
    if not row or row.org_id != ctx.org_id:
        raise HTTPException(404, "结果不存在")
    get_project(db, ctx, row.project_id)
    audit(db, ctx.org_id, ctx.actor, "quant.survey_delete", row.id)
    db.delete(row)
    db.commit()


# ---------- the keyed API for an outside survey platform ----------
def _project_of_key(authorization: str | None, db: Session) -> tuple[IngestKey, Project]:
    presented = (authorization or "").removeprefix("Bearer ").strip()
    row = quant.key_row(db, presented)
    if not row:
        raise HTTPException(401, "密钥无效或已撤销")
    if wait := ratelimit.blocked(f"ingest:{row.id}", INGEST_PER_MINUTE):
        raise HTTPException(429, ratelimit.MESSAGE, headers={"Retry-After": str(wait)})
    ratelimit.record(f"ingest:{row.id}", INGEST_PER_MINUTE[1])
    row.last_used_at = utcnow()
    return row, db.get(Project, row.project_id)


class PeopleBody(BaseModel):
    participants: list[dict]


class SurveyBody(BaseModel):
    survey_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    respondents: int = Field(ge=0)
    questions: list[dict]


@router.get("/ingest/ping")
def ping(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Check a key: which project it belongs to."""
    row, proj = _project_of_key(authorization, db)
    db.commit()
    return {"ok": True, "project": proj.name}


@router.post("/ingest/participants")
def ingest_people(body: PeopleBody, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """People who passed screening in the survey tool, with their answers. Sending the same external_id again updates, never duplicates."""
    row, proj = _project_of_key(authorization, db)
    try:
        out = quant.upsert_people(db, proj, body.participants)
    except quant.QuantError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    s = db.get(RecruitSettings, proj.id)
    if s and s.rules:
        recruit.apply_rules(db, proj.id, s.rules, s.rules_version)
    audit(db, proj.org_id, f"ingest:{row.prefix}", "quant.people", proj.id, **out)
    db.commit()
    return out


@router.post("/ingest/surveys")
def ingest_survey(body: SurveyBody, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """A survey's aggregated results. The same survey_id again replaces the earlier numbers."""
    row, proj = _project_of_key(authorization, db)
    try:
        survey, created = quant.upsert_survey(db, proj.org_id, proj.id, body.survey_id, body.title, body.respondents, body.questions, "api")
    except quant.QuantError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    audit(db, proj.org_id, f"ingest:{row.prefix}", "quant.survey", survey.id, created=created)
    db.commit()
    return {"id": survey.id, "created": created}
