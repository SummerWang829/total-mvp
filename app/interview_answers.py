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

from . import ai_prompts
from .models import Interview, TranscriptSegment
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MATCH_MIN = 0.16   # how much of the question's wording a moderator turn must share to count as asking it
MAX_ANSWER_CHARS = 900
MAX_THEMES = 3
MAX_TURNS = 120          # moderator turns sent to the model when it has to match them to questions
MAX_FOLLOWUPS = 6
MAX_FOLLOW_CHARS = 400
THEME_CHARS = 12
SYSTEM = """You label what interview participants said. You get a numbered list of answers. For each one reply with 1 to 3 short theme labels (at most 6 words each, in the language {language}, neutral nouns or short phrases such as "price" or "natural ingredients"; the same idea always gets the same label). Never quote the answer. Reply with ONE JSON object: {"themes": [["label", ...], ...]} with one list per answer, in the same order."""
MATCH_SYSTEM = """You match interview questions to the moderator turns that asked them. You get outline questions (numbered) and moderator turns (numbered); the interview may be in another language than the question, and the moderator may have rephrased it. For each question that was really asked, give the number of the turn that asked it; leave it out when none did. A turn can match only one question. Reply with ONE JSON object: {"matches": {"<question number>": <turn number>}}."""
_JUNK = re.compile(r"[\s\W_]+", re.UNICODE)


def _grams(text: str) -> set[str]:
    s = _JUNK.sub("", text.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set(s)


def _score(question: str, said: str) -> float:
    """How much of the question's wording is in what was said (0 to 1)."""
    q, s = _grams(question), _grams(said)
    return len(q & s) / len(q) if q else 0.0


def rule_match(texts: list[str], segs: list[TranscriptSegment]) -> dict[int, int]:
    """Which moderator turn asked which outline question, by how much their wording overlaps: {question index: position in segs}."""
    asks: dict[int, int] = {}
    taken: set[int] = set()
    for qi, q in enumerate(texts):
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
    return asks


def model_match(chat: ModeratorChatProvider, texts: list[str], segs: list[TranscriptSegment], asks: dict[int, int]) -> dict[int, int]:
    """For the questions the wording did not find (another language, a rephrased question), ask the model which moderator turn asked them.
    Only the moderator's own words and the outline questions are sent, never what the participant said.

    Raises:
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    missing = [qi for qi in range(len(texts)) if qi not in asks]
    free = [i for i, s in enumerate(segs) if s.speaker == "moderator" and i not in asks.values()]
    if not missing or not free:
        return {}
    payload = {"questions": {str(qi + 1): texts[qi][:300] for qi in missing}, "moderator_turns": {str(i + 1): segs[i].text_orig[:240] for i in free[:MAX_TURNS]}}
    try:
        reply = chat.complete([{"role": "system", "content": ai_prompts.get("answers.match", MATCH_SYSTEM)}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], temperature=0.0)
    except ChatFatalError:
        raise
    except ChatError:
        log.warning("outline question matching: no usable reply from the model")
        return {}
    got = reply.get("matches") if isinstance(reply, dict) else None
    out: dict[int, int] = {}
    for k, v in (got.items() if isinstance(got, dict) else []):
        try:
            qi, ti = int(k) - 1, int(v) - 1
        except (TypeError, ValueError):
            continue
        if qi in missing and ti in free and qi not in out and ti not in out.values():
            out[qi] = ti
    return out


def _turns(block: list[TranscriptSegment]) -> list[dict]:
    """The segments in order, consecutive ones of the same side merged into one turn: {"mod", "text", "seg", "at_ms"}."""
    turns: list[dict] = []
    for s in block:
        mod = s.speaker == "moderator"
        if turns and turns[-1]["mod"] == mod:
            turns[-1]["text"] += " " + s.text_orig.strip()
            turns[-1]["seg"].append(s.id)
        else:
            turns.append({"mod": mod, "text": s.text_orig.strip(), "seg": [s.id], "at_ms": s.start_ms})
    return turns


def _thread(block: list[TranscriptSegment]) -> tuple[str, list[dict]]:
    """(the main answer, the follow-ups): the first thing the participant said, then each later moderator question with what the participant answered to it."""
    main, ask, followups = "", None, []
    for t in _turns(block):
        if t["mod"]:
            ask = t if main else None
        elif not main:
            main = t["text"]
        elif ask:
            followups.append({"ask": ask["text"][:MAX_FOLLOW_CHARS], "a": t["text"][:MAX_FOLLOW_CHARS], "at_ms": t["at_ms"]})
            ask = None
        elif followups:
            followups[-1]["a"] = (followups[-1]["a"] + " " + t["text"])[:MAX_FOLLOW_CHARS]
    return main[:MAX_ANSWER_CHARS], followups[:MAX_FOLLOWUPS]


def extract(questions: list[str], segs: list[TranscriptSegment], asks: dict[int, int] | None = None, texts: list[str] | None = None) -> list[dict]:
    """One item per outline question: {"q", "a" (everything the participant said to it, follow-ups included), "main" (the first answer), "followups", "asked"
    (a moderator turn was found for it), "seg" (segment ids), "at_ms"}. `texts` are the questions in the wording of the interview's own language; `asks` is a
    ready-made matching (when the model helped)."""
    asks = rule_match(texts or questions, segs) if asks is None else asks
    order = sorted(asks.values())
    out = []
    for qi, q in enumerate(questions):
        if qi not in asks:
            out.append({"q": q, "a": "", "main": "", "followups": [], "asked": False, "seg": [], "at_ms": None})
            continue
        start = asks[qi]
        stop = next((o for o in order if o > start), len(segs))
        block = segs[start + 1:stop]
        mine = [s for s in block if s.speaker != "moderator"]
        main, followups = _thread(block)
        out.append({"q": q, "a": " ".join(s.text_orig.strip() for s in mine)[:MAX_ANSWER_CHARS], "main": main, "followups": followups, "asked": True,
                    "seg": [s.id for s in mine], "at_ms": mine[0].start_ms if mine else None})
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
    reply = chat.complete([{"role": "system", "content": ai_prompts.get("answers.tag", SYSTEM).replace("{language}", language)}, {"role": "user", "content": numbered}], temperature=0.2)
    lists = reply.get("themes") if isinstance(reply, dict) else None
    if not isinstance(lists, list):
        raise ChatError("themes reply has no list")
    for n, i in enumerate(idx):
        got = lists[n] if n < len(lists) and isinstance(lists[n], list) else []
        items[i]["themes"] = [str(x).strip()[:THEME_CHARS * 2] for x in got if str(x).strip()][:MAX_THEMES]


def generate(db: Session, itv: Interview, questions: list[str], chat: ModeratorChatProvider | None, language: str, texts: list[str] | None = None) -> dict:
    """Work out the answers of one interview and store them with it. Returns {"answered": n, "themes": whether labels were added}. The caller commits.
    `texts` are the outline questions in the interview's own language (they find the moderator's turns better than the main version does)."""
    segs = list(db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx)).all())
    asks = rule_match(texts or questions, segs)
    matched_by_model = 0
    if chat is not None:
        found = model_match(chat, texts or questions, segs, asks)
        asks.update(found)
        matched_by_model = len(found)
    items = extract(questions, segs, asks)
    themes = False
    if chat is not None:
        try:
            add_themes(chat, items, language)
            themes = True
        except ChatFatalError:
            raise
        except ChatError:
            log.warning("theme labels unavailable for one interview")
    itv.answers_json = json.dumps({"v": 2, "items": items}, ensure_ascii=False)
    return {"answered": sum(1 for it in items if it["a"]), "themes": themes, "by_model": matched_by_model}


def load(itv: Interview) -> dict[str, dict]:
    """The stored items by question text ({} when none were made yet)."""
    try:
        data = json.loads(itv.answers_json) if itv.answers_json else {}
    except ValueError:
        return {}
    return {it["q"]: it for it in data.get("items", []) if isinstance(it, dict) and "q" in it}
