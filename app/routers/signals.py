"""Emoji reactions from participants, and the "how it was said" overview for the team (see app/signals)."""
import re
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import concept, signals
from ..signals import emoji as emoji_data
from ..db import get_db
from ..deps import Ctx, current_ctx, require_roles, visible_interview
from ..models import CaptionLine, Reaction, utcnow
from ..signals import constants as K
from .concept import _entered_participant

router = APIRouter(prefix="/api", tags=["signals"])
OPERATE = ("admin", "researcher", "support", "moderator")
_LOCK = threading.Lock()
MOMENTS_SHOWN = 30


def who(speaker: str) -> str:
    """The room's words for a speaker key: "参与者 ·02" for a seat, "受访者" for the single participant of a depth interview, "主持人"."""
    m = re.fullmatch(r"p(\d+)", speaker)
    return f"参与者 ·{int(m.group(1)):02d}" if m else {"participant": "受访者", "moderator": "主持人"}.get(speaker, speaker)


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=emoji_data.MAX_LEN)

    @field_validator("emoji")
    @classmethod
    def _a_real_emoji(cls, value: str) -> str:
        """Only a real emoji from the official list: a reaction can never carry free text, and a few insulting ones are refused."""
        if not emoji_data.is_valid(value):
            raise ValueError("not an emoji that can be sent")
        return value


@router.post("/public/join/{token}/reaction", status_code=201)
def send_reaction(token: str, body: ReactionIn, db: Session = Depends(get_db)):
    """A participant who is in the room sends an emoji (the same conditions as live captions: live, let in, consented)."""
    from .. import groups

    j = _entered_participant(db, token)
    speaker = groups.speaker_key(j.itv, j.seat)
    with _LOCK:  # two quick taps must not take the same sequence number
        newest = db.scalar(select(func.max(Reaction.created_at)).where(Reaction.interview_id == j.itv.id, Reaction.speaker == speaker))
        if newest and (utcnow() - newest) < timedelta(seconds=K.MIN_REACTION_GAP_S):
            raise HTTPException(429, "慢一点，稍后再发")
        count = db.scalar(select(func.count()).select_from(Reaction).where(Reaction.interview_id == j.itv.id)) or 0
        if count >= K.MAX_REACTIONS_PER_INTERVIEW:
            raise HTTPException(429, "表情数量已达上限")
        shown = concept.current_stimulus(db, j.itv.id, staff=True)
        seq = (db.scalar(select(func.max(Reaction.seq)).where(Reaction.interview_id == j.itv.id)) or 0) + 1
        db.add(Reaction(org_id=j.itv.org_id, interview_id=j.itv.id, seq=seq, speaker=speaker, emoji=body.emoji, at_ms=concept.at_ms(j.itv),
                        stimulus_key=(shown or {}).get("key", "")))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(503, "稍后再试")
    return {"ok": True}


def reaction_out(r: Reaction) -> dict:
    return {"seq": r.seq, "speaker": r.speaker, "who": who(r.speaker), "emoji": r.emoji, "glyph": r.emoji, "label": emoji_data.label_zh(r.emoji), "at_ms": r.at_ms,
            "stimulus": r.stimulus_key}


@router.get("/interviews/{iid}/reactions")
def get_reactions(iid: str, after: int = 0, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """The reactions after sequence number `after` (the whole team incl. hidden observers may see them: they are what a participant chose to show)."""
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(Reaction).where(Reaction.interview_id == itv.id, Reaction.seq > after).order_by(Reaction.seq).limit(200)).all()
    return {"reactions": [reaction_out(r) for r in rows]}


@router.get("/interviews/{iid}/signals")
def get_signals(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """How things were said, for the operating team: per speaker a summary, the moments worth a look, and the reactions per speaker and per material.

    Hints only (always against the speaker's own habits); not for client observers, not for participants.
    """
    itv = visible_interview(db, ctx, iid)
    lines = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq)).all()
    reactions = db.scalars(select(Reaction).where(Reaction.interview_id == itv.id).order_by(Reaction.seq)).all()
    speakers: dict[str, dict] = {}
    for c in lines:
        if c.speaker == "moderator":
            continue
        s = speakers.setdefault(c.speaker, {"speaker": c.speaker, "who": who(c.speaker), "sentences": 0, "tone_sum": 0, "tags": {}, "reactions": {}})
        s["sentences"] += 1
        s["tone_sum"] += c.senti
        for t in filter(None, c.tags.split(",")):
            s["tags"][t] = s["tags"].get(t, 0) + 1
    by_stimulus: dict[str, dict[str, int]] = {}
    for r in reactions:
        s = speakers.setdefault(r.speaker, {"speaker": r.speaker, "who": who(r.speaker), "sentences": 0, "tone_sum": 0, "tags": {}, "reactions": {}})
        s["reactions"][r.emoji] = s["reactions"].get(r.emoji, 0) + 1
        if r.stimulus_key:
            by_stimulus.setdefault(r.stimulus_key, {})
            by_stimulus[r.stimulus_key][r.emoji] = by_stimulus[r.stimulus_key].get(r.emoji, 0) + 1
    summary = [{"speaker": s["speaker"], "who": s["who"], "sentences": s["sentences"], "tone": round(s["tone_sum"] / s["sentences"] / 100, 2) if s["sentences"] else 0.0,
                "tags": s["tags"], "reactions": s["reactions"]} for s in sorted(speakers.values(), key=lambda x: x["speaker"])]
    moments = [{"seq": c.seq, "speaker": c.speaker, "who": who(c.speaker), "text": c.text, "at_ms": c.at_ms, "hints": signals.labels(c.tags.split(",")), "tone": round(c.senti / 100, 2)}
               for c in lines if c.speaker != "moderator" and c.tags and signals.is_notable(c.tags.split(","), c.senti / 100)]
    return {"speakers": summary, "moments": moments[-MOMENTS_SHOWN:], "stimuli": by_stimulus, "note": "仅供参考：只和说话人自己平时的习惯比较，不是对情绪的结论"}
