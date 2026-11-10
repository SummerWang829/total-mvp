"""Reading an attached document into text for the AI chats (researchers, admins, support). Nothing is stored; only the kind and size are audited."""
import os
import tempfile

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


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), lang: str = "zh", ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Turn one uploaded voice clip into text for the AI chat input (不一定要打字). Nothing is stored; the clip is deleted after reading.

    The file is a browser recording (webm/ogg/mp3/wav/m4a). Tencent recorded-file ASR needs a real file on disk, so it is written to a
    temp file, transcribed, then removed. Only Chinese and English are supported; a plain message is returned otherwise.
    """
    from ..asr_file import ASRError, ASRUnsupported, TencentFileASR, file_asr_configured

    if not file_asr_configured():
        raise HTTPException(503, "语音识别服务还没配置，请先打字说明")
    data = await file.read(12 * 1024 * 1024)
    if not data:
        raise HTTPException(422, "没有收到录音内容")
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".webm"
    if suffix not in (".webm", ".ogg", ".oga", ".mp3", ".wav", ".m4a", ".aac", ".flac"):
        suffix = ".webm"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            rows = TencentFileASR().transcribe_audio(path, lang or "zh", "")
        except ASRUnsupported as e:
            raise HTTPException(422, str(e)) from e
        except ASRError as e:
            raise HTTPException(502, str(e)) from e
        text = "".join(r.get("text", "") for r in rows).strip()
        if not text:
            raise HTTPException(422, "没有识别出文字，请再说一次或直接打字")
        audit(db, ctx.org_id, ctx.actor, "attachment.transcribe", "", lang=lang or "zh", chars=len(text))
        db.commit()
        return {"text": text}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
