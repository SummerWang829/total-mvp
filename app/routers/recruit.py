"""Recruiting (staff of the project): import from a spreadsheet, tags, eligibility rules, quotas, and inviting many people to sessions at once."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import library_context, participant_code, recruit, recruit_form, recruit_insights
from ..audit import audit
from ..db import get_db
from ..deps import RECRUITERS, STAFF, Ctx, require_roles
from ..moderator.providers import ChatFatalError
from ..recruit_form import MAX_CHAT
from ..models import Participant, Project, RecruitForm, RecruitSettings, utcnow
from ..retention import erase_participant
from .interviews import NewInterview, create_interview
from .projects import _own_participant, get_project, participant_out

router = APIRouter(prefix="/api", tags=["recruiting"])
MAX_BATCH = 100


class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = False
    source_name: str = Field(default="", max_length=80)  # where the list came from (shown next to every person of the batch)
    mode: Literal["invite", "screen"] = "invite"  # invite = straight to "can be invited"; screen = still to be screened
    confirm_basis: bool = False  # the team confirms there is a lawful basis to contact these people


class FormIn(BaseModel):
    source: str = "ai"
    idea: str = Field(default="", max_length=5000)
    goals: list[str] = Field(default_factory=list, max_length=8)
    need: str = Field(default="", max_length=1500)
    html_url: str = Field(default="", max_length=500)
    questions: list[dict] = Field(default_factory=list, max_length=15)


class PreviewBody(BaseModel):
    questions: list[dict] = Field(default_factory=list, max_length=30)
    answers: dict = Field(default_factory=dict)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    questions: list[dict] = Field(default_factory=list, max_length=30)  # the questionnaire as the page has it now (manual edits included)
    goals: list[str] = Field(default_factory=list, max_length=8)
    need: str = Field(default="", max_length=1500)


class IdeaBody(BaseModel):
    idea: str = Field(default="", max_length=5000)
    use_library: bool = True  # kept for older pages: the project's research library always goes along now


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
    label = f"{body.source_name.strip()} · {utcnow():%m-%d}"
    if not body.dry_run and (not body.source_name.strip() or not body.confirm_basis):
        raise HTTPException(422, "请写这批名单的来源，并勾选“有合法的联系依据”")
    out = recruit.import_rows(db, ctx.org_id, proj.id, rows, proj.language, body.dry_run, source="import", source_name=label, invite=body.mode == "invite")
    if not body.dry_run:
        s = _settings(db, proj)
        if s.rules:
            recruit.apply_rules(db, proj.id, s.rules, s.rules_version)
        audit(db, ctx.org_id, ctx.actor, "participant.import", proj.id, created=out["created"], skipped=len(out["skipped"]) + len(problems), source=label, mode=body.mode, basis_confirmed=True)
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
        return {"source": "ai", "idea": "", "html_url": "", "questions": [], "version": 0, "generated_by": "", "status": "draft", "link": "", "stats": {"qualified": 0, "out": 0, "out_by": {}},
                "goals": [], "need": "", "chat": []}
    return {"source": f.source, "idea": f.idea, "html_url": f.html_url, "questions": [q.model_dump() for q in recruit_form.load(f.questions)], "version": f.version, "generated_by": f.generated_by,
            "goals": f.goals or [], "need": f.need or "", "chat": (f.chat or [])[-MAX_CHAT:],
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


@router.get("/projects/{pid}/recruiting/dashboard")
def dashboard(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The numbers of the recruiting page: started / qualified / screened out, the funnel, where people came from, which question stops most, quotas, what the qualified have in common."""
    proj = get_project(db, ctx, pid)
    return {**recruit_insights.build(db, proj), "form": _form_out(db.get(RecruitForm, proj.id))}


@router.post("/projects/{pid}/recruiting/dashboard/summary")
def dashboard_summary(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The AI reads the aggregate numbers and says in plain words who the qualified people are and where to aim next. Only statistics are sent (no personal data)."""
    proj = get_project(db, ctx, pid)
    chat = _ai_chat()
    try:
        text = recruit_insights.summarize(chat, recruit_insights.build(db, proj))
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except recruit_form.FormError as e:
        raise HTTPException(422 if "还没有足够" in str(e) else 502, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "recruit.summary", proj.id, model=chat.name if chat else "mock")
    db.commit()
    return {"summary": text}


class ExportBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=2000)  # empty = everyone
    contacts: bool = True  # false: only the number, tags, source and key answers (to show a client)


class ExportLog(BaseModel):
    count: int = Field(ge=0, le=100_000)


RESULT_LABEL = {"qualified": "合格", "disqualified": "不合格", "review": "待人工看", "": "未筛"}


def _result_label(p: Participant) -> str:
    if p.screening == "blocked_region":
        return "地区暂未开放"
    return "外部名单 · 可邀请" if p.screen_reason == recruit.EXTERNAL_REASON else RESULT_LABEL.get(p.screen_result, "")


@router.post("/projects/{pid}/participants/export")
def export_participants(pid: str, body: ExportBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The participant list as a CSV file that Excel opens (UTF-8 with a mark). Everyone, or only the given people; with or without name and e-mail. Recorded in the audit log."""
    import csv
    import io
    proj = get_project(db, ctx, pid)
    people = db.scalars(select(Participant).where(Participant.project_id == proj.id, Participant.anonymized_at.is_(None)).order_by(Participant.created_at)).all()
    number = participant_code.code_map(db, proj.id)  # the same numbers the list shows
    chosen = set(body.ids)
    rows = [p for p in people if not chosen or p.id in chosen]
    key = recruit_insights.key_answers(db, [p.id for p in rows])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["编号", *(["姓名", "邮箱"] if body.contacts else []), "国家", "语言", "标签", "资格", "来源", "关键回答"])
    for p in rows:
        w.writerow([number[p.id], *([p.name, p.email] if body.contacts else []), p.country, p.language, "、".join(p.tags or []), _result_label(p), p.source_name or recruit_insights.SOURCE_LABELS.get(p.source, ""), key.get(p.id, "")])
    audit(db, ctx.org_id, ctx.actor, "participant.export", proj.id, count=len(rows), contacts=body.contacts, scope="selected" if chosen else "all")
    db.commit()
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="participants.csv"', "Cache-Control": "no-store"})


@router.post("/projects/{pid}/participants/export-log", status_code=204)
def log_copy(pid: str, body: ExportLog, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The page copied the list to the clipboard: only how many people is written to the audit log (never the content)."""
    proj = get_project(db, ctx, pid)
    audit(db, ctx.org_id, ctx.actor, "participant.copy", proj.id, count=body.count)
    db.commit()


@router.get("/projects/{pid}/recruiting/form")
def get_form(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The recruiting questionnaire (empty until the team writes one)."""
    proj = get_project(db, ctx, pid)
    return {**_form_out(db.get(RecruitForm, proj.id)), "library_docs": len(library_context.project_documents(db, proj.id)), "brief": proj.brief or ""}


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
    f.goals, f.need = form.goals, form.need
    f.version, f.updated_at = (f.version or 0) + 1, utcnow()
    if form.source != "ai" and f.status == "published":
        f.status = "closed"  # the link only serves the questionnaire written here
    audit(db, ctx.org_id, ctx.actor, "recruit.form", proj.id, source=form.source, questions=len(form.questions))
    db.commit()
    return _form_out(f)


@router.post("/projects/{pid}/recruiting/form/chat")
def chat_form(pid: str, body: ChatBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """One turn of the conversation that edits the questionnaire: the AI reads what the team says, the current questionnaire, the plan's research background and the
    research library, and answers with a reply and the changes (which are saved). The conversation is kept with the form. The text is sent to the AI service (the page says
    so) and is not written to the log. Takes several seconds."""
    proj = get_project(db, ctx, pid)
    chat = _ai_chat()
    try:
        current = recruit_form.normalize({"source": "ai", "questions": body.questions, "goals": body.goals, "need": body.need})
    except recruit_form.FormError as e:
        raise HTTPException(422, str(e)) from e
    f = db.get(RecruitForm, proj.id)
    if f is None:
        f = RecruitForm(project_id=proj.id, org_id=proj.org_id)
        db.add(f)
    library = library_context.library_text(db, proj.id)[0]
    try:
        out = recruit_form.chat_turn(chat, body.message, f.chat or [], current.questions, current.goals, current.need, proj.language, proj.brief or "", library)
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except recruit_form.FormError as e:
        raise HTTPException(422 if "先说点什么" in str(e) else 502, str(e)) from e
    questions = out.questions if out.questions is not None else current.questions
    f.source, f.html_url, f.questions = "ai", "", [q.model_dump() for q in questions]
    f.goals = out.goals if out.goals is not None else current.goals
    f.need = out.need if out.need is not None else current.need
    f.chat = [*(f.chat or []), {"role": "user", "text": body.message.strip()}, {"role": "ai", "text": out.reply}][-MAX_CHAT:]
    f.generated_by, f.version, f.updated_at = chat.name if chat else "mock", (f.version or 0) + 1, utcnow()
    audit(db, ctx.org_id, ctx.actor, "recruit.form_chat", proj.id, model=f.generated_by, changed=out.questions is not None, library=bool(library))  # what was said is not written to the log
    db.commit()
    return {**_form_out(f), "reply": out.reply, "changed": out.questions is not None, "used": {"brief": bool((proj.brief or "").strip()), "library": len(library_context.project_documents(db, proj.id))}}


@router.post("/projects/{pid}/recruiting/form/generate")
def generate_form(pid: str, body: IdeaBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The AI writes the questionnaire from the research idea and it replaces the saved questions. The idea is sent to the AI service (the page says so); takes several seconds."""
    proj = get_project(db, ctx, pid)
    chat = _ai_chat()
    library = library_context.library_text(db, proj.id)[0]  # the idea, the plan's research background and the library are combined, always
    try:
        questions = recruit_form.generate(chat, body.idea, proj.language, library, proj.brief or "")
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
    audit(db, ctx.org_id, ctx.actor, "recruit.form_generate", proj.id, model=f.generated_by, questions=len(f.questions), library=bool(library), brief=bool((proj.brief or "").strip()))  # the idea itself is not written to the log
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


class TierBody(BaseModel):
    tier: Literal["formal", "backup", "auto"]  # auto = back to what the quotas say


@router.put("/participants/{part_id}/tier")
def put_tier(part_id: str, body: TierBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Set a person formal or backup by hand (kept until set back to auto)."""
    p = _own_participant(db, ctx, part_id)
    p.tier_override = "" if body.tier == "auto" else body.tier
    audit(db, ctx.org_id, ctx.actor, "participant.tier", p.id, tier=body.tier)
    db.commit()
    return participant_out(p)


class TagBody(BaseModel):
    tag: str = Field(min_length=1, max_length=40)


@router.get("/projects/{pid}/recruiting/tags")
def tag_library(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The tags people in this project can be given, with how many people have each."""
    proj = get_project(db, ctx, pid)
    people = db.scalars(select(Participant.tags).where(Participant.project_id == proj.id, Participant.anonymized_at.is_(None))).all()
    return {"tags": [{"tag": t, "n": sum(t in (x or []) for x in people)} for t in recruit_insights.tag_library(db, proj.id)]}


@router.post("/projects/{pid}/recruiting/tags", status_code=201)
def add_library_tag(pid: str, body: TagBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Add a tag to the project's library so it can be picked for anyone."""
    proj = get_project(db, ctx, pid)
    tags = recruit.clean_tags([body.tag])
    if not tags:
        raise HTTPException(422, "标签不能为空")
    s = _settings(db, proj)
    s.tag_hidden = [t for t in (s.tag_hidden or []) if t != tags[0]]
    if tags[0] not in (s.tag_lib or []):
        s.tag_lib = [*(s.tag_lib or []), tags[0]]
    audit(db, ctx.org_id, ctx.actor, "recruit.tag_add", proj.id)
    db.commit()
    return tag_library(pid, ctx, db)


@router.delete("/projects/{pid}/recruiting/tags")
def remove_library_tag(pid: str, tag: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Take a tag out of the library and off everybody who has it."""
    proj = get_project(db, ctx, pid)
    s = _settings(db, proj)
    s.tag_lib = [t for t in (s.tag_lib or []) if t != tag]
    if tag not in (s.tag_hidden or []):
        s.tag_hidden = [*(s.tag_hidden or []), tag]
    removed = 0
    for p in db.scalars(select(Participant).where(Participant.project_id == proj.id)):
        if tag in (p.tags or []):
            p.tags = [t for t in p.tags if t != tag]
            removed += 1
    audit(db, ctx.org_id, ctx.actor, "recruit.tag_remove", proj.id, people=removed)
    db.commit()
    return tag_library(pid, ctx, db)


class DeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/projects/{pid}/participants/delete")
def delete_participants(pid: str, body: DeleteBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Erase the data of several participants at once (irreversible; the same as erasing each one)."""
    proj = get_project(db, ctx, pid)
    people = db.scalars(select(Participant).where(Participant.project_id == proj.id, Participant.id.in_(set(body.ids)), Participant.anonymized_at.is_(None))).all()
    if not people:
        raise HTTPException(404, "没有可删除的受访者")
    purged = sum(erase_participant(db, p, ctx.actor) for p in people)
    audit(db, ctx.org_id, ctx.actor, "participant.bulk_erase", proj.id, people=len(people), interviews=purged)
    db.commit()
    return {"erased": len(people), "purged_interviews": purged}


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
