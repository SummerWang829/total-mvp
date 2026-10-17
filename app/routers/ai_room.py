"""AI moderation in the room: staff controls (start, take over, hand back, whisper, alert) and the participant's text chat.

The logic is in app/ai_room.py; this file only checks who may do what. Observers can read the conversation but nothing else,
and never see whispers, the AI's internal notes or alerts. Participants only ever get the conversation.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai_room, guide_text
from ..db import get_db
from ..deps import Ctx, current_ctx, require_roles, visible_interview
from ..models import Interview, Stimulus
from .public import resolve_join  # noqa: PLC2701 — the same invite-token lookup as the rest of the participant flow

router = APIRouter(prefix="/api", tags=["ai-room"])
STAFF_AND_MODERATOR = ("admin", "researcher", "moderator")  # who may control the AI (observers may not)


def _run(db: Session, fn, *args) -> None:
    """Call a service function; turn its refusal into an HTTP error and commit on success."""
    try:
        fn(db, *args)
    except ai_room.AiRoomError as e:
        db.rollback()
        raise HTTPException(e.status, e.message) from e
    db.commit()


class StartBody(BaseModel):
    guide: dict | None = None


class TextBody(BaseModel):
    text: str = Field(max_length=4000)


def _view(ctx: Ctx) -> str:
    return "observer" if ctx.user.role == "observer" else "staff"


class GuideText(BaseModel):
    text: str = Field(max_length=guide_text.MAX_CHARS + 1000)


@router.post("/interviews/{iid}/ai/guide/check")
def ai_guide_check(iid: str, body: GuideText, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    """Turn a typed / uploaded text outline into a guide and say what is wrong with it (nothing is stored; the guide is sent with 'start')."""
    itv = visible_interview(db, ctx, iid)
    try:
        guide = guide_text.parse_guide_text(body.text)
    except guide_text.GuideTextError as e:
        raise HTTPException(422, str(e)) from e
    installed = {s.key for s in db.scalars(select(Stimulus).where(Stimulus.interview_id == itv.id))}
    return {"guide": guide, "missing_stimuli": [s["id"] for s in guide["stimuli"] if s["id"] not in installed],
            "summary": {"topics": len(guide["topics"]), "questions": sum(len(t["questions"]) for t in guide["topics"]), "stimuli": len(guide["stimuli"])}}


@router.post("/interviews/{iid}/ai/start")
def ai_start(iid: str, body: StartBody | None = None, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.start, itv, body.guide if body else None, ctx.actor)
    return ai_room.state(db, itv, 0, "staff")


@router.get("/interviews/{iid}/ai/state")
def ai_state(iid: str, after: int = 0, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """Polled every 2 s by the room page: control, finished, new chat lines, and (not for observers) the alert."""
    itv = visible_interview(db, ctx, iid)
    return ai_room.state(db, itv, after, _view(ctx))


@router.post("/interviews/{iid}/ai/takeover")
def ai_takeover(iid: str, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.takeover, itv, ctx.actor)
    return {"control": "human"}


@router.post("/interviews/{iid}/ai/handback")
def ai_handback(iid: str, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.handback, itv, ctx.actor)
    return {"control": "ai"}


@router.post("/interviews/{iid}/ai/say")
def ai_say(iid: str, body: TextBody, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.human_says, itv, body.text, ctx.actor)
    return {"ok": True}


@router.post("/interviews/{iid}/ai/whisper")
def ai_whisper(iid: str, body: TextBody, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.whisper, itv, body.text, ctx.actor)
    return {"ok": True}


@router.post("/interviews/{iid}/ai/ack-alert")
def ai_ack_alert(iid: str, ctx: Ctx = Depends(require_roles(*STAFF_AND_MODERATOR)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _run(db, ai_room.ack_alert, itv, ctx.actor)
    return {"ok": True}


# ---------- the participant's side (by invitation token) ----------
def _in_room(db: Session, token: str) -> Interview:
    """The interview of this invitation, for a participant who has been let in (the same gate as entering the room)."""
    j = resolve_join(db, token)
    if not j.seat.admitted_at or not j.seat.entered_at:
        raise HTTPException(409, "Not in the room yet / 还没有进入房间")
    return j.itv


@router.get("/public/join/{token}/chat")
def chat_state(token: str, after: int = 0, db: Session = Depends(get_db)):
    """The conversation so far (only what the participant may see: no whispers, notes or alerts)."""
    itv = _in_room(db, token)
    return ai_room.state(db, itv, after, "participant")


@router.post("/public/join/{token}/chat", status_code=201)
def chat_send(token: str, body: TextBody, db: Session = Depends(get_db)):
    """The participant types an answer; while the AI is in control it replies (this call returns after the reply, ~3 s)."""
    itv = _in_room(db, token)
    _run(db, ai_room.participant_says, itv, body.text)
    return {"ok": True}
