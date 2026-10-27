"""The recruiting questionnaire in more than one language.

Questions, options and the screening logic stay one thing: jumps, tags, quotas and tallies follow the *options* (by id), so every language screens the same way. A translation only
swaps the words a candidate reads. It is made by the AI (marked 机器翻译 until a person confirms it) or typed by hand; a candidate sees a language only when every question has a
translation (machine or confirmed) that still matches the question as it is now: editing the original makes its translations "stale" at once, so a candidate never reads an out-of-date one.
A language the project did not choose (a "仅团队" version, added by the team for its own reading) is never offered to candidates.
"""
import hashlib
import logging
import re

from pydantic import ValidationError

from . import recruit_form
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider
from .project_langs import NAMES

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
CJK = re.compile(r"[一-鿿]")
SYSTEM = """You translate a recruiting questionnaire for a market research study into {language}. Reply with ONE JSON object and nothing else:
{"items": [{"id": "<question id>", "text": "<translated question>", "options": [{"id": "<option id>", "text": "<translated option>"}]}]}
Rules: translate every question and every option you are given, keep each id exactly, keep the meaning and a neutral, polite tone, add nothing, drop nothing, never explain which answer
qualifies or not, keep numbers, units and names as they are. Use the everyday language a native speaker would read in a survey."""


def source_lang(questions: list[recruit_form.Question]) -> str:
    """The language the questionnaire was written in: Chinese when it has Chinese characters, else English."""
    return "zh" if any(CJK.search(q.text) for q in questions) else "en"


def fingerprint(q: recruit_form.Question) -> str:
    """Changes when the question's words change, so a translation made from an older wording is noticed."""
    return hashlib.sha1((q.text + "|" + "|".join(f"{o.id}:{o.text}" for o in q.options)).encode("utf-8")).hexdigest()[:12]


def state_of(q: recruit_form.Question, entry: dict | None) -> str:
    """missing | stale | machine | confirmed."""
    if not entry:
        return "missing"
    if entry.get("fp") != fingerprint(q):
        return "stale"
    return "confirmed" if entry.get("state") == "confirmed" else "machine"


def team_only_languages(translations: dict, project_languages: list[str]) -> list[str]:
    """Languages the team added for itself: they have translations but are not project languages."""
    return [c for c in translations if c in NAMES and c not in project_languages and translations[c]]


def summarize(questions: list[recruit_form.Question], translations: dict, project_languages: list[str]) -> dict:
    """Per language version: how many questions are translated, confirmed or out of date; the source language is the questionnaire itself.

    `ready` = every question has a current translation, which is what lets a project language reach candidates. Team-only versions are listed after the project's, marked `team_only`.
    """
    src, out = source_lang(questions), []
    for code in list(project_languages) + team_only_languages(translations, project_languages):
        team_only = code not in project_languages
        if code == src:
            out.append({"code": code, "name": NAMES[code], "source": True, "team_only": False, "state": "source", "ready": True, "total": len(questions), "confirmed": len(questions), "machine": 0, "stale": 0, "missing": 0})
            continue
        st = [state_of(q, (translations.get(code) or {}).get(q.id)) for q in questions]
        n = {k: st.count(k) for k in ("confirmed", "machine", "stale", "missing")}
        whole = "confirmed" if questions and n["confirmed"] == len(questions) else "stale" if n["stale"] else "machine" if n["machine"] and not n["missing"] else "missing"
        out.append({"code": code, "name": NAMES[code], "source": False, "team_only": team_only, "state": whole, "ready": bool(questions) and not n["missing"] and not n["stale"], "total": len(questions), **n})
    return {"source": src, "languages": out}


def candidate_languages(questions: list[recruit_form.Question], translations: dict, project_languages: list[str], default: str) -> list[str]:
    """What the candidate may pick: the source language plus every project language with a current translation of every question. The default comes first when it qualifies. Team-only versions never."""
    s = summarize(questions, translations, project_languages)
    ok = [x["code"] for x in s["languages"] if x["ready"] and not x["team_only"]]
    if s["source"] not in ok:
        ok.insert(0, s["source"])  # the questionnaire itself is always readable, even when the project does not list its language
    return ([default] if default in ok else []) + [x for x in ok if x != default]


def localize(public_q: dict, q: recruit_form.Question, translations: dict, lang: str) -> dict:
    """The question in `lang` when a current translation exists (machine or confirmed); otherwise unchanged. The caller decides which languages a candidate may ask for."""
    entry = (translations.get(lang) or {}).get(q.id)
    if state_of(q, entry) not in ("machine", "confirmed"):
        return public_q
    opts = entry.get("options") or {}
    return {**public_q, "text": entry["text"], "options": [{**o, "text": opts.get(o["id"], o["text"])} for o in public_q["options"]]}


def mock_translation(questions: list[recruit_form.Question], lang: str) -> dict:
    """The offline translation used in development and tests: the original with a language mark in front (it says nothing about a model's quality)."""
    tag = f"[{lang.upper()}] "
    return {q.id: {"text": tag + q.text, "options": {o.id: tag + o.text for o in q.options}} for q in questions}


def _check(raw: object, questions: list[recruit_form.Question]) -> dict:
    """Accept the model's reply only when it has exactly the questions and options that were sent. Raises ValueError otherwise."""
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("no items")
    got = {str(i.get("id")): i for i in items if isinstance(i, dict)}
    out = {}
    for q in questions:
        i = got.get(q.id)
        if not i or not str(i.get("text", "")).strip():
            raise ValueError("a question is missing")
        opts = {str(o.get("id")): str(o.get("text", "")).strip() for o in (i.get("options") or []) if isinstance(o, dict)}
        if {o.id for o in q.options} != set(opts) or any(not v for v in opts.values()):
            raise ValueError("options do not match")
        out[q.id] = {"text": str(i["text"]).strip()[:300], "options": {k: v[:120] for k, v in opts.items()}}
    return out


def translate(chat: ModeratorChatProvider | None, questions: list[recruit_form.Question], lang: str) -> dict:
    """Translate the given questions into `lang`. `chat` None = the offline mock.

    Returns:
        {question id: {"text", "options": {option id: text}}}

    Raises:
        recruit_form.FormError: no usable translation after MAX_ATTEMPTS tries or the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if chat is None:
        return mock_translation(questions, lang)
    payload = [{"id": q.id, "text": q.text, "options": [{"id": o.id, "text": o.text} for o in q.options]} for q in questions]
    msgs = [{"role": "system", "content": SYSTEM.replace("{language}", NAMES.get(lang, lang))}, {"role": "user", "content": str(payload)}]
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _check(chat.complete(msgs, temperature=0.2), questions)
        except ChatFatalError:
            raise
        except ChatError as e:
            raise recruit_form.FormError("AI 暂时无法翻译，请稍后再试，或直接手写译文") from e
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            log.warning("translation attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise recruit_form.FormError("AI 没能给出完整的译文，请再试一次，或直接手写译文")


def store(translations: dict, questions: list[recruit_form.Question], lang: str, made: dict, state: str) -> dict:
    """A copy of `translations` with `made` saved for `lang`, each entry tied to the question's current wording."""
    by_id = {q.id: q for q in questions}
    cur = dict(translations.get(lang) or {})
    for qid, e in made.items():
        if qid in by_id:
            cur[qid] = {"text": e["text"], "options": e["options"], "fp": fingerprint(by_id[qid]), "state": state}
    return {**translations, lang: cur}


def confirm(translations: dict, questions: list[recruit_form.Question], lang: str, qids: list[str] | None) -> dict:
    """Mark current machine translations as confirmed by a person (all of them when `qids` is None). Stale or missing ones are left alone."""
    cur = dict(translations.get(lang) or {})
    for q in questions:
        if (qids is None or q.id in qids) and state_of(q, cur.get(q.id)) == "machine":
            cur[q.id] = {**cur[q.id], "state": "confirmed"}
    return {**translations, lang: cur}
