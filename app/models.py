import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


_last_now = datetime.min
_now_lock = threading.Lock()


def utcnow() -> datetime:
    """The current UTC time, strictly increasing within this process.

    The Windows clock ticks every ~15 ms, so two events in the same tick got the same timestamp and lists sorted by time showed them
    in a random order (the activity feed, the chat). Two calls never return the same value now: the later one is at least 1 µs later."""
    global _last_now
    with _now_lock:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now <= _last_now:
            now = _last_now + timedelta(microseconds=1)
        _last_now = now
        return now


def uid() -> str:
    return uuid.uuid4().hex


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))  # admin | researcher | moderator | observer | recruiter (项目经理) | viewer (客户) | support (技术支持)
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    research_kind: Mapped[str] = mapped_column(String(10), default="all")  # researchers only: quant (定量) | qual (定性) | all (全科); decides the default ticks in the plan
    brand_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # clients only: the brand they speak for (Brand)
    job_title: Mapped[str] = mapped_column(String(100), default="")  # clients: 职位
    phone: Mapped[str] = mapped_column(String(60), default="")  # clients: 联系方式


class Brand(Base):
    """A client's brand: a client (role viewer) is added under one."""
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_brand_org_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text, default="")
    market: Mapped[str] = mapped_column(String(2), default="")  # ISO-3166 alpha-2 of target market
    language: Mapped[str] = mapped_column(String(10), default="en")  # interview language
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # retention clock starts here
    purge_hold_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # an admin kept the data longer: nothing is purged before this date
    methods: Mapped[str] = mapped_column(String(100), default="depth,group,async,listening")  # the research methods this project uses (app/methods.py), comma separated
    retention_days: Mapped[int] = mapped_column(Integer, default=180)  # chosen in the plan (30/60/120/180, never above the platform's); the consent text names it
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), default="")
    country: Mapped[str] = mapped_column(String(2), default="")
    language: Mapped[str] = mapped_column(String(10), default="")
    screening: Mapped[str] = mapped_column(String(20), default="eligible")  # eligible | blocked_region (= region not open)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # the id in an outside survey tool (app/quant.py)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # free labels for filtering and quotas (app/recruit.py)
    screen_result: Mapped[str] = mapped_column(String(14), default="")  # "" (rules not run) | qualified | disqualified | review
    screen_reason: Mapped[str] = mapped_column(String(300), default="")
    screen_version: Mapped[int] = mapped_column(Integer, default=0)  # the version of the project's rules that produced the result
    source: Mapped[str] = mapped_column(String(12), default="")  # how the person came in: link | import | manual | platform | pool | "" (not recorded)
    source_name: Mapped[str] = mapped_column(String(120), default="")  # for an imported list: its name and the day, e.g. "客户 A 名单 · 10-23"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    moderator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(10), default="en")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled | live | ended
    room_name: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: "r_" + uuid.uuid4().hex)
    invite_hash: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # participant journey, shown to staff as the invitation state: sent -> opened -> device_ok -> consented -> entered -> done
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # waiting room: the moderator lets the participant in (admit) or asks them to wait (hold); leaving needs a new admit
    waiting_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    moderation: Mapped[str] = mapped_column(String(10), default="human")  # app.moderator.constants.ModerationMode; "ai": the consent text discloses it
    kind: Mapped[str] = mapped_column(String(10), default="depth")  # depth (1:1) | group (focus group, up to 10 participants; see app/groups.py) | async (a study done alone, any time; see app/studies.py)
    study_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # kind "async": the study this session belongs to
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    participant: Mapped[Participant] = relationship(lazy="joined")
    observers: Mapped[list["InterviewObserver"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    group_participants: Mapped[list["InterviewParticipant"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AsyncStudy(Base):
    """A study that participants complete on their own, at any time, in a text conversation with the AI moderator (app/studies.py).

    The researcher writes the goal, the AI drafts the outline, the researcher edits and publishes it: publishing freezes a numbered version
    (`published_guide`) that every session started afterwards follows word for word, so all participants are asked the same prepared questions.
    """
    __tablename__ = "async_studies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)  # internal research goal: never read out to participants
    participant_topic: Mapped[str] = mapped_column(String(20), default="")  # what the AI tells participants the topic is
    language: Mapped[str] = mapped_column(String(10), default="en")
    duration_min: Mapped[int] = mapped_column(Integer, default=20)
    outline_text: Mapped[str] = mapped_column(Text, default="")  # the editable outline (app/guide_text.py format)
    drafted_by: Mapped[str] = mapped_column(String(60), default="")  # which model wrote the draft (empty: written by hand)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)  # draft | published | closed
    version: Mapped[int] = mapped_column(Integer, default=0)  # 0 until first published; +1 at every publish
    published_guide: Mapped[str] = mapped_column(Text, default="")  # the frozen guide (JSON) of the current version
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    open_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # one public sign-up link for the study (only its hash is kept); None = no public link
    open_cap: Mapped[int] = mapped_column(Integer, default=100)  # at most this many sessions in total (invited + signed up) while the public link is on
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InterviewObserver(Base):
    __tablename__ = "interview_observers"
    __table_args__ = (UniqueConstraint("interview_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class InterviewParticipant(Base):
    """One participant's seat in a focus group (``Interview.kind == "group"``). Mirrors ``InterviewObserver`` but also
    carries the per-participant join journey (invite link, device check, waiting-room admit, entry) that a 1:1 depth
    interview keeps on the ``Interview`` itself.

    Depth interviews are untouched: they keep their single participant on ``Interview.participant_id`` and never create
    these rows. The ``seat`` number (1..10) is stable and doubles as the participant's recording-track / transcript
    speaker key (``p1``, ``p2``, …) and the "参与者 ·NN" label shown in the room. Personal data: purged with the
    interview (``app/retention.py``)."""
    __tablename__ = "interview_participants"
    __table_args__ = (UniqueConstraint("interview_id", "participant_id"), UniqueConstraint("interview_id", "seat"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    seat: Mapped[int] = mapped_column(Integer)  # 1..MAX_GROUP, stable; also the track/speaker key ("p1"…)
    invite_hash: Mapped[str] = mapped_column(String(64), index=True)
    # this participant's journey (a depth interview keeps the same fields on Interview): sent -> opened -> device_ok -> consented -> entered
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    waiting_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    participant: Mapped[Participant] = relationship(lazy="joined")


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    text_sha256: Mapped[str] = mapped_column(String(64))
    recording: Mapped[bool] = mapped_column(Boolean)
    observers: Mapped[bool] = mapped_column(Boolean)
    cross_border: Mapped[bool] = mapped_column(Boolean)  # separate consent (PIPL art. 39)
    ai_moderation: Mapped[bool] = mapped_column(Boolean, default=False)  # agreed to be interviewed by the AI moderator
    country: Mapped[str] = mapped_column(String(2))
    ip: Mapped[str] = mapped_column(String(64), default="")
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Recording(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    egress_id: Mapped[str] = mapped_column(String(100), default="")
    storage_key: Mapped[str] = mapped_column(String(300))  # private bucket key; never exposed to clients
    status: Mapped[str] = mapped_column(String(20), default="recording")  # recording | processing | ready | failed | deleted
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(String(200), default="")


class RecordingTrack(Base):
    """One speaker's own browser recording (moderator or participant). Chunks arrive over HTTP and are mixed on the server."""
    __tablename__ = "recording_tracks"
    __table_args__ = (UniqueConstraint("recording_id", "role", "part"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # moderator | participant
    part: Mapped[str] = mapped_column(String(16), default="0")  # a page reload mid-recording starts a new, independent stream of the same speaker
    ext: Mapped[str] = mapped_column(String(8), default="webm")
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)  # when this browser's recording began, relative to the recording start
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set when the browser says "this was the last chunk"


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("interview_id", "idx"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))  # moderator | participant
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text_orig: Mapped[str] = mapped_column(Text)
    text_zh: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(10), default="")


class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    theme: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10), default="manual")  # ai | manual
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | needs_review
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evidence: Mapped[list["Evidence"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("transcript_segments.id"), nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    quote_lang: Mapped[str] = mapped_column(String(10), default="orig")  # orig | zh
    start_ms: Mapped[int] = mapped_column(Integer, default=0)  # copied from segment, never from the client
    end_ms: Mapped[int] = mapped_column(Integer, default=0)


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShareLink(Base):
    __tablename__ = "share_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    watermark: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    """A long task run in the background (transcribe, analyse...): queued -> running -> done | failed, retried with a back-off when the failure may pass (app/jobs.py)."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Reel(Base):
    """A highlight reel: a titled, ordered set of a project's clips (app/reels.py)."""
    __tablename__ = "reels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReelItem(Base):
    __tablename__ = "reel_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    reel_id: Mapped[str] = mapped_column(ForeignKey("reels.id"), index=True)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ReelShare(Base):
    __tablename__ = "reel_shares"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    reel_id: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    watermark: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(80))  # user:<id> | participant:<id> | share:<id> | system
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str] = mapped_column(String(80), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AiSession(Base):
    """One AI-moderated interview's live state (the engine itself lives in memory, see app/ai_room.py)."""
    __tablename__ = "ai_sessions"
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    guide_json: Mapped[str] = mapped_column(Text)  # the discussion guide the AI follows (JSON); a demo guide when staff did not supply one
    control: Mapped[str] = mapped_column(String(10), default="ai")  # ai | human (a staff member took over; the AI stays silent)
    alert: Mapped[str] = mapped_column(String(10), default="none")  # none | yellow | red, shown to staff only
    alert_reason: Mapped[str] = mapped_column(String(300), default="")
    finished: Mapped[bool] = mapped_column(Boolean, default=False)  # the AI said its closing statement: the participant can no longer type
    strikes: Mapped[int] = mapped_column(Integer, default=0)  # chat messages with abusive or hateful words (app/content_rules); the AI ends the chat politely at the limit
    snapshot: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # the engine, saved after each turn of a study done at the participant's own pace (app/moderator/snapshot.py)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessage(Base):
    """One line of the AI-mode text chat. `whisper` lines are staff-only notes to the AI and never reach the participant."""
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("interview_id", "seq"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    sender: Mapped[str] = mapped_column(String(12))  # ai | participant | moderator | whisper
    text: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")  # the AI's internal note (JSON), staff only
    operator: Mapped[str] = mapped_column(String(20), default="")
    phase: Mapped[str] = mapped_column(String(12), default="")
    prompt_version: Mapped[str] = mapped_column(String(12), default="")  # the AI's lines: which prompt version and which model said it (traceability)
    model: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- concept test: the interview script, the materials shown in the room, live captions, quotes / follow-ups ----
class InterviewPlan(Base):
    """The moderator's script for one interview (JSON, see app/concept.py). Internal: participants never see it."""
    __tablename__ = "interview_plans"
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    template: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    script_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PreTask(Base):
    """Pre-interview task for one interview: the questions the participant answers after consent, before the waiting room.

    Opt-in per interview: no row = no pre-task step (the journey is unchanged). `questions_json` is a JSON list of
    {"key", "prompt", "required"} in the interview's language. Participants never see other interviews' tasks.
    """
    __tablename__ = "pre_tasks"
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    questions_json: Mapped[str] = mapped_column(Text)  # JSON list of {key, prompt, required}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PreTaskAnswer(Base):
    """One participant's answer to one pre-task question. Personal data: purged with the interview (app/retention.py)."""
    __tablename__ = "pre_task_answers"
    __table_args__ = (UniqueConstraint("interview_id", "participant_id", "question_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    question_key: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TimeSlot(Base):
    """A candidate interview time offered to one participant. They pick one and it becomes ``Interview.scheduled_at``.

    Opt-in per interview: no ``TimeSlot`` rows means the research team fixed the time directly, so the journey is
    unchanged. ``start_at`` is naive UTC (same convention as ``Interview.scheduled_at``).
    """
    __tablename__ = "time_slots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Claim(Base):
    """What a focus-group participant said about a topic: a stance with the transcript lines it rests on (app/group_analysis.py). A suggestion until a researcher accepts it."""
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    speaker: Mapped[str] = mapped_column(String(8))  # seat key: p1, p2...
    topic: Mapped[str] = mapped_column(String(60))
    stance: Mapped[str] = mapped_column(String(8))  # pro | con | unsure
    reason: Mapped[str] = mapped_column(String(200), default="")
    line_ids: Mapped[list] = mapped_column(JSON, default=list)  # transcript segment ids; the quotes are copied from them, never taken from the model
    status: Mapped[str] = mapped_column(String(10), default="suggested")  # suggested | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PersuasionEvent(Base):
    """Someone's stance on a topic changed during the discussion, with the claim of another person that may have caused it. A suggestion until accepted."""
    __tablename__ = "persuasion_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    speaker: Mapped[str] = mapped_column(String(8))
    topic: Mapped[str] = mapped_column(String(60))
    from_stance: Mapped[str] = mapped_column(String(8))
    to_stance: Mapped[str] = mapped_column(String(8))
    trigger_claim_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.3)
    status: Mapped[str] = mapped_column(String(10), default="suggested")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SlotVote(Base):
    """A focus-group participant says a candidate time suits them. The team confirms one time (the one most people can make) and it becomes the session's time."""
    __tablename__ = "slot_votes"
    slot_id: Mapped[str] = mapped_column(ForeignKey("time_slots.id"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), primary_key=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StimulusMark(Base):
    """A participant's point mark on a shown material: ♥ like or ✕ dislike at a position on a concept card / image.

    Coordinates are fractions of the image (0..1) so they line up on any screen. Personal data: purged with the
    interview (``app/retention.py``). Same shape as the concept-test circling an external survey platform pushes in
    (roadmap §5), so the two feed one heatmap.
    """
    __tablename__ = "stimulus_marks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    stimulus_id: Mapped[str] = mapped_column(ForeignKey("stimuli.id"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # like | dislike
    x: Mapped[float] = mapped_column(Float)  # 0..1, fraction of the image width
    y: Mapped[float] = mapped_column(Float)  # 0..1, fraction of the image height
    at_ms: Mapped[int] = mapped_column(Integer, default=0)  # ms since the interview started, on our clock
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Stimulus(Base):
    """A material (ad film, concept card, image) the moderator can put on the participant's screen."""
    __tablename__ = "stimuli"
    __table_args__ = (UniqueConstraint("interview_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))  # stable name used by the script ("ad_film", "concept_a")
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(10))  # image | video (a video is an embedded player page, never a file on our server)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))  # image: the picture; video: the player's embed address
    source_note: Mapped[str] = mapped_column(String(300), default="")  # where the material comes from (staff only)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoomState(Base):
    """What is on the participant's screen right now (one row per interview)."""
    __tablename__ = "room_states"
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    stimulus_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shown_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    shown_by: Mapped[str] = mapped_column(String(40), default="")


class CaptionLine(Base):
    """One finished sentence of the live captions. Personal data: purged with the interview (app/retention.py)."""
    __tablename__ = "caption_lines"
    __table_args__ = (UniqueConstraint("interview_id", "seq"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))  # moderator | participant (each browser captions its own microphone)
    text: Mapped[str] = mapped_column(Text)
    at_ms: Mapped[int] = mapped_column(Integer, default=0)  # milliseconds since the interview started (when the sentence ENDED), taken from OUR clock
    dur_ms: Mapped[int] = mapped_column(Integer, default=0)  # how long the sentence took to say, as measured by the recogniser (0 = unknown)
    text_zh: Mapped[str] = mapped_column(Text, default="")  # Chinese translation for non-Chinese interviews, filled in a moment after the sentence arrives
    flags: Mapped[str] = mapped_column(String(40), default="")  # what app/content_rules hid in this sentence: contact, abuse, hate, threat, sexual (comma separated)
    # signals about HOW it was said (app/signals), always compared with this speaker's own habits; hints for the team, never conclusions
    rate_x10: Mapped[int | None] = mapped_column(Integer, nullable=True)  # characters (Chinese) or words per second, times ten
    vol_db10: Mapped[int | None] = mapped_column(Integer, nullable=True)  # average level while speaking in dBFS times ten, measured in the speaker's own browser
    gap_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # silence between the end of the previous speaker's sentence and the start of this one
    senti: Mapped[int] = mapped_column(Integer, default=0)  # tone of the words, -100 (very negative) .. 100 (very positive)
    tags: Mapped[str] = mapped_column(String(80), default="")  # slow, fast, quiet, loud, hesitation, negative, positive (comma separated)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Highlight(Base):
    """A quote the team marked, or a follow-up question queued for the moderator. The quote text is copied from the caption line by the server."""
    __tablename__ = "highlights"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # quote | probe
    speaker: Mapped[str] = mapped_column(String(20), default="")
    text: Mapped[str] = mapped_column(Text)
    caption_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stimulus_key: Mapped[str] = mapped_column(String(40), default="")  # which material was on screen when it was said
    at_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | done (a probe that was asked)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BackroomMessage(Base):
    """The team's private chat during an interview (observers <-> moderator <-> researchers). The participant never sees it."""
    __tablename__ = "backroom_messages"
    __table_args__ = (UniqueConstraint("interview_id", "seq"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(32))
    sender: Mapped[str] = mapped_column(String(100))  # the sender's name at the time
    role: Mapped[str] = mapped_column(String(20))  # admin | researcher | moderator | observer
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserInvite(Base):
    """A one-time link: a new team member sets their own name and password (join), or an existing member sets a new password (reset)."""
    __tablename__ = "user_invites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # join | reset
    email: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # reset: whose password
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    research_kind: Mapped[str] = mapped_column(String(10), default="all")  # copied to the new account (join links)
    brand_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    job_title: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    created_by: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RepoDocument(Base):
    """A document the team imported into the research library (an earlier report, notes, desk research), as text. Searchable by everyone who can see its project
    (no project = the whole company's staff)."""
    __tablename__ = "repo_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    source_note: Mapped[str] = mapped_column(String(300), default="")  # where it came from (who wrote it, when), for the reader
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PoolNote(Base):
    """A note the team adds to the insight pool (a hypothesis, something from outside the data); it joins the finding with the same theme (app/pool.py)."""
    __tablename__ = "pool_notes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    theme: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IngestKey(Base):
    """A key that lets an outside survey platform push people and results into ONE project (only its hash is kept; shown once when made)."""
    __tablename__ = "ingest_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12))  # the first characters, to recognise the key by
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QuantSurvey(Base):
    """Aggregated results of a survey (pushed by the platform or imported from a spreadsheet): questions with option counts or a mean."""
    __tablename__ = "quant_surveys"
    __table_args__ = (UniqueConstraint("project_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    respondents: Mapped[int] = mapped_column(Integer, default=0)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(8), default="api")  # api | csv
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecruitSettings(Base):
    """A project's eligibility rules (versioned) and quotas (app/recruit.py)."""
    __tablename__ = "recruit_settings"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    rules_version: Mapped[int] = mapped_column(Integer, default=0)
    quotas: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecruitForm(Base):
    """A project's recruiting questionnaire (甄别问卷, app/recruit_form.py): written by the AI from the research idea, or a link to one made in another system."""
    __tablename__ = "recruit_forms"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | html
    idea: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(String(500), default="")
    questions: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=0)
    generated_by: Mapped[str] = mapped_column(String(100), default="")  # the model that wrote the questions ("" = by hand)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | published | closed (the public link works only while published)
    token: Mapped[str] = mapped_column(String(40), default="", index=True)  # the public link's token, made at the first publish and kept
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    n_qualified: Mapped[int] = mapped_column(Integer, default=0)  # people who finished and qualified (they are in the participant list)
    n_out: Mapped[int] = mapped_column(Integer, default=0)  # people screened out (nothing about them is kept)
    out_by: Mapped[dict] = mapped_column(JSON, default=dict)  # question id -> how many were screened out there
    goals: Mapped[list] = mapped_column(JSON, default=list)  # the research goals the outline shows (written by the AI from the background, editable)
    need: Mapped[str] = mapped_column(Text, default="")  # the recruiting need in words (who, how many, what screens out)
    n_started: Mapped[int] = mapped_column(Integer, default=0)  # how many people opened the first question
    tally: Mapped[dict] = mapped_column(JSON, default=dict)  # anonymous: how many chose each option, among people who qualified and people who were screened out (app/recruit_insights.py)
    chat: Mapped[list] = mapped_column(JSON, default=list)  # the conversation with the AI that edits the questionnaire: [{"role": "user" | "ai", "text"}], the latest 40


class ListeningSource(Base):
    """One place the project watches for public opinion (a news feed, an app's reviews...). Settings live in `config` (app/listening/connectors.py)."""
    __tablename__ = "listening_sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # mock | rss | appstore
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_min: Mapped[int] = mapped_column(Integer, default=360)  # how often the background job collects
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(8), default="")  # ok | error
    last_error: Mapped[str] = mapped_column(String(300), default="")
    last_count: Mapped[int] = mapped_column(Integer, default=0)  # new mentions in the last run
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Mention(Base):
    """One public post / review / article. Only the text, the link and a salted hash of the author are kept (never a name or picture)."""
    __tablename__ = "mentions"
    __table_args__ = (UniqueConstraint("project_id", "dedup_hash"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("listening_sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(10), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    author_hash: Mapped[str] = mapped_column(String(16), default="", index=True)
    dedup_hash: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sentiment: Mapped[str] = mapped_column(String(3), default="")  # neg | neu | pos ("" until tagged)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enrich_attempts: Mapped[int] = mapped_column(Integer, default=0)


class MentionSuppression(Base):
    """A take-down: a link (hash) or an author (hash) that must never be collected again for this project."""
    __tablename__ = "mention_suppressions"
    __table_args__ = (UniqueConstraint("project_id", "kind", "value"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # hash | author
    value: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmailOutbox(Base):
    """An account email waiting to go out (or tried and failed). The text holds a one-time link, so it is wiped as soon as the mail is sent or given up."""
    __tablename__ = "email_outbox"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # join | reset | forgot
    to_email: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Code(Base):
    """A coding label (a theme tag) in a project's codebook. Applied to insights; kept after the retention period together with the insights."""
    __tablename__ = "codes"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InsightCode(Base):
    """A code on an insight. suggested=True is an AI proposal that a researcher has not accepted yet (it does not count in the analysis)."""
    __tablename__ = "insight_codes"
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), primary_key=True)
    code_id: Mapped[str] = mapped_column(ForeignKey("codes.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    suggested: Mapped[bool] = mapped_column(Boolean, default=False)


class SegmentCode(Base):
    """A code on a line of a transcript (a researcher tags what was said). Deleted with the interview's personal data."""
    __tablename__ = "segment_codes"
    segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id"), primary_key=True)
    code_id: Mapped[str] = mapped_column(ForeignKey("codes.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScreenerAnswer(Base):
    """One screening-questionnaire question and the participant's answer (up to ~20 per participant).

    They travel with the participant into every interview: the team sees them, and the AI moderator reads them as background so it
    does not ask again what was already answered and can make its follow-ups personal. Personal data: deleted when the participant
    is anonymised (``app/retention.py``). Entered by hand or pasted today; an external survey platform will push the same shape.
    """
    __tablename__ = "screener_answers"
    __table_args__ = (UniqueConstraint("participant_id", "position"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # the order of the questionnaire
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CueCard(Base):
    """A private suggestion for the moderator of a focus group (a "cue card"): who to invite, who talks too much, time, off topic, care with personal data.

    The AI never speaks to participants; the moderator decides (used / dismissed). Evidence is stored as caption sequence numbers and the text is
    copied from the caption lines by the server when shown. Cards may quote a participant's own screening answer, so they are for the team's
    operating roles only (not for client observers). Personal data: purged with the interview (``app/retention.py``).
    """
    __tablename__ = "cue_cards"
    __table_args__ = (UniqueConstraint("interview_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # compliance | dominant | silent | time | offtopic
    seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    ask: Mapped[str] = mapped_column(Text, default="")  # a question the moderator could use as it stands (optional)
    evidence: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of caption seq numbers
    priority: Mapped[int] = mapped_column(Integer, default=0)
    dedupe_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | used | dismissed | expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    handled_by: Mapped[str] = mapped_column(String(32), default="")
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Reaction(Base):
    """An emoji reaction a participant sent during the interview (one of a fixed set, see ``app/signals/constants.py``).

    Stored with the moment (milliseconds from the start of the interview, on our clock) and the material that was on screen. Personal data:
    purged with the interview (``app/retention.py``).
    """
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("interview_id", "seq"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))  # "participant" in a depth interview, "p1" .. "p10" in a focus group
    emoji: Mapped[str] = mapped_column(String(32))  # the emoji itself (any real emoji, checked against the official list in app/signals/emoji.py)
    at_ms: Mapped[int] = mapped_column(Integer, default=0)
    stimulus_key: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectMember(Base):
    """A research-team member who may work on one project. Admins see every project; a researcher sees only the projects they were added to.

    (Moderators and client observers are not project members: they are assigned to single interviews.)
    """
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)  # may add and remove members (an admin always may)
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # the research stages (methods: depth, group, quant, listening, async) this person takes part in; several at once
    added_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProjectPlan(Base):
    """The set-up of one project (the 立项向导): draft -> review -> approved -> published (locked). Projects made before plans existed have none."""
    __tablename__ = "project_plans"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | review | approved | published (app/plans.py STATUSES)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=0)  # 0 until first published, then +1 at each (re)publish
    suggest_json: Mapped[str] = mapped_column(Text, default="")  # the AI's suggestions from the research background (app/plan_suggest.py)
    published_json: Mapped[str] = mapped_column(Text, default="")  # the config as last published: the project runs on this while an unlocked plan is being edited
    review_hash: Mapped[str] = mapped_column(String(32), default="")  # fingerprint of the config when it was submitted for review
    review_note: Mapped[str] = mapped_column(String(300), default="")  # why a reviewer sent it back
    submitted_by: Mapped[str] = mapped_column(String(32), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(32), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
