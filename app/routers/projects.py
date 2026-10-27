from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import member_scopes, plans, recruit, recruit_form, recruit_insights, screener
from .. import methods as methods_mod
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import ANY_PROJECT_ROLE, MEMBER_ROLES, RECRUITERS, STAFF, Ctx, can_see_project, current_ctx, ensure_project, require_roles
from ..models import Interview, InterviewObserver, Participant, Project, ProjectMember, ProjectPlan, RecruitForm, RecruitSettings, User
from ..models import utcnow
from ..retention import days_of, erase_participant, purge_date

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
    methods: list[str] = Field(default_factory=lambda: list(methods_mod.DEFAULT))
    member_ids: list[str] = Field(default_factory=list, max_length=50)  # researchers to add besides the creator
    _l = field_validator("language")(_lang)


class MethodsBody(BaseModel):
    methods: list[str]


class NewParticipant(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = ""
    country: str = Field(default="", max_length=2)
    language: str = Field(default="", max_length=10)
    screener_text: str = Field(default="", max_length=20000)  # optional: the screening answers, one "question：answer" per line
    _l = field_validator("language")(_lang)


class ScreenerBody(BaseModel):
    text: str = Field(max_length=20000)


def project_out(p: Project, plan_status: str = "", setup: dict | None = None, editing: str = "") -> dict:
    """`plan_status`: "" for a project made before plans existed (it counts as published), else draft / review / approved / published (a plan that was
    published stays "published" while it is unlocked; `editing` then says where the unlocked plan stands: draft / review / approved)."""
    pd = purge_date(p)
    return {"id": p.id, "name": p.name, "brief": p.brief, "market": p.market, "language": p.language, "created_at": p.created_at.isoformat(),
            "closed_at": p.closed_at.isoformat() if p.closed_at else None, "purge_at": pd.isoformat() if pd else None,
            "retention_days": days_of(p), "methods": methods_mod.of(p), "plan_status": plan_status, "setup": setup, "editing": editing}


def participant_out(p: Participant, screener_count: int = 0, key_answers: str = "", tier: str = "", kind: str = "") -> dict:
    return {"id": p.id, "name": p.name, "email": p.email, "country": p.country, "language": p.language,
            "screening": p.screening, "anonymized": p.anonymized_at is not None, "screener": screener_count,
            "tags": p.tags or [], "screen_result": p.screen_result, "screen_reason": p.screen_reason, "source": p.source, "source_name": p.source_name, "key_answers": key_answers,
            "code": p.code, "tier": tier, "tier_manual": bool(p.tier_override), "interview_kind": kind, "availability": [a for a in (p.availability or [])]}


def get_project(db: Session, ctx: Ctx, pid: str) -> Project:
    """The project, or 404: another company's, or one this researcher is not a member of."""
    p = db.get(Project, pid)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, p.id)
    return p


@router.post("/projects", status_code=201)
def create_project(body: NewProject, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    try:
        chosen = methods_mod.clean(body.methods)
    except methods_mod.MethodsError as e:
        raise HTTPException(422, str(e)) from e
    others = [u for u in dict.fromkeys(body.member_ids) if u != ctx.user.id]
    users = {u.id: u for u in db.scalars(select(User).where(User.org_id == ctx.org_id, User.id.in_(others)))} if others else {}
    if any(u not in users or users[u].role != "researcher" or not users[u].active for u in others):
        raise HTTPException(422, "只能把本公司在职的研究员加进项目")
    p = Project(org_id=ctx.org_id, created_by=ctx.user.id, **body.model_dump(exclude={"methods", "member_ids"}), methods=methods_mod.store(chosen))
    p.market = p.market.upper()
    db.add(p)
    db.flush()  # assigns the id, so the audit row points at this project
    db.add(ProjectMember(org_id=ctx.org_id, project_id=p.id, user_id=ctx.user.id, is_owner=True, added_by=ctx.user.id))  # the creator owns it
    for uid_ in others:
        db.add(ProjectMember(org_id=ctx.org_id, project_id=p.id, user_id=uid_, is_owner=False, added_by=ctx.user.id))
    audit(db, ctx.org_id, ctx.actor, "project.create", p.id, methods=",".join(chosen), members=len(others) + 1)
    db.commit()
    return project_out(p)


@router.get("/projects")
def list_projects(ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    q = select(Project).where(Project.org_id == ctx.org_id).order_by(Project.created_at.desc())
    rows = db.scalars(select(ProjectPlan).where(ProjectPlan.org_id == ctx.org_id)).all()
    status = {r.project_id: plans.public_status(r) for r in rows}
    setups = {r.project_id: plans.summary(plans.live_json(r)) for r in rows}
    edits = {r.project_id: r.status for r in rows if r.version > 0 and r.status != "published"}
    if ctx.user.role in ("recruiter", "viewer"):  # recruiters and viewers see the projects they were added to, without the staff cards
        q = q.where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == ctx.user.id)))
        return [project_out(p, status.get(p.id, ""), setups.get(p.id), edits.get(p.id, "")) for p in db.scalars(q)]
    if ctx.user.role not in STAFF:  # moderators / observers only see projects they are assigned to
        mine = select(Interview.project_id).where(Interview.org_id == ctx.org_id)
        if ctx.user.role == "moderator":
            mine = mine.where(Interview.moderator_id == ctx.user.id)
        else:
            mine = mine.where(Interview.id.in_(select(InterviewObserver.interview_id).where(InterviewObserver.user_id == ctx.user.id)))
        q = q.where(Project.id.in_(mine))
        return [project_out(p, status.get(p.id, ""), setups.get(p.id), edits.get(p.id, "")) for p in db.scalars(q)]
    if ctx.user.role in ("researcher", "support"):  # a researcher (or technical support) sees only the projects they belong to
        q = q.where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == ctx.user.id)))
    from .overview import project_summary  # staff project cards: participants, sessions, next session
    return [{**project_out(p, status.get(p.id, ""), setups.get(p.id), edits.get(p.id, "")), "summary": project_summary(db, p)} for p in db.scalars(q)]


@router.get("/projects/{pid}")
def project_detail(pid: str, ctx: Ctx = Depends(require_roles(*ANY_PROJECT_ROLE)), db: Session = Depends(get_db)):
    p = get_project(db, ctx, pid)
    plan = db.get(ProjectPlan, pid)
    return project_out(p, plans.public_status(plan) if plan else "", plans.summary(plans.live_json(plan)) if plan else None, plan.status if plan and plan.version > 0 and plan.status != "published" else "")


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
def add_participant(pid: str, body: NewParticipant, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    try:
        answers = screener.parse_text(body.screener_text)
    except screener.ScreenerError as e:
        raise HTTPException(422, str(e))
    country = body.country.upper()
    screening = "eligible" if (not country or settings.region_open(country)) else "blocked_region"  # empty: declared at consent
    p = Participant(org_id=ctx.org_id, project_id=proj.id, name=body.name, email=body.email, country=country,
                    language=body.language, screening=screening, source="manual", source_name="手动添加")
    db.add(p)
    db.flush()  # assigns the id, so the audit row points at this participant
    screener.save(db, ctx.org_id, p.id, answers)
    audit(db, ctx.org_id, ctx.actor, "participant.create", p.id, screening=screening, screener=len(answers))
    db.commit()
    return participant_out(p, len(answers))


@router.get("/projects/{pid}/participants")
def list_participants(pid: str, tag: str = "", country: str = "", language: str = "", screen: str = "", ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The project's people; optionally only those with a tag, a country, a language, or a screening result (qualified / disqualified / review)."""
    proj = get_project(db, ctx, pid)
    people = db.scalars(select(Participant).where(Participant.project_id == proj.id).order_by(Participant.created_at)).all()
    people = [p for p in people if (not tag or tag in (p.tags or [])) and (not country or p.country == country.upper()) and (not language or p.language == language)
              and (not screen or p.screen_result == screen)]
    counted = screener.counts(db, [p.id for p in people])
    key = recruit_insights.key_answers(db, [p.id for p in people])
    rs = db.get(RecruitSettings, proj.id)
    tiers, _ = recruit.tiers_and_progress(db, proj.id, (rs.quotas if rs else None) or [])
    moderation: dict[str, set[str]] = {}
    for part_id, how in db.execute(select(Interview.participant_id, Interview.moderation).where(Interview.project_id == proj.id)):
        moderation.setdefault(part_id, set()).add("ai" if how == "ai" else "human")
    kind = {k: ("both" if len(v) > 1 else next(iter(v))) for k, v in moderation.items()}
    return [participant_out(p, counted.get(p.id, 0), key.get(p.id, ""), tiers.get(p.id, ""), kind.get(p.id, "")) for p in people]


def _own_participant(db: Session, ctx: Ctx, part_id: str) -> Participant:
    p = db.get(Participant, part_id)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "受访者不存在")
    ensure_project(db, ctx, p.project_id, "受访者不存在")
    return p


@router.get("/participants/{part_id}/screener")
def get_screener(part_id: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The participant's screening answers (research team only; clients and observers never see them)."""
    p = _own_participant(db, ctx, part_id)
    items = screener.items_for(db, p.id)
    form = db.get(RecruitForm, p.project_id)
    shown = recruit_insights.annotate(items, recruit_form.load(form.questions) if form else [])
    return {"items": shown, "text": screener.as_text(items)}


@router.put("/participants/{part_id}/screener")
def put_screener(part_id: str, body: ScreenerBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Replace the participant's screening answers with the pasted lines ("question：answer", one per line)."""
    p = _own_participant(db, ctx, part_id)
    if p.anonymized_at:
        raise HTTPException(409, "这位受访者的数据已被清除")
    try:
        answers = screener.parse_text(body.text)
    except screener.ScreenerError as e:
        raise HTTPException(422, str(e))
    n = screener.save(db, ctx.org_id, p.id, answers)
    audit(db, ctx.org_id, ctx.actor, "participant.screener", p.id, count=n)
    db.commit()
    return {"count": n}


@router.delete("/participants/{part_id}/data")
def erase(part_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _own_participant(db, ctx, part_id)
    return {"purged_interviews": erase_participant(db, p, ctx.actor)}


@router.put("/projects/{pid}/methods")
def put_methods(pid: str, body: MethodsBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Change which research methods the project uses (project owners and admins). Data of a method switched off is kept, only hidden."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, pid):
        raise HTTPException(403, "只有项目负责人或管理员能调整研究方法")
    try:
        proj.methods = methods_mod.store(methods_mod.clean(body.methods))
    except methods_mod.MethodsError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "project.methods", proj.id, methods=proj.methods)
    db.commit()
    return project_out(proj)


# ---------- who works on a project ----------
def _can_manage(db: Session, ctx: Ctx, pid: str) -> bool:
    """An admin, or a member marked as owner, may add and remove members."""
    if ctx.user.role == "admin":
        return True
    return bool(db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == pid, ProjectMember.user_id == ctx.user.id, ProjectMember.is_owner.is_(True))))


def _members_out(db: Session, ctx: Ctx, pid: str) -> dict:
    rows = db.execute(select(ProjectMember, User).join(User, User.id == ProjectMember.user_id).where(ProjectMember.project_id == pid)
                      .order_by(ProjectMember.created_at)).all()
    options = member_scopes.stage_options(db, db.get(Project, pid))
    return {"can_manage": _can_manage(db, ctx, pid), "admins_see_all": True, "stages": options,
            "members": [{"user_id": u.id, "name": u.name, "email": u.email, "role": u.role, "research_kind": u.research_kind or "all", "active": u.active, "is_owner": m.is_owner,
                         "scopes": m.scopes or []} for m, u in rows]}


class NewMember(BaseModel):
    user_id: str
    scopes: list[str] | None = None  # the research stages; None = by the person's role


class ScopesBody(BaseModel):
    scopes: list[str] = Field(default_factory=list, max_length=10)


@router.get("/projects/{pid}/members")
def list_members(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The researchers who work on this project (admins see every project without being listed)."""
    proj = get_project(db, ctx, pid)
    return _members_out(db, ctx, proj.id)


@router.post("/projects/{pid}/members", status_code=201)
def add_member(pid: str, body: NewMember, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Add a researcher, recruiter or viewer of this company to the project (an admin or an owner of the project)."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能添加成员")
    user = db.get(User, body.user_id)
    if not user or user.org_id != ctx.org_id or not user.active:
        raise HTTPException(404, "没有这位同事")
    if user.role == "admin":
        raise HTTPException(422, "管理员可以看到所有项目，不需要添加")
    if user.role not in MEMBER_ROLES:
        raise HTTPException(422, "只有研究员、技术支持、项目经理和客户（只读）可以加入项目；主持人和观察员是按场次指派的")
    options = member_scopes.stage_options(db, proj)
    try:
        scopes = member_scopes.clean(body.scopes, options) if body.scopes is not None else member_scopes.default_scopes(user, options)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if not db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == proj.id, ProjectMember.user_id == user.id)):
        db.add(ProjectMember(org_id=ctx.org_id, project_id=proj.id, user_id=user.id, added_by=ctx.user.id, scopes=scopes))
        audit(db, ctx.org_id, ctx.actor, "project.member.add", proj.id, user=user.id)
        db.commit()
    return _members_out(db, ctx, proj.id)


@router.put("/projects/{pid}/members/{uid}/scopes")
def set_member_scopes(pid: str, uid: str, body: ScopesBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Change which research stages a member takes part in (an admin or an owner of the project); a person can have several."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能修改成员的研究阶段")
    m = db.scalar(select(ProjectMember).where(ProjectMember.project_id == proj.id, ProjectMember.user_id == uid))
    if m is None:
        raise HTTPException(404, "这位同事不在项目里")
    try:
        m.scopes = member_scopes.clean(body.scopes, member_scopes.stage_options(db, proj))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "project.member.scopes", proj.id, user=uid, scopes=",".join(m.scopes))
    db.commit()
    return _members_out(db, ctx, proj.id)


@router.delete("/projects/{pid}/members/{uid}")
def remove_member(pid: str, uid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Take a researcher off the project at once (an admin or an owner). The last owner can only be removed by an admin."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能移除成员")
    m = db.scalar(select(ProjectMember).where(ProjectMember.project_id == proj.id, ProjectMember.user_id == uid))
    if not m:
        raise HTTPException(404, "这位同事不在项目里")
    owners = db.scalars(select(ProjectMember).where(ProjectMember.project_id == proj.id, ProjectMember.is_owner.is_(True))).all()
    if m.is_owner and len(owners) == 1 and ctx.user.role != "admin":
        raise HTTPException(409, "这是最后一位项目负责人，请先指定另一位，或请管理员处理")
    db.delete(m)
    audit(db, ctx.org_id, ctx.actor, "project.member.remove", proj.id, user=uid)
    db.commit()
    if ctx.user.id == uid and not can_see_project(db, ctx, proj.id):
        return {"can_manage": False, "admins_see_all": True, "members": [], "left": True}
    return _members_out(db, ctx, proj.id)


class OwnerBody(BaseModel):
    is_owner: bool


@router.put("/projects/{pid}/members/{uid}/owner")
def set_owner(pid: str, uid: str, body: OwnerBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Make a member an owner of the project (may manage members), or take that away."""
    proj = get_project(db, ctx, pid)
    if not _can_manage(db, ctx, proj.id):
        raise HTTPException(403, "只有项目负责人或管理员能设置负责人")
    m = db.scalar(select(ProjectMember).where(ProjectMember.project_id == proj.id, ProjectMember.user_id == uid))
    if not m:
        raise HTTPException(404, "这位同事不在项目里")
    if m.is_owner and not body.is_owner and ctx.user.role != "admin" and len(db.scalars(select(ProjectMember).where(
            ProjectMember.project_id == proj.id, ProjectMember.is_owner.is_(True))).all()) == 1:
        raise HTTPException(409, "至少要保留一位项目负责人")
    m.is_owner = body.is_owner
    audit(db, ctx.org_id, ctx.actor, "project.member.owner", proj.id, user=uid, owner=body.is_owner)
    db.commit()
    return _members_out(db, ctx, proj.id)
