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


# 修正方案第 1 轮第 2 条（用户 2026-11-20 确认：受访者端也放麦克风）: a participant's voice input on their own pages (screening questionnaire, questions before
# the interview, the room's answer box, the written conversation). Allowed only with that page's own link; the recording is not kept and its text is not logged;
# the consent text and the page say the voice goes to Tencent Cloud speech recognition to become text.
public_router = APIRouter(prefix="/api/public/speech", tags=["speech"])


@public_router.post("")
async def transcribe_public(file: UploadFile = File(...), token: str = Form(..., max_length=200), kind: str = Form("join", pattern="^(join|screen)$"),
                            language: str = Form("zh"), db: Session = Depends(get_db)):
    """The words in a short recording made on a participant's page. `kind` says which link `token` is: an interview invitation (join) or a screening questionnaire."""
    from sqlalchemy import select
    from .public import resolve_join
    from ..models import RecruitForm
    if kind == "join":
        itv = resolve_join(db, token).itv   # 404 for a wrong link
        language, org_id, ref = (itv.language if itv.language in speech.LANGUAGES else language), itv.org_id, itv.id
    else:
        f = db.scalar(select(RecruitForm).where(RecruitForm.token == token)) if token else None
        if f is None:
            raise HTTPException(404, "Invalid link / 链接无效")
        org_id, ref = f.org_id, f.project_id
    if not _allowed("p:" + token[:40]):
        raise HTTPException(429, "说得太频繁了，请稍等一下再试 / Too many tries, please wait a moment")
    data = await file.read(speech.MAX_BYTES + 1)
    try:
        text = speech.transcribe_clip(data, language)
    except speech.SpeechError as e:
        raise HTTPException(e.status, str(e)) from e
    audit(db, org_id, "participant", "speech.read_participant", ref, bytes=len(data), language=language)
    db.commit()
    return {"text": text}


# Voice option B (修正方案第 1 轮第 2 条: both are built and tried before choosing): the live recogniser the interview captions use. The server only hands out a
# short-lived signed address; the secret key never leaves the server; nothing is stored.
@router.get("/live")
def live_token(language: str = "zh", ctx: Ctx = Depends(require_roles(*ROLES))):
    from ..asr_live import token_out
    if not _allowed(ctx.user.id):
        raise HTTPException(429, "说得太频繁了，请稍等一下再试")
    return token_out(language)


@public_router.post("/live")
def live_token_public(token: str = Form(..., max_length=200), kind: str = Form("join", pattern="^(join|screen)$"), language: str = Form("zh"), db: Session = Depends(get_db)):
    from sqlalchemy import select
    from ..asr_live import token_out
    from ..models import RecruitForm
    from .public import resolve_join
    if kind == "join":
        itv = resolve_join(db, token).itv
        language = itv.language if itv.language in speech.LANGUAGES else language
    elif db.scalar(select(RecruitForm).where(RecruitForm.token == token)) is None:
        raise HTTPException(404, "Invalid link / 链接无效")
    if not _allowed("p:" + token[:40]):
        raise HTTPException(429, "Too many tries / 太频繁")
    return token_out(language)
