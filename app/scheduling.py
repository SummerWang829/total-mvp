"""Candidate interview times: the research team offers a few, the participant picks one in their own timezone.

Opt-in per interview: no ``TimeSlot`` rows means the team fixed the time directly and the journey is unchanged.
Offering slots clears ``Interview.scheduled_at`` so the participant must choose; picking sets it again. Times are
naive UTC throughout, matching ``Interview.scheduled_at``.
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Interview, InterviewParticipant, SlotVote, TimeSlot, utcnow

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


def vote(db: Session, itv: Interview, participant_id: str, slot_ids: list[str]) -> None:
    """A focus-group participant marks every candidate time that suits them (replacing what they marked before).

    Raises:
        ValueError: the time is already confirmed, nothing was chosen, or a time is not one of this session's.
    """
    if itv.scheduled_at is not None:
        raise ValueError("already confirmed")
    valid = {s.id for s in slots(db, itv.id)}
    chosen = list(dict.fromkeys(slot_ids))
    if not chosen or any(i not in valid for i in chosen):
        raise ValueError("unknown candidate time")
    db.execute(delete(SlotVote).where(SlotVote.interview_id == itv.id, SlotVote.participant_id == participant_id))
    for sid in chosen:
        db.add(SlotVote(slot_id=sid, participant_id=participant_id, interview_id=itv.id, org_id=itv.org_id))
    db.flush()


def _votes(db: Session, interview_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for slot_id, pid in db.execute(select(SlotVote.slot_id, SlotVote.participant_id).where(SlotVote.interview_id == interview_id)):
        out.setdefault(slot_id, []).append(pid)
    return out


def confirm(db: Session, itv: Interview, slot_id: str) -> datetime:
    """The team fixes the session's time to one of the candidate times (focus groups: the one most people can make; a depth interview: overriding the participant).

    Raises:
        ValueError: the time is not one of this session's candidates.
    """
    slot = db.get(TimeSlot, slot_id)
    if not slot or slot.interview_id != itv.id:
        raise ValueError("unknown candidate time")
    itv.scheduled_at = slot.start_at
    return slot.start_at


def public_view(db: Session, itv: Interview, participant_id: str | None = None) -> dict | None:
    """What the participant's browser gets: the candidate times to choose from, or None when there is nothing to pick.

    A focus group votes: every time comes with how many people marked it and whether this participant did (never who)."""
    if not needs_pick(db, itv):
        return None
    if itv.kind == "group":
        votes = _votes(db, itv.id)
        return {"group": True, "voted": any(participant_id in v for v in votes.values()),
                "slots": [{"id": s.id, "start_at": s.start_at.isoformat(), "votes": len(votes.get(s.id, [])), "mine": participant_id in votes.get(s.id, [])} for s in slots(db, itv.id)]}
    return {"slots": [{"id": s.id, "start_at": s.start_at.isoformat()} for s in slots(db, itv.id)]}


def staff_view(db: Session, itv: Interview) -> dict:
    """The research team's view: the candidate times and which one was chosen (if any). A focus group also shows who can make each time and the best one."""
    rows = slots(db, itv.id)
    chosen = itv.scheduled_at.isoformat() if (rows and itv.scheduled_at is not None) else None
    out = {"slots": [{"id": s.id, "start_at": s.start_at.isoformat()} for s in rows], "chosen": chosen}
    if itv.kind == "group":
        votes = _votes(db, itv.id)
        seat = {p.participant_id: p.seat for p in db.scalars(select(InterviewParticipant).where(InterviewParticipant.interview_id == itv.id))}
        from .groups import speaker_label
        for sl in out["slots"]:
            who = sorted(votes.get(sl["id"], []), key=lambda pid: seat.get(pid, 99))
            sl["votes"], sl["voters"] = len(who), [speaker_label(f"p{seat[p]}") for p in who if p in seat]
        voted = {pid for v in votes.values() for pid in v}
        best = max(out["slots"], key=lambda sl: (sl["votes"], -out["slots"].index(sl)), default=None)
        out.update(group=True, seats=len(seat), voted=len(voted), best=best["id"] if best and best["votes"] else None)
    return out
