"""A participant's permanent number: a letter for where the person came from, then a running number inside the project (Q-001, X-001, M-001 ...).

The number is given when the person is added and never changes (deleting someone does not renumber the others), so exports, insights and the
list always show the same number for the same person.
"""
import logging

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .models import Participant

log = logging.getLogger(__name__)

PREFIX = {"link": "Q", "import": "X", "manual": "M", "platform": "E", "pool": "R"}
DEFAULT_PREFIX = "M"  # a person whose source was not recorded


def prefix_of(source: str) -> str:
    """The letter for a source."""
    return PREFIX.get(source or "", DEFAULT_PREFIX)


def _top(session: Session, project_id: str, prefix: str) -> int:
    """The highest running number already used with this letter in the project."""
    codes = session.scalars(select(Participant.code).where(Participant.project_id == project_id, Participant.code.like(prefix + "-%"))).all()
    return max([int(c[2:]) for c in codes if c[2:].isdigit()] or [0])


def give_codes(session: Session, people: list[Participant]) -> None:
    """Give each person in `people` who has no number the next one for their project and source letter, in the order of the list."""
    top: dict[tuple[str, str], int] = {}
    for p in people:
        if p.code:
            continue
        key = (p.project_id, prefix_of(p.source))
        if key not in top:
            top[key] = _top(session, *key)
        top[key] += 1
        p.code = f"{key[1]}-{top[key]:03d}"


@event.listens_for(Session, "before_flush")
def _on_flush(session: Session, _ctx, _instances) -> None:
    """Every new participant leaves the session with a number, whichever code path added them."""
    give_codes(session, [o for o in session.new if isinstance(o, Participant)])


def backfill(db: Session) -> int:
    """Number the participants that existed before numbers were stored, in the order they were added. Returns how many were numbered."""
    todo = db.scalars(select(Participant).where(Participant.code == "").order_by(Participant.created_at, Participant.id)).all()
    if todo:
        give_codes(db, todo)
        db.commit()
        log.info("numbered %d existing participants", len(todo))
    return len(todo)


def code_map(db: Session, project_id: str) -> dict[str, str]:
    """{participant id: number} for a project."""
    return {pid: code or "?" for pid, code in db.execute(select(Participant.id, Participant.code).where(Participant.project_id == project_id))}
