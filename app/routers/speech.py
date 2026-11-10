"""Voice input for the team's text boxes (admins, researchers, support, project managers): one short clip in, text out. Nothing is stored or logged."""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import speech
from ..audit import audit
from ..db import get_db
from ..deps import Ctx, require_roles

router = APIRouter(prefix="/api/speech", tags=["speech"])
ROLES = ("admin", "researcher", "support", "recruiter")
PER_MINUTE = 20  # per person: a text box, not a bulk transcription service
_recent: dict[str, deque] = defaultdict(deque)


def _allowed(user_id: str) -> bool:
    now, q = time.monotonic(), _recent[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= PER_MINUTE:
        return False
    q.append(now)
    return True


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("zh"), ctx: Ctx = Depends(require_roles(*ROLES)), db: Session = Depends(get_db)):
    """The words in a short recording. The page puts the text into the box the person is typing in; it never sends anything by itself."""
    if not _allowed(ctx.user.id):
        raise HTTPException(429, "说得太频繁了，请稍等一下再试")
    data = await file.read(speech.MAX_BYTES + 1)
    try:
        text = speech.transcribe_clip(data, language)
    except speech.SpeechError as e:
        raise HTTPException(e.status, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "speech.read", "", bytes=len(data), language=language)
    db.commit()
    return {"text": text}
