"""The speaking-fairness panel of a focus group: who has talked how much, who has been quiet, who talks over whom (team only)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import concept, groups
from ..db import get_db
from ..deps import Ctx, current_ctx, visible_interview
from ..group_metrics.compute import Sentence, compute
from ..models import CaptionLine, Interview

router = APIRouter(prefix="/api", tags=["group-metrics"])


def elapsed_ms(itv: Interview) -> int:
    """How long the interview has run (live: until now; ended: its whole length; not started: 0)."""
    if itv.status == "ended" and itv.started_at and itv.ended_at:
        return max(0, int((itv.ended_at - itv.started_at) / timedelta(milliseconds=1)))
    return concept.at_ms(itv)


@router.get("/interviews/{iid}/group/metrics")
def group_metrics(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """Per-seat talk time and share, turns, silence, interruptions, the fairness index and its curve, and a few plain hints.

    Readable by the interview's team including the hidden observers (as the captions are); a participant has no staff login, so cannot.
    """
    itv = visible_interview(db, ctx, iid)
    if not groups.is_group(itv):
        raise HTTPException(409, "只有座谈会有发言情况")
    rows = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq)).all()
    sentences = [Sentence(r.speaker, r.text, r.at_ms, r.dur_ms) for r in rows]
    return {"elapsed_ms": elapsed_ms(itv), **compute(sentences, [s.seat for s in groups.seats(db, itv.id)], elapsed_ms(itv))}
