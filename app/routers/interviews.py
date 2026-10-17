import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import ai_room, concept, export, groups, pretask, scheduling
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..moderator.compliance import ai_moderation_blocker
from ..moderator.constants import ModerationMode
from ..deps import STAFF, Ctx, current_ctx, require_roles, visible_interview
from ..evidence import check_evidence, quote_matches
from ..livekit import build_room_token, delete_room, url_for_client
from ..models import (AuditLog, Clip, InsightCode, Consent, Evidence, Insight, Interview, InterviewObserver, Participant, Project,
                      Recording, ShareLink, TranscriptSegment, User, uid, utcnow)
from ..pipeline import build_segments
from ..providers import get_llm
from ..recording import elapsed_ms, rec_file, save_chunk
from ..retention import run_retention
from ..security import new_secret_token, sign_media, token_hash
from .projects import get_project

router = APIRouter(prefix="/api", tags=["interviews"])


# ---------- helpers ----------
def get_user_in_org(db: Session, ctx: Ctx, user_id: str, roles: tuple[str, ...]) -> User:
    u = db.get(User, user_id)
    if not u or u.org_id != ctx.org_id or not u.active or u.role not in roles:
        raise HTTPException(422, "用户不存在或角色不符")
    return u


def invite_state(db: Session, itv: Interview) -> str:
    """Where the participant is on their journey: sent | opened | device_ok | consented | entered | done."""
    if itv.status == "ended":
        return "done" if itv.entered_at else "ended_absent"
    if itv.entered_at:
        return "entered"
    if db.scalar(select(Consent.id).where(Consent.interview_id == itv.id, Consent.participant_id == itv.participant_id)):
        return "consented"
    if itv.device_ok_at:
        return "device_ok"
    return "opened" if itv.opened_at else "sent"


WAITING_FRESH_S = 10  # the waiting room polls every 3 s; seen within this window = waiting right now


def in_waiting_room(itv: Interview, seat=None) -> bool:
    """True when this person is in the waiting room right now. ``seat`` is the journey row (the interview, for a depth interview)."""
    row = itv if seat is None else seat
    return (itv.status != "ended" and row.waiting_seen_at is not None and row.admitted_at is None
            and (utcnow() - row.waiting_seen_at).total_seconds() <= WAITING_FRESH_S)


def interview_out(itv: Interview, ctx: Ctx, invite_token: str | None = None, db: Session | None = None) -> dict:
    sees_name = ctx.user.role != "observer"  # client observers never see who the participant is
    out = {
        "id": itv.id, "project_id": itv.project_id, "title": itv.title, "language": itv.language, "status": itv.status,
        "scheduled_at": itv.scheduled_at.isoformat() if itv.scheduled_at else None,
        "started_at": itv.started_at.isoformat() if itv.started_at else None,
        "ended_at": itv.ended_at.isoformat() if itv.ended_at else None,
        "moderator_id": itv.moderator_id, "observer_ids": [o.user_id for o in itv.observers],
        "participant": {"id": itv.participant.id, "name": itv.participant.name, "country": itv.participant.country} if sees_name
        else {"name": "受访者"},
        "purged": itv.purged_at is not None, "moderation": itv.moderation, "kind": itv.kind,
    }
    if groups.is_group(itv):  # a group has no single participant; show the headcount, and (staff only) the roster
        n = len(itv.group_participants)
        out["participant"] = {"name": f"{n} 位受访者" if sees_name else "受访者"}
        if db is not None and sees_name:
            out["group"] = groups.roster_view(db, itv)
            rows = itv.group_participants
            out["waiting"] = any(in_waiting_room(itv, s) for s in rows)
            out["admitted"] = any(s.admitted_at is not None for s in rows)
    if db is not None and sees_name and not groups.is_group(itv):  # the participant's progress is for the research team, not the client
        out["invite_state"] = invite_state(db, itv)
        out["waiting"] = in_waiting_room(itv)
        out["admitted"] = itv.admitted_at is not None
    if invite_token:
        out["invite_path"] = f"/join.html?t={invite_token}"
        out["invite_token"] = invite_token
    return out


def segment_out(s: TranscriptSegment) -> dict:
    return {"id": s.id, "idx": s.idx, "speaker": s.speaker, "start_ms": s.start_ms, "end_ms": s.end_ms,
            "text_orig": s.text_orig, "text_zh": s.text_zh, "lang": s.lang}


def insight_out(db: Session, i: Insight) -> dict:
    ev = []
    for e in i.evidence:
        seg = db.get(TranscriptSegment, e.segment_id) if e.segment_id else None
        ev.append({"id": e.id, "quote": e.quote, "quote_lang": e.quote_lang, "start_ms": e.start_ms, "end_ms": e.end_ms,
                   "segment_id": e.segment_id, "segment_idx": seg.idx if seg else None, "speaker": seg.speaker if seg else None})
    return {"id": i.id, "theme": i.theme, "statement": i.statement, "kind": i.kind, "status": i.status, "evidence": ev}


def clip_out(c: Clip) -> dict:
    return {"id": c.id, "title": c.title, "start_ms": c.start_ms, "end_ms": c.end_ms, "evidence_id": c.evidence_id}


# ---------- interviews ----------
class NewInterview(BaseModel):
    participant_id: str | None = None  # depth: the one participant
    participant_ids: list[str] = []  # group (focus group): the seated participants, 1..groups.MAX_GROUP
    kind: str = groups.KIND_DEPTH
    title: str = Field(min_length=1, max_length=200)
    scheduled_at: datetime | None = None
    language: str | None = None
    moderator_id: str | None = None
    observer_ids: list[str] = []
    moderation: ModerationMode = ModerationMode.HUMAN

    @field_validator("language")
    @classmethod
    def _lang(cls, v):
        if v and v not in settings.languages:
            raise ValueError("language must be one of " + ", ".join(settings.languages))
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in groups.KINDS:
            raise ValueError("kind must be one of " + ", ".join(groups.KINDS))
        return v


def _eligible_participant(db: Session, ctx: Ctx, proj: Project, participant_id: str) -> Participant:
    """A participant that belongs to this project and may be interviewed (region open)."""
    part = db.get(Participant, participant_id)
    if not part or part.org_id != ctx.org_id or part.project_id != proj.id:
        raise HTTPException(422, "受访者不在这个项目里")
    if part.screening != "eligible":
        raise HTTPException(422, "该受访者所在地区不在首批开放范围内，不能安排访谈")
    return part


@router.post("/projects/{pid}/interviews", status_code=201)
def create_interview(pid: str, body: NewInterview, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    is_group = body.kind == groups.KIND_GROUP
    if is_group and body.moderation is ModerationMode.AI:
        raise HTTPException(422, "座谈会暂不支持 AI 主持，请选择人工主持")
    if body.moderation is ModerationMode.AI and (why := ai_moderation_blocker()):
        raise HTTPException(422, why)
    if is_group:
        pids = list(dict.fromkeys(body.participant_ids))
        if not (1 <= len(pids) <= groups.MAX_GROUP):
            raise HTTPException(422, f"座谈会需要 1 到 {groups.MAX_GROUP} 位受访者")
        parts = [_eligible_participant(db, ctx, proj, p) for p in pids]
    else:
        if not body.participant_id:
            raise HTTPException(422, "请选择受访者")
        parts = [_eligible_participant(db, ctx, proj, body.participant_id)]
    if body.moderator_id:
        get_user_in_org(db, ctx, body.moderator_id, ("moderator", "researcher", "admin"))
    obs = [get_user_in_org(db, ctx, uid_, ("observer", "researcher", "admin")) for uid_ in dict.fromkeys(body.observer_ids)]
    token = new_secret_token()  # depth: the participant's link; group: unused placeholder (each seat has its own link)
    itv = Interview(org_id=ctx.org_id, project_id=proj.id, participant_id=parts[0].id, moderator_id=body.moderator_id, title=body.title,
                    language=body.language or parts[0].language or proj.language, scheduled_at=body.scheduled_at, invite_hash=token_hash(token),
                    moderation=body.moderation.value, kind=body.kind)
    itv.observers = [InterviewObserver(user_id=u.id) for u in obs]
    db.add(itv)
    db.flush()  # assigns the id, so the audit row points at this interview
    group_invites = []
    if is_group:
        for p in parts:
            tok = new_secret_token()
            seat = groups.add_seat(db, itv, p, token_hash(tok))
            group_invites.append({"participant_id": p.id, "seat": seat.seat, "invite_path": f"/join.html?t={tok}", "invite_token": tok})
    audit(db, ctx.org_id, ctx.actor, "interview.create", itv.id, ip=ctx.ip, kind=body.kind)
    db.commit()
    db.refresh(itv)
    out = interview_out(itv, ctx, None if is_group else token, db=db)
    if is_group:
        out["group_invites"] = group_invites  # shown once, like the depth invite link
    return out


@router.get("/projects/{pid}/interviews")
def list_interviews(pid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    rows = db.scalars(select(Interview).where(Interview.project_id == pid).order_by(Interview.scheduled_at.desc().nullslast(), Interview.created_at.desc())).all()
    out = []
    for itv in rows:
        try:
            visible_interview(db, ctx, itv.id)
        except HTTPException:
            continue
        out.append(interview_out(itv, ctx, db=db))
    return out


@router.get("/interviews/{iid}")
def get_interview(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    return interview_out(visible_interview(db, ctx, iid), ctx, db=db)


# ---------- pre-interview task ----------
class PreTaskQuestion(BaseModel):
    prompt: str = Field(min_length=1, max_length=pretask.MAX_PROMPT)
    required: bool = True


class PreTaskConfig(BaseModel):
    questions: list[PreTaskQuestion] = Field(default_factory=list, max_length=pretask.MAX_QUESTIONS)


@router.put("/interviews/{iid}/pretask")
def set_pretask(iid: str, body: PreTaskConfig, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Configure the questions the participant answers after consent, before the waiting room. Empty list = no pre-task."""
    itv = visible_interview(db, ctx, iid)
    try:
        pretask.set_questions(db, ctx.org_id, itv.id, [q.model_dump() for q in body.questions])
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, ctx.org_id, ctx.actor, "pretask.config", itv.id, n=len(body.questions))
    db.commit()
    return pretask.staff_view(db, itv.id, itv.participant_id)


@router.get("/interviews/{iid}/pretask")
def get_pretask(iid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    """The questions and this participant's answers. Moderators see it in the room; observers (clients) never do."""
    itv = visible_interview(db, ctx, iid)
    return pretask.staff_view(db, itv.id, itv.participant_id)


# ---------- candidate interview times ----------
class TimeSlots(BaseModel):
    starts: list[datetime] = Field(default_factory=list, max_length=scheduling.MAX_SLOTS)


@router.put("/interviews/{iid}/timeslots")
def set_timeslots(iid: str, body: TimeSlots, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Offer the participant a few candidate times to choose from. Empty list removes them. Non-empty clears the fixed time."""
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    try:
        scheduling.set_slots(db, ctx.org_id, itv.id, body.starts)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, ctx.org_id, ctx.actor, "timeslots.config", itv.id, n=len(body.starts))
    db.commit()
    return scheduling.staff_view(db, itv)


@router.get("/interviews/{iid}/timeslots")
def get_timeslots(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The candidate times and which one the participant chose."""
    itv = visible_interview(db, ctx, iid)
    return scheduling.staff_view(db, itv)


@router.post("/interviews/{iid}/invite/rotate")
def rotate_invite(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    token = new_secret_token()
    itv.invite_hash = token_hash(token)
    audit(db, ctx.org_id, ctx.actor, "interview.invite.rotate", itv.id)
    db.commit()
    return {"invite_path": f"/join.html?t={token}", "invite_token": token}


class ObserverBody(BaseModel):
    user_id: str


@router.post("/interviews/{iid}/observers", status_code=201)
def add_observer(iid: str, body: ObserverBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    u = get_user_in_org(db, ctx, body.user_id, ("observer", "researcher", "admin"))
    if not any(o.user_id == u.id for o in itv.observers):
        itv.observers.append(InterviewObserver(user_id=u.id))
        audit(db, ctx.org_id, ctx.actor, "observer.add", itv.id, user=u.id)
        db.commit()
    return {"observer_ids": [o.user_id for o in itv.observers]}


@router.delete("/interviews/{iid}/observers/{user_id}")
def remove_observer(iid: str, user_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    itv.observers = [o for o in itv.observers if o.user_id != user_id]
    audit(db, ctx.org_id, ctx.actor, "observer.remove", itv.id, user=user_id)
    db.commit()
    return {"observer_ids": [o.user_id for o in itv.observers]}


# ---------- focus-group roster (seats; only for kind="group") ----------
class SeatBody(BaseModel):
    participant_id: str


def _group_only(itv: Interview) -> None:
    if not groups.is_group(itv):
        raise HTTPException(409, "这不是座谈会")


@router.post("/interviews/{iid}/participants", status_code=201)
def add_group_participant(iid: str, body: SeatBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Seat another participant in a focus group; returns their one-time invite link."""
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    _group_only(itv)
    proj = get_project(db, ctx, itv.project_id)
    part = _eligible_participant(db, ctx, proj, body.participant_id)
    tok = new_secret_token()
    try:
        seat = groups.add_seat(db, itv, part, token_hash(tok))
    except ValueError:
        raise HTTPException(422, f"无法加入：可能已在名单中，或已满 {groups.MAX_GROUP} 人")
    audit(db, ctx.org_id, ctx.actor, "group.add", itv.id, seat=seat.seat)
    db.commit()
    return {"seat": seat.seat, "participant_id": part.id, "invite_path": f"/join.html?t={tok}", "invite_token": tok}


@router.delete("/interviews/{iid}/participants/{participant_id}", status_code=204)
def remove_group_participant(iid: str, participant_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Remove a participant from a focus group (only before it starts, and never the last one)."""
    itv = visible_interview(db, ctx, iid)
    _group_only(itv)
    if itv.status != "scheduled":
        raise HTTPException(409, "访谈已开始，不能移除受访者")
    if len(groups.seats(db, itv.id)) <= 1:
        raise HTTPException(409, "座谈会至少保留一位受访者")
    if not groups.remove_seat(db, itv.id, participant_id):
        raise HTTPException(404, "不在名单中")
    audit(db, ctx.org_id, ctx.actor, "group.remove", itv.id)
    db.commit()


@router.post("/interviews/{iid}/participants/{participant_id}/invite/rotate")
def rotate_group_invite(iid: str, participant_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A fresh invite link for one seat (the old one stops working)."""
    itv = visible_interview(db, ctx, iid)
    _group_only(itv)
    seat = groups.seat_for(db, itv.id, participant_id)
    if not seat:
        raise HTTPException(404, "不在名单中")
    tok = new_secret_token()
    seat.invite_hash = token_hash(tok)
    audit(db, ctx.org_id, ctx.actor, "group.invite.rotate", itv.id, seat=seat.seat)
    db.commit()
    return {"invite_path": f"/join.html?t={tok}", "invite_token": tok}


# ---------- room tokens (staff side; participant side is in public.py) ----------
class TokenReq(BaseModel):
    as_: str | None = Field(default=None, alias="as")


@router.post("/interviews/{iid}/token")
def room_token(iid: str, request: Request, body: TokenReq | None = None, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    role_user, want = ctx.user.role, (body.as_ if body else None)
    if role_user in STAFF:
        role = want if want in ("moderator", "observer") else "moderator"
    elif role_user == "moderator":
        role = "moderator"
    else:
        role = "observer"
    if want and role_user not in STAFF and want != role:
        raise HTTPException(403, "没有权限以该身份进入")
    token = build_room_token(identity=f"{'obs' if role == 'observer' else 'staff'}:{ctx.user.id}", name=ctx.user.name, room=itv.room_name, role=role)
    audit(db, ctx.org_id, ctx.actor, "room.token", itv.id, ip=ctx.ip, role=role)
    db.commit()
    return {"token": token, "url": url_for_client(request.headers.get("host")), "room": itv.room_name, "role": role, "dev_mode": not settings.livekit_url, "moderation": itv.moderation, "kind": itv.kind}


@router.post("/interviews/{iid}/start")
def start_interview(iid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended":
        raise HTTPException(409, "访谈已结束")
    if itv.status == "scheduled":
        itv.status, itv.started_at = "live", utcnow()
        audit(db, ctx.org_id, ctx.actor, "interview.start", itv.id)
        db.commit()
    return interview_out(itv, ctx)


def _active_recording(db: Session, iid: str) -> Recording | None:
    return db.scalar(select(Recording).where(Recording.interview_id == iid, Recording.status == "recording"))


def _stop_recording(db: Session, rec: Recording) -> None:
    rec.stopped_at = utcnow()
    # dev (no video server): nothing was recorded, so it is "ready" at once. Otherwise the worker mixes the uploaded tracks.
    rec.status = "ready" if rec.egress_id.startswith("dev-") else "processing"


@router.post("/interviews/{iid}/end")
def end_interview(iid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status != "ended":
        if rec := _active_recording(db, iid):
            _stop_recording(db, rec)
        itv.status, itv.ended_at = "ended", utcnow()
        ai_room.finalize(db, itv)  # an AI-moderated chat becomes the transcript
        audit(db, ctx.org_id, ctx.actor, "interview.end", itv.id)
        db.commit()
        delete_room(itv.room_name)  # everyone is removed from the video room
    return interview_out(itv, ctx)


# ---------- recording ----------
@router.post("/interviews/{iid}/recording/start", status_code=201)
def recording_start(iid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status != "live":
        raise HTTPException(409, "访谈还没开始")
    if not db.scalar(select(Consent).where(Consent.interview_id == itv.id, Consent.recording.is_(True), Consent.cross_border.is_(True))):
        raise HTTPException(409, "受访者还没有同意录制，不能开始录制")
    if _active_recording(db, iid):
        raise HTTPException(409, "已经在录制")
    rid = uid()
    # With a video server, every browser records itself and uploads chunks (app/recording.py). Without one (dev), nothing is recorded.
    rec = Recording(id=rid, org_id=ctx.org_id, interview_id=itv.id, egress_id=("client-" if settings.livekit_url else "dev-") + rid,
                    storage_key=f"private/{ctx.org_id}/{itv.id}/{rid}.mp4")
    db.add(rec)
    audit(db, ctx.org_id, ctx.actor, "recording.start", itv.id)
    db.commit()
    return {"id": rec.id, "status": rec.status}  # storage_key is never returned


@router.post("/interviews/{iid}/recording/stop")
def recording_stop(iid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    rec = _active_recording(db, itv.id)
    if not rec:
        raise HTTPException(409, "没有正在进行的录制")
    _stop_recording(db, rec)
    audit(db, ctx.org_id, ctx.actor, "recording.stop", itv.id)
    db.commit()
    return {"id": rec.id, "status": rec.status, "duration_ms": rec.duration_ms}


@router.get("/interviews/{iid}/recordings")
def recordings(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(Recording).where(Recording.interview_id == itv.id).order_by(Recording.started_at))
    return [{"id": r.id, "status": r.status, "duration_ms": r.duration_ms, "started_at": r.started_at.isoformat(), "error": r.error,
             "playable": r.status == "ready" and os.path.isfile(rec_file(r))} for r in rows]


@router.get("/interviews/{iid}/live")
def live_state(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """Polled by the room page every 2 s: interview status, whether the participant consented, and the recording in progress."""
    itv = visible_interview(db, ctx, iid)
    rec = _active_recording(db, itv.id)
    consented = db.scalar(select(Consent.id).where(Consent.interview_id == itv.id, Consent.recording.is_(True), Consent.cross_border.is_(True))) is not None
    out = {"status": itv.status, "consented": consented, "recording": {"id": rec.id, "elapsed_ms": elapsed_ms(rec)} if rec else None,
           "stimulus": concept.current_stimulus(db, itv.id, staff=True), "elapsed_ms": concept.at_ms(itv) if itv.status == "live" else None}
    if ctx.user.role != "observer":  # who is in the waiting room is for the moderator, not the client
        if groups.is_group(itv):
            out["participants"] = [{"participant_id": s.participant_id, "seat": s.seat, "label": groups.label(s),
                                    "name": s.participant.name, "waiting": in_waiting_room(itv, s),
                                    "admitted": s.admitted_at is not None, "held": s.held_at is not None and s.admitted_at is None,
                                    "device_ok": s.device_ok_at is not None, "entered": s.entered_at is not None}
                                   for s in groups.seats(db, itv.id)]
        else:
            out["participant"] = {"waiting": in_waiting_room(itv), "admitted": itv.admitted_at is not None,
                                  "held": itv.held_at is not None and itv.admitted_at is None, "device_ok": itv.device_ok_at is not None}
    return out


class AdmitBody(BaseModel):
    admit: bool
    participant_id: str | None = None  # which seat, for a focus group; a depth interview has only one


@router.post("/interviews/{iid}/admit")
def admit(iid: str, body: AdmitBody, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    """Waiting room: let a participant into the room, or ask them to wait a little longer.

    A depth interview has one participant. A focus group admits one seat at a time (``participant_id``)."""
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    if groups.is_group(itv):
        if not body.participant_id:
            raise HTTPException(422, "请指定要放行的受访者")
        row = groups.seat_for(db, itv.id, body.participant_id)
        if not row:
            raise HTTPException(404, "不在名单中")
    else:
        row = itv
    if body.admit:
        row.admitted_at, row.held_at = utcnow(), None
    else:
        row.admitted_at, row.held_at = None, utcnow()
    audit(db, ctx.org_id, ctx.actor, "participant.admit" if body.admit else "participant.hold", itv.id)
    db.commit()
    return {"admitted": row.admitted_at is not None, "held": row.held_at is not None}


_EXT = {"video/webm": "webm", "audio/webm": "webm", "video/mp4": "mp4", "audio/mp4": "mp4", "audio/ogg": "ogg"}


@router.post("/interviews/{iid}/recording/{rid}/chunk", status_code=204)
async def upload_chunk(iid: str, rid: str, request: Request, seq: int, offset_ms: int = 0, last: int = 0, part: str = "0",
                       ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    """The moderator's browser uploads its own recording in chunks."""
    itv = visible_interview(db, ctx, iid)
    rec = db.get(Recording, rid)
    if not rec or rec.interview_id != itv.id:
        raise HTTPException(404, "录制不存在")
    if rec.status not in ("recording", "processing"):
        raise HTTPException(409, "这次录制已经结束")
    db.commit()  # 先把数据库连接还给连接池，再等（可能很慢的跨国）上传；save_chunk 会重新取一个
    data = await request.body()
    if len(data) > settings.max_chunk_bytes or seq < 0 or seq > 20_000:
        raise HTTPException(413, "分片太大")
    ext = _EXT.get(request.headers.get("content-type", "").split(";")[0].strip(), "webm")
    await run_in_threadpool(save_chunk, db, rec, "moderator", part, seq, offset_ms, ext, data, bool(last))


@router.post("/interviews/{iid}/recordings/{rid}/play")
def play_recording(iid: str, rid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    """Internal team only: a short-lived link to watch the full recording. Clients never get this."""
    itv = visible_interview(db, ctx, iid)
    rec = db.get(Recording, rid)
    if not rec or rec.interview_id != itv.id or rec.status != "ready":
        raise HTTPException(404, "录制还没有准备好")
    audit(db, ctx.org_id, ctx.actor, "recording.play", itv.id, ip=ctx.ip)
    db.commit()
    return {"url": "/api/public/media?t=" + sign_media(rec.storage_key, -1, -1), "duration_ms": rec.duration_ms}


# ---------- transcript ----------
class SegIn(BaseModel):
    idx: int = Field(ge=0)
    speaker: str = Field(pattern=groups.SPEAKER_PATTERN)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text_orig: str = Field(min_length=1)
    text_zh: str = ""
    lang: str = ""


class TranscriptIn(BaseModel):
    segments: list[SegIn] = Field(min_length=1)


def _ingest(db: Session, itv: Interview, segs: list[dict]) -> None:
    """Replace an interview's transcript. A focus group stores ``p1`` … ``p10``; a depth interview stores ``participant``.

    Raises:
        HTTPException: insights already quote the old transcript (409), a speaker key does not belong on this interview (422),
            or a segment ends before it starts (422).
    """
    if db.scalar(select(Insight.id).where(Insight.interview_id == itv.id)):
        raise HTTPException(409, "已有洞察引用旧转写，不能整体替换；请逐段校对")
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id))
    for s in segs:
        if not groups.speaker_fits(itv, s["speaker"]):
            raise HTTPException(422, f"片段 #{s['idx']} 的发言人无效")
        if s["end_ms"] < s["start_ms"]:
            raise HTTPException(422, f"片段 #{s['idx']} 结束时间早于开始时间")
        db.add(TranscriptSegment(org_id=itv.org_id, interview_id=itv.id, **s))


@router.post("/interviews/{iid}/transcript")
def transcript_ingest(iid: str, body: TranscriptIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _ingest(db, itv, [s.model_dump() for s in body.segments])
    audit(db, ctx.org_id, ctx.actor, "transcript.ingest", itv.id, n=len(body.segments))
    db.commit()
    return {"segments": len(body.segments)}


@router.post("/interviews/{iid}/transcribe")
def transcribe(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    rec = db.scalar(select(Recording).where(Recording.interview_id == itv.id, Recording.status.in_(("ready", "processing"))).order_by(Recording.started_at.desc()))
    if not rec:
        raise HTTPException(409, "没有可转写的录制")
    if rec.status == "processing":
        raise HTTPException(409, "录像还在合成中，请稍等几十秒再试")
    segs = build_segments(db, itv, rec)
    _ingest(db, itv, segs)
    audit(db, ctx.org_id, ctx.actor, "transcript.transcribe", itv.id, n=len(segs), provider=settings.asr_provider)
    db.commit()
    return {"segments": len(segs)}


@router.get("/interviews/{iid}/transcript")
def transcript(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx))
    audit(db, ctx.org_id, ctx.actor, "transcript.view", itv.id, ip=ctx.ip)
    db.commit()
    return [segment_out(s) for s in rows]


class SegPatch(BaseModel):
    text_orig: str | None = Field(default=None, min_length=1)
    text_zh: str | None = None


@router.patch("/transcript/{seg_id}")
def patch_segment(seg_id: str, body: SegPatch, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    seg = db.get(TranscriptSegment, seg_id)
    if not seg or seg.org_id != ctx.org_id:
        raise HTTPException(404, "片段不存在")
    if body.text_orig is not None:
        seg.text_orig = body.text_orig
    if body.text_zh is not None:
        seg.text_zh = body.text_zh
    flagged = 0
    for e in db.scalars(select(Evidence).where(Evidence.segment_id == seg.id)):
        text = seg.text_orig if e.quote_lang == "orig" else seg.text_zh
        if not quote_matches(e.quote, text):  # edited text no longer contains the quote -> a human must look again
            ins = db.get(Insight, e.insight_id)
            if ins.status != "needs_review":
                ins.status, flagged = "needs_review", flagged + 1
    audit(db, ctx.org_id, ctx.actor, "transcript.edit", seg.id, flagged=flagged)
    db.commit()
    return {**segment_out(seg), "insights_flagged": flagged}


# ---------- insights ----------
class EvidenceIn(BaseModel):
    segment_id: str
    quote: str = Field(min_length=1)
    lang: str | None = None


class NewInsight(BaseModel):
    theme: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1)
    evidence: list[EvidenceIn] = Field(min_length=1)


def _make_insight(db: Session, ctx: Ctx, itv: Interview, theme: str, statement: str, items: list[dict], kind: str):
    ok, errors = check_evidence(db, itv.id, items)
    if not ok:
        return None, errors
    ins = Insight(org_id=itv.org_id, interview_id=itv.id, theme=theme[:200], statement=statement, kind=kind, status="draft", created_by=ctx.user.id)
    ins.evidence = [Evidence(segment_id=c.segment.id, quote=c.quote, quote_lang=c.lang, start_ms=c.segment.start_ms, end_ms=c.segment.end_ms) for c in ok]
    db.add(ins)
    return ins, errors


@router.post("/interviews/{iid}/insights", status_code=201)
def create_insight(iid: str, body: NewInsight, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    ins, errors = _make_insight(db, ctx, itv, body.theme, body.statement, [e.model_dump() for e in body.evidence], "manual")
    if not ins:
        raise HTTPException(422, {"message": "没有一条证据能在转写里核对上", "errors": errors})
    audit(db, ctx.org_id, ctx.actor, "insight.create", itv.id)
    db.commit()
    return {**insight_out(db, ins), "dropped_evidence": errors}


@router.post("/interviews/{iid}/insights/generate")
def generate_insights(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    segs = [segment_out(s) for s in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx))]
    if not segs:
        raise HTTPException(409, "还没有转写")
    try:
        proposals = get_llm().insights(segs)
    except Exception as e:  # provider failure must not look like "no insights"
        raise HTTPException(502, f"模型调用失败：{type(e).__name__}")
    created, dropped = [], []
    for p in proposals:
        ins, errors = _make_insight(db, ctx, itv, str(p.get("theme", ""))[:200] or "未命名", str(p.get("statement", "")), list(p.get("evidence", [])), "ai")
        if ins:
            created.append(ins)
        else:
            dropped.append({"theme": p.get("theme"), "reasons": errors or ["没有证据"]})
    audit(db, ctx.org_id, ctx.actor, "insight.generate", itv.id, created=len(created), dropped=len(dropped), provider=settings.llm_provider)
    db.commit()
    return {"created": [insight_out(db, i) for i in created], "dropped": dropped}


@router.get("/interviews/{iid}/insights")
def list_insights(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    q = select(Insight).where(Insight.interview_id == itv.id).order_by(Insight.created_at)
    if ctx.user.role not in STAFF:  # clients only see what a researcher approved
        q = q.where(Insight.status == "approved")
    return [insight_out(db, i) for i in db.scalars(q)]


def _insight(db: Session, ctx: Ctx, insight_id: str) -> Insight:
    ins = db.get(Insight, insight_id)
    if not ins or ins.org_id != ctx.org_id:
        raise HTTPException(404, "洞察不存在")
    visible_interview(db, ctx, ins.interview_id)
    return ins


@router.get("/projects/{pid}/insights/export")
def export_insights(pid: str, format: str = "docx", approved_only: bool = False, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Download the project's insights with their verbatim quotes as Word or CSV (staff only; participants appear as P-001 codes)."""
    if format not in ("docx", "csv"):
        raise HTTPException(422, "格式只能是 docx 或 csv")
    proj = get_project(db, ctx, pid)
    code = {p.id: f"P-{n + 1:03d}" for n, p in enumerate(db.scalars(select(Participant).where(Participant.project_id == proj.id).order_by(Participant.created_at)))}
    rows = []
    for itv in db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.org_id == ctx.org_id).order_by(Interview.created_at)):
        q = select(Insight).where(Insight.interview_id == itv.id).order_by(Insight.created_at)
        if approved_only:
            q = q.where(Insight.status == "approved")
        for ins in db.scalars(q):
            rows.append({**insight_out(db, ins), "pcode": code.get(itv.participant_id, "?"), "interview": itv.title})
    audit(db, ctx.org_id, ctx.actor, "insight.export", proj.id, format=format, count=len(rows))
    db.commit()
    if format == "csv":
        return Response(export.to_csv(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="insights.csv"'})
    data = export.to_docx(proj.name, rows, utcnow().strftime("%Y-%m-%d"))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": 'attachment; filename="insights.docx"'})


@router.post("/insights/{insight_id}/approve")
def approve_insight(insight_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    ins = _insight(db, ctx, insight_id)
    if not ins.evidence or any(e.segment_id is None for e in ins.evidence):
        raise HTTPException(409, "证据已失效，不能通过")
    ins.status = "approved"
    audit(db, ctx.org_id, ctx.actor, "insight.approve", ins.id)
    db.commit()
    return insight_out(db, ins)


@router.delete("/insights/{insight_id}")
def delete_insight(insight_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    ins = _insight(db, ctx, insight_id)
    db.execute(delete(InsightCode).where(InsightCode.insight_id == ins.id))
    db.delete(ins)
    audit(db, ctx.org_id, ctx.actor, "insight.delete", ins.id)
    db.commit()
    return {"ok": True}


# ---------- clips & share ----------
class NewClip(BaseModel):
    evidence_id: str | None = None
    title: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


MAX_CLIP_MS = 120_000
PAD_MS = 2_000


@router.post("/interviews/{iid}/clips", status_code=201)
def create_clip(iid: str, body: NewClip, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    title, ev_id = body.title, None
    if body.evidence_id:
        ev = db.get(Evidence, body.evidence_id)
        ins = db.get(Insight, ev.insight_id) if ev else None
        if not ev or not ins or ins.interview_id != itv.id:
            raise HTTPException(404, "证据不存在")
        start, end, ev_id, title = max(0, ev.start_ms - PAD_MS), ev.end_ms + PAD_MS, ev.id, title or ins.theme
    else:
        if body.start_ms is None or body.end_ms is None:
            raise HTTPException(422, "需要 evidence_id，或起止时间")
        start, end, title = body.start_ms, body.end_ms, title or "未命名片段"
    if end <= start or end - start > MAX_CLIP_MS:
        raise HTTPException(422, f"片段长度需在 0–{MAX_CLIP_MS // 1000} 秒之间")
    rec = db.scalar(select(Recording).where(Recording.interview_id == itv.id, Recording.status.in_(("ready", "processing"))))
    if rec and rec.duration_ms:
        end = min(end, rec.duration_ms)
        if end <= start:
            raise HTTPException(422, "片段超出录制时长")
    c = Clip(org_id=itv.org_id, interview_id=itv.id, evidence_id=ev_id, title=title[:200], start_ms=start, end_ms=end, created_by=ctx.user.id)
    db.add(c)
    audit(db, ctx.org_id, ctx.actor, "clip.create", itv.id)
    db.commit()
    return clip_out(c)


@router.get("/interviews/{iid}/clips")
def list_clips(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    return [clip_out(c) for c in db.scalars(select(Clip).where(Clip.interview_id == itv.id).order_by(Clip.start_ms))]


@router.post("/clips/{clip_id}/preview")
def preview_clip(clip_id: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "moderator")), db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if not clip or clip.org_id != ctx.org_id:
        raise HTTPException(404, "片段不存在")
    visible_interview(db, ctx, clip.interview_id)
    rec = db.scalar(select(Recording).where(Recording.interview_id == clip.interview_id, Recording.status == "ready"))
    if not rec:
        raise HTTPException(404, "录制还没有准备好")
    return {"url": "/api/public/media?t=" + sign_media(rec.storage_key, clip.start_ms, clip.end_ms, watermark=f"星桐 · {ctx.user.email} · 内部预览")}


class NewShare(BaseModel):
    expires_hours: int = Field(default=72, ge=1, le=720)
    watermark: str | None = Field(default=None, max_length=200)


@router.post("/clips/{clip_id}/share", status_code=201)
def share_clip(clip_id: str, body: NewShare, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if not clip or clip.org_id != ctx.org_id:
        raise HTTPException(404, "片段不存在")
    token = new_secret_token()
    sl = ShareLink(org_id=ctx.org_id, clip_id=clip.id, token_hash=token_hash(token), created_by=ctx.user.id,
                   watermark=body.watermark or f"星桐 · {ctx.user.email}", expires_at=utcnow() + timedelta(hours=body.expires_hours))
    db.add(sl)
    audit(db, ctx.org_id, ctx.actor, "share.create", sl.id, clip=clip.id, hours=body.expires_hours)
    db.commit()
    return {"id": sl.id, "path": f"/share.html?t={token}", "token": token, "expires_at": sl.expires_at.isoformat(), "watermark": sl.watermark}


@router.get("/clips/{clip_id}/shares")
def list_shares(clip_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    clip = db.get(Clip, clip_id)
    if not clip or clip.org_id != ctx.org_id:
        raise HTTPException(404, "片段不存在")
    return [{"id": s.id, "expires_at": s.expires_at.isoformat(), "revoked": s.revoked_at is not None, "views": s.views, "watermark": s.watermark}
            for s in db.scalars(select(ShareLink).where(ShareLink.clip_id == clip.id))]


@router.delete("/shares/{share_id}")
def revoke_share(share_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    sl = db.get(ShareLink, share_id)
    if not sl or sl.org_id != ctx.org_id:
        raise HTTPException(404, "分享链接不存在")
    sl.revoked_at = utcnow()
    audit(db, ctx.org_id, ctx.actor, "share.revoke", sl.id)
    db.commit()
    return {"ok": True}


# ---------- admin ----------
@router.get("/audit")
def audit_log(limit: int = 100, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == ctx.org_id).order_by(AuditLog.at.desc()).limit(min(limit, 500)))
    return [{"at": r.at.isoformat(), "actor": r.actor, "action": r.action, "target": r.target, "meta": r.meta} for r in rows]


@router.post("/admin/retention/run")
def retention_run(ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    # Note: runs for all orgs (system job). Restricted to admins only for manual testing; production uses `python -m app.retention`.
    return {"purged_interviews": run_retention(db)}
