import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    role: Mapped[str] = mapped_column(String(20))  # admin | researcher | moderator | observer
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    participant: Mapped[Participant] = relationship(lazy="joined")
    observers: Mapped[list["InterviewObserver"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class InterviewObserver(Base):
    __tablename__ = "interview_observers"
    __table_args__ = (UniqueConstraint("interview_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


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
