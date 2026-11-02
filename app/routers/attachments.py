"""Reading an attached document into text for the AI chats (researchers, admins, support). Nothing is stored; only the kind and size are audited."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import attachments
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post("/extract")
async def extract(file: UploadFile = File(...), ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Turn one uploaded PDF / Word / text file into text. The page keeps the text and sends it with the next chat message (as 资料, not as instructions)."""
    data = await file.read(attachments.MAX_BYTES + 1)
    try:
        out = attachments.extract(file.filename or "", data)
    except attachments.AttachmentError as e:
        raise HTTPException(e.status, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "attachment.read", "", kind=out["kind"], chars=out["chars"])
    db.commit()
    return out
