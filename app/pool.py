"""The insight pool: what the project has learned, from every method, side by side.

Four kinds of source feed it: insights a researcher approved from interviews (and written async studies), topics people talk about in public (monitoring),
questions of survey results, and notes the team writes. Items about the same thing are grouped into one finding by the words of their theme, and a finding
supported by two or more kinds of source is marked as triangulated: the interviews say it and the numbers or the public conversation agree.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Insight, Interview, Mention, PoolNote, Project, QuantSurvey
from .repo.tokens import tokens

KINDS = ("interview", "sentiment", "survey", "note")
KIND_LABEL = {"interview": "访谈", "sentiment": "舆情", "survey": "问卷", "note": "笔记"}
MIN_OVERLAP = 0.6
MAX_TOPICS = 30
MAX_QUESTIONS = 60


@dataclass
class Item:
    kind: str
    theme: str
    text: str
    ref: str                      # insight id, topic name, survey id, note id
    interview_id: str | None = None
    weight: int = 1               # how much stands behind it (quotes, mentions, respondents)


@dataclass
class Finding:
    theme: str
    items: list[Item] = field(default_factory=list)
    _tokens: set[str] = field(default_factory=set)
    _seen: list[set[str]] = field(default_factory=list)

    @property
    def kinds(self) -> list[str]:
        return [k for k in KINDS if any(i.kind == k for i in self.items)]


def _close(a: set[str], b: set[str]) -> bool:
    """Whether two themes are about the same thing: they share most of the words of the shorter one."""
    if not a or not b:
        return False
    common = len(a & b)
    return common >= 1 and common / min(len(a), len(b)) >= MIN_OVERLAP


def gather(db: Session, project: Project, include_notes: bool = True) -> list[Item]:
    """Every item the project's sources offer (a client does not get the team's internal notes)."""
    out: list[Item] = []
    rows = db.execute(select(Insight, Interview).join(Interview, Interview.id == Insight.interview_id)
                      .where(Interview.project_id == project.id, Interview.purged_at.is_(None), Insight.status == "approved")).all()
    out += [Item("interview", ins.theme, ins.statement, ins.id, itv.id, max(1, len(ins.evidence))) for ins, itv in rows]
    topics: Counter = Counter()
    negative: Counter = Counter()
    for m in db.scalars(select(Mention).where(Mention.project_id == project.id)):
        for t in m.topics or []:
            topics[t] += 1
            negative[t] += m.sentiment == "neg"
    for t, n in topics.most_common(MAX_TOPICS):
        out.append(Item("sentiment", t, f"公开渠道上有 {n} 条提到，其中负面 {round(negative[t] / n * 100)}%", t, None, n))
    for s in db.scalars(select(QuantSurvey).where(QuantSurvey.project_id == project.id)):
        for q in (s.questions or [])[:MAX_QUESTIONS]:
            top = max(q.get("results") or [], key=lambda r: r["count"], default=None)
            total = sum(r["count"] for r in q.get("results") or []) or 1
            summary = f"{s.title}：{top['option']} {round(top['count'] / total * 100)}%（{s.respondents} 份回答）" if top else (f"{s.title}：平均 {q['mean']}（{s.respondents} 份回答）" if q.get("mean") is not None else s.title)
            out.append(Item("survey", q["text"], summary, s.id, None, s.respondents))
    if include_notes:
        out += [Item("note", n.theme, n.text, n.id, None, 1) for n in db.scalars(select(PoolNote).where(PoolNote.project_id == project.id).order_by(PoolNote.created_at))]
    return out


def group(items: list[Item]) -> list[Finding]:
    """Group items into findings by theme; findings backed by more kinds of source, then by more weight, come first."""
    findings: list[Finding] = []
    for it in sorted(items, key=lambda i: (len(i.theme), i.kind)):   # short themes first: they become the group's name
        toks = set(tokens(it.theme))
        home = next((f for f in findings if _close(f._tokens, toks) or any(_close(t, toks) for t in f._seen)), None)   # close to the group's name, or to any theme already in it
        if home is None:
            home = Finding(theme=it.theme, _tokens=set(toks))   # the name's words stay as they were: a group does not drift as it grows
            findings.append(home)
        home._seen.append(toks)
        home.items.append(it)
    findings.sort(key=lambda f: (-len(f.kinds), -sum(i.weight for i in f.items), f.theme))
    return findings


def finding_out(f: Finding) -> dict:
    return {"theme": f.theme, "kinds": f.kinds, "triangulated": len(f.kinds) >= 2, "weight": sum(i.weight for i in f.items),
            "items": [{"kind": i.kind, "kind_label": KIND_LABEL[i.kind], "theme": i.theme, "text": i.text, "ref": i.ref, "interview_id": i.interview_id, "weight": i.weight} for i in f.items]}


def clean_note(theme: str, text: str) -> tuple[str, str]:
    """A note's theme and text, trimmed.

    Raises:
        ValueError: empty theme or text.
    """
    theme, text = re.sub(r"\s+", " ", theme).strip()[:200], text.strip()[:2000]
    if not theme or not text:
        raise ValueError("请填写主题和内容")
    return theme, text
