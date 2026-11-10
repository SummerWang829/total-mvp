"""Question slices of one continuous interview recording (respondent detail panel).

One video, starting at second 0. The model groups the caption track into outline-question spans;
all start/end times come from transcript_segments (ASR), never from the model. A rule-based
grouping is always available so the page works before the model has run.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import interview_detail_ai as timeline_ai
from .. import respondent_table
from ..db import get_db
from ..deps import Ctx, STAFF, require_roles
from ..models import Interview, InterviewTimeline, Project, TranscriptSegment, utcnow
from ..moderator.providers import ChatFatalError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["timeline"])


def _interview(db: Session, ctx: Ctx, iid: str) -> Interview:
    itv = db.get(Interview, iid)
    if not itv or itv.org_id != ctx.org_id:
        raise HTTPException(404, "访谈不存在")
    return itv


def _captions(db: Session, iid: str) -> list[dict]:
    rows = db.scalars(select(TranscriptSegment).where(
        TranscriptSegment.interview_id == iid).order_by(TranscriptSegment.idx)).all()
    return [{"id": r.id, "speaker": r.speaker, "text": r.text_orig, "text_zh": r.text_zh,
             "start_ms": r.start_ms, "end_ms": r.end_ms} for r in rows]


def _questions(db: Session, itv: Interview) -> list[str]:
    proj = db.get(Project, itv.project_id)
    return respondent_table.interview_questions(proj) if proj else []


@router.get("/interviews/{iid}/timeline/segments")
def get_segments(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Cached model slices, or rule slices plus a hint that the model still has to run."""
    itv = _interview(db, ctx, iid)
    caps = _captions(db, iid)
    row = db.scalar(select(InterviewTimeline).where(
        InterviewTimeline.interview_id == iid, InterviewTimeline.org_id == ctx.org_id))
    if row and row.segments and row.caption_count == len(caps):
        return {"state": "ready", "source": "model", "segments": row.segments,
                "caption_count": len(caps), "needs_generate": False}
    out = timeline_ai.rule_slices(caps)
    return {"state": "fallback" if caps else "no_transcript", "source": "rule", "segments": out,
            "caption_count": len(caps), "needs_generate": bool(caps)}


@router.post("/interviews/{iid}/timeline/generate")
def generate(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Ask the model which outline question each caption line belongs to, then cache the spans."""
    itv = _interview(db, ctx, iid)
    caps = _captions(db, iid)
    if not caps:
        return {"state": "no_transcript", "source": "none", "segments": [], "caption_count": 0,
                "needs_generate": False, "reason": "还没有转写字幕，无法切片"}
    questions = _questions(db, itv)
    if not questions:
        return {"state": "fallback", "source": "rule", "segments": timeline_ai.rule_slices(caps),
                "caption_count": len(caps), "needs_generate": False, "reason": "项目还没有访谈大纲，暂用规则切片"}
    try:
        chat = timeline_ai.chat_or_none()
    except RuntimeError as e:
        return {"state": "fallback", "source": "rule", "segments": timeline_ai.rule_slices(caps),
                "caption_count": len(caps), "needs_generate": False, "reason": str(e)}
    if chat is None:
        return {"state": "fallback", "source": "rule", "segments": timeline_ai.rule_slices(caps),
                "caption_count": len(caps), "needs_generate": False, "reason": "离线演示模式：模型未接入"}
    try:
        segs = timeline_ai.generate(chat, caps, questions)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    if not segs:
        return {"state": "fallback", "source": "rule", "segments": timeline_ai.rule_slices(caps),
                "caption_count": len(caps), "needs_generate": True, "reason": "模型没给出可用切片，已回退规则切片"}
    row = db.scalar(select(InterviewTimeline).where(
        InterviewTimeline.interview_id == iid, InterviewTimeline.org_id == ctx.org_id))
    if row is None:
        row = InterviewTimeline(org_id=ctx.org_id, interview_id=iid)
        db.add(row)
    row.segments, row.caption_count = segs, len(caps)
    row.created_by, row.updated_at = ctx.actor, utcnow()
    db.commit()
    log.info("timeline generated iid=%s slices=%s captions=%s", iid, len(segs), len(caps))
    return {"state": "ready", "source": "model", "segments": segs, "caption_count": len(caps),
            "needs_generate": False, "reason": ""}
