"""Concept-test room features: the script, the material on the participant's screen, live captions, quotes and follow-up questions.

Who may do what: the moderator and the research team run the room (show a material, post their own captions, queue follow-ups);
a hidden observer may read captions and mark quotes / suggest follow-ups; the participant only posts captions of their own microphone
and sees the current material. Quote text is always copied from the caption line by the server, never taken from the browser.
"""
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import concept
from ..asr_live import token_out
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, current_ctx, require_roles, visible_interview
from ..models import CaptionLine, Highlight, Interview, RoomState, Stimulus, utcnow
from .public import _by_invite, _consented

router = APIRouter(prefix="/api", tags=["concept"])

OPERATE = ("admin", "researcher", "moderator")
MAX_TEXT = 600
MAX_LINES_PER_INTERVIEW = 5000
PARTIAL_TTL_S = 8.0
_PARTIAL: dict[tuple[str, str], tuple[str, float]] = {}  # the sentence being spoken right now, per (interview, speaker); memory only, never stored
_LINE_LOCK = threading.Lock()


at_ms = concept.at_ms


def _open(itv: Interview) -> None:
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    if itv.status != "live":
        raise HTTPException(409, "访谈进行中才能这样做")


# ---------- the script and the materials ----------
class TemplateBody(BaseModel):
    template: str


@router.post("/interviews/{iid}/concept/template")
def install_template(iid: str, body: TemplateBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    try:
        concept.install(db, ctx.org_id, itv.id, body.template)
    except concept.TemplateError as e:
        raise HTTPException(422, str(e))
    audit(db, ctx.org_id, ctx.actor, "concept.install", itv.id, ip=ctx.ip)
    db.commit()
    return concept.plan_out(db, itv.id)


@router.get("/interviews/{iid}/concept")
def get_concept(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The script and the materials, for the moderator and the team (observers and participants never get the script)."""
    itv = visible_interview(db, ctx, iid)
    return concept.plan_out(db, itv.id) or {"template": None}


class ShowBody(BaseModel):
    stimulus_id: str | None = None  # None: take the material off the screen


@router.post("/interviews/{iid}/stimulus/show")
def show_stimulus(iid: str, body: ShowBody, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _open(itv)
    if body.stimulus_id:
        s = db.get(Stimulus, body.stimulus_id)
        if not s or s.interview_id != itv.id:
            raise HTTPException(404, "没有这个素材")
    state = db.get(RoomState, itv.id) or RoomState(interview_id=itv.id, org_id=ctx.org_id)
    state.stimulus_id, state.shown_at, state.shown_by = body.stimulus_id, utcnow(), ctx.actor
    db.add(state)
    audit(db, ctx.org_id, ctx.actor, "stimulus.show" if body.stimulus_id else "stimulus.hide", body.stimulus_id or itv.id, ip=ctx.ip)
    db.commit()
    return {"stimulus": concept.current_stimulus(db, itv.id, staff=True)}


# ---------- live captions ----------
class CaptionIn(BaseModel):
    text: str = Field(max_length=MAX_TEXT)
    final: bool = True  # false: the sentence is still being spoken (shown greyed, not stored)


def _store_caption(db: Session, itv: Interview, speaker: str, body: CaptionIn) -> None:
    text = body.text.strip()
    if not text:
        return
    if not body.final:
        _PARTIAL[(itv.id, speaker)] = (text, time.time())
        return
    _PARTIAL.pop((itv.id, speaker), None)
    with _LINE_LOCK:  # moderator and participant finish sentences at the same time: keep seq numbers unique
        for _ in range(3):
            seq = (db.scalar(select(func.max(CaptionLine.seq)).where(CaptionLine.interview_id == itv.id)) or 0) + 1
            if seq > MAX_LINES_PER_INTERVIEW:
                raise HTTPException(429, "字幕条数已达上限")
            db.add(CaptionLine(org_id=itv.org_id, interview_id=itv.id, seq=seq, speaker=speaker, text=text, at_ms=at_ms(itv)))
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
    raise HTTPException(503, "字幕保存冲突，请稍后重试")


@router.post("/interviews/{iid}/captions", status_code=204)
def post_staff_caption(iid: str, body: CaptionIn, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The moderator's browser captions the moderator's own microphone."""
    itv = visible_interview(db, ctx, iid)
    _open(itv)
    _store_caption(db, itv, "moderator", body)


def _entered_participant(db: Session, token: str) -> Interview:
    itv = _by_invite(db, token)
    if itv.status != "live" or itv.admitted_at is None or not _consented(db, itv):
        raise HTTPException(409, "还不能发送字幕")  # no consent to recording / not let in / not live: nothing is captured
    return itv


@router.post("/public/join/{token}/captions", status_code=204)
def post_participant_caption(token: str, body: CaptionIn, db: Session = Depends(get_db)):
    """The participant's browser captions the participant's own microphone."""
    _store_caption(db, _entered_participant(db, token), "participant", body)


@router.get("/interviews/{iid}/captions")
def get_captions(iid: str, after: int = 0, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """Polled once a second by the team's room page: finished sentences after `after`, and what is being said right now."""
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id, CaptionLine.seq > after).order_by(CaptionLine.seq).limit(500)).all()
    now = time.time()
    partial = {sp: txt for (i, sp), (txt, ts) in list(_PARTIAL.items()) if i == itv.id and now - ts < PARTIAL_TTL_S}
    return {"lines": [{"id": c.id, "seq": c.seq, "speaker": c.speaker, "text": c.text, "at_ms": c.at_ms} for c in rows], "partial": partial}


@router.get("/interviews/{iid}/asr")
def staff_asr(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """A fresh signed address for the moderator's browser to stream its microphone to the speech recogniser (or why captions are off)."""
    return token_out(visible_interview(db, ctx, iid).language)


@router.get("/public/join/{token}/asr")
def participant_asr(token: str, db: Session = Depends(get_db)):
    itv = _by_invite(db, token)
    if itv.status != "live" or itv.admitted_at is None or not _consented(db, itv):
        return {"enabled": False, "retry": True, "reason": "访谈开始、你同意录制并被放行后才会开启字幕"}  # temporary: the browser asks again
    return token_out(itv.language)


# ---------- quotes and follow-up questions ----------
class HighlightIn(BaseModel):
    kind: Literal["quote", "probe"]
    caption_id: str | None = None  # the caption line being quoted / followed up (its text is copied by the server)
    text: str | None = Field(default=None, max_length=300)  # a typed follow-up question when there is no caption line


def highlight_out(h: Highlight, ctx: Ctx) -> dict:
    return {"id": h.id, "kind": h.kind, "speaker": h.speaker, "text": h.text, "caption_id": h.caption_id, "stimulus_key": h.stimulus_key,
            "at_ms": h.at_ms, "status": h.status, "created_at": h.created_at.isoformat(), "mine": h.created_by == ctx.user.id}


@router.post("/interviews/{iid}/highlights", status_code=201)
def create_highlight(iid: str, body: HighlightIn, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    speaker, text, cap_id, when = "", (body.text or "").strip(), None, at_ms(itv)
    if body.caption_id:
        line = db.get(CaptionLine, body.caption_id)
        if not line or line.interview_id != itv.id:
            raise HTTPException(404, "没有这条字幕")
        speaker, text, cap_id, when = line.speaker, line.text, line.id, line.at_ms  # verbatim, from the stored line
    elif body.kind == "quote" or not text:
        raise HTTPException(422, "引用必须来自一条字幕；追问需要一句话")
    stim = concept.current_stimulus(db, itv.id, staff=True)
    h = Highlight(org_id=ctx.org_id, interview_id=itv.id, kind=body.kind, speaker=speaker, text=text, caption_id=cap_id,
                  stimulus_key=(stim or {}).get("key", ""), at_ms=when, created_by=ctx.user.id)
    db.add(h)
    audit(db, ctx.org_id, ctx.actor, f"highlight.{body.kind}", itv.id, ip=ctx.ip)
    db.commit()
    return highlight_out(h, ctx)


@router.get("/interviews/{iid}/highlights")
def list_highlights(iid: str, kind: str | None = None, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    q = select(Highlight).where(Highlight.interview_id == itv.id)
    if kind in ("quote", "probe"):
        q = q.where(Highlight.kind == kind)
    return [highlight_out(h, ctx) for h in db.scalars(q.order_by(Highlight.created_at)).all()]


def _highlight(db: Session, ctx: Ctx, hid: str) -> Highlight:
    h = db.get(Highlight, hid)
    if not h or h.org_id != ctx.org_id:
        raise HTTPException(404, "没有这条记录")
    visible_interview(db, ctx, h.interview_id)  # an observer / moderator only reaches highlights of interviews they may see
    return h


class HighlightPatch(BaseModel):
    status: Literal["open", "done"]


@router.patch("/highlights/{hid}")
def patch_highlight(hid: str, body: HighlightPatch, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The moderator marks a queued follow-up as asked."""
    h = _highlight(db, ctx, hid)
    h.status = body.status
    db.commit()
    return highlight_out(h, ctx)


@router.delete("/highlights/{hid}", status_code=204)
def delete_highlight(hid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    h = _highlight(db, ctx, hid)
    if ctx.user.role not in OPERATE and h.created_by != ctx.user.id:
        raise HTTPException(403, "只能删除自己标记的")
    db.delete(h)
    db.commit()
