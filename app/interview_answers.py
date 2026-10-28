"""What each person said to each question of the interview outline, for the respondent table on the insights page.

The answer is never written by a model: it is the participant's own words, copied from the transcript, from the moderator's turn that asked the question up to the turn
that asks the next outline question (follow-ups in between belong to the answer). Which turn asked which question is decided by how much their wording overlaps. A model
is only used for the short theme labels beside an answer, and only when one is configured; the labels are not quotes.
"""
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Interview, TranscriptSegment
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MATCH_MIN = 0.16   # how much of the question's wording a moderator turn must share to count as asking it
MAX_ANSWER_CHARS = 900
MAX_THEMES = 3
THEME_CHARS = 12
SYSTEM = """You label what interview participants said. You get a numbered list of answers. For each one reply with 1 to 3 short theme labels (at most 6 words each, in the language {language}, neutral nouns or short phrases such as "price" or "natural ingredients"; the same idea always gets the same label). Never quote the answer. Reply with ONE JSON object: {"themes": [["label", ...], ...]} with one list per answer, in the same order."""
_JUNK = re.compile(r"[\s\W_]+", re.UNICODE)


def _grams(text: str) -> set[str]:
    s = _JUNK.sub("", text.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set(s)


def _score(question: str, said: str) -> float:
    """How much of the question's wording is in what was said (0 to 1)."""
    q, s = _grams(question), _grams(said)
    return len(q & s) / len(q) if q else 0.0


def extract(questions: list[str], segs: list[TranscriptSegment]) -> list[dict]:
    """One item per outline question: {"q", "a" (the participant's words, "" when not asked), "seg" (segment ids), "at_ms"}."""
    asks: dict[int, int] = {}   # outline question index -> position in segs of the moderator turn that asked it
    taken: set[int] = set()
    for qi, q in enumerate(questions):
        best, best_i = 0.0, -1
        for i, s in enumerate(segs):
            if s.speaker != "moderator" or i in taken:
                continue
            sc = _score(q, s.text_orig)
            if sc > best:
                best, best_i = sc, i
        if best >= MATCH_MIN:
            asks[qi] = best_i
            taken.add(best_i)
    order = sorted(asks.values())
    out = []
    for qi, q in enumerate(questions):
        if qi not in asks:
            out.append({"q": q, "a": "", "seg": [], "at_ms": None})
            continue
        start = asks[qi]
        stop = next((o for o in order if o > start), len(segs))
        mine = [s for s in segs[start + 1:stop] if s.speaker != "moderator"]
        text = " ".join(s.text_orig.strip() for s in mine)[:MAX_ANSWER_CHARS]
        out.append({"q": q, "a": text, "seg": [s.id for s in mine], "at_ms": mine[0].start_ms if mine else None})
    return out


def add_themes(chat: ModeratorChatProvider, items: list[dict], language: str) -> None:
    """Put 1 to 3 short labels on every answered item (in place).

    Raises:
        ChatFatalError: the model service refused (bad key, no balance...).
        ChatError: no usable reply; the answers stay without labels.
    """
    idx = [i for i, it in enumerate(items) if it["a"]]
    if not idx:
        return
    numbered = "\n".join(f"{n + 1}. {items[i]['a'][:400]}" for n, i in enumerate(idx))
    reply = chat.complete([{"role": "system", "content": SYSTEM.replace("{language}", language)}, {"role": "user", "content": numbered}], temperature=0.2)
    lists = reply.get("themes") if isinstance(reply, dict) else None
    if not isinstance(lists, list):
        raise ChatError("themes reply has no list")
    for n, i in enumerate(idx):
        got = lists[n] if n < len(lists) and isinstance(lists[n], list) else []
        items[i]["themes"] = [str(x).strip()[:THEME_CHARS * 2] for x in got if str(x).strip()][:MAX_THEMES]


def generate(db: Session, itv: Interview, questions: list[str], chat: ModeratorChatProvider | None, language: str) -> dict:
    """Work out the answers of one interview and store them with it. Returns {"answered": n, "themes": whether labels were added}. The caller commits."""
    segs = db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx)).all()
    items = extract(questions, list(segs))
    themes = False
    if chat is not None:
        try:
            add_themes(chat, items, language)
            themes = True
        except ChatFatalError:
            raise
        except ChatError:
            log.warning("theme labels unavailable for one interview")
    itv.answers_json = json.dumps({"v": 1, "items": items}, ensure_ascii=False)
    return {"answered": sum(1 for it in items if it["a"]), "themes": themes}


def load(itv: Interview) -> dict[str, dict]:
    """The stored items by question text ({} when none were made yet)."""
    try:
        data = json.loads(itv.answers_json) if itv.answers_json else {}
    except ValueError:
        return {}
    return {it["q"]: it for it in data.get("items", []) if isinstance(it, dict) and "q" in it}
