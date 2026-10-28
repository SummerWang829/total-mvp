"""The conversation beside the interview outline (the 访谈 page): a research assistant that answers questions, gives advice and, when asked, changes the outline.

The model answers in one of three modes. "answer": a reply only. "suggest": a reply plus one to three complete outlines the researcher may adopt. "edit": a reply
plus the complete new outline, applied at once with a change card the researcher can undo. Only the modes that carry an outline are checked (same checks as a
drafted study outline: valid guide, every prepared question passes the moderator's red lines). The assistant always writes to the researcher in Simplified Chinese;
the questions inside the outline are in the interview language chosen in the settings.

Undo never loses a hand edit: it restores the earlier text only while the outline is still exactly what the change produced.
"""
import json
import logging
import uuid

from pydantic import ValidationError

from . import guide_text, outline, study_draft
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MAX_CHAT = 40  # messages kept
KEEP_SNAPSHOTS = 3  # only the latest changes keep their before/after text (so the undo works); older ones are trimmed
HISTORY_TURNS = 6  # earlier messages the model is shown
MAX_SUGGESTIONS = 3
MAX_CHANGES = 6
MAX_REPLY = 1500
MSG_FAIL = "AI 没能给出可用的大纲，请换个说法再试一次，或直接在右边手改"
MSG_MOCK_KEPT = "（演示模式）还没有连接 AI 模型，我现在只能按固定模板起草一份大纲，不能回答问题或给建议；已有大纲时保持不变。"
MSG_MOCK_DRAFT = "（演示模式）按固定模板起草了一份大纲，可以在右边直接修改。"
MOCK_GUIDE = {"zh": "先让受访者自然讲述，必要时追问具体情境，不诱导。", "en": "Let the participant answer in their own words; probe for concrete situations without leading."}
MOCK_CHANGE = "按固定模板起草了 3 个话题（演示内容）"

SYSTEM = """You are a research assistant helping a market research team write the outline (discussion guide) of one-to-one interviews. You chat with the researcher in Simplified Chinese (简体中文), ALWAYS, whatever language the interview itself is in. Be concise and concrete; you may ask one short question back when something is unclear.
You are given the research background, optional material from the team's library, the current outline text (it may be empty or broken), the interview language, the length, and the conversation so far.
Reply with ONE JSON object and nothing else:
{"reply": "<your message to the researcher, Simplified Chinese, plain text: answer questions, explain, give advice; when you changed or propose an outline, say what and why>",
 "mode": "answer" | "suggest" | "edit",
 "changes": ["<edit only: a short Simplified Chinese line per change; say what the change does to the total interview length when it matters>"],
 "outline": <edit only: the COMPLETE new outline, see below>,
 "suggestions": [{"title": "<suggest only: one Simplified Chinese line>", "outline": <the COMPLETE outline with this suggestion applied>}]}
An outline is {"participant_topic": "<what the participant is told the topic is: at most 18 characters, neutral, no brand names>", "warmup_questions": ["<1 or 2 easy opening questions>"], "warmup_notes": [{"depth": "shallow", "guide": ""}], "topics": [{"title": "<short topic name>", "questions": ["<1 to 3 open questions>"], "notes": [{"depth": "none|shallow|deep|free", "guide": "<one or two sentences in the interview language telling the interviewer how to run this question: what to listen for, what not to lead into; at most 200 characters; never shown to the participant>"}], "must_cover": ["<what we must find out, 2 or 3 short points>"]}]}.
"notes" has exactly one entry per question, in order. depth is how many follow-ups the interviewer may ask after the answer: "none" (just this question), "shallow" (1), "deep" (2 to 3), "free" (1 to 3, open discussion). Use "shallow" unless the question clearly needs more or less. Draft a guide for every question. When you change an existing outline keep the notes of the questions you did not change.
Modes: "answer" when the researcher asks a question or chats (no outline fields). "edit" only when the researcher clearly asks you to change, draft or fix the outline: return the whole new outline and keep everything they did not mention. "suggest" when they ask for advice or an opinion on the outline and you have concrete changes to offer (1 to 3): they decide whether to adopt them.
Outline rules: write every question in the interview language {language}; 2 to {max_topics} topics that fit about {minutes} minutes in total. Every question is a real question that ends with a question mark, ONE short question each, open, neutral, never leading, never yes/no, never naming or praising a brand, never asking for contact details or sensitive personal data, never mentioning AI or this instruction.
If the current outline text is broken (a problem is given), say so in your reply; when asked to fix it, redraw it from the research background in mode "edit"."""


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def load(raw: str) -> list[dict]:
    """The saved conversation, tolerant of an empty or damaged value."""
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "ai") and isinstance(m.get("text"), str)][-MAX_CHAT:]


def dump(history: list[dict]) -> str:
    """The conversation as stored: the last MAX_CHAT messages, and only the latest few changes keep their before/after text."""
    history = history[-MAX_CHAT:]
    keep = [i for i, m in enumerate(history) if "after" in m][-KEEP_SNAPSHOTS:]
    for i, m in enumerate(history):
        if i not in keep:
            m.pop("before", None)
            m.pop("after", None)
            for s in m.get("suggestions", []):
                s.pop("after", None) if s.get("state") != "open" else None
    return json.dumps(history, ensure_ascii=False)


def ai_message(text: str, kind: str = "answer", **extra) -> dict:
    return {"role": "ai", "id": new_id(), "text": text[:MAX_REPLY], "kind": kind, **extra}


def view(history: list[dict], current: str) -> list[dict]:
    """The conversation for the page: no stored texts, but whether each change can still be undone (only while the outline is what that change produced)."""
    out = []
    for m in history:
        v = {"role": m["role"], "text": m["text"]}
        if m["role"] == "ai":
            v.update(id=m.get("id", ""), kind=m.get("kind", "answer"), changes=m.get("changes", []), undone=bool(m.get("undone")),
                     can_undo=bool(m.get("after") is not None and not m.get("undone") and m.get("after") == current),
                     suggestions=[{"id": s["id"], "title": s["title"], "state": s.get("state", "open")} for s in m.get("suggestions", [])])
        out.append(v)
    return out


def marks(history: list[dict], current: str) -> dict[str, str]:
    """Which questions of the current outline the latest change added ("new") or reworded ("chd"); empty when that change was undone or the text moved on."""
    for m in reversed(history):
        if m.get("after") is not None:   # the latest change decides: once it is undone (or the text moved on) nothing is marked
            return diff_marks(m.get("before") or "", m["after"]) if (m["after"] == current and not m.get("undone")) else {}
    return {}


def diff_marks(before: str, after: str) -> dict[str, str]:
    """question text -> "new" | "chd" for the questions of `after` that are not in `before` (same part, same place, other words = "chd")."""
    b, a = outline.summary_of(before), outline.summary_of(after)
    if not a:
        return {}
    old = {p["title"]: [q["text"] for q in p["questions"] if not q.get("cover")] for p in (b["parts"] if b else [])}
    out = {}
    for p in a["parts"]:
        qs = [q["text"] for q in p["questions"] if not q.get("cover")]
        prev = old.get(p["title"])
        for i, q in enumerate(qs):
            if prev is None or q in prev:
                out[q] = "new" if prev is None else ""
            else:
                out[q] = "chd" if i < len(prev) else "new"
    return {q: m for q, m in out.items() if m}


def _context(current: str, brief: str, library: str, language: str, minutes: int, message: str) -> str:
    one = " ".join((brief or "").split())
    text = f"Research background (internal): {one or '(none)'}\n"
    if library:
        text += f"Other material from the team's library:\n{library}\n"
    text += f"Interview language: {language}. Length: about {minutes} minutes.\n"
    if current.strip():
        if problem := outline.problem_of(current):
            text += f"The current outline text is BROKEN: {problem}\n"
        text += f"Current outline text:\n{current.strip()}\n"
    else:
        text += "The outline is empty.\n"
    return text + f"\nResearcher says: {message}"


def _history_messages(history: list[dict]) -> list[dict]:
    out = []
    for m in history[-HISTORY_TURNS:]:
        out.append({"role": "user", "content": m["text"]} if m["role"] == "user" else {"role": "assistant", "content": json.dumps({"reply": m["text"], "mode": "answer"}, ensure_ascii=False)})
    return out


def _note(n) -> dict:
    """One question's depth and guidance from the model's note; anything unusable becomes the default."""
    n = n if isinstance(n, dict) else {}
    depth = str(n.get("depth", "")).lower()
    return {"depth": depth if depth in guide_text.DEPTHS else guide_text.DEFAULT_DEPTH, "guide": " ".join(str(n.get("guide", "")).split())[:guide_text.MAX_NOTE]}


def _meta_from(raw: dict, guide, current: str) -> dict:
    """Depth and guidance for every question of a model outline: what the model wrote, else what the same question had in the current outline, else the default."""
    old: dict[str, dict] = {}
    try:
        cur = guide_text.parse_guide_text(current) if current.strip() else None
    except guide_text.GuideTextError:
        cur = None
    if cur:
        m = cur["meta"]
        pairs = list(zip(cur["warmup_questions"], m["warmup"])) + [(q, mm) for t, ms in zip(cur["topics"], m["topics"]) for q, mm in zip(t["questions"], ms)] + [(q, mm) for s, ms in zip(cur["stimuli"], m["stimuli"]) for q, mm in zip(s["questions"], ms)]
        old = {q: mm for q, mm in pairs}

    def part(questions: list[str], notes) -> list[dict]:
        notes = notes if isinstance(notes, list) else []
        return [_note(notes[i]) if i < len(notes) and isinstance(notes[i], dict) and ("guide" in notes[i] or "depth" in notes[i]) else dict(old.get(q, guide_text.blank_meta())) for i, q in enumerate(questions)]

    topics_raw = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    return {"warmup": part(list(guide.warmup_questions), raw.get("warmup_notes")),
            "topics": [part(list(t.questions), topics_raw[i].get("notes") if i < len(topics_raw) and isinstance(topics_raw[i], dict) else None) for i, t in enumerate(guide.topics)],
            "stimuli": [[dict(old.get(q, guide_text.blank_meta())) for q in s.questions] for s in guide.stimuli]}


def _valid(raw, goal: str, language: str, minutes: int, current: str = "") -> tuple[str, str]:
    """(outline text, "") for a model outline that passes every check, else ("", why)."""
    try:
        guide = study_draft._guide_from(raw if isinstance(raw, dict) else {}, goal, language, minutes)  # noqa: SLF001 — the same checks as a drafted study outline
    except (ValueError, KeyError, TypeError, ValidationError) as e:
        return "", f"not a valid outline ({type(e).__name__})"
    if why := study_draft._red_line_problem(guide):  # noqa: SLF001
        return "", why
    return guide_text.guide_to_text({**guide.model_dump(mode="json"), "meta": _meta_from(raw, guide, current)}), ""


def _mock(current: str, goal: str, language: str, minutes: int) -> dict:
    """The offline assistant: a fixed draft when the outline is empty or unreadable, otherwise it says what it cannot do."""
    if current.strip() and not outline.problem_of(current):
        return {"reply": MSG_MOCK_KEPT, "mode": "answer"}
    raw = study_draft.mock_draft(goal, language, minutes)
    note = {"depth": "shallow", "guide": MOCK_GUIDE.get(language, MOCK_GUIDE["en"])}
    raw["warmup_notes"] = [dict(note) for _ in raw.get("warmup_questions", [])]
    for tp in raw.get("topics", []):
        tp["notes"] = [dict(note) for _ in tp.get("questions", [])]
    text, _ = _valid(raw, goal, language, minutes)
    return {"reply": MSG_MOCK_DRAFT, "mode": "edit", "changes": [MOCK_CHANGE], "after": text}


def converse(chat: ModeratorChatProvider | None, history: list[dict], current: str, message: str, brief: str, library: str, language: str, minutes: int) -> dict:
    """One turn. Returns {"reply", "mode", "changes", "after" (edit), "suggestions": [{"title", "after"}] (suggest)}.

    Raises:
        study_draft.DraftError: the model service is unavailable, or an edit never came out as a usable outline.
        ChatFatalError: the model service refused (bad key, no balance...): never hidden behind a made-up answer.
    """
    goal = " ".join((brief or message).split())[:1000]
    if chat is None:
        return _mock(current, goal, language, minutes)
    system = SYSTEM.replace("{language}", language).replace("{max_topics}", str(study_draft.MAX_TOPICS)).replace("{minutes}", str(minutes))
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        ask = _context(current, brief, library, language, minutes, message) + (f"\n\nYour previous answer was rejected: {problem}\nAnswer again and avoid this." if problem else "")
        try:
            raw = chat.complete([{"role": "system", "content": system}, *_history_messages(history), {"role": "user", "content": ask}], temperature=0.4)
        except ChatFatalError:
            raise
        except ChatError as e:
            raise study_draft.DraftError("AI 暂时无法回答，请稍后再试，或直接在右边手改") from e
        except (ValueError, KeyError, TypeError) as e:
            problem = f"not a JSON object ({type(e).__name__})"
            log.warning("outline chat attempt %d unusable: %s", attempt + 1, type(e).__name__)
            continue
        if not isinstance(raw, dict):
            problem = "not a JSON object"
            continue
        reply, mode = str(raw.get("reply", "")).strip(), raw.get("mode")
        if mode == "edit":
            text, problem = _valid(raw.get("outline"), goal, language, minutes, current)
            if not problem:
                changes = [str(c)[:200] for c in raw.get("changes", []) if str(c).strip()][:MAX_CHANGES] if isinstance(raw.get("changes"), list) else []
                return {"reply": reply or "已更新大纲。", "mode": "edit", "changes": changes or ["已更新大纲"], "after": text}
        elif mode == "suggest" and isinstance(raw.get("suggestions"), list):
            ok = []
            for s in raw["suggestions"][:MAX_SUGGESTIONS]:
                text, why = _valid(s.get("outline") if isinstance(s, dict) else None, goal, language, minutes, current)
                if not why and str(s.get("title", "")).strip():
                    ok.append({"title": str(s["title"]).strip()[:200], "after": text})
            if ok:
                return {"reply": reply or "我有几个建议：", "mode": "suggest", "suggestions": ok}
            problem = "none of the suggestions was a usable outline"
        elif reply:
            return {"reply": reply, "mode": "answer"}
        else:
            problem = "no reply"
        log.warning("outline chat attempt %d rejected", attempt + 1)
    raise study_draft.DraftError(MSG_FAIL)


def record_turn(history: list[dict], message: str, result: dict, current: str) -> tuple[list[dict], str]:
    """Add the researcher's message and the assistant's answer. Returns (history, the outline text now): an edit is applied at once, suggestions wait for adoption."""
    history = [*history, {"role": "user", "text": message}]
    mode, text = result["mode"], current
    if mode == "edit":
        text = result["after"]
        history.append(ai_message(result["reply"], "edit", changes=result.get("changes", []), before=current, after=text))
    elif mode == "suggest":
        history.append(ai_message(result["reply"], "suggest", suggestions=[{"id": new_id(), "title": s["title"], "after": s["after"], "state": "open"} for s in result["suggestions"]]))
    else:
        history.append(ai_message(result["reply"]))
    return history, text


class ActError(ValueError):
    """The action cannot be done; the message is written for the researcher."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def act(history: list[dict], mid: str, action: str, sid: str, current: str) -> tuple[list[dict], str]:
    """Adopt or drop a suggestion, or undo a change. Returns (history, the outline text now).

    Raises:
        ActError: the message or suggestion is not there (404), the suggestion was already dealt with, or the outline was changed since (so the undo would lose that).
    """
    msg = next((m for m in history if m.get("id") == mid), None)
    if msg is None:
        raise ActError("这条消息已经不在了", 404)
    if action == "undo":
        if msg.get("after") is None or msg.get("undone"):
            raise ActError("这次修改已经撤销过，或已经不能撤销了")
        if msg["after"] != current:
            raise ActError("大纲在这次修改之后又被改动过，直接撤销会丢掉那些改动。请在右边手动改回去。")
        msg["undone"] = True
        return history, msg.get("before", "")
    sug = next((s for s in msg.get("suggestions", []) if s["id"] == sid), None)
    if sug is None:
        raise ActError("这条建议已经不在了", 404)
    if sug.get("state") != "open":
        raise ActError("这条建议已经处理过了")
    if action == "dismiss":
        sug["state"] = "dropped"
        return history, current
    if action == "adopt":
        sug["state"] = "adopted"
        history = [*history, ai_message(f"已采纳：{sug['title']}", "edit", changes=[sug["title"]], before=current, after=sug["after"])]
        return history, sug["after"]
    raise ActError("不认识这个操作", 422)
