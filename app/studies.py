"""Studies that participants complete on their own, any time, in a text conversation with the AI moderator.

Researcher: sets the goal, the AI drafts the outline, the researcher edits it and publishes a numbered, frozen version, then invites participants
(each gets a personal link, like a depth interview). Participant: consents, opens the link when it suits them, talks to the AI, can leave and
come back (the AI is saved after every turn and carries on). When the AI finishes, the chat becomes a normal transcript and everything downstream
(insights, clips, coding, signals, export) works as for any interview.
"""
import json
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ai_room, guide_text
from .audit import audit
from .models import AiSession, AsyncStudy, Consent, Interview, Participant, utcnow
from .moderator.compliance import ai_moderation_blocker
from .moderator.constants import ModerationMode
from .moderator.knowledge import InterviewGuide
from .security import new_secret_token, token_hash
from .study_draft import _red_line_problem

log = logging.getLogger(__name__)

KIND = "async"
DURATIONS = (10, 15, 20, 30, 45, 60)


class StudyError(Exception):
    """Something cannot be done now; `status` is the HTTP status and `message` plain Chinese for the researcher or the participant."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def guide_of(study: AsyncStudy) -> dict:
    """The guide the outline text describes, with the study's own language and length (what is published is exactly this).

    Raises:
        StudyError: the outline is missing or invalid, or one of its prepared questions breaks a red line.
    """
    if not study.outline_text.strip():
        raise StudyError(422, "还没有提纲：请先让 AI 起草，或自己写")
    try:
        g = guide_text.parse_guide_text(study.outline_text)
    except guide_text.GuideTextError as e:
        raise StudyError(422, str(e)) from e
    g["language"], g["duration_min"] = study.language, float(study.duration_min)
    g["participant_topic"] = study.participant_topic or g.get("participant_topic", "")
    try:
        guide = InterviewGuide.model_validate(g)
    except ValueError as e:
        raise StudyError(422, f"提纲不合格：{str(e)[:200]}") from e
    if problem := _red_line_problem(guide):
        raise StudyError(422, f"提纲里有问题没通过安全检查，请改写后再发布：{problem}")
    return guide.model_dump(mode="json")


def publish(db: Session, study: AsyncStudy, actor: str) -> AsyncStudy:
    """Freeze the current outline as the next version.

    Raises:
        StudyError: the study is closed, the AI interview cannot run with the current settings, or the outline does not pass.
    """
    if study.status == "closed":
        raise StudyError(409, "这个研究已经关闭")
    if why := ai_moderation_blocker():
        raise StudyError(422, why)
    guide = guide_of(study)
    study.published_guide = json.dumps(guide, ensure_ascii=False)
    study.version += 1
    study.status, study.published_at = "published", utcnow()
    audit(db, study.org_id, actor, "study.publish", study.id, version=study.version)
    return study


def invite(db: Session, study: AsyncStudy, participant: Participant, actor: str) -> tuple[Interview, str]:
    """One personal session for a participant of the project; returns it with the link token (shown once).

    Raises:
        StudyError: the study is not published, or this participant already has a session in it.
    """
    if study.status != "published":
        raise StudyError(409, "请先发布研究，再邀请受访者")
    if db.scalar(select(Interview.id).where(Interview.study_id == study.id, Interview.participant_id == participant.id)):
        raise StudyError(409, f"{participant.name} 已经被邀请过了")
    token = new_secret_token()
    itv = Interview(org_id=study.org_id, project_id=study.project_id, participant_id=participant.id, title=study.title, language=study.language,
                    invite_hash=token_hash(token), moderation=ModerationMode.AI.value, kind=KIND, study_id=study.id)
    db.add(itv)
    db.flush()
    audit(db, study.org_id, actor, "study.invite", itv.id, study=study.id)
    return itv, token


def begin(db: Session, itv: Interview) -> None:
    """The participant opens their session: the first time the AI starts (its fixed opening discloses that it is an AI), later the same session carries on.

    Raises:
        StudyError: not an async session, no consent yet, the study was closed, or the session is over.
    """
    study = db.get(AsyncStudy, itv.study_id) if itv.study_id else None
    if itv.kind != KIND or study is None:
        raise StudyError(404, "Invalid link / 链接无效")
    if itv.status == "ended":
        raise StudyError(409, "This study is over, thank you / 这次访谈已经结束，谢谢")
    if study.status != "published":
        raise StudyError(409, "This study is closed / 这项研究已经关闭")
    if itv.participant.screening != "eligible":
        raise StudyError(403, "Not open in your region yet / 暂时不面向你所在的地区")
    if not db.scalar(select(Consent.id).where(Consent.interview_id == itv.id, Consent.participant_id == itv.participant_id)):
        raise StudyError(403, "Please read and agree first / 请先阅读并同意")
    now = utcnow()
    if db.get(AiSession, itv.id) is None:
        itv.status, itv.started_at = "live", now
        try:
            ai_room.start(db, itv, json.loads(study.published_guide), f"participant:{itv.participant_id}")
        except ai_room.AiRoomError as e:
            raise StudyError(e.status, e.message) from e
    itv.entered_at = itv.entered_at or now
    itv.admitted_at = now  # the chat endpoints let a participant in who has been admitted and has entered


def close(db: Session, study: AsyncStudy, actor: str) -> int:
    """Close the study: no new sessions; unfinished ones end now and keep what was said. Returns how many were still open."""
    study.status, study.closed_at = "closed", utcnow()
    open_ones = db.scalars(select(Interview).where(Interview.study_id == study.id, Interview.status != "ended")).all()
    for itv in open_ones:
        ai_room.finalize(db, itv)
        itv.status, itv.ended_at = "ended", utcnow()
    audit(db, study.org_id, actor, "study.close", study.id, open=len(open_ones))
    return len(open_ones)


def session_state(itv: Interview, sess: AiSession | None) -> str:
    """Where one participant is: sent | opened | in_progress | finished | ended_early (closed before they finished)."""
    if itv.status == "ended":
        return "finished" if sess is not None and sess.finished else "ended_early"
    if sess is not None:
        return "in_progress"
    return "opened" if itv.opened_at else "sent"


# ---------- one public sign-up link per study ----------
MAX_CAP = 1000
DEFAULT_CAP = 100
SELF_SIGNUP_ACTOR = "participant:signup"


def set_open_link(db: Session, study: AsyncStudy, enabled: bool, cap: int, actor: str) -> str | None:
    """Turn the public sign-up link on (a new link each time: the old one stops working) or off. Returns the new link token, shown once.

    Raises:
        StudyError: the study is not published, or the cap is out of range.
    """
    if enabled and study.status != "published":
        raise StudyError(409, "请先发布研究，再开启公开链接")
    if not 1 <= cap <= MAX_CAP:
        raise StudyError(422, f"人数上限要在 1 到 {MAX_CAP} 之间")
    token = new_secret_token() if enabled else None
    study.open_hash, study.open_cap = (token_hash(token) if token else None), cap
    audit(db, study.org_id, actor, "study.open_link_on" if enabled else "study.open_link_off", study.id, cap=cap)
    return token


def study_by_open_token(db: Session, token: str) -> AsyncStudy | None:
    """The study a public sign-up link belongs to (None for a wrong, rotated or switched-off link)."""
    return db.scalar(select(AsyncStudy).where(AsyncStudy.open_hash == token_hash(token), AsyncStudy.open_hash.isnot(None)))


def session_count(db: Session, study_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Interview).where(Interview.study_id == study_id)) or 0


def open_state(db: Session, study: AsyncStudy) -> dict:
    """Whether the public link is on and has room (the participant page shows 'full' otherwise)."""
    on = bool(study.open_hash) and study.status == "published"
    return {"enabled": on, "cap": study.open_cap, "sessions": session_count(db, study.id), "full": on and session_count(db, study.id) >= study.open_cap}


def sign_up(db: Session, study: AsyncStudy, nickname: str) -> tuple[Interview, str]:
    """Someone opened the public link: make a participant (only the nickname they chose, or a number) and their personal session.

    Raises:
        StudyError: the link is off, the study is closed, or the study is full.
    """
    state = open_state(db, study)
    if not state["enabled"]:
        raise StudyError(404, "Invalid link / 链接无效")
    if state["full"]:
        raise StudyError(409, "This study is full, thank you for your interest / 名额已满，感谢你的关注")
    n = state["sessions"] + 1
    part = Participant(org_id=study.org_id, project_id=study.project_id, name=(nickname.strip()[:40] or f"自助受访者 {n}"), language=study.language)
    db.add(part)
    db.flush()
    return invite(db, study, part, SELF_SIGNUP_ACTOR)


def progress(db: Session, study: AsyncStudy) -> dict:
    """Numbers for the researcher: how many started, finished, ended early, and how long finished conversations took."""
    rows = db.execute(select(Interview, AiSession).outerjoin(AiSession, AiSession.interview_id == Interview.id).where(Interview.study_id == study.id)).all()
    states = [session_state(i, s) for i, s in rows]
    minutes = sorted((i.ended_at - i.started_at).total_seconds() / 60 for i, s in rows if s is not None and s.finished and i.started_at and i.ended_at)
    started = len(rows) - states.count("sent") - states.count("opened")
    return {"invited": len(rows), "started": started, "finished": states.count("finished"), "ended_early": states.count("ended_early"), "in_progress": states.count("in_progress"),
            "completion_rate": round(states.count("finished") / started, 2) if started else None, "median_minutes": round(minutes[len(minutes) // 2], 1) if minutes else None}
