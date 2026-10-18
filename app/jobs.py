"""Background jobs: long tasks (transcribing, analysing) run by a worker instead of inside a web request, so a slow model cannot time the page out and a failure is
kept, shown and retried.

A job goes queued -> running -> done | failed. A failure that may pass (the model or transcription service did not answer: HTTP 502/503/504, a network error)
is retried after a back-off, up to MAX_ATTEMPTS; any other failure (nothing to transcribe, unsupported language) fails at once with its plain message. A job stuck
in "running" (the server restarted) is put back in the queue. The same kind of job for the same interview is never queued twice.
"""
import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Interview, Job, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_S = (30, 120)               # wait before the 2nd and 3rd attempt
STUCK_AFTER_S = 15 * 60
POLL_S = 2
RETRYABLE = (502, 503, 504)

Handler = Callable[[Session, Interview, Job], dict]
HANDLERS: dict[str, Handler] = {}


class JobError(Exception):
    """A handler's failure: `message` is plain Chinese for the researcher; `retry` says whether trying again may help."""

    def __init__(self, message: str, retry: bool = False) -> None:
        super().__init__(message)
        self.message, self.retry = message, retry


def register(kind: str, fn: Handler) -> None:
    """Make `kind` a job type; the handler gets the session, the interview and the job and returns a small result dict."""
    HANDLERS[kind] = fn


def enqueue(db: Session, org_id: str, interview_id: str, kind: str, created_by: str) -> Job:
    """Queue a job (the caller commits). If the same kind is already waiting or running for this interview, that job is returned instead.

    Raises:
        ValueError: the kind is not registered.
    """
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind!r}")
    live = db.scalar(select(Job).where(Job.interview_id == interview_id, Job.kind == kind, Job.status.in_(("queued", "running"))))
    if live:
        return live
    job = Job(org_id=org_id, interview_id=interview_id, kind=kind, created_by=created_by)
    db.add(job)
    db.flush()
    return job


def requeue(job: Job) -> None:
    """Put a failed job back for a fresh set of attempts (the caller commits)."""
    job.status, job.attempts, job.error, job.run_after, job.finished_at = "queued", 0, "", utcnow(), None


def _failure(e: Exception) -> JobError:
    if isinstance(e, JobError):
        return e
    if isinstance(e, HTTPException):
        return JobError(str(e.detail), retry=e.status_code in RETRYABLE)
    if isinstance(e, httpx.HTTPError):
        return JobError("服务暂时没有响应", retry=True)
    log.exception("job failed unexpectedly")          # the traceback stays in the log; the researcher gets a plain line
    return JobError("任务出错了，已记录，请联系管理员")


def _recover_stuck(db: Session) -> None:
    cutoff = utcnow() - timedelta(seconds=STUCK_AFTER_S)
    for j in db.scalars(select(Job).where(Job.status == "running", Job.started_at < cutoff)):
        j.status, j.run_after = "queued", utcnow()
        log.warning("job %s was stuck in running; queued again", j.id)
    db.commit()


def run_pending(db: Session, limit: int = 5) -> int:
    """Run the jobs that are due (oldest first) and return how many were run. Meant for the worker; tests call it directly."""
    _recover_stuck(db)
    done = 0
    for _ in range(limit):
        job = db.scalar(select(Job).where(Job.status == "queued", Job.run_after <= utcnow()).order_by(Job.created_at))
        if not job:
            break
        job.status, job.attempts, job.started_at = "running", job.attempts + 1, utcnow()
        db.commit()
        _run(db, job)
        done += 1
    return done


def _run(db: Session, job: Job) -> None:
    itv = db.get(Interview, job.interview_id) if job.interview_id else None
    try:
        if itv is None or itv.purged_at is not None:
            raise JobError("这场访谈已不存在")
        result = HANDLERS[job.kind](db, itv, job)
        db.commit()
        job.status, job.result, job.error, job.finished_at = "done", result, "", utcnow()
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the job, never lost
        db.rollback()
        job = db.get(Job, job.id)
        err = _failure(e)
        job.error = err.message[:300]
        if err.retry and job.attempts < MAX_ATTEMPTS:
            job.status, job.run_after = "queued", utcnow() + timedelta(seconds=BACKOFF_S[min(job.attempts - 1, len(BACKOFF_S) - 1)])
        else:
            job.status, job.finished_at = "failed", utcnow()
    db.commit()


def job_out(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "error": j.error, "result": j.result,
            "created_at": j.created_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None}


def start_worker() -> None:
    from .db import SessionLocal

    def loop() -> None:
        while True:
            try:
                with SessionLocal() as s:
                    run_pending(s)
            except Exception:  # noqa: BLE001 - the loop must survive anything; the traceback is logged
                log.exception("job worker loop failed")
            time.sleep(POLL_S)

    threading.Thread(target=loop, daemon=True, name="job-worker").start()
