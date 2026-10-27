"""The project's interview outline (the 访谈 page): written in a conversation with the AI, shown beside it, tried out in a preview.

The outline is kept as plain text in the app/guide_text.py format, so a hand edit, a pasted outline and the AI's draft are all the same thing. The conversation with the AI that
changes it lives in app/outline_chat.py.
"""
import logging

from pydantic import ValidationError

from . import guide_text, study_draft
from .moderator.knowledge import InterviewGuide

log = logging.getLogger(__name__)

DEFAULT_MINUTES = 45
LANGS = ("en", "zh")  # the languages the outline writer supports

def settings_of(text: str, fallback_language: str) -> tuple[str, int]:
    """(language, minutes) from the outline's header lines, else the project's language and the default length."""
    lang, minutes = (fallback_language if fallback_language in LANGS else "zh"), DEFAULT_MINUTES
    try:
        g = guide_text.parse_guide_text(text)
        return (g["language"] if g["language"] in LANGS else lang), int(g["duration_min"])
    except (guide_text.GuideTextError, KeyError, ValueError):
        return lang, minutes


def problem_of(text: str) -> str:
    """Empty when the text is a usable outline (or empty); otherwise what is wrong, in words for the researcher."""
    if not text.strip():
        return ""
    try:
        guide_text.parse_guide_text(text)
    except guide_text.GuideTextError as e:
        return str(e)
    return ""


def summary_of(text: str) -> dict | None:
    """The shape of a valid outline for the page: its parts with their questions and minutes. None when it is empty or invalid."""
    if not text.strip():
        return None
    try:
        g = guide_text.parse_guide_text(text)
    except guide_text.GuideTextError:
        return None
    parts = []
    if g.get("warmup_questions"):
        parts.append({"kind": "warmup", "ref": 0, "title": "开场与热身", "questions": [{"text": q} for q in g["warmup_questions"]], "stimulus": False})
    for i, t in enumerate(g.get("topics", [])):
        qs = [{"text": q} for q in t.get("questions", [])] + [{"text": c["text"], "cover": True} for c in t.get("must_cover", [])]
        parts.append({"kind": "topic", "ref": i, "title": t["title"], "questions": qs, "stimulus": False})
    for i, s in enumerate(g.get("stimuli", [])):
        parts.append({"kind": "stimulus", "ref": i, "title": s["description"], "questions": [{"text": q} for q in s["questions"]], "stimulus": True})
    n = sum(1 for p in parts for q in p["questions"] if not q.get("cover"))
    return {"language": g["language"], "minutes": int(g["duration_min"]), "topic": g.get("participant_topic", ""), "parts": parts, "question_count": n}


def warning_of(text: str) -> str:
    """Empty when every prepared question passes the moderator's red lines; otherwise the engine's own words about the one that does not (shown as a hint, the text is kept)."""
    if not text.strip():
        return ""
    try:
        return study_draft._red_line_problem(InterviewGuide.model_validate(guide_text.parse_guide_text(text)))  # noqa: SLF001
    except (guide_text.GuideTextError, ValidationError):
        return ""


class EditError(ValueError):
    """The edit cannot be made; the message is written for the researcher."""


def edit(text: str, op: str, kind: str, ref: int, index: int, value: str) -> str:
    """Change one question or part of the outline and return the new text.

    Args:
        text: the current outline text (must be valid).
        op: set | delete | add | rename | del_part | add_part.
        kind: warmup | topic | stimulus (which list the part is in); ref: its position in that list; index: the question's position in the part.
        value: the new question text (set / add / add_part) or the new title (rename / add_part).

    Raises:
        EditError: the text is not valid yet, the position does not exist, a question is empty, or the result is not a valid outline.
    """
    try:
        g = guide_text.parse_guide_text(text)
    except guide_text.GuideTextError as e:
        raise EditError("先把文字里的问题改好，再用这里的按钮：" + str(e)) from e
    value = value.strip()
    try:
        if kind == "warmup":
            qs = g["warmup_questions"]
        else:
            lst = g["topics"] if kind == "topic" else g.get("stimuli", [])
            qs = lst[ref]["questions"] if op != "add_part" and 0 <= ref < len(lst) else None
            if qs is None and op not in ("add_part",):
                raise IndexError
        if op == "set" or op == "add":
            if not value:
                raise EditError("问题不能是空的")
            if op == "set":
                qs[index] = value
            else:
                qs.append(value)
        elif op == "delete":
            qs.pop(index)
            if not qs and kind != "warmup":
                (g["topics"] if kind == "topic" else g["stimuli"]).pop(ref)  # a part with no question is dropped
        elif op == "rename":
            if kind != "topic" or not value:
                raise EditError("只能给话题改名，名字不能为空")
            g["topics"][ref]["title"] = value[:120]
        elif op == "del_part":
            if kind == "warmup":
                g["warmup_questions"] = []
            else:
                (g["topics"] if kind == "topic" else g["stimuli"]).pop(ref)
        elif op == "add_part":
            title, _, question = value.partition("|")
            if not title.strip() or not question.strip():
                raise EditError("新话题要有名字和第一个问题")
            g["topics"].append({"id": f"t{len(g['topics']) + 1}", "title": title.strip()[:120], "questions": [question.strip()], "must_cover": []})
        else:
            raise EditError("不认识这个操作")
    except (IndexError, KeyError) as e:
        raise EditError("这一条已经不存在了，刷新后再试") from e
    new = guide_text.guide_to_text(g)
    try:
        guide_text.parse_guide_text(new)
    except guide_text.GuideTextError as e:
        raise EditError(str(e)) from e
    return new
