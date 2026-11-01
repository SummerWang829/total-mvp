"""Translating (or rewriting) the words of an interview outline while keeping its shape.

Only the words a participant is asked or told are translated (the participant topic, part titles, questions, must-cover points, material descriptions); ids, the research goal and the
length stay. The model gets a list and must give back a list of the same length, so the shape of the outline cannot change. Offline (no model) the words get a language mark.
"""
import logging

from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider
from .project_langs import NAMES

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
MAX_CHARS = 300
SYSTEM = """You translate parts of an interview outline (discussion guide) for a market research study into {language}. You get a JSON list of strings. Reply with ONE JSON object and nothing else:
{"items": ["<translation of string 1>", "<translation of string 2>", ...]}
Rules: exactly as many items as strings, in the same order; translate every string, keep the meaning and a neutral, polite tone; a question stays ONE short open question ending with a question mark; never name or praise a brand, never add or drop anything, never explain. Use the everyday language a native speaker would say aloud."""
REWRITE = "\nThe interview will be a written text chat, not spoken: rewrite every question as a short, clear chat message (one sentence, no long set-ups), in the same language, keeping its meaning."


class TranslateError(ValueError):
    """The AI could not translate; the message is plain Chinese for the researcher."""


def slots_of(guide: dict, guides: bool = True) -> list[tuple[tuple, str]]:
    """The words to translate, in order, each with the place it came from: [(path, text)]. `guides`: also the interviewer guidance written under questions."""
    out: list[tuple[tuple, str]] = []
    if guide.get("participant_topic"):
        out.append((("participant_topic",), guide["participant_topic"]))
    out += [(("warmup_questions", i), q) for i, q in enumerate(guide.get("warmup_questions", []))]
    for ti, t in enumerate(guide.get("topics", [])):
        out.append((("topics", ti, "title"), t["title"]))
        out += [(("topics", ti, "questions", i), q) for i, q in enumerate(t.get("questions", []))]
        out += [(("topics", ti, "must_cover", i, "text"), m["text"]) for i, m in enumerate(t.get("must_cover", []))]
    for si, s in enumerate(guide.get("stimuli", [])):
        out.append((("stimuli", si, "description"), s["description"]))
        out += [(("stimuli", si, "questions", i), q) for i, q in enumerate(s.get("questions", []))]
    if guides:
        meta = guide.get("meta") or {}
        out += [(("meta", "warmup", i, "guide"), m["guide"]) for i, m in enumerate(meta.get("warmup", [])) if m.get("guide", "").strip()]
        for key in ("topics", "stimuli"):
            out += [(("meta", key, pi, i, "guide"), m["guide"]) for pi, part in enumerate(meta.get(key, [])) for i, m in enumerate(part) if m.get("guide", "").strip()]
    return out


def fill(guide: dict, paths: list[tuple], texts: list[str]) -> None:
    """Write `texts` into `guide` at the given paths (in place)."""
    for path, text in zip(paths, texts, strict=True):
        node = guide
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = text


def mock_translate(strings: list[str], lang: str) -> list[str]:
    """The offline translation used in development and tests: the original with a language mark in front (it says nothing about a model's quality)."""
    return [f"[{lang.upper()}] {s}" for s in strings]


def _check(raw: object, n: int) -> list[str]:
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list) or len(items) != n or any(not str(x).strip() for x in items):
        raise ValueError("wrong number of items")
    return [str(x).strip()[:MAX_CHARS] for x in items]


def translate(chat: ModeratorChatProvider | None, strings: list[str], lang: str, rewrite: bool = False) -> list[str]:
    """The strings in `lang` (rewritten for a text chat when `rewrite`). `chat` None = the offline mock: it marks the language and does not rewrite.

    Raises:
        TranslateError: no usable answer after MAX_ATTEMPTS tries, or the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if not strings:
        return []
    if chat is None:
        return list(strings) if rewrite else mock_translate(strings, lang)
    msgs = [{"role": "system", "content": SYSTEM.replace("{language}", NAMES.get(lang, lang)) + (REWRITE if rewrite else "")}, {"role": "user", "content": str(list(strings))}]
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _check(chat.complete(msgs, temperature=0.2), len(strings))
        except ChatFatalError:
            raise
        except ChatError as e:
            raise TranslateError("小星星助手暂时无法翻译，请稍后再试") from e
        except (ValueError, KeyError, TypeError) as e:
            log.warning("outline translation attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise TranslateError("小星星助手没能给出完整的译文，请再试一次")
