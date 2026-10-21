"""Cue cards for the moderator of a focus group: private suggestions (who to invite, who talks too much, time, off topic, personal data)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import groups
from ..audit import audit
from ..cues import constants as K
from ..cues import service
from ..db import get_db
from ..deps import Ctx, require_roles, visible_interview
from ..models import CueCard

router = APIRouter(prefix="/api", tags=["cues"])
OPERATE = ("admin", "researcher", "support", "moderator")  # not the client observers: a card may quote a participant's screening answer


@router.get("/interviews/{iid}/cues")
def get_cues(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The waiting cards and the last few answered ones. Asking also lets the rules look at the room, so the moderator's page polls this."""
    itv = visible_interview(db, ctx, iid)
    if not groups.is_group(itv):
        raise HTTPException(409, "提词卡只用于座谈会")
    service.refresh(db, itv)
    return {"cards": service.cards_out(db, itv)}


class CueAnswer(BaseModel):
    status: Literal["used", "dismissed"]


@router.patch("/cues/{cid}")
def answer_cue(cid: str, body: CueAnswer, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """Mark a waiting card as used or dismissed."""
    card = db.get(CueCard, cid)
    if not card or card.org_id != ctx.org_id:
        raise HTTPException(404, "没有这张卡")
    itv = visible_interview(db, ctx, card.interview_id)
    service.handle(db, card, K.Status(body.status), ctx.actor)
    audit(db, ctx.org_id, ctx.actor, f"cue.{body.status}", card.id, kind=card.kind)
    db.commit()
    return {"cards": service.cards_out(db, itv)}
