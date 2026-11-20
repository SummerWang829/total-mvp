"""Reading a link the team pastes into an input box (修正方案第 1 轮第 2 条). The server fetches the page (app/web_reader.py: public addresses only, checked again on
every redirect and pinned for the connection; 2MB, 10 seconds). The text goes back to the page, which hands it to the AI with the next message ("只用于这次");
or it is kept as project material in the research library ("存为项目资料"), with the page title and the website name only, never the full address."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import web_reader
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project, RepoDocument

router = APIRouter(prefix="/api/links", tags=["links"])


class LinkBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    project_id: str | None = Field(default=None, max_length=40)
    save: bool = False


@router.post("/read")
def read_link(body: LinkBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A card for the link: title, website, how many characters were read, and the text (for this message), or the id of the kept project material."""
    if body.save:
        if not body.project_id:
            raise HTTPException(422, "存为项目资料要在项目里操作")
        p = db.get(Project, body.project_id)
        if p is None or p.org_id != ctx.org_id:
            raise HTTPException(404, "项目不存在")
        ensure_project(db, ctx, p.id)
    try:
        page = web_reader.read(body.url)
    except web_reader.WebError as e:
        raise HTTPException(422, f"链接没读到：{e}") from e
    title = page["title"] or page["host"]
    out = {"title": title, "host": page["host"], "chars": page["chars"], "truncated": page["truncated"], "repo_id": ""}
    if body.save:
        d = RepoDocument(org_id=ctx.org_id, project_id=body.project_id, title=title[:200], source_note=f"网页 · {page['host']}", kind="link", source_host=page["host"][:120],
                         text=page["text"], created_by=ctx.user.id)
        db.add(d)
        db.flush()
        out["repo_id"] = d.id
    else:
        out["text"] = page["text"]
    audit(db, ctx.org_id, ctx.actor, "link.read", body.project_id or "", host=page["host"], saved=body.save)   # the website only, never the address or the text
    db.commit()
    return out
