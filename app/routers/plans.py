"""The project plan: create a draft project, edit the plan, submit it for review, approve it, publish and lock it, and watch the quotas.

Only the research team (admin, researcher) and the project's manager (recruiter) edit a plan; only an admin approves. A published plan is locked; an admin or
the project's manager can unlock it, and it must then go through review again before it is published as the next version (the project keeps running on the
last published version meanwhile). A project that never had a plan counts as published (it was made before the wizard existed).
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import methods as methods_mod
from .. import library_context, plan_suggest, plans, recruit_form
from ..audit import audit
from ..db import get_db
from ..deps import MEMBER_ROLES, STAFF, Ctx, require_roles
from ..models import Participant, Project, ProjectMember, ProjectPlan, User, utcnow
from ..moderator.providers import ChatFatalError
from .projects import get_project, project_out
from .recruit import _ai_chat

router = APIRouter(prefix="/api", tags=["plans"])

LANG_CODE = {"中文": "zh", "英语": "en", "马来语": "ms", "泰米尔语": "ta"}
COUNTRY_NAMES = {"SG": "新加坡", "MY": "马来西亚", "ID": "印尼", "VN": "越南", "TH": "泰国", "PH": "菲律宾", "CN": "中国", "JP": "日本", "KR": "韩国",
                 "DE": "德国", "SA": "沙特", "AE": "阿联酋", "US": "美国", "GB": "英国", "IN": "印度"}
PLAN_ROLES = (*STAFF, "recruiter")  # the project manager works on the plan too (a recruiter never sees interview content)
UNLOCKERS = ("admin", "support", "recruiter")
EDITABLE = ("draft", "review", "approved")  # editing a plan under review or approved sends it back to draft (it must be reviewed again)


class NewDraft(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class PlanBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brief: str | None = Field(default=None, max_length=5000)
    config: dict


class NoteBody(BaseModel):
    note: str = Field(default="", max_length=300)


def plan_status(db: Session, project_id: str) -> str:
    """"" for a project made before plans existed, otherwise the plan's status."""
    plan = db.get(ProjectPlan, project_id)
    return plan.status if plan else ""


def _plan(db: Session, ctx: Ctx, pid: str) -> tuple[Project, ProjectPlan | None]:
    return get_project(db, ctx, pid), db.get(ProjectPlan, pid)


def _config(plan: ProjectPlan) -> plans.PlanConfig:
    return plans.validate(json.loads(plan.config_json or "{}") or plans.default_config())


def plan_out(project: Project, plan: ProjectPlan | None, library_docs: int | None = None) -> dict:
    """The plan as the wizard needs it: status, config, the compliance list and what still blocks submitting."""
    if plan is None:
        return {"status": "", "legacy": True, "name": project.name, "brief": project.brief}
    cfg = _config(plan)
    return {"status": plan.status, "legacy": False, "name": project.name, "brief": project.brief, "version": plan.version, "config": cfg.model_dump(),
            "compliance": plans.compliance(cfg), "blockers": plans.blockers(cfg), "review_hash": plan.review_hash, "review_note": plan.review_note,
            "submitted_at": plan.submitted_at.isoformat() if plan.submitted_at else None, "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
            "published_at": plan.published_at.isoformat() if plan.published_at else None, "retention_days": cfg.retention, "live": plan.version > 0,
            "suggest": json.loads(plan.suggest_json) if plan.suggest_json else None, "sums": plans.quota_sums(cfg),
            **({"library_docs": library_docs} if library_docs is not None else {})}


@router.post("/projects/draft", status_code=201)
def create_draft(body: NewDraft, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A new project in draft: it exists (so the wizard can save into it) but is not usable until the plan is published."""
    p = Project(org_id=ctx.org_id, created_by=ctx.user.id, name=body.name.strip(), market="SG", language="en", methods=methods_mod.store(["depth"]))
    db.add(p)
    db.flush()
    db.add(ProjectMember(org_id=ctx.org_id, project_id=p.id, user_id=ctx.user.id, is_owner=True, added_by=ctx.user.id))
    db.add(ProjectPlan(project_id=p.id, org_id=ctx.org_id, status="draft", config_json=json.dumps(plans.default_config(), ensure_ascii=False)))
    audit(db, ctx.org_id, ctx.actor, "project.draft", p.id)
    db.commit()
    return {**project_out(p), "plan_status": "draft"}


@router.get("/projects/{pid}/plan")
def get_plan(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    project, plan = _plan(db, ctx, pid)
    return plan_out(project, plan, len(library_context.project_documents(db, project.id)))


@router.get("/projects/{pid}/plan/people")
def plan_people(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """The company's people the plan can pick from: name, role, kind of researcher (no contact details, so the project manager can use it too)."""
    get_project(db, ctx, pid)
    rows = db.scalars(select(User).where(User.org_id == ctx.org_id, User.active.is_(True)).order_by(User.created_at))
    return [{"id": u.id, "name": u.name, "role": u.role, "active": u.active, "research_kind": u.research_kind or "all"} for u in rows]


@router.post("/projects/{pid}/plan/suggest")
def suggest(pid: str, use_library: bool = False, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """The AI reads the research background and suggests interview topics, group topics, a quota hint and listening keywords (to read and take over by hand).
    The background is sent to the AI service (the wizard says so next to the box); it is not written to the log."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    cfg = _config(plan)
    chat = _ai_chat()
    lang = LANG_CODE.get(cfg.subs[0].lang, project.language) if cfg.subs else project.language
    try:
        out = plan_suggest.suggest(chat, project.brief or "", lang, library_context.library_text(db, project.id)[0] if use_library else "")
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except recruit_form.FormError as e:
        raise HTTPException(422 if "先在" in str(e) else 502, str(e)) from e
    plan.suggest_json = json.dumps(out, ensure_ascii=False)
    audit(db, ctx.org_id, ctx.actor, "plan.suggest", project.id, model=chat.name if chat else "mock")
    db.commit()
    return out


@router.post("/projects/{pid}/plan/brief-to-library", status_code=201)
def brief_to_library(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Save the research background into the project's research library (one document, refreshed on each save): the library is where the AI tools and the team read it from."""
    project, plan = _plan(db, ctx, pid)
    if not (project.brief or "").strip():
        raise HTTPException(422, "研究背景还是空的")
    doc = library_context.save_brief(db, ctx.org_id, project.id, project.brief, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "plan.brief_to_library", project.id)
    db.commit()
    return {"document_id": doc.id, "library_docs": len(library_context.project_documents(db, project.id))}


@router.put("/projects/{pid}/plan")
def save_plan(pid: str, body: PlanBody, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """Save the wizard's state. A published plan is locked; editing one under review or approved sends it back to draft."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目创建时没有立项记录，不能用向导修改")
    if plan.status == "published":
        raise HTTPException(409, "项目已发布并锁定，不能再修改立项（管理员或项目经理可以先解锁）")
    try:
        cfg = plans.validate(body.config)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if plan.version > 0 and cfg.retention > project.retention_days:  # people already agreed to the shorter period
        raise HTTPException(422, f"数据留存期限只能缩短，不能超过已发布的 {project.retention_days} 天（受访者已按这个天数同意）")
    if body.name:
        project.name = body.name.strip()
    if body.brief is not None:
        project.brief = body.brief
    new_json = json.dumps(cfg.model_dump(), ensure_ascii=False)
    changed = new_json != json.dumps(_config(plan).model_dump(), ensure_ascii=False) if plan.config_json else True
    plan.config_json = new_json
    plan.updated_at = utcnow()
    if changed and plan.status != "draft":  # saving the same content again (the page autosaves) changes nothing
        plan.status, plan.review_hash, plan.review_note = "draft", "", "内容改动后需要重新提交审查"
    audit(db, ctx.org_id, ctx.actor, "plan.save", project.id)
    db.commit()
    return plan_out(project, plan)


@router.post("/projects/{pid}/plan/tags")
def match_tags(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """Which of this project's own participant labels the target-population sentence mentions, and how many people carry them (a count, not a guess)."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    cfg = _config(plan)
    people = db.scalars(select(Participant).where(Participant.project_id == project.id, Participant.anonymized_at.is_(None))).all()
    known = sorted({t for p in people for t in (p.tags or [])})
    tags = plans.suggest_tags(cfg.pop, known)
    reach = sum(1 for p in people if all(t in (p.tags or []) for t in tags)) if tags else len(people)
    return {"tags": tags, "known": known[:60], "reach": reach, "pool": len(people)}


def _transition(plan: ProjectPlan, frm: tuple[str, ...], to: str, what: str) -> None:
    if plan.status not in frm:
        raise HTTPException(409, f"现在是“{plan.status}”状态，不能{what}")
    plan.status = to


@router.post("/projects/{pid}/plan/submit")
def submit_plan(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """Submit for review: only when every compliance item passes and the quota tables add up."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    cfg = _config(plan)
    if bad := plans.blockers(cfg):
        raise HTTPException(409, "还不能提交审查：" + "；".join(bad[:5]))
    _transition(plan, ("draft",), "review", "提交审查")
    plan.review_hash, plan.review_note, plan.submitted_by, plan.submitted_at = plans.config_hash(cfg), "", ctx.user.id, utcnow()
    audit(db, ctx.org_id, ctx.actor, "plan.submit", project.id, hash=plan.review_hash)
    db.commit()
    return plan_out(project, plan)


@router.post("/projects/{pid}/plan/approve")
def approve_plan(pid: str, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """The legal review: an admin approves the submitted plan (the fingerprint must still match what was submitted)."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    if plans.config_hash(_config(plan)) != plan.review_hash:
        raise HTTPException(409, "提交后内容又变了，请退回重新提交")
    _transition(plan, ("review",), "approved", "通过审查")
    plan.approved_by, plan.approved_at = ctx.user.id, utcnow()
    audit(db, ctx.org_id, ctx.actor, "plan.approve", project.id, hash=plan.review_hash)
    db.commit()
    return plan_out(project, plan)


@router.post("/projects/{pid}/plan/return")
def return_plan(pid: str, body: NoteBody, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Send a plan under review (or approved, not yet published) back to draft, with the reason."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    _transition(plan, ("review", "approved"), "draft", "退回")
    plan.review_note, plan.review_hash = body.note.strip() or "审查没有通过", ""
    audit(db, ctx.org_id, ctx.actor, "plan.return", project.id, note=plan.review_note)
    db.commit()
    return plan_out(project, plan)


@router.post("/projects/{pid}/plan/publish")
def publish_plan(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """Publish and lock: the research methods, interview language and team are applied to the project, and the plan can no longer change."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目没有立项记录")
    cfg = _config(plan)
    if plan.status != "approved" or plans.config_hash(cfg) != plan.review_hash:
        raise HTTPException(409, "需要先通过审查（并且审查后内容没有变）才能发布")
    project.methods = methods_mod.store(cfg.methods)
    project.retention_days = cfg.retention
    if cfg.subs:
        project.language = LANG_CODE.get(cfg.subs[0].lang, project.language)
    have = {m for (m,) in db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))}
    wanted = [m.user_id for m in cfg.team]
    users = {u.id: u for u in db.scalars(select(User).where(User.org_id == ctx.org_id, User.id.in_(wanted)))} if wanted else {}
    for uid_ in dict.fromkeys(wanted):
        u = users.get(uid_)
        if u and u.active and u.role in MEMBER_ROLES and uid_ not in have:
            db.add(ProjectMember(org_id=ctx.org_id, project_id=project.id, user_id=uid_, is_owner=False, added_by=ctx.user.id))
    plan.status, plan.version, plan.published_at = "published", plan.version + 1, utcnow()
    plan.published_json = plan.config_json
    audit(db, ctx.org_id, ctx.actor, "plan.publish", project.id, methods=",".join(cfg.methods), hash=plan.review_hash, version=plan.version)
    db.commit()
    return {**plan_out(project, plan), "project": project_out(project, plans.public_status(plan), plans.summary(plans.live_json(plan)))}


@router.post("/projects/{pid}/plan/unlock")
def unlock_plan(pid: str, ctx: Ctx = Depends(require_roles(*UNLOCKERS)), db: Session = Depends(get_db)):
    """Unlock a published plan for changes (a client adds samples or a new method): an admin or the project's manager. The project keeps running on the
    published version; the changed plan goes through review and publishing again, and then quotas and monitoring follow it."""
    project, plan = _plan(db, ctx, pid)
    if plan is None or plan.status != "published":
        raise HTTPException(409, "只有已发布并锁定的立项才能解锁")
    plan.published_json = plan.published_json or plan.config_json  # the running version, kept while the plan is edited
    plan.status, plan.review_hash, plan.review_note = "draft", "", "已解锁修改，改完需要重新提交审查"
    audit(db, ctx.org_id, ctx.actor, "plan.unlock", project.id, version=plan.version)
    db.commit()
    return plan_out(project, plan)


# ---------- watching the quotas ----------
def _matches(person: Participant, names: list[tuple[str, str]]) -> bool:
    """Whether a person fits a quota row: each (variable, value) is a label they carry, or (for the country) their country."""
    tags = set(person.tags or [])
    for dim, value in names:
        if value in tags:
            continue
        if dim == plans.COUNTRY_VAR and value in (person.country, COUNTRY_NAMES.get(person.country, "")):
            continue
        return False
    return True


@router.get("/projects/{pid}/monitor")
def monitor(pid: str, ctx: Ctx = Depends(require_roles(*PLAN_ROLES)), db: Session = Depends(get_db)):
    """Quota progress per table: target, ready, gap. "Ready" = people in the project who fit the row and were not screened out."""
    project, plan = _plan(db, ctx, pid)
    if plan is None or plan.version == 0:
        return {"published": False}
    cfg = plans.validate(json.loads(plans.live_json(plan) or "{}"))  # the published version, also while the plan is unlocked and being edited
    people = [p for p in db.scalars(select(Participant).where(Participant.project_id == project.id, Participant.anonymized_at.is_(None)))
              if p.screening == "eligible" and p.screen_result in ("", "qualified")]
    quant_people = [p for p in people if p.external_id]  # people pushed by a survey platform count towards the survey

    def table(q: plans.Quota, key: str, pool: list[Participant], scale: int = 1) -> dict:
        values = {d: {v.id: v.name for v in cfg.vars.get(d, [])} for d in q.dims}
        rows = []
        for r in q.rows:
            names = [(d, values[d].get(vid, "")) for d, vid in zip(q.dims, r.vs)]
            target = getattr(r, key) * scale
            ready = sum(1 for p in pool if _matches(p, names))
            rows.append({"id": r.id, "label": " · ".join(n for _, n in names), "target": target, "ready": ready, "gap": max(0, target - ready), "async": r.a if key != "t" else None})  # None: surveys have no choice of who runs them
        return {"dims": q.dims, "rows": rows, "target": sum(x["target"] for x in rows), "ready": sum(min(x["ready"], x["target"]) for x in rows),
                "async_target": sum(x["target"] for x in rows if x["async"])}

    return {"published": True, "methods": cfg.methods, "q": table(cfg.qt, "t", quant_people), "i": table(cfg.lt, "i", people), "f": table(cfg.ft, "f", people, cfg.n.fs),
            "fs": cfg.n.fs, "subs": [s.model_dump() for s in cfg.subs], "version": plan.version}


@router.post("/projects/{pid}/copy", status_code=201)
def copy_project(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """复制项目：a new draft with this project's plan (same research design, sub-projects, quotas, templates). People, interviews and results are not copied."""
    project, plan = _plan(db, ctx, pid)
    if plan is None:
        raise HTTPException(409, "这个项目创建时没有立项记录，不能复制")
    cfg = _config(plan)
    cfg.cons = plans.Cons()  # confirmations are given again for the new project
    new = Project(org_id=ctx.org_id, created_by=ctx.user.id, name=f"{project.name}（副本）"[:200], brief=project.brief, market=project.market, language=project.language,
                  methods=methods_mod.store(["depth"]))
    db.add(new)
    db.flush()
    db.add(ProjectMember(org_id=ctx.org_id, project_id=new.id, user_id=ctx.user.id, is_owner=True, added_by=ctx.user.id))
    db.add(ProjectPlan(project_id=new.id, org_id=ctx.org_id, status="draft", config_json=json.dumps(cfg.model_dump(), ensure_ascii=False)))
    audit(db, ctx.org_id, ctx.actor, "project.copy", new.id, source=project.id)
    db.commit()
    return {**project_out(new, "draft"), "copied_from": project.id}
