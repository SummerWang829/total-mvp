"""The columns of the respondent table on the insights page, beyond the fixed ones: one per question of the recruiting questionnaire, and one pair (answer, theme) per
question of the interview outline. A choice question (or labels) gets an option list to filter by; an open question only gets a search box."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import interview_answers, outline, recruit_form
from .models import Interview, Project, RecruitForm, ScreenerAnswer

MAX_CHOICES = 8      # a questionnaire answer with more different values than this is treated as open text
MAX_CHOICE_CHARS = 20
SEP = "、"           # how a multi-choice answer is stored in words (app/recruit_form.py)


def _kind(values: list[str]) -> str:
    distinct = {v for v in values if v}
    return "choice" if distinct and len(distinct) <= MAX_CHOICES and all(len(v) <= MAX_CHOICE_CHARS for v in distinct) else "text"


def _form_types(db: Session, project_id: str) -> dict[str, tuple[str, list[str], bool]]:
    """{question text: (kind, options, multi)} from the project's recruiting questionnaire, where there is one."""
    form = db.get(RecruitForm, project_id)
    out = {}
    for q in recruit_form.load(form.questions if form else []):
        choice = q.type in ("single", "multi")
        out[q.text] = ("choice" if choice else "text", [o.text for o in q.options] if choice else [], q.type == "multi")
    return out


def screening(db: Session, project_id: str, person_ids: list[str]) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """(columns for the questionnaire questions, {participant id: {column id: answer}}). Columns are in questionnaire order.
    The questionnaire says which questions are choices (and their options); answers that came another way (by hand, an outside list) are judged by their values."""
    known = _form_types(db, project_id)
    rows = db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id.in_(person_ids)).order_by(ScreenerAnswer.position)).all() if person_ids else []
    order: list[str] = []
    values: dict[str, list[str]] = {}
    by_person: dict[str, dict[str, str]] = {}
    for r in rows:
        if r.question not in values:
            order.append(r.question)
            values[r.question] = []
        values[r.question].append(r.answer)
        by_person.setdefault(r.participant_id, {})[f"s{order.index(r.question)}"] = r.answer
    cols = []
    for i, q in enumerate(order):
        vals = [x for v in values[q] for x in (v.split(SEP) if SEP in v else [v])]
        if q in known:
            kind, opts, multi = known[q]
        else:
            kind, opts, multi = _kind(vals), [], any(SEP in v for v in values[q])
        if kind == "choice" and not opts:
            opts = sorted({v for v in vals if v})
        cols.append({"id": f"s{i}", "group": "screen", "label": q, "type": kind, "multi": multi, "options": opts if kind == "choice" else []})
    return cols, by_person


def interview_questions(proj: Project) -> list[str]:
    """The questions of the interview outline, in order (what the answer columns are made from)."""
    s = outline.summary_of(proj.outline_text or "")
    return [q["text"] for p in (s["parts"] if s else []) for q in p["questions"] if not q.get("cover")]


def interview_columns(questions: list[str], itvs: list[Interview]) -> tuple[list[dict], dict[str, dict[str, str | list[str]]]]:
    """(an answer column and a theme column per outline question, {interview id: {column id: value}})."""
    stored = {i.id: interview_answers.load(i) for i in itvs}
    cols, cells = [], {i.id: {} for i in itvs}
    for n, q in enumerate(questions):
        themes = sorted({t for d in stored.values() for t in (d.get(q, {}).get("themes") or [])})
        cols.append({"id": f"a{n}", "group": "answer", "label": q, "type": "text", "options": []})
        cols.append({"id": f"t{n}", "group": "answer", "label": f"{q} · 主题", "type": "choice", "multi": True, "options": themes})
        for i in itvs:
            it = stored[i.id].get(q)
            if it:
                cells[i.id][f"a{n}"] = it["a"]
                cells[i.id][f"t{n}"] = it.get("themes") or []
    return cols, cells
