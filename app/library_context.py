"""The project's research library (研究库) as the source the AI writing tools draw on.

The research background is saved into the library first (the wizard's "存入研究库"), and from there it reaches the questionnaire writer and the suggestions together with
whatever else the team has put in for this project (earlier reports, notes, desk research). Only this project's own documents are used, newest first, trimmed.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import RepoDocument, utcnow

MAX_DOCS = 6
PER_DOC_CHARS = 1500
TOTAL_CHARS = 6000
BRIEF_TITLE = "研究背景（立项）"


def project_documents(db: Session, project_id: str) -> list[RepoDocument]:
    return list(db.scalars(select(RepoDocument).where(RepoDocument.project_id == project_id).order_by(RepoDocument.created_at.desc())))


def library_text(db: Session, project_id: str) -> tuple[str, int]:
    """(the text to hand to the AI, how many documents it came from): each document's title and its first part, newest first, within a total limit."""
    parts, used, size = [], 0, 0
    for d in project_documents(db, project_id)[:MAX_DOCS]:
        piece = f"## {d.title}\n{d.text[:PER_DOC_CHARS]}"
        if size + len(piece) > TOTAL_CHARS:
            break
        parts.append(piece)
        size += len(piece)
        used += 1
    return "\n\n".join(parts), used


def save_brief(db: Session, org_id: str, project_id: str, brief: str, user_id: str) -> RepoDocument:
    """Put (or refresh) the research background in the project's library as one document. The caller commits."""
    doc = next((d for d in project_documents(db, project_id) if d.title == BRIEF_TITLE), None)
    if doc is None:
        doc = RepoDocument(org_id=org_id, project_id=project_id, title=BRIEF_TITLE, source_note="立项向导里写的研究背景", text=brief, created_by=user_id)
        db.add(doc)
    else:
        doc.text, doc.created_at = brief, utcnow()
    return doc
