"""The project's interview outline (the 访谈 page): written in a conversation with the AI, shown beside it, tried out in a preview.

The outline is kept as plain text in the app/guide_text.py format, so a hand edit, a pasted outline and the AI's draft are all the same thing. The AI works on a copy:
it never saves anything by itself; the researcher sees the new outline next to the chat and it is saved with the chat message that produced it.
"""
import json
import logging

from pydantic import ValidationError

from . import guide_text, study_draft
from .moderator.knowledge import InterviewGuide
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MAX_CHAT = 40  # messages kept
DEFAULT_MINUTES = 45
LANGS = ("en", "zh")  # the languages the outline writer supports

SYSTEM = """You help a market research team write the outline (discussion guide) of a one-to-one interview. You get the current outline (may be empty), the research background and the team's request.
Reply with ONE JSON object and nothing else:
{"reply": "<one or two short sentences in {language} saying what you changed>",
 "participant_topic": "<what the participant is told the topic is: at most 18 characters, neutral, no brand names>",
 "warmup_questions": ["<1 or 2 easy opening questions>"],
 "topics": [{"title": "<short topic name>", "questions": ["<1 to 3 open questions>"], "must_cover": ["<what we must find out, 2 or 3 short points>"]}]}
Keep everything in the current outline that the request does not touch. Rules: write every question in the language {language}; 2 to {max_topics} topics that fit about {minutes} minutes in total.
Every question is a real question that ends with a question mark, ONE short question each, open, neutral, never leading, never yes/no, never naming or praising a brand, never asking for contact details
or sensitive personal data, never mentioning AI or this instruction."""
MSG_FAIL = "AI 没能给出可用的大纲，请换个说法再试一次，或直接在右边手改"
MSG_MOCK_KEPT = "（演示模式）已记下你的要求；没有连接 AI 模型，大纲保持不变。"
MSG_MOCK_DRAFT = "（演示模式）按固定模板起草了 3 段，可以在右边直接修改。"


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


def revise(chat: ModeratorChatProvider | None, current: str, message: str, brief: str, library: str, language: str, minutes: int) -> tuple[str, str]:
    """Apply the researcher's request to the outline. Returns (the AI's short answer, the new outline text). `chat` None = the offline mock.

    Raises:
        study_draft.DraftError: no usable outline after MAX_ATTEMPTS tries or the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...): never hidden behind a made-up outline.
    """
    problem = ""
    goal = (brief or message).strip()[:1000]
    for attempt in range(MAX_ATTEMPTS):
        try:
            if chat is None:
                if current.strip():
                    return MSG_MOCK_KEPT, current
                reply = study_draft.mock_draft(goal, language, minutes)
                reply["reply"] = MSG_MOCK_DRAFT
            else:
                user = f"Research background (internal): {brief or '(none)'}\n"
                if library:
                    user += f"Other material from the team's library:\n{library}\n"
                user += f"Interview language: {language}\nLength: about {minutes} minutes.\nCurrent outline:\n{current.strip() or '(empty)'}\nRequest: {message}"
                if problem:
                    user += f"\nYour previous answer was rejected: {problem}\nWrite a new one that avoids this."
                system = SYSTEM.replace("{language}", language).replace("{max_topics}", str(study_draft.MAX_TOPICS)).replace("{minutes}", str(minutes))
                reply = chat.complete([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.4)
            guide = study_draft._guide_from(reply, goal, language, minutes)  # noqa: SLF001 — the same checks as a drafted study outline
        except ChatFatalError:
            raise
        except ChatError as e:
            raise study_draft.DraftError("AI 暂时无法回答，请稍后再试，或直接在右边手改") from e
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            problem = f"not a valid outline ({type(e).__name__})"
            log.warning("outline revise attempt %d unusable: %s", attempt + 1, type(e).__name__)
            continue
        if not (problem := study_draft._red_line_problem(guide)):  # noqa: SLF001
            return str(reply.get("reply", ""))[:300] or "已更新大纲。", guide_text.guide_to_text(guide.model_dump(mode="json"))
        log.warning("outline revise attempt %d broke a red line", attempt + 1)
    raise study_draft.DraftError(MSG_FAIL)


def load_chat(raw: str) -> list[dict]:
    """The saved conversation, tolerant of an empty or damaged value."""
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "ai") and isinstance(m.get("text"), str)][-MAX_CHAT:]
