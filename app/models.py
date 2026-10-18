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
    added_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
