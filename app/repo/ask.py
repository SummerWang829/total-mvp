"""Asking the library a question: the answer is only ever built from passages the person is allowed to see, and every claim carries the
verbatim passages it rests on, copied by the server (never typed by the model).

What the model returns is untrusted: it may only cite passage numbers that were given to it; a claim without a valid number is dropped; a claim
whose figures do not appear in the passages it cites is dropped (no invented statistics); if nothing survives, the answer says the evidence is
not enough instead of guessing (AC-M08-05).
"""
import logging
import re

import httpx
from sqlalchemy.orm import Session

from ..deps import Ctx
from ..providers import LLM, get_llm
from . import search

log = logging.getLogger(__name__)

TOP_K = 10
MAX_QUESTION_CHARS = 500
MSG_NOT_ENOUGH = "资料库里没有足够的证据来回答这个问题。可以换个问法，或先导入相关资料、完成相关访谈。"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


class AskError(Exception):
    """The model could not be reached or gave nothing usable; `message` is plain Chinese for the researcher."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def hit_out(s: search.Source, projects: dict[str, str], context: str = "") -> dict:
    """A source as the page shows it (a participant appears only as a code, never by name)."""
    out = {"ref": s.ref, "type": s.type, "text": s.text, "who": s.who, "at_ms": s.at_ms, "title": s.title, "interview_id": s.interview_id,
           "project": {"id": s.project_id, "name": projects.get(s.project_id, "")} if s.project_id else None, "score": round(s.score, 2)}
    if context:
        out["question_before"] = context
    return out


def run_search(db: Session, ctx: Ctx, q: str, project_id: str | None, types: tuple[str, ...], limit: int) -> list[dict]:
    """Plain search: the best passages, with the interviewer's question before each transcript line."""
    projects = search.visible_projects(db, ctx)
    top = search.rank(q, search.candidates(db, ctx, project_id, types), limit)
    return [hit_out(s, projects, search.context_before(db, s.ref)) for s in top]


def _supported(text: str, cited: list[search.Source], question: str) -> bool:
    """Every figure in a claim must appear in the passages it cites (or in the question itself)."""
    haystack = " ".join([question, *[c.text for c in cited]])
    return all(n in haystack for n in _NUMBER.findall(text))


def ask(db: Session, ctx: Ctx, question: str, project_id: str | None = None, llm: LLM | None = None) -> dict:
    """Answer a question from the library.

    Returns:
        {"answer": [{"text", "evidence": [hit, ...]}], "insufficient": bool, "message": str, "searched": passages considered}

    Raises:
        AskError: the model service could not be reached or returned something unusable.
    """
    question = question.strip()[:MAX_QUESTION_CHARS]
    pool = search.candidates(db, ctx, project_id)
    top = search.rank(question, pool, TOP_K)
    if not top:
        return {"answer": [], "insufficient": True, "message": MSG_NOT_ENOUGH, "searched": len(pool)}
    projects = search.visible_projects(db, ctx)
    contexts = {s.ref: search.context_before(db, s.ref) for s in top}
    numbered = [{"n": i, "type": s.type, "text": s.text, **({"interviewer_asked": contexts[s.ref]} if contexts[s.ref] else {})} for i, s in enumerate(top, 1)]
    try:
        raw = (llm or get_llm()).answer(question, numbered)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("library answer failed: %s", type(e).__name__)
        raise AskError("AI 暂时无法回答，请稍后再试。你仍然可以用搜索查看相关原文。") from e
    if not isinstance(raw, dict):
        raw = {}  # not a JSON object: treated as "no usable answer"
    claims = []
    for c in raw.get("claims", []) if isinstance(raw.get("claims"), list) else []:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text", "")).strip()[:600]
        nums = list(dict.fromkeys(int(n) for n in c.get("evidence", []) if str(n).strip().isdigit() and 1 <= int(n) <= len(top)))
        cited = [top[n - 1] for n in nums]
        if not text or not cited or not _supported(text, cited, question):
            continue
        claims.append({"text": text, "evidence": [hit_out(s, projects, contexts[s.ref]) for s in cited]})
    if raw.get("insufficient") is True or not claims:
        return {"answer": [], "insufficient": True, "message": MSG_NOT_ENOUGH, "searched": len(pool)}
    return {"answer": claims, "insufficient": False, "message": "", "searched": len(pool)}
