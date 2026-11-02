"""Concept-test room features: the script, the material on the participant's screen, live captions, quotes and follow-up questions.

Who may do what: the moderator and the research team run the room (show a material, post their own captions, queue follow-ups);
a hidden observer may read captions and mark quotes / suggest follow-ups; the participant only posts captions of their own microphone
and sees the current material. Quote text is always copied from the caption line by the server, never taken from the browser.
"""
import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import concept, content_rules, groups, marks, signals
from ..asr_live import token_out
from ..audit import audit
from ..db import SessionLocal, get_db
from ..deps import STAFF, Ctx, current_ctx, require_roles, visible_interview
from ..models import BackroomMessage, CaptionLine, Highlight, Interview, RoomState, Stimulus, User, utcnow
from ..providers import get_llm
from .interviews import _ingest
from .public import _consented, resolve_join

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["concept"])

OPERATE = ("admin", "researcher", "support", "moderator")
MAX_TEXT = 600
MAX_QUOTES = 6   # caption lines one follow-up may be built from
PROPOSER_LABEL = {"admin": "管理员", "researcher": "研究员", "support": "技术支持", "moderator": "主持人", "viewer": "客户", "observer": "观察员", "recruiter": "项目经理"}
MAX_LINES_PER_INTERVIEW = 5000
PARTIAL_TTL_S = 8.0
_PARTIAL: dict[tuple[str, str], tuple[str, float]] = {}  # the sentence being spoken right now, per (interview, speaker); memory only, never stored
_LINE_LOCK = threading.Lock()
_TRANSLATOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="caption-translate")
_translations: set[Future] = set()


def _translate_line(line_id: str) -> None:
    """Chinese translation of one caption line, in the background: the sentence is already on screen, the Chinese follows a moment later."""
    try:
        with SessionLocal() as db:
            row = db.get(CaptionLine, line_id)
            if row and not row.text_zh:
                out = get_llm().translate([row.text])
                if out and out[0]:
                    row.text_zh = out[0]
                    db.commit()
    except Exception:  # noqa: BLE001 - a translation hiccup must never affect the interview; the line simply stays untranslated
        log.warning("caption translation failed", exc_info=True)  # no caption text in the log


def wait_translations(timeout: float = 10.0) -> None:
    """Tests: wait until the background translations have finished."""
    wait(list(_translations), timeout=timeout)


at_ms = concept.at_ms


def _open(itv: Interview) -> None:
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    if itv.status != "live":
        raise HTTPException(409, "访谈进行中才能这样做")


# ---------- the script and the materials ----------
class TemplateBody(BaseModel):
    template: str


@router.post("/interviews/{iid}/concept/template")
def install_template(iid: str, body: TemplateBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.status == "ended" or itv.purged_at:
        raise HTTPException(409, "访谈已结束")
    try:
        concept.install(db, ctx.org_id, itv.id, body.template)
    except concept.TemplateError as e:
        raise HTTPException(422, str(e))
    audit(db, ctx.org_id, ctx.actor, "concept.install", itv.id, ip=ctx.ip)
    db.commit()
    return concept.plan_out(db, itv.id)


@router.get("/interviews/{iid}/concept")
def get_concept(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The script and the materials, for the moderator and the team (observers and participants never get the script)."""
    itv = visible_interview(db, ctx, iid)
    return concept.plan_out(db, itv.id) or {"template": None}


class ShowBody(BaseModel):
    stimulus_id: str | None = None  # None: take the material off the screen


@router.post("/interviews/{iid}/stimulus/show")
def show_stimulus(iid: str, body: ShowBody, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _open(itv)
    if body.stimulus_id:
        s = db.get(Stimulus, body.stimulus_id)
        if not s or s.interview_id != itv.id:
            raise HTTPException(404, "没有这个素材")
    state = db.get(RoomState, itv.id) or RoomState(interview_id=itv.id, org_id=ctx.org_id)
    state.stimulus_id, state.shown_at, state.shown_by = body.stimulus_id, utcnow(), ctx.actor
    db.add(state)
    audit(db, ctx.org_id, ctx.actor, "stimulus.show" if body.stimulus_id else "stimulus.hide", body.stimulus_id or itv.id, ip=ctx.ip)
    db.commit()
    return {"stimulus": concept.current_stimulus(db, itv.id, staff=True)}


# ---------- marking the material (♥ like / ✕ dislike on an image) ----------
class MarkBody(BaseModel):
    kind: Literal["like", "dislike"]
    x: float = Field(ge=0, le=1)  # fraction of the image width
    y: float = Field(ge=0, le=1)  # fraction of the image height


@router.post("/public/join/{token}/stimulus/{sid}/mark", status_code=201)
def add_mark(token: str, sid: str, body: MarkBody, db: Session = Depends(get_db)):
    """The participant taps the material to mark a spot they like or dislike."""
    j = _entered_participant(db, token)
    try:
        m = marks.add_mark(db, j.itv.org_id, j.itv, sid, j.participant.id, body.kind, body.x, body.y)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return marks.mark_out(m)


@router.delete("/public/join/{token}/stimulus/{sid}/mark/{mid}", status_code=204)
def remove_mark(token: str, sid: str, mid: str, db: Session = Depends(get_db)):
    """The participant taps one of their own pins to take it off."""
    j = _entered_participant(db, token)
    if not marks.remove_mark(db, j.itv.id, j.participant.id, mid):
        raise HTTPException(404, "没有这个标记")
    db.commit()


@router.get("/public/join/{token}/stimulus/{sid}/marks")
def my_marks(token: str, sid: str, db: Session = Depends(get_db)):
    """The participant's own marks on one material (to redraw their pins after a refresh)."""
    j = _entered_participant(db, token)
    return {"marks": marks.participant_marks(db, j.itv.id, sid, j.participant.id)}


@router.get("/interviews/{iid}/stimulus/{sid}/marks")
def stimulus_marks(iid: str, sid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """Every mark on one material, for the moderator's live overlay (observers excluded)."""
    itv = visible_interview(db, ctx, iid)
    return {"marks": marks.stimulus_marks(db, itv.id, sid)}


@router.get("/interviews/{iid}/stimulus-marks")
def marks_summary(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The research team's heatmap: each image material with its marks and like / dislike totals."""
    itv = visible_interview(db, ctx, iid)
    return {"stimuli": marks.summary(db, itv.id)}


# ---------- live captions ----------
class CaptionIn(BaseModel):
    text: str = Field(max_length=MAX_TEXT)
    final: bool = True  # false: the sentence is still being spoken (shown greyed, not stored)
    dur_ms: int = Field(default=0, ge=0, le=120_000)  # how long the sentence took, from the recogniser (used to place it on the transcript timeline)
    vol_db: float | None = Field(default=None, ge=-100, le=0)  # average level while speaking, dBFS, measured in the speaker's own browser (one number, no audio)


def _how_it_was_said(db: Session, itv: Interview, speaker: str, text: str, body: CaptionIn) -> dict:
    """Pace, volume, the pause before answering and the tone of the words for a new sentence, against this speaker's own earlier sentences."""
    rows = db.execute(select(CaptionLine.rate_x10, CaptionLine.vol_db10, CaptionLine.gap_ms).where(
        CaptionLine.interview_id == itv.id, CaptionLine.speaker == speaker).order_by(CaptionLine.seq.desc()).limit(40)).all()[::-1]
    history = [signals.Past(None if r is None else r / 10, None if v is None else v / 10, g) for r, v, g in rows]
    last = db.execute(select(CaptionLine.speaker, CaptionLine.at_ms).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq.desc()).limit(1)).first()
    start = max(0, at_ms(itv) - body.dur_ms) if body.dur_ms else None
    gap = start - last[1] if last and last[0] != speaker and start is not None and start >= last[1] else None  # silence after someone else finished
    got = signals.analyze(text, body.dur_ms, body.vol_db, gap, history)
    return {"rate_x10": None if got.rate is None else round(got.rate * 10), "vol_db10": None if body.vol_db is None else round(body.vol_db * 10),
            "gap_ms": gap, "senti": round(got.senti * 100), "tags": ",".join(got.tags)}


def _store_caption(db: Session, itv: Interview, speaker: str, body: CaptionIn) -> None:
    checked = content_rules.check(body.text, itv.language)  # contact details and abusive words never reach the record or the team's screen
    text = checked.text.strip()
    if not text:
        return
    if not body.final:
        _PARTIAL[(itv.id, speaker)] = (text, time.time())
        return
    _PARTIAL.pop((itv.id, speaker), None)
    said = _how_it_was_said(db, itv, speaker, text, body)
    with _LINE_LOCK:  # moderator and participant finish sentences at the same time: keep seq numbers unique
        for _ in range(3):
            seq = (db.scalar(select(func.max(CaptionLine.seq)).where(CaptionLine.interview_id == itv.id)) or 0) + 1
            if seq > MAX_LINES_PER_INTERVIEW:
                raise HTTPException(429, "字幕条数已达上限")
            line = CaptionLine(org_id=itv.org_id, interview_id=itv.id, seq=seq, speaker=speaker, text=text, at_ms=at_ms(itv), dur_ms=body.dur_ms,
                               flags=",".join(sorted(checked.flags)), **said)
            db.add(line)
            try:
                db.commit()
                if itv.language != "zh":  # English interviews: the team reads the Chinese next to the original
                    fut = _TRANSLATOR.submit(_translate_line, line.id)
                    _translations.add(fut)
                    fut.add_done_callback(_translations.discard)
                return
            except IntegrityError:
                db.rollback()
    raise HTTPException(503, "字幕保存冲突，请稍后重试")


@router.post("/interviews/{iid}/captions", status_code=204)
def post_staff_caption(iid: str, body: CaptionIn, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The moderator's browser captions the moderator's own microphone."""
    itv = visible_interview(db, ctx, iid)
    _open(itv)
    _store_caption(db, itv, "moderator", body)


def _entered_participant(db: Session, token: str) -> groups.Joiner:
    j = resolve_join(db, token)
    if j.itv.status != "live" or j.seat.entered_at is None or not _consented(db, j.itv, j.participant.id):
        raise HTTPException(409, "还不能发送字幕")  # no consent to recording / not let in / not live: nothing is captured
    return j


@router.post("/public/join/{token}/captions", status_code=204)
def post_participant_caption(token: str, body: CaptionIn, db: Session = Depends(get_db)):
    """The participant's browser captions the participant's own microphone."""
    j = _entered_participant(db, token)
    _store_caption(db, j.itv, groups.speaker_key(j.itv, j.seat), body)


@router.get("/public/join/{token}/captions")
def get_participant_captions(token: str, after: int = 0, db: Session = Depends(get_db)):
    """The participant's phone screen shows the conversation so far: finished sentences of both speakers (the words only, no hints or
    translations) and what is being said right now. Only for a one-to-one interview, and only once the person is in the room."""
    j = _entered_participant(db, token)
    if groups.is_group(j.itv):
        raise HTTPException(404, "这场访谈没有字幕页")
    rows = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == j.itv.id, CaptionLine.seq > after).order_by(CaptionLine.seq).limit(200)).all()
    now = time.time()
    partial = {sp: txt for (i, sp), (txt, ts) in list(_PARTIAL.items()) if i == j.itv.id and now - ts < PARTIAL_TTL_S}
    return {"lines": [{"seq": c.seq, "speaker": c.speaker, "text": c.text, "at_ms": c.at_ms} for c in rows], "partial": partial}


@router.get("/interviews/{iid}/captions")
def get_captions(iid: str, after: int = 0, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """Polled once a second by the team's room page: finished sentences after `after`, and what is being said right now."""
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id, CaptionLine.seq > after).order_by(CaptionLine.seq).limit(500)).all()
    now = time.time()
    partial = {sp: txt for (i, sp), (txt, ts) in list(_PARTIAL.items()) if i == itv.id and now - ts < PARTIAL_TTL_S}
    fills = []  # the Chinese of sentences the browser already shows: translations arrive a moment after the sentence
    if after > 0:
        fills = [{"id": c.id, "text_zh": c.text_zh} for c in db.scalars(select(CaptionLine).where(
            CaptionLine.interview_id == itv.id, CaptionLine.seq <= after, CaptionLine.seq > after - 15, CaptionLine.text_zh != "")).all()]
    team = ctx.user.role in OPERATE  # how-it-was-said hints go to the operating team only (not to client observers)
    return {"lines": [{"id": c.id, "seq": c.seq, "speaker": c.speaker, "text": c.text, "text_zh": c.text_zh, "at_ms": c.at_ms, "dur_ms": c.dur_ms,
                       **({"hints": signals.labels(c.tags.split(","))} if team and c.tags else {})} for c in rows],
            "partial": partial, "fills": fills}


@router.post("/interviews/{iid}/transcript/from-captions")
def transcript_from_captions(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Make the transcript out of the live captions (they may contain recognition errors; a recording-based transcript replaces them later).

    Refused when insights already quote the old transcript (the same rule as any whole-transcript replacement)."""
    itv = visible_interview(db, ctx, iid)
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    lines = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq)).all()
    if not lines:
        raise HTTPException(409, "这场访谈没有实时字幕")
    segs = []
    for i, c in enumerate(lines):
        dur = c.dur_ms or min(8000, 250 * len(c.text) + 800)  # unknown duration: a plausible one from the length
        segs.append(dict(idx=i, speaker=c.speaker, start_ms=max(0, c.at_ms - dur), end_ms=c.at_ms, text_orig=c.text, text_zh=c.text_zh, lang=itv.language))
    _ingest(db, itv, segs)
    audit(db, ctx.org_id, ctx.actor, "transcript.from_captions", itv.id, n=len(segs), ip=ctx.ip)
    db.commit()
    return {"segments": len(segs)}


@router.get("/interviews/{iid}/asr")
def staff_asr(iid: str, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """A fresh signed address for the moderator's browser to stream its microphone to the speech recogniser (or why captions are off)."""
    return token_out(visible_interview(db, ctx, iid).language)


@router.get("/public/join/{token}/asr")
def participant_asr(token: str, db: Session = Depends(get_db)):
    j = resolve_join(db, token)
    if j.itv.status != "live" or j.seat.entered_at is None or not _consented(db, j.itv, j.participant.id):
        return {"enabled": False, "retry": True, "reason": "访谈开始、你同意录制并被放行后才会开启字幕"}  # temporary: the browser asks again
    return token_out(j.itv.language)


# ---------- the team's private chat (observers <-> moderator <-> researchers) ----------
MAX_BACKROOM = 2000


class BackroomIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


def backroom_out(m: BackroomMessage, ctx: Ctx) -> dict:
    return {"id": m.id, "seq": m.seq, "sender": m.sender, "role": m.role, "text": m.text, "mine": m.user_id == ctx.user.id, "at": m.created_at.isoformat()}


@router.get("/interviews/{iid}/backroom")
def get_backroom(iid: str, after: int = 0, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    rows = db.scalars(select(BackroomMessage).where(BackroomMessage.interview_id == itv.id, BackroomMessage.seq > after).order_by(BackroomMessage.seq).limit(200)).all()
    return {"messages": [backroom_out(m, ctx) for m in rows]}


@router.post("/interviews/{iid}/backroom", status_code=201)
def post_backroom(iid: str, body: BackroomIn, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "消息不能为空")
    with _LINE_LOCK:
        for _ in range(3):
            seq = (db.scalar(select(func.max(BackroomMessage.seq)).where(BackroomMessage.interview_id == itv.id)) or 0) + 1
            if seq > MAX_BACKROOM:
                raise HTTPException(429, "消息条数已达上限")
            m = BackroomMessage(org_id=ctx.org_id, interview_id=itv.id, seq=seq, user_id=ctx.user.id, sender=ctx.user.name, role=ctx.user.role, text=text)
            db.add(m)
            try:
                db.commit()
                return backroom_out(m, ctx)
            except IntegrityError:
                db.rollback()
    raise HTTPException(503, "消息保存冲突，请稍后重试")


# ---------- connection diagnostics: how each browser is connected to the video server ----------
class NetStat(BaseModel):
    transport: Literal["udp", "tcp", "unknown"] = "unknown"
    kind: Literal["host", "srflx", "prflx", "relay", "unknown"] = "unknown"  # relay = through a TURN server
    rtt_ms: int = Field(default=0, ge=0, le=60_000)
    loss_pct: float = Field(default=0.0, ge=0, le=100)
    jitter_ms: int = Field(default=0, ge=0, le=60_000)


_NETSTAT: dict[tuple[str, str], dict] = {}  # latest report per (interview, role); memory only, expires after a minute
_NETSTAT_TTL_S = 60.0


def _store_netstat(itv: Interview, role: str, body: NetStat) -> None:
    rec = {**body.model_dump(), "at": time.time()}
    first = (itv.id, role) not in _NETSTAT
    _NETSTAT[(itv.id, role)] = rec
    if first or body.loss_pct >= 5 or body.rtt_ms >= 300 or body.transport == "tcp":  # a baseline per session, then only what looks wrong
        log.warning("netstat interview=%s role=%s transport=%s/%s rtt=%dms loss=%.1f%% jitter=%dms", itv.id[:8], role, body.transport, body.kind, body.rtt_ms, body.loss_pct, body.jitter_ms)


@router.post("/interviews/{iid}/netstat", status_code=204)
def post_staff_netstat(iid: str, body: NetStat, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    _store_netstat(itv, "observer" if ctx.user.role == "observer" else "moderator", body)


@router.post("/public/join/{token}/netstat", status_code=204)
def post_participant_netstat(token: str, body: NetStat, db: Session = Depends(get_db)):
    j = resolve_join(db, token)
    if j.itv.status == "live" and j.seat.entered_at is not None:
        _store_netstat(j.itv, groups.speaker_key(j.itv, j.seat), body)


@router.get("/interviews/{iid}/netstat")
def get_netstat(iid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    """The latest connection report of the moderator and of each participant (what the room header shows to the team).

    A depth interview reports ``participant``. A focus group reports ``p1`` … ``p10``, one line per seat that has checked in.
    """
    itv = visible_interview(db, ctx, iid)
    now = time.time()
    out = {}
    for role in groups.NET_ROLES:
        rec = _NETSTAT.get((itv.id, role))
        if rec and now - rec["at"] < _NETSTAT_TTL_S:
            out[role] = {k: v for k, v in rec.items() if k != "at"}
    return out


# ---------- quotes and follow-up questions ----------
class HighlightIn(BaseModel):
    kind: Literal["quote", "probe", "mark"]
    caption_id: str | None = None  # the caption line being quoted / followed up (its text is copied by the server)
    text: str | None = Field(default=None, max_length=300)  # a typed follow-up question when there is no caption line
    caption_ids: list[str] = Field(default_factory=list, max_length=MAX_QUOTES)  # several caption lines the follow-up is about (probe only); the guidance goes in `text`
    depth: Literal["shallow", "deep"] | None = None


def highlight_out(h: Highlight, ctx: Ctx, by: str = "") -> dict:
    return {"id": h.id, "kind": h.kind, "speaker": h.speaker, "text": h.text, "caption_id": h.caption_id, "stimulus_key": h.stimulus_key,
            "at_ms": h.at_ms, "status": h.status, "created_at": h.created_at.isoformat(), "mine": h.created_by == ctx.user.id,
            "quotes": json.loads(h.quotes_json) if h.quotes_json else [], "depth": h.depth or "", "by": by}


def _by_labels(db: Session, hs: list[Highlight]) -> dict[str, str]:
    """Who proposed each follow-up, by role only (never a name): the team and the client's observers all see these."""
    ids = {h.created_by for h in hs if h.created_by}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {h.id: PROPOSER_LABEL.get(users[h.created_by].role, "团队") for h in hs if h.created_by in users}


@router.post("/interviews/{iid}/highlights", status_code=201)
def create_highlight(iid: str, body: HighlightIn, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.purged_at:
        raise HTTPException(404, "访谈不存在")
    if body.kind == "mark":
        _open(itv)
        if ctx.user.role == "recruiter":
            raise HTTPException(403, "项目经理不能查看访谈内容")
        if body.caption_id or body.caption_ids or body.depth or (body.text or "").strip() not in ("高光", "痛点", "金句", "存疑"):
            raise HTTPException(422, "请选择有效打点类型，打点不接受字幕或追问内容")
    speaker, text, cap_id, when = "", (body.text or "").strip(), None, at_ms(itv)
    quotes: list[dict] = []
    if body.caption_ids:
        if body.kind != "probe":
            raise HTTPException(422, "只有追问可以引用多句")
        ids = list(dict.fromkeys(body.caption_ids))
        lines = {x.id: x for x in db.scalars(select(CaptionLine).where(CaptionLine.id.in_(ids), CaptionLine.interview_id == itv.id)).all()}
        if len(lines) != len(ids):
            raise HTTPException(404, "没有这条字幕")
        quotes = [{"id": x.id, "speaker": x.speaker, "text": x.text, "at_ms": x.at_ms} for x in sorted(lines.values(), key=lambda x: x.at_ms)]  # verbatim, from the stored lines
        when = quotes[0]["at_ms"]
    elif body.caption_id:
        line = db.get(CaptionLine, body.caption_id)
        if not line or line.interview_id != itv.id:
            raise HTTPException(404, "没有这条字幕")
        speaker, text, cap_id, when = line.speaker, line.text, line.id, line.at_ms  # verbatim, from the stored line
    if not body.caption_ids and not body.caption_id and (body.kind == "quote" or not text):
        raise HTTPException(422, "引用必须来自一条字幕；追问需要一句话")
    stim = concept.current_stimulus(db, itv.id, staff=True)
    h = Highlight(org_id=ctx.org_id, interview_id=itv.id, kind=body.kind, speaker=speaker, text=text, caption_id=cap_id,
                  stimulus_key=(stim or {}).get("key", ""), at_ms=when, created_by=ctx.user.id,
                  quotes_json=json.dumps(quotes, ensure_ascii=False) if quotes else "", depth=body.depth or "")
    db.add(h)
    audit(db, ctx.org_id, ctx.actor, f"highlight.{body.kind}", itv.id, ip=ctx.ip)
    db.commit()
    return highlight_out(h, ctx)


@router.get("/interviews/{iid}/highlights")
def list_highlights(iid: str, kind: str | None = None, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    q = select(Highlight).where(Highlight.interview_id == itv.id)
    if kind in ("quote", "probe", "mark"):
        q = q.where(Highlight.kind == kind)
    rows = db.scalars(q.order_by(Highlight.created_at)).all()
    by = _by_labels(db, rows)
    return [highlight_out(h, ctx, by.get(h.id, "")) for h in rows]


def _highlight(db: Session, ctx: Ctx, hid: str) -> Highlight:
    h = db.get(Highlight, hid)
    if not h or h.org_id != ctx.org_id:
        raise HTTPException(404, "没有这条记录")
    visible_interview(db, ctx, h.interview_id)  # an observer / moderator only reaches highlights of interviews they may see
    return h


class HighlightPatch(BaseModel):
    status: Literal["open", "done"]


@router.patch("/highlights/{hid}")
def patch_highlight(hid: str, body: HighlightPatch, ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """The moderator marks a queued follow-up as asked."""
    h = _highlight(db, ctx, hid)
    h.status = body.status
    db.commit()
    return highlight_out(h, ctx)


@router.delete("/highlights/{hid}", status_code=204)
def delete_highlight(hid: str, ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    h = _highlight(db, ctx, hid)
    if ctx.user.role not in OPERATE and h.created_by != ctx.user.id:
        raise HTTPException(403, "只能删除自己标记的")
    db.delete(h)
    db.commit()
