"""The insight pool of a project (staff of the project): findings from interviews, public opinion, survey results and the team's own notes, side by side."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import pool
from ..audit import audit
from ..db import get_db
from ..deps import READERS, STAFF, Ctx, require_roles
from ..models import PoolNote
from .projects import get_project

router = APIRouter(prefix="/api", tags=["pool"])


class NoteBody(BaseModel):
    theme: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2000)


@router.get("/projects/{pid}/pool")
def get_pool(pid: str, ctx: Ctx = Depends(require_roles(*READERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    client = ctx.user.role == "viewer"
    items = pool.gather(db, proj, include_notes=not client)   # a client does not see the team's internal notes, nor links into interviews it cannot open
    findings = pool.group(items)
    counts = {k: sum(1 for i in items if i.kind == k) for k in pool.KINDS}
    out = [pool.finding_out(f) for f in findings]
    if client:
        for f in out:
            for i in f["items"]:
                i["interview_id"] = None
    return {"counts": counts, "triangulated": sum(len(f.kinds) >= 2 for f in findings), "findings": out}


@router.post("/projects/{pid}/pool/notes", status_code=201)
def add_note(pid: str, body: NoteBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A note of the team's own (a hypothesis, something from outside the data) that joins the finding with the same theme."""
    proj = get_project(db, ctx, pid)
    try:
        theme, text = pool.clean_note(body.theme, body.text)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    n = PoolNote(org_id=ctx.org_id, project_id=proj.id, theme=theme, text=text, created_by=ctx.user.id)
    db.add(n)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, "pool.note_add", n.id)
    db.commit()
    return {"id": n.id}


@router.delete("/pool/notes/{nid}", status_code=204)
def delete_note(nid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    n = db.get(PoolNote, nid)
    if not n or n.org_id != ctx.org_id:
        raise HTTPException(404, "笔记不存在")
    get_project(db, ctx, n.project_id)
    audit(db, ctx.org_id, ctx.actor, "pool.note_delete", n.id)
    db.delete(n)
    db.commit()
