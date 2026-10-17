"""The screening questionnaire's answers, kept with the participant (used by the team and, as background, by the AI moderator).

Pasted text is one question per line: ``question：answer`` (a full-width or ordinary colon, or a tab). Lines without a separator are
rejected with their line number so a mistake is fixed at once instead of silently dropping a question.
"""
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import ScreenerAnswer

MAX_ITEMS = 30
MAX_QUESTION_CHARS = 200
MAX_ANSWER_CHARS = 500
PROFILE_CONTEXT_CHARS = 3000  # what the AI moderator is shown; the newest items are never cut mid-way
_SEPARATOR = re.compile(r"[：:\t]")


class ScreenerError(ValueError):
    """The pasted questionnaire cannot be read (the message says which line and why, in Chinese)."""


def parse_text(text: str) -> list[tuple[str, str]]:
    """Read ``question：answer`` lines.

    Raises:
        ScreenerError: a line without a separator, an empty question or answer, too long, or more than MAX_ITEMS lines.
    """
    items: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = _SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ScreenerError(f"第 {number} 行要写成“题目：答案”")
        question, answer = parts[0].strip(), parts[1].strip()
        if len(question) > MAX_QUESTION_CHARS or len(answer) > MAX_ANSWER_CHARS:
            raise ScreenerError(f"第 {number} 行太长（题目 {MAX_QUESTION_CHARS} 字、答案 {MAX_ANSWER_CHARS} 字以内）")
        items.append((question, answer))
    if len(items) > MAX_ITEMS:
        raise ScreenerError(f"最多 {MAX_ITEMS} 题")
    return items


def save(db: Session, org_id: str, participant_id: str, items: list[tuple[str, str]]) -> int:
    """Replace the participant's answers with these (the caller commits). Returns how many were stored."""
    db.execute(delete(ScreenerAnswer).where(ScreenerAnswer.participant_id == participant_id))
    db.flush()
    for position, (question, answer) in enumerate(items, start=1):
        db.add(ScreenerAnswer(org_id=org_id, participant_id=participant_id, position=position, question=question, answer=answer))
    return len(items)


def items_for(db: Session, participant_id: str) -> list[dict]:
    """The answers in questionnaire order, as the API shows them."""
    rows = db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id == participant_id).order_by(ScreenerAnswer.position)).all()
    return [{"question": r.question, "answer": r.answer} for r in rows]


def as_text(items: list[dict]) -> str:
    """The same lines again, for editing."""
    return "\n".join(f"{i['question']}：{i['answer']}" for i in items)


def profile_lines(db: Session, participant_id: str) -> list[str]:
    """The answers as short lines for the AI moderator's background, cut to PROFILE_CONTEXT_CHARS (later questions dropped first)."""
    lines, used = [], 0
    for item in items_for(db, participant_id):
        line = f"{item['question']} → {item['answer']}"
        if used + len(line) > PROFILE_CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line)
    return lines


def counts(db: Session, participant_ids: list[str]) -> dict[str, int]:
    """How many answers each of these participants has (for the recruiting list)."""
    from sqlalchemy import func

    rows = db.execute(select(ScreenerAnswer.participant_id, func.count()).where(ScreenerAnswer.participant_id.in_(participant_ids))
                      .group_by(ScreenerAnswer.participant_id)).all()
    return {pid: n for pid, n in rows}


def erase(db: Session, participant_id: str) -> None:
    """Delete the participant's answers (they are personal data)."""
    db.execute(delete(ScreenerAnswer).where(ScreenerAnswer.participant_id == participant_id))
