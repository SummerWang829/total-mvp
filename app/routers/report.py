"""The project's research report as a Word file (staff of the project). See app/report.py."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import docx_writer, report
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles
from ..models import utcnow
from .projects import get_project

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/projects/{pid}/report")
def project_report(pid: str, format: str = "docx", ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The research report: a Word download, or the same content as JSON blocks (format=json) for a preview."""
    if format not in ("docx", "json"):
        raise HTTPException(422, "格式只能是 docx 或 json")
    proj = get_project(db, ctx, pid)
    blocks = report.build(db, proj, utcnow())
    audit(db, ctx.org_id, ctx.actor, "report.export", proj.id, format=format, blocks=len(blocks))
    db.commit()
    if format == "json":
        return {"blocks": [list(b) for b in blocks]}
    return Response(docx_writer.render(blocks), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="research-report.docx"'})
