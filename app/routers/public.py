"""Unauthenticated endpoints: participant join flow, share-link viewing, media, LiveKit webhook."""
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import concept, groups, pretask, scheduling, mailer
from ..audit import audit
from ..config import settings
from ..consent import consent_for
from ..moderator.compliance import ai_moderation_blocker
from ..moderator.constants import MSG_AI_INTERVIEW_NOT_READY, MSG_EVERY_CONSENT_NEEDED, ModerationMode
from ..db import get_db
from ..deps import client_ip
from ..livekit import build_room_token, url_for_client, verify_webhook
from ..media import cut_clip
from ..models import Clip, Consent, Evidence, Insight, Interview, Org, Participant, Project, Recording, ShareLink, utcnow
from ..recording import elapsed_ms, save_chunk
from ..retention import days_of
from ..security import sign_media, token_hash, verify_media

router = APIRouter(prefix="/api/public", tags=["public"])


def resolve_join(db: Session, token: str):
    """The interview and the seat this invite link belongs to (the interview itself for a depth interview)."""
    j = groups.resolve_joiner(db, token)
    if not j:
        raise HTTPException(404, "Invalid invitation link / 邀请链接无效")
    return j


def _by_invite(db: Session, token: str) -> Interview:
    """The interview for this invite link. Callers that read the journey (admit, device, entry) use :func:`resolve_join`."""
    return resolve_join(db, token).itv


def _window_ok(itv: Interview) -> bool:
    if itv.status == "ended":
        return False
    if itv.scheduled_at is None:
        return True
    now = utcnow()
    return itv.scheduled_at - timedelta(minutes=settings.join_early_min) <= now <= itv.scheduled_at + timedelta(minutes=settings.join_late_min)


@router.get("/config")
def public_config():
    from ..moderator.compliance import moderator_runs_locally  # where the AI writing tools run: the wizard tells people whether their text leaves our servers
    local = moderator_runs_locally()
    return {"allowed_countries": sorted(settings.allowed_countries), "languages": list(settings.languages), "asr": settings.asr_provider, "llm": settings.llm_provider, "video": bool(settings.livekit_url),
            "ai_local": local, "ai_vendor": "" if local else settings.moderator_vendor.strip(), "mail_ready": mailer.configured()}


def _days(db: Session, itv) -> int:
    """The retention period the project chose: the consent text names it."""
    return days_of(db.get(Project, itv.project_id))


@router.get("/join/{token}")
def join_info(token: str, db: Session = Depends(get_db)):
    j = resolve_join(db, token)
    itv, seat = j.itv, j.seat
    org = db.get(Org, itv.org_id)
    consent = db.scalar(select(Consent).where(Consent.interview_id == itv.id, Consent.participant_id == j.participant.id))
    return {"org": org.name, "scheduled_at": itv.scheduled_at.isoformat() if itv.scheduled_at else None,
            "status": itv.status, "can_enter_now": _window_ok(itv), "consented": consent is not None,
            "device_ok": seat.device_ok_at is not None, "async": itv.kind == "async",
            "consent": consent_for(itv.language, org.name, ai=itv.moderation == ModerationMode.AI.value, text_only=itv.kind == "async", retention_days=_days(db, itv)),
            "pretask": pretask.participant_view(db, itv.id, j.participant.id), "timeslots": scheduling.public_view(db, itv, j.participant.id)}


@router.post("/join/{token}/opened", status_code=204)
def mark_opened(token: str, request: Request, db: Session = Depends(get_db)):
    """The invitation page was really opened by a person (a POST, so link previews in chat apps do not count)."""
    j = resolve_join(db, token)
    if not j.seat.opened_at:
        j.seat.opened_at = utcnow()
        audit(db, j.itv.org_id, f"participant:{j.participant.id}", "invite.open", j.itv.id, ip=client_ip(request))
        db.commit()


class DeviceBody(BaseModel):
    camera: bool
    microphone: bool
    speaker: bool


@router.post("/join/{token}/device")
def device_check(token: str, body: DeviceBody, request: Request, db: Session = Depends(get_db)):
    """Result of the participant's device test. A working microphone is enough to take part (the camera may stay off)."""
    j = resolve_join(db, token)
    passed = body.microphone
    if passed and not j.seat.device_ok_at:
        j.seat.device_ok_at = utcnow()
    audit(db, j.itv.org_id, f"participant:{j.participant.id}", "device.check", j.itv.id, ip=client_ip(request),
          camera=body.camera, microphone=body.microphone, speaker=body.speaker)
    db.commit()
    return {"passed": passed}


class ConsentBody(BaseModel):
    country: str
    recording: bool
    observers: bool
    cross_border: bool
    ai: bool = False  # "the interviewer is an AI": required when the interview is AI-moderated


@router.post("/join/{token}/consent", status_code=201)
def give_consent(token: str, body: ConsentBody, request: Request, db: Session = Depends(get_db)):
    j = resolve_join(db, token)
    itv, part = j.itv, j.participant
    country = body.country.strip().upper()
    if len(country) != 2:
        raise HTTPException(422, "Please select your country or region / 请选择你所在的国家或地区")
    if not settings.region_open(country):
        part.screening = "blocked_region"
        audit(db, itv.org_id, f"participant:{part.id}", "consent.blocked_region", itv.id, ip=client_ip(request), country=country)
        db.commit()
        raise HTTPException(403, "Sorry, this study is not open in your region yet / 抱歉，这项研究暂时不面向你所在的地区")
    ai = itv.moderation == ModerationMode.AI.value
    if not (body.recording and body.observers and body.cross_border and (body.ai or not ai)):
        raise HTTPException(422, MSG_EVERY_CONSENT_NEEDED)
    if ai and ai_moderation_blocker():  # never let anyone agree to a text that cannot name who receives their words
        raise HTTPException(409, MSG_AI_INTERVIEW_NOT_READY)
    org = db.get(Org, itv.org_id)
    c = consent_for(itv.language, org.name, ai=ai, text_only=itv.kind == "async", retention_days=_days(db, itv))
    part.country = country
    db.add(Consent(org_id=itv.org_id, interview_id=itv.id, participant_id=part.id, version=c["version"], text_sha256=c["sha256"],
                   recording=True, observers=True, cross_border=True, ai_moderation=ai, country=country, ip=client_ip(request)))
    audit(db, itv.org_id, f"participant:{part.id}", "consent.give", itv.id, ip=client_ip(request), version=c["version"])
    db.commit()
    return {"ok": True}


class SlotBody(BaseModel):
    slot_id: str | None = None          # a depth interview: the one time the participant chooses
    slot_ids: list[str] = []            # a focus group: every time that suits them


@router.post("/join/{token}/slot")
def pick_slot(token: str, body: SlotBody, request: Request, db: Session = Depends(get_db)):
    """A depth interview: the participant chooses one of the candidate times and it becomes the session's time.
    A focus group: the participant marks every time that suits them; the team then confirms one."""
    j = resolve_join(db, token)
    itv = j.itv
    if itv.status == "ended":
        raise HTTPException(409, "The interview has ended / 访谈已结束")
    try:
        if itv.kind == "group":
            scheduling.vote(db, itv, j.participant.id, body.slot_ids)
        else:
            scheduling.pick(db, itv, body.slot_id or "")
    except ValueError:
        raise HTTPException(409, "That time is no longer available / 该时间已不可选")
    audit(db, itv.org_id, f"participant:{j.participant.id}", "timeslot.vote" if itv.kind == "group" else "timeslot.pick", itv.id, ip=client_ip(request))
    db.commit()
    return {"voted": True} if itv.kind == "group" else {"scheduled_at": itv.scheduled_at.isoformat()}


class PreTaskBody(BaseModel):
    answers: dict[str, str]


@router.post("/join/{token}/pretask", status_code=201)
def submit_pretask(token: str, body: PreTaskBody, request: Request, db: Session = Depends(get_db)):
    """The participant's answers to the pre-interview questions (after consent, before the waiting room)."""
    j = resolve_join(db, token)
    itv, part = j.itv, j.participant
    if not db.scalar(select(Consent).where(Consent.interview_id == itv.id, Consent.participant_id == part.id)):
        raise HTTPException(403, "Please read and agree first / 请先阅读并同意")
    pt = pretask.get(db, itv.id)
    if not pt:
        raise HTTPException(404, "No pre-interview task / 没有访谈前问题")
    try:
        pretask.save_answers(db, pt, part.id, body.answers)
    except ValueError:
        raise HTTPException(422, "Please answer every required question / 请回答所有必答问题")
    audit(db, itv.org_id, f"participant:{part.id}", "pretask.submit", itv.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/join/{token}/enter")
def enter(token: str, request: Request, db: Session = Depends(get_db)):
    j = resolve_join(db, token)
    itv, seat, part = j.itv, j.seat, j.participant
    if part.screening != "eligible":
        raise HTTPException(403, "Not open in your region yet / 暂时不面向你所在的地区")
    if scheduling.needs_pick(db, itv):
        raise HTTPException(409, "The team will confirm the time soon / 研究团队会尽快确认时间" if itv.kind == "group" else "Please choose an interview time first / 请先选择访谈时间")
    if not db.scalar(select(Consent).where(Consent.interview_id == itv.id, Consent.participant_id == part.id)):
        raise HTTPException(403, "Please read and agree first / 请先阅读并同意")
    if not pretask.answered(db, itv.id, part.id):
        raise HTTPException(403, "Please answer the pre-interview questions first / 请先完成访谈前的问题")
    if not _window_ok(itv):
        raise HTTPException(409, "Not time yet, or the interview has ended / 还没到访谈时间，或访谈已结束")
    if not seat.device_ok_at:
        raise HTTPException(403, "Please test your device first / 请先完成设备检测")
    # real name never goes into the video room; a group seat is "P-01" and the browser translates it
    shown_name = f"P-{seat.seat:02d}" if groups.is_group(itv) else "Participant"
    jwt_ = build_room_token(identity=f"participant:{part.id}", name=shown_name, room=itv.room_name, role="participant",
                            seat=seat.seat if groups.is_group(itv) else None)
    if not seat.entered_at:
        seat.entered_at = utcnow()
    seat.admitted_at = seat.admitted_at or seat.entered_at  # kept for what still reads it: "has been in the room"; nobody lets anyone in any more
    audit(db, itv.org_id, f"participant:{part.id}", "room.token", itv.id, ip=client_ip(request), role="participant")
    db.commit()
    return {"token": jwt_, "url": url_for_client(request.headers.get("host")), "room": itv.room_name, "role": "participant", "dev_mode": not settings.livekit_url,
            "interview_status": itv.status, "language": itv.language, "moderation": itv.moderation}


def _consented(db: Session, itv: Interview, participant_id: str | None = None) -> bool:
    """True when this participant agreed to be recorded. Defaults to the depth interview's single participant."""
    pid = participant_id if participant_id is not None else itv.participant_id
    return db.scalar(select(Consent.id).where(Consent.interview_id == itv.id, Consent.participant_id == pid, Consent.recording.is_(True))) is not None


@router.get("/join/{token}/live")
def live(token: str, waiting: int = 0, db: Session = Depends(get_db)):
    """Polled by the participant's waiting room (waiting=1, so the moderator sees who is waiting) and room page:
    has the interview started / ended, has the moderator let them in, and is it being recorded right now."""
    j = resolve_join(db, token)
    itv, seat = j.itv, j.seat
    if waiting and itv.status != "ended":
        seat.waiting_seen_at = utcnow()
        db.commit()
    rec = db.scalar(select(Recording).where(Recording.interview_id == itv.id, Recording.status == "recording")) if _consented(db, itv, j.participant.id) else None
    shown = concept.current_stimulus(db, itv.id, staff=False) if itv.status == "live" and seat.entered_at is not None else None  # only someone who is in the room sees the material
    return {"status": itv.status, "admitted": True,
            "recording": {"id": rec.id, "elapsed_ms": elapsed_ms(rec)} if rec else None, "stimulus": shown}


@router.post("/join/{token}/leave", status_code=204)
def leave(token: str, request: Request, db: Session = Depends(get_db)):
    """The participant pressed Leave. Coming back needs nobody's permission: the link works again until the interview ends."""
    j = resolve_join(db, token)
    j.seat.waiting_seen_at = None
    audit(db, j.itv.org_id, f"participant:{j.participant.id}", "room.leave", j.itv.id, ip=client_ip(request))
    db.commit()


_EXT = {"video/webm": "webm", "audio/webm": "webm", "video/mp4": "mp4", "audio/mp4": "mp4", "audio/ogg": "ogg"}


@router.post("/join/{token}/recording/{rid}/chunk", status_code=204)
async def upload_chunk(token: str, rid: str, request: Request, seq: int, offset_ms: int = 0, last: int = 0, part: str = "0", db: Session = Depends(get_db)):
    """The participant's browser uploads its own camera+mic recording. Only accepted after consent, and only for the live recording."""
    j = resolve_join(db, token)
    itv = j.itv
    rec = db.get(Recording, rid)
    if not _consented(db, itv, j.participant.id) or not rec or rec.interview_id != itv.id:
        raise HTTPException(404, "not found")
    if rec.status not in ("recording", "processing"):
        raise HTTPException(409, "recording finished")
    role = groups.speaker_key(itv, j.seat)  # depth: "participant"; a group seat: "p1"… so each person is their own track
    db.commit()  # give the pooled connection back BEFORE waiting for the (possibly slow, overseas) upload; save_chunk takes one again
    data = await request.body()
    if len(data) > settings.max_chunk_bytes or seq < 0 or seq > 20_000:
        raise HTTPException(413, "chunk too large")
    ext = _EXT.get(request.headers.get("content-type", "").split(";")[0].strip(), "webm")
    await run_in_threadpool(save_chunk, db, rec, role, part, seq, offset_ms, ext, data, bool(last))


# ---------- share links ----------
def _share(db: Session, token: str) -> ShareLink:
    sl = db.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if not sl or sl.revoked_at or sl.expires_at < utcnow():
        raise HTTPException(404, "链接无效或已过期")
    return sl


@router.get("/share/{token}")
def view_share(token: str, request: Request, db: Session = Depends(get_db)):
    sl = _share(db, token)
    clip = db.get(Clip, sl.clip_id)
    rec = db.scalar(select(Recording).where(Recording.interview_id == clip.interview_id, Recording.status == "ready"))
    ev = db.get(Evidence, clip.evidence_id) if clip.evidence_id else None
    ins = db.get(Insight, ev.insight_id) if ev else None
    sl.views += 1
    audit(db, sl.org_id, f"share:{sl.id}", "share.view", clip.id, ip=client_ip(request))
    db.commit()
    playback = None
    if rec:  # short-lived signed URL scoped to this clip's time range
        playback = {"url": "/api/public/media?t=" + sign_media(rec.storage_key, clip.start_ms, clip.end_ms, watermark=sl.watermark),
                    "start_ms": clip.start_ms, "end_ms": clip.end_ms, "expires_in": settings.media_url_ttl_s, "download": False}
    return {"title": clip.title, "watermark": sl.watermark, "expires_at": sl.expires_at.isoformat(),
            "quote": ev.quote if ev and ins and ins.status == "approved" else None,
            "theme": ins.theme if ins and ins.status == "approved" else None, "playback": playback}


@router.get("/media")
async def media(t: str):
    p = verify_media(t)
    if not p:
        raise HTTPException(403, "链接已失效")
    root = os.path.realpath(settings.media_dir)
    path = os.path.realpath(os.path.join(root, p["k"]))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise HTTPException(404, "媒体文件还没有就绪")
    hdr = {"Content-Disposition": "inline", "Cache-Control": "no-store"}
    if p["s"] < 0:  # internal full-recording view (staff only issued this link)
        return FileResponse(path, media_type="video/mp4", headers=hdr)
    try:  # everyone else only ever gets the short cut with the watermark burned in
        cut = await run_in_threadpool(cut_clip, path, os.path.basename(path).rsplit(".", 1)[0], p["s"], p["e"], p.get("w") or "星桐")
    except Exception:
        raise HTTPException(503, "片段正在处理，请稍后刷新")
    return FileResponse(cut, media_type="video/mp4", headers=hdr)


# ---------- LiveKit webhook ----------
@router.post("/livekit/webhook")
async def livekit_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if not verify_webhook(request.headers.get("authorization", ""), body):
        raise HTTPException(401, "签名无效")
    import json

    ev = json.loads(body or b"{}")
    kind = ev.get("event")
    if kind == "egress_ended":
        info = ev.get("egressInfo", {})
        rec = db.scalar(select(Recording).where(Recording.egress_id == info.get("egressId", "")))
        if rec:
            files = info.get("fileResults") or info.get("file_results") or []
            if files and files[0].get("duration"):
                rec.duration_ms = int(int(files[0]["duration"]) / 1_000_000)  # LiveKit reports nanoseconds
            rec.status = "ready" if info.get("status") in (None, "EGRESS_COMPLETE") else "deleted"
            audit(db, rec.org_id, "livekit", "recording.ready", rec.interview_id, status=rec.status)
    elif kind == "room_finished":
        itv = db.scalar(select(Interview).where(Interview.room_name == ev.get("room", {}).get("name", "")))
        if itv and itv.status == "live":
            itv.status, itv.ended_at = "ended", utcnow()
            audit(db, itv.org_id, "livekit", "interview.end", itv.id)
    db.commit()
    return {"ok": True}
