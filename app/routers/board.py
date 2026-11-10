"""The 综合看板 (focus board): every question/part the team marked ★ 重点关注 in the interview outline, with what all finished
interviews said about it, aggregated into one page so a human moderator or a client can watch many AI-moderated interviews at once.

Only what is already stored is shown; nothing is written by a model here. The counts and theme percentages are computed by the
server from the stored answers (app/interview_answers.py); the quotes are copied by the server from the transcript; an AI's own
reading, when there is one, is a separate, clearly-labelled line (app/interview_answers.py writes the themes). A client observer
sees only the participants' numbers, never their names, tags, source or device (the same as the interview list).
"""
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import interview_answers, outline, outline_versions
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles
from ..models import Interview
from .projects import get_project

router = APIRouter(prefix="/api", tags=["board"])

MAX_QUOTES = 3       # the most quotes shown under one focus point
MAX_THEMES = 5       # the most themes counted under one focus point
QUOTE_CHARS = 120    # a quote is cut to this many characters


def _focus_points(db: Session, proj) -> list[dict]:
    """The questions marked ★ 重点关注 in the outline, in order: {q, num, reason, kind, ref, index}. Empty when there is no outline or no marks."""
    key = outline_versions.base_key(proj)
    text = outline_versions.text_of(proj, key)
    if not text.strip():
        return []
    summary = outline.summary_of(text)
    if not summary:
        return []
    out = []
    n = 0
    for part in summary["parts"]:
        for qi, q in enumerate(part["questions"]):
            if q.get("cover"):
                continue
            n += 1
            if q.get("mark"):
                out.append({"q": q["text"], "num": n, "reason": q.get("reason", ""), "mark_by": q.get("mark_by", ""),
                            "kind": part["kind"], "ref": part["ref"], "index": qi})
    return out


@router.get("/projects/{pid}/board")
def board(pid: str, ctx: Ctx = Depends(require_roles(*STAFF, "viewer")), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    points = _focus_points(db, proj)
    its = list(db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.status == "ended")).all())
    # one row per focus point: how many answered it, the theme percentages, and the first few quotes
    rows = []
    for fp in points:
        answered = 0
        themes: Counter = Counter()
        quotes: list[str] = []
        for itv in its:
            ans = interview_answers.load(itv).get(fp["q"])
            if not ans or not ans.get("a"):
                continue
            answered += 1
            for t in ans.get("themes", []) or []:
                themes[str(t)] += 1
            if len(quotes) < MAX_QUOTES and ans.get("main"):
                quotes.append(ans["main"][:QUOTE_CHARS])
        rows.append({
            "q": fp["q"], "num": fp["num"], "reason": fp["reason"], "mark_by": fp["mark_by"],
            "answered": answered, "total": len(its),
            "themes": [{"label": k, "n": v} for k, v in themes.most_common(MAX_THEMES)],
            "quotes": quotes,
        })
    total = len(list(db.scalars(select(Interview.id).where(Interview.project_id == proj.id))))
    return {
        "kpi": {
            "live": sum(1 for i in db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.status == "live"))),
            "done": len(its),
            "total": total,
            "focus": len(points),
        },
        "rows": rows,
    }
