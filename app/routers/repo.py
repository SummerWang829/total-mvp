"""The research library (staff only): search, ask a question across everything the company has learned, and import earlier documents.

Everything is filtered by what the person may open before anything is ranked (app/repo/search.py): a researcher never gets a passage of a project
they are not on, in a search or in an answer. Participants appear as codes only.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ratelimit
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles
from ..models import RepoDocument
from ..repo import ask as ask_mod
from ..repo import documents, search

router = APIRouter(prefix="/api/repo", tags=["research-library"])
MAX_RESULTS = 30


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=ask_mod.MAX_QUESTION_CHARS)
    project_id: str | None = None


class DocBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=documents.MAX_CHARS)
    project_id: str | None = None
    source_note: str = Field(default="", max_length=300)


def _project_filter(db: Session, ctx: Ctx, project_id: str | None) -> None:
    if project_id and project_id not in search.visible_projects(db, ctx):
        raise HTTPException(404, "项目不存在")


@router.get("/search")
def do_search(q: str = Query("", max_length=ask_mod.MAX_QUESTION_CHARS), project_id: str | None = None, types: str = ",".join(search.TYPES), limit: int = 20,
              ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The passages that best match the words, with the interviewer's question before each transcript line."""
    _project_filter(db, ctx, project_id)
    kinds = tuple(t for t in types.split(",") if t in search.TYPES)
    hits = ask_mod.run_search(db, ctx, q, project_id, kinds, max(1, min(limit, MAX_RESULTS))) if kinds else []
    return {"hits": hits}


@router.post("/ask")
def do_ask(body: AskBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Answer from the library: claims, each with the verbatim passages it rests on; "not enough evidence" when that is the truth."""
    _project_filter(db, ctx, body.project_id)
    if wait := ratelimit.ask_allowed(ctx.user.id):
        raise HTTPException(429, ratelimit.MESSAGE, headers={"Retry-After": str(wait)})
    try:
        out = ask_mod.ask(db, ctx, body.question, body.project_id)
    except ask_mod.AskError as e:
        raise HTTPException(502, e.message) from e
    audit(db, ctx.org_id, ctx.actor, "repo.ask", ctx.user.id, scope="project" if body.project_id else "all", answered=not out["insufficient"])
    db.commit()
    return out


def _doc_out(d: RepoDocument, projects: dict[str, str]) -> dict:
    return {"id": d.id, "title": d.title, "source_note": d.source_note, "chars": len(d.text), "pieces": len(documents.pieces(d.text)),
            "project": {"id": d.project_id, "name": projects.get(d.project_id, "")} if d.project_id else None, "created_at": d.created_at.isoformat()}


@router.get("/documents")
def list_documents(ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    projects = search.visible_projects(db, ctx)
    rows = db.scalars(select(RepoDocument).where(RepoDocument.org_id == ctx.org_id).order_by(RepoDocument.created_at.desc())).all()
    return [_doc_out(d, projects) for d in rows if not d.project_id or d.project_id in projects]


@router.post("/documents", status_code=201)
def add_document(body: DocBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Import a document as text. With a project it is visible to that project's members only; without, to all staff of the company."""
    _project_filter(db, ctx, body.project_id)
    if not documents.pieces(body.text):
        raise HTTPException(422, "文件里没有可用的文字")
    doc = RepoDocument(org_id=ctx.org_id, project_id=body.project_id, title=body.title.strip(), text=body.text, source_note=body.source_note.strip(), created_by=ctx.user.id)
    db.add(doc)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "repo.document_add", doc.id, chars=len(body.text))
    db.commit()
    return _doc_out(doc, search.visible_projects(db, ctx))


@router.delete("/documents/{did}", status_code=204)
def delete_document(did: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    doc = db.get(RepoDocument, did)
    if not doc or doc.org_id != ctx.org_id or (doc.project_id and doc.project_id not in search.visible_projects(db, ctx)):
        raise HTTPException(404, "文件不存在")
    audit(db, ctx.org_id, ctx.actor, "repo.document_delete", doc.id)
    db.delete(doc)
    db.commit()
