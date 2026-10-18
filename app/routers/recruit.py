"""Recruiting (staff of the project): import from a spreadsheet, tags, eligibility rules, quotas, and inviting many people to sessions at once."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import recruit
from ..audit import audit
from ..db import get_db
from ..deps import RECRUITERS, STAFF, Ctx, require_roles
from ..models import Participant, Project, RecruitSettings, utcnow
from .interviews import NewInterview, create_interview
from .projects import _own_participant, get_project, participant_out

router = APIRouter(prefix="/api", tags=["recruiting"])
MAX_BATCH = 100


class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = False


class RulesBody(BaseModel):
    rules: list[dict] = Field(default_factory=list)


class QuotasBody(BaseModel):
    quotas: list[dict] = Field(default_factory=list)


class TagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=30)


class BatchBody(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH)
    title: str = Field(min_length=1, max_length=200)
    language: str | None = None
    moderator_id: str | None = None
    observer_ids: list[str] = []
    moderation: str = "human"


def _settings(db: Session, proj: Project) -> RecruitSettings:
    s = db.get(RecruitSettings, proj.id)
    if not s:
        s = RecruitSettings(project_id=proj.id, org_id=proj.org_id, rules=[], quotas=[])
        db.add(s)
        db.flush()
    return s


def _overview(db: Session, proj: Project) -> dict:
    s = _settings(db, proj)
    people = db.scalars(select(Participant.screen_result).where(Participant.project_id == proj.id, Participant.anonymized_at.is_(None))).all()
    return {"rules": s.rules or [], "rules_version": s.rules_version, "quotas": recruit.quota_progress(db, proj.id, s.quotas or []),
            "counts": {k: people.count(k) for k in ("qualified", "disqualified", "review")} | {"not_screened": people.count("")}}


@router.post("/projects/{pid}/participants/import")
def import_participants(pid: str, body: ImportBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Add people from a spreadsheet (CSV, UTF-8). With dry_run nothing is stored: it only says what would happen."""
    proj = get_project(db, ctx, pid)
    try:
        rows, problems = recruit.parse_csv(body.csv)
    except recruit.RecruitError as e:
        raise HTTPException(422, str(e)) from e
    out = recruit.import_rows(db, ctx.org_id, proj.id, rows, proj.language, body.dry_run)
    if not body.dry_run:
        s = _settings(db, proj)
        if s.rules:
            recruit.apply_rules(db, proj.id, s.rules, s.rules_version)
        audit(db, ctx.org_id, ctx.actor, "participant.import", proj.id, created=out["created"], skipped=len(out["skipped"]) + len(problems))
        db.commit()
    return {"dry_run": body.dry_run, "created": out["created"], "skipped": sorted(out["skipped"] + problems, key=lambda x: x["line"])}


@router.get("/projects/{pid}/recruiting")
def recruiting(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    out = _overview(db, proj)
    db.commit()
    return out


@router.put("/projects/{pid}/recruiting/rules")
def put_rules(pid: str, body: RulesBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Save the eligibility rules (a new version) and evaluate everyone with them straight away."""
    proj = get_project(db, ctx, pid)
    try:
        rules = recruit.validate_rules(body.rules)
    except recruit.RecruitError as e:
        raise HTTPException(422, str(e)) from e
    s = _settings(db, proj)
    s.rules, s.rules_version, s.updated_at = rules, s.rules_version + 1, utcnow()
    recruit.apply_rules(db, proj.id, rules, s.rules_version)
    audit(db, ctx.org_id, ctx.actor, "recruit.rules", proj.id, version=s.rules_version, rules=len(rules))
    db.commit()
    return _overview(db, proj)


@router.post("/projects/{pid}/recruiting/apply")
def apply_now(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Evaluate everyone again with the current rules (after answers changed)."""
    proj = get_project(db, ctx, pid)
    s = _settings(db, proj)
    recruit.apply_rules(db, proj.id, s.rules or [], s.rules_version)
    db.commit()
    return _overview(db, proj)


@router.put("/projects/{pid}/recruiting/quotas")
def put_quotas(pid: str, body: QuotasBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    try:
        quotas = recruit.validate_quotas(body.quotas)
    except recruit.RecruitError as e:
        raise HTTPException(422, str(e)) from e
    s = _settings(db, proj)
    s.quotas, s.updated_at = quotas, utcnow()
    audit(db, ctx.org_id, ctx.actor, "recruit.quotas", proj.id, quotas=len(quotas))
    db.commit()
    return _overview(db, proj)


@router.put("/participants/{part_id}/tags")
def put_tags(part_id: str, body: TagsBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    p = _own_participant(db, ctx, part_id)
    p.tags = recruit.clean_tags(body.tags)
    audit(db, ctx.org_id, ctx.actor, "participant.tags", p.id, count=len(p.tags))
    db.commit()
    return participant_out(p)


@router.post("/projects/{pid}/interviews/batch", status_code=201)
def batch_invite(pid: str, body: BatchBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Create one session per selected participant with the same settings, and return every invitation link once. People who cannot be invited are listed, not fatal.

    Disqualified people are refused unless nothing else is selected for them (they are reported as skipped)."""
    proj = get_project(db, ctx, pid)
    made, skipped = [], []
    for part_id in dict.fromkeys(body.participant_ids):
        p = db.get(Participant, part_id)
        if not p or p.project_id != proj.id or p.org_id != ctx.org_id:
            raise HTTPException(422, "受访者不在这个项目里")
        if p.screen_result == "disqualified":
            skipped.append({"participant_id": p.id, "name": p.name, "why": f"不符合条件：{p.screen_reason}"})
            continue
        try:
            out = create_interview(pid, NewInterview(participant_id=p.id, title=body.title, language=body.language, moderator_id=body.moderator_id,
                                                     observer_ids=body.observer_ids, moderation=body.moderation), ctx, db)
        except HTTPException as e:
            db.rollback()
            skipped.append({"participant_id": p.id, "name": p.name, "why": str(e.detail)})
            continue
        made.append({"participant_id": p.id, "name": p.name, "interview_id": out["id"], "invite_path": out["invite_path"]})
    return {"invites": made, "skipped": skipped}
