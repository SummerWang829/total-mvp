"""Focus groups (``Interview.kind == "group"``): a group interview seats up to :data:`MAX_GROUP` participants, each with
their own invite link and their own join journey (device check, waiting-room admit, entry).

Design (kept deliberately simple to avoid two divergent code paths):

* A **depth** interview is unchanged — one participant on ``Interview.participant_id``, the join journey on the
  ``Interview`` row itself, and no ``InterviewParticipant`` rows.
* A **group** interview creates one ``InterviewParticipant`` ("seat") per participant. A seat carries the same journey
  field *names* as ``Interview`` (``device_ok_at``, ``admitted_at``, ``entered_at``, …), so the public-side journey code
  can operate on "a seat" that is either the ``Interview`` (depth) or an ``InterviewParticipant`` (group) by duck typing.

The ``seat`` number (1..:data:`MAX_GROUP`) is stable for the life of the interview and doubles as the recording-track /
transcript speaker key (``p1``, ``p2``, …) and the "参与者 ·NN" label shown in the room.
"""
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Interview, InterviewParticipant, Participant
from .security import token_hash

MAX_GROUP = 10
KIND_DEPTH = "depth"
KIND_GROUP = "group"
KINDS = (KIND_DEPTH, KIND_GROUP)


def is_group(itv: Interview) -> bool:
    """True when this interview is a focus group (has its own per-participant seats)."""
    return itv.kind == KIND_GROUP


def seats(db: Session, interview_id: str) -> list[InterviewParticipant]:
    """The group's seats, lowest seat number first."""
    return list(db.scalars(select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview_id).order_by(InterviewParticipant.seat)))


def seat_for(db: Session, interview_id: str, participant_id: str) -> InterviewParticipant | None:
    """The seat a given participant holds in this group, or None."""
    return db.scalar(select(InterviewParticipant).where(
        InterviewParticipant.interview_id == interview_id, InterviewParticipant.participant_id == participant_id))


def _next_seat(existing: list[InterviewParticipant]) -> int:
    """The lowest free seat number (so a removed seat is reused and numbers stay compact)."""
    taken = {s.seat for s in existing}
    for n in range(1, MAX_GROUP + 1):
        if n not in taken:
            return n
    raise ValueError("group is full")


def add_seat(db: Session, itv: Interview, participant: Participant, invite_hash: str) -> InterviewParticipant:
    """Seat a participant in a group interview.

    Returns:
        The created seat.

    Raises:
        ValueError: the participant is already seated, or the group already has :data:`MAX_GROUP` seats.
    """
    rows = seats(db, itv.id)
    if any(s.participant_id == participant.id for s in rows):
        raise ValueError("participant already in this group")
    if len(rows) >= MAX_GROUP:
        raise ValueError("group is full")
    seat = InterviewParticipant(org_id=itv.org_id, interview_id=itv.id, participant_id=participant.id,
                                seat=_next_seat(rows), invite_hash=invite_hash)
    db.add(seat)
    db.flush()  # so the next add_seat / seats() sees this seat number is taken (sessions run with autoflush off)
    return seat


def remove_seat(db: Session, interview_id: str, participant_id: str) -> bool:
    """Remove a participant from a group. Returns True when a seat was removed."""
    seat = seat_for(db, interview_id, participant_id)
    if not seat:
        return False
    db.delete(seat)
    return True


def seat_by_invite(db: Session, token: str) -> InterviewParticipant | None:
    """Resolve a group seat's invite token to its seat (None when the token matches no seat)."""
    return db.scalar(select(InterviewParticipant).where(InterviewParticipant.invite_hash == token_hash(token)))


def speaker_key(itv: Interview, seat: object) -> str:
    """The recording-track / transcript speaker key for a participant seat.

    Depth interviews use ``"participant"`` (unchanged); group seats use ``"p{seat}"`` so each track stays separate.
    """
    if not is_group(itv):
        return "participant"
    return f"p{seat.seat}"  # type: ignore[attr-defined]


def label(seat: InterviewParticipant) -> str:
    """The room label for a seat — a number only, never the real name (observers / other participants must not learn it)."""
    return f"参与者 ·{seat.seat:02d}"


def purge(db: Session, interview_id: str) -> None:
    """Delete every seat for an interview (its per-participant journey is personal data)."""
    db.execute(delete(InterviewParticipant).where(InterviewParticipant.interview_id == interview_id))


@dataclass
class Joiner:
    """The person an invite link belongs to.

    ``seat`` is the journey row the join flow reads and writes: the ``Interview`` itself for a depth interview, or the
    ``InterviewParticipant`` for one focus-group seat. Both carry the same journey fields (``device_ok_at``,
    ``admitted_at``, ``entered_at``, …), so one set of endpoints serves both.
    """
    itv: Interview
    seat: Interview | InterviewParticipant
    participant: Participant


def resolve_joiner(db: Session, token: str) -> Joiner | None:
    """The interview and seat for an invite token, or None when it matches nothing or the interview is purged.

    A focus group's own ``Interview.invite_hash`` is not a participant link — each seat has its own — so it does not resolve.
    """
    seat = seat_by_invite(db, token)
    if seat:
        itv = db.get(Interview, seat.interview_id)
        if not itv or itv.purged_at or not is_group(itv):
            return None
        return Joiner(itv, seat, seat.participant)
    itv = db.scalar(select(Interview).where(Interview.invite_hash == token_hash(token)))
    if not itv or itv.purged_at or is_group(itv):
        return None
    return Joiner(itv, itv, itv.participant)


def admitted_count(db: Session, interview_id: str) -> int:
    """How many seats the moderator has let into the room (for the group waiting-room summary)."""
    return len([s for s in seats(db, interview_id) if s.admitted_at is not None])


def roster_view(db: Session, itv: Interview) -> dict | None:
    """Staff view of a group's seats and where each participant is on their journey. None for a depth interview."""
    if not is_group(itv):
        return None
    rows = seats(db, itv.id)
    out = []
    for s in rows:
        p: Participant = s.participant
        out.append({"seat": s.seat, "participant_id": s.participant_id, "name": p.name,
                    "country": p.country, "label": label(s),
                    "opened": s.opened_at is not None, "device_ok": s.device_ok_at is not None,
                    "admitted": s.admitted_at is not None, "held": s.held_at is not None and s.admitted_at is None,
                    "entered": s.entered_at is not None})
    return {"max": MAX_GROUP, "seats": out}
