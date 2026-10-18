"""Searching what this person is allowed to see.

Permission is applied *before* ranking: the candidate list is built only from projects the person can open (admins: every project of the
company; researchers: the projects they are members of), so nothing outside it can ever be ranked, quoted or leak into an answer.

Sources: what participants said (transcript lines of interviews that are not purged), insights a researcher approved, and imported documents.
The moderator's own lines are not sources. Ranking is BM25 over the terms of app/repo/tokens.py, computed over the candidates at query time, so
a new transcript is searchable the moment it exists and a purged one is gone at once (no separate index to keep in step).
"""
import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Ctx
from ..models import Insight, Interview, Participant, Project, ProjectMember, RepoDocument, TranscriptSegment
from .documents import pieces
from .tokens import tokens

K1, B = 1.5, 0.75
MIN_COVERAGE = 0.34  # a hit must contain at least this share of the question's distinct terms (otherwise it is only a coincidence)
NOT_SPEAKERS = ("moderator", "ai_moderator")
TYPES = ("segment", "insight", "document")


@dataclass
class Source:
    """One searchable passage. `ref` is its stable address (segment:ID, insight:ID, document:ID:N); `text` is verbatim what the source says."""
    ref: str
    type: str
    text: str
    project_id: str | None
    interview_id: str | None = None
    at_ms: int = 0
    who: str = ""
    title: str = ""
    score: float = 0.0


def visible_projects(db: Session, ctx: Ctx) -> dict[str, str]:
    """{project id: name} of every project this staff member may open."""
    q = select(Project.id, Project.name).where(Project.org_id == ctx.org_id)
    if ctx.user.role != "admin":
        q = q.where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == ctx.user.id)))
    return {pid: name for pid, name in db.execute(q)}


def _codes(db: Session, project_ids: list[str]) -> dict[str, str]:
    """{participant id: "P-001"} per project, in the order participants were added (the same codes as the exports)."""
    out: dict[str, str] = {}
    for pid in project_ids:
        for n, (part_id,) in enumerate(db.execute(select(Participant.id).where(Participant.project_id == pid).order_by(Participant.created_at))):
            out[part_id] = f"P-{n + 1:03d}"
    return out


def _seat_label(speaker: str) -> str:
    return f"参与者 ·{int(speaker[1:]):02d}" if speaker[:1] == "p" and speaker[1:].isdigit() else ""


def candidates(db: Session, ctx: Ctx, project_id: str | None = None, types: tuple[str, ...] = TYPES) -> list[Source]:
    """Every passage this person may search, optionally narrowed to one project and some source types."""
    projects = visible_projects(db, ctx)
    if project_id:
        projects = {project_id: projects[project_id]} if project_id in projects else {}
    ids = list(projects)
    out: list[Source] = []
    codes = _codes(db, ids) if ids else {}
    if "segment" in types and ids:
        rows = db.execute(select(TranscriptSegment, Interview).join(Interview, Interview.id == TranscriptSegment.interview_id)
                          .where(Interview.org_id == ctx.org_id, Interview.project_id.in_(ids), Interview.purged_at.is_(None),
                                 TranscriptSegment.speaker.notin_(NOT_SPEAKERS))).all()
        for seg, itv in rows:
            who = _seat_label(seg.speaker) or codes.get(itv.participant_id, "")
            out.append(Source(f"segment:{seg.id}", "segment", seg.text_orig, itv.project_id, itv.id, seg.start_ms, who, itv.title))
    if "insight" in types and ids:
        rows = db.execute(select(Insight, Interview).join(Interview, Interview.id == Insight.interview_id)
                          .where(Interview.org_id == ctx.org_id, Interview.project_id.in_(ids), Interview.purged_at.is_(None), Insight.status == "approved")).all()
        for ins, itv in rows:
            out.append(Source(f"insight:{ins.id}", "insight", f"{ins.theme}：{ins.statement}", itv.project_id, itv.id, 0, codes.get(itv.participant_id, ""), itv.title))
    if "document" in types:
        for doc in db.scalars(select(RepoDocument).where(RepoDocument.org_id == ctx.org_id)):
            if doc.project_id and doc.project_id not in projects:
                continue  # a document of a project this person is not on (or outside the project filter)
            if project_id and doc.project_id != project_id:
                continue
            for n, piece in enumerate(pieces(doc.text)):
                out.append(Source(f"document:{doc.id}:{n}", "document", piece, doc.project_id, None, 0, "", doc.title))
    return out


def rank(question: str, sources: list[Source], limit: int) -> list[Source]:
    """The best passages for a question, best first (BM25), each with its score set. Passages that share too little with the question are left out."""
    q_terms = list(dict.fromkeys(tokens(question)))
    if not q_terms or not sources:
        return []
    docs = [Counter(tokens(s.text)) for s in sources]
    n = len(docs)
    avg = (sum(sum(c.values()) for c in docs) / n) or 1.0
    df = {t: sum(1 for c in docs if t in c) for t in q_terms}
    scored = []
    for s, c in zip(sources, docs):
        matched = [t for t in q_terms if t in c]
        if len(matched) / len(q_terms) < MIN_COVERAGE:
            continue
        length = sum(c.values())
        s.score = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * c[t] * (K1 + 1) / (c[t] + K1 * (1 - B + B * length / avg)) for t in matched)
        scored.append(s)
    scored.sort(key=lambda s: -s.score)
    return scored[:limit]


def context_before(db: Session, ref: str) -> str:
    """For a transcript line: the line just before it (usually the interviewer's question), so a short answer makes sense on its own."""
    if not ref.startswith("segment:"):
        return ""
    seg = db.get(TranscriptSegment, ref.split(":", 1)[1])
    if not seg or seg.idx == 0:
        return ""
    prev = db.scalar(select(TranscriptSegment).where(TranscriptSegment.interview_id == seg.interview_id, TranscriptSegment.idx == seg.idx - 1))
    return prev.text_orig if prev and prev.speaker in NOT_SPEAKERS else ""
