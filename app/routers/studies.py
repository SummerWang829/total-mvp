"""Studies done by participants on their own (app/studies.py): the researcher's side (create, AI draft, edit, publish, invite, close) and the one
participant call that opens their session. The chat itself reuses the AI room's participant endpoints (app/routers/ai_room.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import guide_text, studies, study_draft
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import AiSession, AsyncStudy, Interview, Participant, Project
from ..moderator.providers import ChatFatalError, get_moderator_chat
from .public import resolve_join  # noqa: PLC2701 — the same invite-token lookup as the rest of the participant flow

router = APIRouter(prefix="/api", tags=["studies"])
LANGS = "^(en|zh)$"  # the AI moderator speaks English and Chinese for now (app/moderator/templates.py)


class NewStudy(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=1000)
    language: str = Field(pattern=LANGS)
    duration_min: int = 20
    participant_topic: str = Field(default="", max_length=18)


class OutlineBody(BaseModel):
    text: str = Field(max_length=guide_text.MAX_CHARS)


class InvitesBody(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=200)


def _run(db: Session, fn, *args):
    """Call a service function; turn its refusal into an HTTP error."""
    try:
        return fn(db, *args)
    except studies.StudyError as e:
        db.rollback()
        raise HTTPException(e.status, e.message) from e


def _study(db: Session, ctx: Ctx, sid: str) -> AsyncStudy:
    """The study, 404 when it is another company's or in a project this person is not on."""
    st = db.get(AsyncStudy, sid)
    if not st or st.org_id != ctx.org_id:
        raise HTTPException(404, "研究不存在")
    ensure_project(db, ctx, st.project_id, "研究不存在")
    return st


def _counts(db: Session, study_id: str) -> dict:
    rows = db.execute(select(Interview, AiSession).outerjoin(AiSession, AiSession.interview_id == Interview.id).where(Interview.study_id == study_id)).all()
    states = [studies.session_state(i, s) for i, s in rows]
    return {"invited": len(states), "in_progress": states.count("in_progress"), "finished": states.count("finished"), "ended_early": states.count("ended_early")}


def study_out(db: Session, st: AsyncStudy, detail: bool = False) -> dict:
    out = {"id": st.id, "project_id": st.project_id, "title": st.title, "goal": st.goal, "language": st.language, "duration_min": st.duration_min,
           "participant_topic": st.participant_topic, "status": st.status, "version": st.version, "drafted_by": st.drafted_by,
           "published_at": st.published_at.isoformat() if st.published_at else None, "counts": _counts(db, st.id)}
    if detail:
        out["outline_text"] = st.outline_text
        rows = db.execute(select(Interview, AiSession).outerjoin(AiSession, AiSession.interview_id == Interview.id)
                          .where(Interview.study_id == st.id).order_by(Interview.created_at)).all()
        out["sessions"] = [{"interview_id": i.id, "participant": {"id": i.participant.id, "name": i.participant.name, "country": i.participant.country},
                            "state": studies.session_state(i, s), "alert": s.alert if s else "none", "alert_reason": s.alert_reason if s else ""} for i, s in rows]
    return out


@router.post("/projects/{pid}/studies", status_code=201)
def create_study(pid: str, body: NewStudy, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    if body.duration_min not in studies.DURATIONS:
        raise HTTPException(422, f"时长可选：{'、'.join(map(str, studies.DURATIONS))} 分钟")
    st = AsyncStudy(org_id=ctx.org_id, project_id=pid, title=body.title.strip(), goal=body.goal.strip(), language=body.language,
                    duration_min=body.duration_min, participant_topic=body.participant_topic.strip(), created_by=ctx.user.id)
    db.add(st)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "study.create", st.id)
    db.commit()
    return study_out(db, st, detail=True)


@router.get("/projects/{pid}/studies")
def list_studies(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    rows = db.scalars(select(AsyncStudy).where(AsyncStudy.project_id == pid).order_by(AsyncStudy.created_at.desc())).all()
    return [study_out(db, r) for r in rows]


@router.get("/studies/{sid}")
def get_study(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return study_out(db, _study(db, ctx, sid), detail=True)


@router.post("/studies/{sid}/draft")
def draft(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The AI writes a first outline from the goal (replacing the text in the editor; nothing is published). Takes several seconds."""
    st = _study(db, ctx, sid)
    if st.status == "closed":
        raise HTTPException(409, "这个研究已经关闭")
    from ..config import settings
    chat = None if settings.moderator_provider == "mock" else get_moderator_chat()
    try:
        guide = study_draft.draft_guide(chat, st.goal, st.language, st.duration_min)
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except study_draft.DraftError as e:
        raise HTTPException(502, str(e)) from e
    st.outline_text = guide_text.guide_to_text(guide.model_dump(mode="json"))
    st.drafted_by = chat.name if chat else "mock"
    audit(db, ctx.org_id, ctx.actor, "study.draft", st.id, model=st.drafted_by)
    db.commit()
    return study_out(db, st, detail=True)


@router.put("/studies/{sid}/outline")
def set_outline(sid: str, body: OutlineBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Save the researcher's edited outline text (a published version is not touched until the next publish). Says what is wrong with it, if anything."""
    st = _study(db, ctx, sid)
    if st.status == "closed":
        raise HTTPException(409, "这个研究已经关闭")
    st.outline_text = body.text  # always kept, so a half-finished edit is not lost
    problem = None
    try:
        studies.guide_of(st)  # the same checks as publishing, so mistakes show up while editing
    except studies.StudyError as e:
        problem = e.message
    audit(db, ctx.org_id, ctx.actor, "study.outline", st.id)
    db.commit()
    return study_out(db, st, detail=True) | {"problem": problem}


@router.post("/studies/{sid}/publish")
def publish(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    st = _study(db, ctx, sid)
    _run(db, studies.publish, st, ctx.actor)
    db.commit()
    return study_out(db, st, detail=True)


@router.post("/studies/{sid}/close")
def close(sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    st = _study(db, ctx, sid)
    if st.status == "closed":
        raise HTTPException(409, "这个研究已经关闭")
    _run(db, studies.close, st, ctx.actor)
    db.commit()
    return study_out(db, st, detail=True)


@router.post("/studies/{sid}/invites", status_code=201)
def invites(sid: str, body: InvitesBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One personal link per participant of this project (shown once). Already-invited and ineligible people are reported, not failed."""
    st = _study(db, ctx, sid)
    if st.status != "published":
        raise HTTPException(409, "请先发布研究，再邀请受访者")
    made, skipped = [], []
    for pid_ in dict.fromkeys(body.participant_ids):
        p = db.get(Participant, pid_)
        if not p or p.org_id != ctx.org_id or p.project_id != st.project_id:
            raise HTTPException(422, "受访者不在这个项目里")
        if p.screening != "eligible":
            skipped.append({"participant_id": p.id, "name": p.name, "why": "所在地区暂不开放"})
            continue
        try:
            itv, token = studies.invite(db, st, p, ctx.actor)
        except studies.StudyError as e:
            skipped.append({"participant_id": p.id, "name": p.name, "why": e.message})
            continue
        made.append({"participant_id": p.id, "name": p.name, "interview_id": itv.id, "invite_path": f"/join.html?t={token}"})
    db.commit()
    return {"invites": made, "skipped": skipped}


@router.post("/public/join/{token}/async/start")
def participant_start(token: str, db: Session = Depends(get_db)):
    """The participant opens their session (first time: the AI starts; later: it carries on). They then use the /chat endpoints."""
    j = resolve_join(db, token)
    _run(db, studies.begin, j.itv)
    db.commit()
    return {"ok": True, "language": j.itv.language}

