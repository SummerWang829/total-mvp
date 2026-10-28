"""Retention + erasure. Recordings, transcripts, quotes, clips and share links are personal data and are purged.

Decided 2026-10-10: the 180 days run from project close; insight statements are kept (treated as de-identified).
"""
import os
import shutil
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .audit import audit
from .config import settings
from .media import purge_cache
from . import ai_room, screener
from .chat_models import ChatThread
from .report_models import ReportTemplate
from .models import (BackroomMessage, CaptionLine, Clip, CueCard, Evidence, Highlight, Insight, Claim, Interview, InterviewParticipant, Job, Mention, Participant, PersuasionEvent, SlotVote, PreTask, PreTaskAnswer, Project, RepoDocument, SegmentCode,
                     Recording, RecordingTrack, Reaction, ReelItem, ShareLink, StimulusMark, TimeSlot, TranscriptSegment, utcnow)

PURGED = "[已按保留期删除]"


def _delete_file(storage_key: str) -> None:
    path = os.path.join(settings.media_dir, storage_key)
    if os.path.isfile(path):
        os.remove(path)
    shutil.rmtree(path.rsplit(".", 1)[0], ignore_errors=True)  # per-speaker tracks, audio extracts


def purge_interview(db: Session, itv: Interview, reason: str, actor: str = "system") -> None:
    for rec in db.scalars(select(Recording).where(Recording.interview_id == itv.id)):
        _delete_file(rec.storage_key)
        purge_cache(rec.id)  # watermarked cuts
        db.execute(delete(RecordingTrack).where(RecordingTrack.recording_id == rec.id))
        rec.status = "deleted"
    ins_ids = [i.id for i in db.scalars(select(Insight).where(Insight.interview_id == itv.id))]
    if ins_ids:
        db.execute(update(Evidence).where(Evidence.insight_id.in_(ins_ids)).values(quote=PURGED, segment_id=None))
    db.execute(delete(SegmentCode).where(SegmentCode.interview_id == itv.id))
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id))
    ai_room.purge(db, itv.id)  # the AI-mode chat is personal data too
    db.execute(delete(CaptionLine).where(CaptionLine.interview_id == itv.id))  # live captions and the quotes copied from them
    db.execute(delete(Highlight).where(Highlight.interview_id == itv.id))
    db.execute(delete(BackroomMessage).where(BackroomMessage.interview_id == itv.id))  # the team's private chat
    db.execute(delete(PreTaskAnswer).where(PreTaskAnswer.interview_id == itv.id))  # pre-interview answers are personal data
    db.execute(delete(PreTask).where(PreTask.interview_id == itv.id))
    db.execute(delete(PersuasionEvent).where(PersuasionEvent.interview_id == itv.id))   # the disagreement map quotes participants
    db.execute(delete(Claim).where(Claim.interview_id == itv.id))
    db.execute(delete(SlotVote).where(SlotVote.interview_id == itv.id))  # which times each participant could make
    db.execute(delete(TimeSlot).where(TimeSlot.interview_id == itv.id))  # candidate times (config)
    db.execute(delete(StimulusMark).where(StimulusMark.interview_id == itv.id))  # material marks are personal data
    db.execute(delete(CueCard).where(CueCard.interview_id == itv.id))  # cue cards may quote participants
    db.execute(delete(Reaction).where(Reaction.interview_id == itv.id))  # emoji reactions belong to the participant
    db.execute(delete(InterviewParticipant).where(InterviewParticipant.interview_id == itv.id))  # focus-group seats (per-participant journey)
    db.execute(delete(Job).where(Job.interview_id == itv.id))  # background-job history of this interview
    clip_ids = [c.id for c in db.scalars(select(Clip).where(Clip.interview_id == itv.id))]
    if clip_ids:
        db.execute(update(ShareLink).where(ShareLink.clip_id.in_(clip_ids)).values(revoked_at=utcnow()))
        db.execute(delete(ReelItem).where(ReelItem.clip_id.in_(clip_ids)))  # highlight reels simply lose these clips
        db.execute(delete(Clip).where(Clip.id.in_(clip_ids)))
    itv.purged_at = utcnow()
    audit(db, itv.org_id, actor, "interview.purge", itv.id, reason=reason)


def anonymize_participant(db: Session, p: Participant) -> None:
    p.name, p.email, p.anonymized_at = "已匿名", "", utcnow()
    screener.erase(db, p.id)  # the questionnaire answers are personal data too


def days_of(project: Project) -> int:
    """How many days after the project closes its personal data is deleted: the project's own choice (plan: 30/60/120/180), never above the platform's."""
    return min(project.retention_days or settings.retention_days, settings.retention_days)


def _closed_due(project: Project, now) -> bool:
    return project.closed_at is not None and project.closed_at + timedelta(days=days_of(project)) < now


def purge_date(project: Project):
    """When this project's personal data will be deleted (None until the project is closed); an admin's hold can push it later."""
    if not project.closed_at:
        return None
    due = project.closed_at + timedelta(days=days_of(project))
    return max(due, project.purge_hold_until) if project.purge_hold_until else due


def purge_project_extras(db: Session, project: Project, actor: str, reason: str) -> dict:
    """What a project holds besides interviews that is personal data: people nobody interviewed (imported, pushed by a survey tool, recruited), the public posts
    collected for it, and documents imported into it. The caller commits. Returns how many of each."""
    people = db.scalars(select(Participant).where(Participant.project_id == project.id, Participant.anonymized_at.is_(None))).all()
    for p in people:
        anonymize_participant(db, p)
    posts = db.execute(delete(Mention).where(Mention.project_id == project.id)).rowcount or 0
    docs = db.execute(delete(RepoDocument).where(RepoDocument.project_id == project.id)).rowcount or 0
    db.execute(delete(ReportTemplate).where(ReportTemplate.project_id == project.id))  # the report's block choices and readings
    db.execute(delete(ChatThread).where(ChatThread.project_id == project.id))  # the research assistant conversations about the project go with it
    out = {"participants": len(people), "mentions": posts, "documents": docs}
    if any(out.values()):
        audit(db, project.org_id, actor, "project.purge_extras", project.id, reason=reason, **out)
    return out


def run_retention(db: Session, now=None) -> int:
    """Delete personal data 180 days after the PROJECT closed. Projects never closed hit the backstop
    (RETENTION_BACKSTOP_DAYS after each interview ended) so data cannot be kept forever by forgetting to close."""
    now = now or utcnow()
    back_cut = now - timedelta(days=settings.retention_backstop_days)
    q = (select(Interview).join(Project, Project.id == Interview.project_id)
         .where(Interview.purged_at.is_(None), Interview.ended_at.is_not(None))
         .where((Project.closed_at.is_not(None)) | (Interview.ended_at < back_cut)))
    q = q.where((Project.purge_hold_until.is_(None)) | (Project.purge_hold_until < now))   # an admin's hold keeps the data until its date
    due = []
    for itv in db.scalars(q).all():
        proj = db.get(Project, itv.project_id)
        if proj.closed_at is None or _closed_due(proj, now):  # a project that never closed was already filtered by the backstop above
            due.append(itv)
    for itv in due:
        proj = db.get(Project, itv.project_id)
        people = [s.participant for s in itv.group_participants] if itv.kind == "group" else [itv.participant]
        purge_interview(db, itv, f"retention:{days_of(proj)}d-after-project-close" if proj.closed_at is not None else f"retention-backstop:{settings.retention_backstop_days}d")
        for person in people:
            anonymize_participant(db, person)
    for proj in db.scalars(select(Project).where(Project.closed_at.is_not(None), (Project.purge_hold_until.is_(None)) | (Project.purge_hold_until < now))).all():
        if _closed_due(proj, now):
            purge_project_extras(db, proj, "system", f"retention:{days_of(proj)}d-after-project-close")
    db.commit()
    return len(due)


def erase_participant(db: Session, p: Participant, actor: str) -> int:
    ids = {i.id for i in db.scalars(select(Interview).where(Interview.participant_id == p.id, Interview.purged_at.is_(None)))}
    seated = db.scalars(select(InterviewParticipant.interview_id).where(InterviewParticipant.participant_id == p.id)).all()
    if seated:
        ids.update(i.id for i in db.scalars(select(Interview).where(Interview.id.in_(seated), Interview.purged_at.is_(None))))
    itvs = [db.get(Interview, i) for i in ids]
    for itv in itvs:
        purge_interview(db, itv, "data-subject-request", actor)
    anonymize_participant(db, p)
    audit(db, p.org_id, actor, "participant.erase", p.id)
    db.commit()
    return len(itvs)


if __name__ == "__main__":  # python -m app.retention  (run daily from cron)
    from .db import SessionLocal

    with SessionLocal() as s:
        print("purged interviews:", run_retention(s))
