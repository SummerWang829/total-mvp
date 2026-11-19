"""Tables of the outline workbench (大纲工作台): outlines with versions, items, snapshots, revisions and what pilots and live interviews attach to them.

Two ids per item: `item_uid` never changes (copied into every new version); `label` (H1, C2 ...) is what people see and may change. Everything that points at an
item (transcript lines, marks, focus questions, coded answers) stores `item_uid`, so renumbering never breaks history.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..models import uid, utcnow


class Outline(Base):
    """One interview outline of a project (a project may have several)."""
    __tablename__ = "outlines"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutlineVersion(Base):
    """A version of an outline. Not locked: it can be edited at any time; `rev` counts the edits so an interview can say exactly which state it ran on."""
    __tablename__ = "outline_versions"
    __table_args__ = (UniqueConstraint("outline_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 ...
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | published
    base_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(String(300), default="")  # what changed compared with the base
    rev: Mapped[int] = mapped_column(Integer, default=0)  # +1 at every edit
    ready_by: Mapped[str] = mapped_column(String(32), default="")  # 修正方案第 1 轮第 9 条: the researcher said "可以正式开始" on this version
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pilot_skipped: Mapped[bool] = mapped_column(Boolean, default=False)  # published without any pilot (跳过 Pilot，直接上线)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutlineItem(Base):
    """A question, note or material in a version (they share one ordered list)."""
    __tablename__ = "outline_items"
    __table_args__ = (UniqueConstraint("version_id", "item_uid"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("outline_versions.id"), index=True)
    item_uid: Mapped[str] = mapped_column(String(32), index=True, default=uid)  # permanent, copied to new versions
    label: Mapped[str] = mapped_column(String(20), default="")  # what people see: H1, C2 ...; may change
    module: Mapped[str] = mapped_column(String(80), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(12), default="question")  # question | note | concept | video | website | rating
    texts: Mapped[dict] = mapped_column(JSON, default=dict)  # language code (zh, en, ms, ta) -> the text
    followups: Mapped[list] = mapped_column(JSON, default=list)  # the follow-up ladder
    must_cover: Mapped[list] = mapped_column(JSON, default=list)  # what the question must find out
    key_question: Mapped[str] = mapped_column(String(300), default="")  # the key research question it serves
    hypothesis: Mapped[str] = mapped_column(String(300), default="")  # internal only, never said to a participant
    minutes: Mapped[float] = mapped_column(Float, default=0.0)
    is_backup: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # per kind: concept card, video clip, website task, rating scale ...


class OutlineSnapshot(Base):
    """The frozen content of one (version, rev): interviews that start on it keep using it however the outline is edited afterwards. One row per (version, rev), shared."""
    __tablename__ = "outline_snapshots"
    __table_args__ = (UniqueConstraint("version_id", "rev"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("outline_versions.id"), index=True)
    rev: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Material(Base):
    """A picture, video or web address the outline can show; several outlines may use the same one."""
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # image | video | website
    title: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(500), default="")  # where it lives (address or storage key); concept.py's reviewed list still governs what a participant's browser may embed
    precheck: Mapped[dict] = mapped_column(JSON, default=dict)  # reachable from Singapore, load seconds, needs login, works on a phone, languages complete
    languages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StudySetting(Base):
    """What the generation flow worked out (kept so it can be looked at again) and how far the researcher got, so leaving halfway can be resumed."""
    __tablename__ = "study_settings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), unique=True)
    background: Mapped[str] = mapped_column(Text, default="")
    slots: Mapped[dict] = mapped_column(JSON, default=dict)  # 13 slots: name -> {value, source (text|inferred|default|project), confidence 0..1}
    decisions: Mapped[list] = mapped_column(JSON, default=list)  # the business decisions
    key_questions: Mapped[list] = mapped_column(JSON, default=list)
    hypotheses: Mapped[list] = mapped_column(JSON, default=list)
    rounds: Mapped[dict] = mapped_column(JSON, default=dict)  # round name -> {status: todo|doing|done, answers: {...}}
    gates: Mapped[dict] = mapped_column(JSON, default=dict)  # goal | structure | final -> confirmed?
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # what the researcher answered in the rounds: slot name -> value
    overview: Mapped[dict] = mapped_column(JSON, default=dict)  # the outline overview table: {"modules": [...], "minutes": n, "context": {...flags}, "dismissed": [...ids], "undo": {...}}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Revision(Base):
    """A proposed change. An AI only ever produces a pending one; a person accepts or rejects it."""
    __tablename__ = "revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), index=True)
    base_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    op: Mapped[str] = mapped_column(String(12))  # rewrite | add_followup | insert | rename | config | minutes
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(500), default="")
    minutes_delta: Mapped[float] = mapped_column(Float, default=0.0)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)  # {"blocked": bool, "issues": [...]}: a blocked revision (red line) cannot be accepted
    destination: Mapped[str] = mapped_column(String(10), default="outline")  # outline | material | host_engine
    train_case_id: Mapped[str] = mapped_column(String(60), default="")  # destination host_engine: the training-set case it was written to
    source: Mapped[str] = mapped_column(String(12), default="editor")  # generate | editor | pilot | board
    source_ref: Mapped[str] = mapped_column(String(32), default="")  # the mark or alert it came from
    round_id: Mapped[str] = mapped_column(String(32), default="")  # source pilot: the round it came from (applied to the NEXT version, never to the one the round ran on)
    proposed_by: Mapped[str] = mapped_column(String(6), default="ai")  # ai | human
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | accepted | rejected
    decided_by: Mapped[str] = mapped_column(String(32), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Mark(Base):
    """A problem circled in a pilot transcript. A client's mark is only a suggestion."""
    __tablename__ = "marks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    line_id: Mapped[str] = mapped_column(String(32), default="")  # the transcript line (caption line or transcript segment)
    item_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")  # copied from the line by the server
    start: Mapped[int] = mapped_column(Integer, default=0)
    end: Mapped[int] = mapped_column(Integer, default=0)
    tag: Mapped[str] = mapped_column(String(12), default="other")  # unclear | leading | missed | material | host | long | other
    note: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(String(32), default="")
    author_role: Mapped[str] = mapped_column(String(12), default="researcher")  # researcher | client
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # a mark made in the room: when, from the interview's start (set by the server, never by the browser)
    origin: Mapped[str] = mapped_column(String(10), default="transcript")  # room | transcript | auto
    auto_key: Mapped[str] = mapped_column(String(80), default="")  # origin auto: which automatic finding it is (so it is written once)
    round_id: Mapped[str] = mapped_column(String(32), default="")  # the pilot round it belongs to
    decision: Mapped[str] = mapped_column(String(10), default="")  # "" | accepted | rejected | deferred (暂缓)
    resolved_revision_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FocusQuestion(Base):
    """A question the client or the team wants answered; the board counts per focus question."""
    __tablename__ = "focus_questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    asked_by: Mapped[str] = mapped_column(String(32), default="")
    asked_role: Mapped[str] = mapped_column(String(12), default="researcher")  # researcher | client
    timing: Mapped[str] = mapped_column(String(8), default="before")  # before | during the interviews
    item_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_uids: Mapped[list] = mapped_column(JSON, default=list)  # every question it points at (item_uid is the first); a line under any of them is "asked"
    coding: Mapped[str] = mapped_column(String(10), default="preset")  # preset | template | collect | custom
    codes: Mapped[list] = mapped_column(JSON, default=list)  # the code list
    from_interview_seq: Mapped[int] = mapped_column(Integer, default=0)  # asked from this interview on (0 = all)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CodedAnswer(Base):
    """One coded answer to a focus question. The AI's own code is kept when a person changes it (it is how the coding gets calibrated)."""
    __tablename__ = "coded_answers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    focus_id: Mapped[str] = mapped_column(ForeignKey("focus_questions.id"), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    line_id: Mapped[str] = mapped_column(String(32), default="")
    origin: Mapped[str] = mapped_column(String(10), default="asked")  # asked | volunteered (said unprompted, in another question)
    mentioned_in_item_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)  # volunteered: the question it was said in
    code: Mapped[str] = mapped_column(String(80), default="")
    ai_code: Mapped[str] = mapped_column(String(80), default="")  # what the AI coded first; never overwritten
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1; under 0.75 goes to review
    coded_by: Mapped[str] = mapped_column(String(4), default="ai")  # ai | human
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    clip_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "add to report quotes" points at an existing insight clip, not a new kind of object
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HostDecision(Base):
    """Why the AI host did what it did on one line: asked a follow-up, moved on, how saturated the topic was, what was still uncovered."""
    __tablename__ = "host_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), index=True)
    line_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(20), default="")  # followup | next_question | next_module | close ...
    reason: Mapped[str] = mapped_column(String(300), default="")
    saturation: Mapped[float] = mapped_column(Float, default=0.0)
    uncovered: Mapped[list] = mapped_column(JSON, default=list)  # must-cover points still open
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RehearsalRun(Base):
    """A simulated rehearsal of one outline state. The detailed report is the existing eval report file (`eval_report`); this row keeps the summary per company."""
    __tablename__ = "rehearsal_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(32), default="")
    rev: Mapped[int] = mapped_column(Integer, default=0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    avg_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    must_cover_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    findings: Mapped[list] = mapped_column(JSON, default=list)  # problems found (confusing questions, overruns ...)
    eval_report: Mapped[str] = mapped_column(String(200), default="")  # file name under eval_reports/, "" = none
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PilotRound(Base):
    """One round of a real pilot (修正方案第 1 轮第 9 条): it runs on one version; what it finds is decided mark by mark; the accepted changes make the next version."""
    __tablename__ = "pilot_rounds"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    outline_id: Mapped[str] = mapped_column(ForeignKey("outlines.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(32))
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 ... within the outline
    status: Mapped[str] = mapped_column(String(10), default="planned")  # planned | reviewed (made the next version)
    next_version_id: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
