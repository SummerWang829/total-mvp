"""Recruiting (staff of the project): import from a spreadsheet, tags, eligibility rules, quotas, and inviting many people to sessions at once."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import library_context, recruit, recruit_form
from ..audit import audit
from ..db import get_db
from ..deps import RECRUITERS, STAFF, Ctx, require_roles
from ..moderator.providers import ChatFatalError
from ..models import Participant, Project, RecruitForm, RecruitSettings, utcnow
from .interviews import NewInterview, create_interview
from .projects import _own_participant, get_project, participant_out

router = APIRouter(prefix="/api", tags=["recruiting"])
MAX_BATCH = 100


class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = False


class FormIn(BaseModel):
    source: str = "ai"
    idea: str = Field(default="", max_length=5000)
    html_url: str = Field(default="", max_length=500)
    questions: list[dict] = Field(default_factory=list, max_length=15)


class PreviewBody(BaseModel):
    questions: list[dict] = Field(default_factory=list, max_length=30)
    answers: dict = Field(default_factory=dict)


class IdeaBody(BaseModel):
    idea: str = Field(default="", max_length=5000)
    use_library: bool = False  # also hand the project's research library to the AI


def _ai_chat():
    """The model that writes questionnaires (None = the offline mock). A cloud model needs its vendor named, because the idea is sent there."""
    from ..config import settings
    from ..moderator.compliance import moderator_runs_locally
    from ..moderator.providers import get_moderator_chat
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, recruit_form.MSG_VENDOR_MISSING)
    return get_moderator_chat()


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


def _form_out(f: RecruitForm | None) -> dict:
    if f is None:
        return {"source": "ai", "idea": "", "html_url": "", "questions": [], "version": 0, "generated_by": "", "status": "draft", "link": "", "stats": {"qualified": 0, "out": 0, "out_by": {}}}
    return {"source": f.source, "idea": f.idea, "html_url": f.html_url, "questions": [q.model_dump() for q in recruit_form.load(f.questions)], "version": f.version, "generated_by": f.generated_by,
            "status": f.status or "draft", "link": f"/screen.html?t={f.token}" if f.token else "", "published_at": f.published_at.isoformat() if f.published_at else None,
            "stats": {"qualified": f.n_qualified or 0, "out": f.n_out or 0, "out_by": f.out_by or {}}}


def _questions(f: RecruitForm) -> list[recruit_form.Question]:
    return [recruit_form.Question.model_validate(q) for q in (f.questions or [])]


@router.post("/projects/{pid}/recruiting/form/preview")
def preview_form(pid: str, body: PreviewBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Test the questionnaire as it is being edited (nothing is stored or counted): the same walk candidates get, with the reason shown to the team."""
    get_project(db, ctx, pid)
    try:
        form = recruit_form.normalize({"source": "ai", "questions": body.questions})
        step = recruit_form.next_step(form.questions, body.answers)
    except recruit_form.FormError as e:
        raise HTTPException(422, str(e)) from e
    except recruit_form.AnswerError as e:
        raise HTTPException(422, "这个回答不符合题目要求") from e
    return {"question": step.question, "result": step.result, "reason": step.reason, "tags": step.tags}


@router.post("/projects/{pid}/recruiting/form/publish")
def publish_form(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Make the public link work (the first time it also creates the link). Needs written questions."""
    proj = get_project(db, ctx, pid)
    f = db.get(RecruitForm, proj.id)
    if f is None or f.source != "ai" or not f.questions:
        raise HTTPException(422, "先在这里写好问卷题目，再发布")
    if not f.token:
        f.token = recruit_form.new_token()
    f.status, f.published_at, f.updated_at = "published", utcnow(), utcnow()
    audit(db, ctx.org_id, ctx.actor, "recruit.form_publish", proj.id, questions=len(f.questions))
    db.commit()
    return _form_out(f)


@router.post("/projects/{pid}/recruiting/form/close")
def close_form(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Stop taking answers (the link shows "closed"); publishing again opens the same link."""
    proj = get_project(db, ctx, pid)
    f = db.get(RecruitForm, proj.id)
    if f is None or f.status != "published":
        raise HTTPException(409, "问卷没有在发布中")
    f.status, f.updated_at = "closed", utcnow()
    audit(db, ctx.org_id, ctx.actor, "recruit.form_close", proj.id)
    db.commit()
    return _form_out(f)


@router.get("/projects/{pid}/recruiting/form/qr.svg")
def form_qr(pid: str, request: Request, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The QR code of the public link, as an image (fetched by the page with the login header, so it is not a public address)."""
    import io

    import segno
    proj = get_project(db, ctx, pid)
    f = db.get(RecruitForm, proj.id)
    if f is None or not f.token:
        raise HTTPException(404, "问卷还没有发布过")
    host = request.headers.get("host") or request.url.netloc
    buf = io.BytesIO()
    segno.make(f"{request.url.scheme}://{host}/screen.html?t={f.token}", error="m").save(buf, kind="svg", scale=6, border=2, dark="#0B382C")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/projects/{pid}/recruiting/form")
def get_form(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The recruiting questionnaire (empty until the team writes one)."""
    proj = get_project(db, ctx, pid)
    return {**_form_out(db.get(RecruitForm, proj.id)), "library_docs": len(library_context.project_documents(db, proj.id))}


@router.put("/projects/{pid}/recruiting/form")
def put_form(pid: str, body: FormIn, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Save the questionnaire as edited by hand (an AI one with changed options, or an HTML link)."""
    proj = get_project(db, ctx, pid)
    try:
        form = recruit_form.normalize(body.model_dump())
    except recruit_form.FormError as e:
        raise HTTPException(422, str(e)) from e
    f = db.get(RecruitForm, proj.id)
    if f is None:
        f = RecruitForm(project_id=proj.id, org_id=proj.org_id)
        db.add(f)
    f.source, f.idea, f.html_url, f.questions = form.source, form.idea, form.html_url, [q.model_dump() for q in form.questions]
    f.version, f.updated_at = (f.version or 0) + 1, utcnow()
    if form.source != "ai" and f.status == "published":
        f.status = "closed"  # the link only serves the questionnaire written here
    audit(db, ctx.org_id, ctx.actor, "recruit.form", proj.id, source=form.source, questions=len(form.questions))
    db.commit()
    return _form_out(f)


@router.post("/projects/{pid}/recruiting/form/generate")
def generate_form(pid: str, body: IdeaBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The AI writes the questionnaire from the research idea and it replaces the saved questions. The idea is sent to the AI service (the page says so); takes several seconds."""
    proj = get_project(db, ctx, pid)
    chat = _ai_chat()
    library = library_context.library_text(db, proj.id)[0] if body.use_library else ""
    try:
        questions = recruit_form.generate(chat, body.idea, proj.language, library)
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except recruit_form.FormError as e:
        raise HTTPException(422 if "先写一下" in str(e) else 502, str(e)) from e
    f = db.get(RecruitForm, proj.id)
    if f is None:
        f = RecruitForm(project_id=proj.id, org_id=proj.org_id)
        db.add(f)
    f.source, f.idea, f.html_url, f.questions = "ai", body.idea.strip(), "", [q.model_dump() for q in questions]
    f.generated_by, f.version, f.updated_at = chat.name if chat else "mock", (f.version or 0) + 1, utcnow()
    audit(db, ctx.org_id, ctx.actor, "recruit.form_generate", proj.id, model=f.generated_by, questions=len(f.questions), library=bool(library))  # the idea itself is not written to the log
    db.commit()
    return _form_out(f)


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
