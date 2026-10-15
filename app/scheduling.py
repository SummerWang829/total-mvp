"""Candidate interview times: the research team offers a few, the participant picks one in their own timezone.

Opt-in per interview: no ``TimeSlot`` rows means the team fixed the time directly and the journey is unchanged.
Offering slots clears ``Interview.scheduled_at`` so the participant must choose; picking sets it again. Times are
naive UTC throughout, matching ``Interview.scheduled_at``.
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Interview, TimeSlot, utcnow

MAX_SLOTS = 6


def _naive_utc(dt: datetime) -> datetime:
    """Drop any timezone so the value compares with ``utcnow()`` (naive UTC)."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def slots(db: Session, interview_id: str) -> list[TimeSlot]:
    """The interview's candidate times, earliest first."""
    return list(db.scalars(select(TimeSlot).where(TimeSlot.interview_id == interview_id).order_by(TimeSlot.start_at)))


def needs_pick(db: Session, itv: Interview) -> bool:
    """True when candidate times are offered but the participant has not chosen one yet."""
    if itv.scheduled_at is not None:
        return False
    return db.scalar(select(TimeSlot.id).where(TimeSlot.interview_id == itv.id)) is not None


def set_slots(db: Session, org_id: str, interview_id: str, starts: list[datetime]) -> None:
    """Replace the candidate times for an interview.

    An empty list removes them (the time stays whatever it already was). A non-empty list clears
    ``Interview.scheduled_at`` so the participant must pick one.

    Raises:
        ValueError: more than ``MAX_SLOTS`` distinct times were given.
    """
    uniq = sorted({_naive_utc(s) for s in starts})
    if len(uniq) > MAX_SLOTS:
        raise ValueError("too many candidate times")
    db.execute(delete(TimeSlot).where(TimeSlot.interview_id == interview_id))
    if not uniq:
        return
    for s in uniq:
        db.add(TimeSlot(org_id=org_id, interview_id=interview_id, start_at=s))
    itv = db.get(Interview, interview_id)
    itv.scheduled_at = None  # a time is no longer fixed until the participant chooses


def pick(db: Session, itv: Interview, slot_id: str) -> datetime:
    """Set ``Interview.scheduled_at`` to the chosen candidate time.

    Returns:
        The chosen time (naive UTC).

    Raises:
        ValueError: there is nothing to pick (already chosen, or no slots) or the slot is not one of this interview's.
    """
    if not needs_pick(db, itv):
        raise ValueError("no time to pick")
    slot = db.get(TimeSlot, slot_id)
    if not slot or slot.interview_id != itv.id:
        raise ValueError("unknown candidate time")
    itv.scheduled_at = slot.start_at
    return slot.start_at


def public_view(db: Session, itv: Interview) -> dict | None:
    """What the participant's browser gets: the candidate times to choose from, or None when there is nothing to pick."""
    if not needs_pick(db, itv):
        return None
    return {"slots": [{"id": s.id, "start_at": s.start_at.isoformat()} for s in slots(db, itv.id)]}


def staff_view(db: Session, itv: Interview) -> dict:
    """The research team's view: the candidate times and which one the participant chose (if any)."""
    rows = slots(db, itv.id)
    chosen = itv.scheduled_at.isoformat() if (rows and itv.scheduled_at is not None) else None
    return {"slots": [{"id": s.id, "start_at": s.start_at.isoformat()} for s in rows], "chosen": chosen}
