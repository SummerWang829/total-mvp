"""Pre-interview task: the questions a participant answers after consent, before the waiting room.

Opt-in per interview: no ``PreTask`` row means no pre-task step, so the journey is unchanged. Questions are stored as
a JSON list in the interview's language. We assign the keys (``q1``, ``q2``, …) so answers stay matched to a question
by position even if the researcher edits a prompt. Answers are personal data and are purged with the interview
(``app/retention.py``).
"""
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import PreTask, PreTaskAnswer

MAX_QUESTIONS = 10
MAX_PROMPT = 500
MAX_ANSWER = 4000


def _questions(pt: PreTask) -> list[dict]:
    """The stored question list: ``[{"key", "prompt", "required"}, …]``."""
    return json.loads(pt.questions_json)


def get(db: Session, interview_id: str) -> PreTask | None:
    """The pre-task for this interview, or None when none is configured."""
    return db.get(PreTask, interview_id)


def set_questions(db: Session, org_id: str, interview_id: str, questions: list[dict]) -> PreTask | None:
    """Replace this interview's pre-task questions, keeping the row consistent with the answers.

    An empty list (or one with only blank prompts) removes the pre-task and its answers (opts the interview out).
    Answers whose question disappears are deleted so the moderator never sees an answer with no question.

    Args:
        questions: items with ``prompt`` (str) and optional ``required`` (bool, default True); keys are assigned here.

    Returns:
        The stored ``PreTask`` row, or None when the interview was opted out.

    Raises:
        ValueError: a prompt is longer than ``MAX_PROMPT`` or there are more than ``MAX_QUESTIONS`` questions.
    """
    cleaned: list[dict] = []
    for q in questions:
        prompt = (q.get("prompt") or "").strip()
        if not prompt:
            continue
        if len(prompt) > MAX_PROMPT:
            raise ValueError("pre-task question is too long")
        cleaned.append({"key": f"q{len(cleaned) + 1}", "prompt": prompt, "required": bool(q.get("required", True))})
    if len(cleaned) > MAX_QUESTIONS:
        raise ValueError("too many pre-task questions")
    pt = db.get(PreTask, interview_id)
    if not cleaned:
        if pt:
            db.execute(delete(PreTaskAnswer).where(PreTaskAnswer.interview_id == interview_id))
            db.delete(pt)
        return None
    payload = json.dumps(cleaned, ensure_ascii=False)
    if pt:
        pt.questions_json = payload
    else:
        pt = PreTask(interview_id=interview_id, org_id=org_id, questions_json=payload)
        db.add(pt)
    keys = [q["key"] for q in cleaned]
    db.execute(delete(PreTaskAnswer).where(PreTaskAnswer.interview_id == interview_id, PreTaskAnswer.question_key.notin_(keys)))
    return pt


def _answers(db: Session, interview_id: str, participant_id: str) -> dict[str, str]:
    rows = db.scalars(select(PreTaskAnswer).where(
        PreTaskAnswer.interview_id == interview_id, PreTaskAnswer.participant_id == participant_id))
    return {a.question_key: a.text for a in rows}


def participant_view(db: Session, interview_id: str, participant_id: str) -> dict | None:
    """What the participant's browser gets: the questions (never other people's answers) and whether they are done.

    Returns None when no pre-task is configured, so the journey shows no extra step.
    """
    pt = db.get(PreTask, interview_id)
    if not pt:
        return None
    qs = _questions(pt)
    done = _answers(db, interview_id, participant_id)
    return {"questions": [{"key": q["key"], "prompt": q["prompt"], "required": q["required"]} for q in qs],
            "done": all(done.get(q["key"], "").strip() for q in qs if q["required"])}


def staff_view(db: Session, interview_id: str, participant_id: str) -> dict:
    """The research team's view: the questions with this participant's answer filled in (personal data)."""
    pt = db.get(PreTask, interview_id)
    if not pt:
        return {"questions": []}
    done = _answers(db, interview_id, participant_id)
    return {"questions": [{**q, "answer": done.get(q["key"], "")} for q in _questions(pt)]}


def save_answers(db: Session, pt: PreTask, participant_id: str, answers: dict[str, str]) -> None:
    """Upsert one participant's answers.

    Args:
        answers: ``{question_key: text}``; unknown keys are ignored, missing optional answers stay blank.

    Raises:
        ValueError: a required question has no answer, or an answer is longer than ``MAX_ANSWER``.
    """
    qs = _questions(pt)
    for q in qs:
        text = (answers.get(q["key"]) or "").strip()
        if q["required"] and not text:
            raise ValueError("a required pre-task question was left blank")
        if len(text) > MAX_ANSWER:
            raise ValueError("pre-task answer is too long")
    existing = {a.question_key: a for a in db.scalars(select(PreTaskAnswer).where(
        PreTaskAnswer.interview_id == pt.interview_id, PreTaskAnswer.participant_id == participant_id))}
    for q in qs:
        text = (answers.get(q["key"]) or "").strip()
        if q["key"] in existing:
            existing[q["key"]].text = text
        elif text:
            db.add(PreTaskAnswer(org_id=pt.org_id, interview_id=pt.interview_id, participant_id=participant_id,
                                 question_key=q["key"], text=text))


def answered(db: Session, interview_id: str, participant_id: str) -> bool:
    """True when there is no pre-task, or every required question is answered (used to gate entry)."""
    pt = db.get(PreTask, interview_id)
    if not pt:
        return True
    done = _answers(db, interview_id, participant_id)
    return all(done.get(q["key"], "").strip() for q in _questions(pt) if q["required"])
