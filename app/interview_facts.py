"""Small facts about one interview that the live-room list shows next to what was planned: how long it really lasted, and the answers the person gave in the
recruiting questionnaire (for the card that opens when the mouse is on a row)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Interview, Recording, ScreenerAnswer

MAX_ANSWERS = 30
ANSWER_CHARS = 600


def actual_minutes(db: Session, itv: Interview) -> int | None:
    """How long the interview really lasted: the recording's length when there is one, else the time from start to end. None until it has ended."""
    if itv.status != "ended":
        return None
    ms = db.scalar(select(func.sum(Recording.duration_ms)).where(Recording.interview_id == itv.id, Recording.status == "ready"))
    if ms:
        return max(1, round(ms / 60000))
    if itv.started_at and itv.ended_at:
        return max(0, round((itv.ended_at - itv.started_at).total_seconds() / 60))
    return None


def key_answers(db: Session, participant_id: str) -> list[dict]:
    """The questionnaire answers as [{"q", "a"}], in the order of the questionnaire, shortened."""
    rows = db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id == participant_id).order_by(ScreenerAnswer.position).limit(MAX_ANSWERS)).all()
    return [{"q": r.question[:ANSWER_CHARS], "a": r.answer[:ANSWER_CHARS]} for r in rows if r.answer]
