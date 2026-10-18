"""Background jobs of an interview (staff of the project): start one, ask how it is going, list the recent ones, retry a failed one. Logic in app/jobs.py."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles, visible_interview
from ..models import Job

router = APIRouter(prefix="/api", tags=["jobs"])
RECENT = 10


class JobIn(BaseModel):
    kind: str


def _job(db: Session, ctx: Ctx, jid: str) -> Job:
    j = db.get(Job, jid)
    if not j or j.org_id != ctx.org_id or not j.interview_id:
        raise HTTPException(404, "任务不存在")
    visible_interview(db, ctx, j.interview_id)          # 404 for an interview the person is not on
    return j


@router.post("/interviews/{iid}/jobs", status_code=202)
def start(iid: str, body: JobIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Queue a job and answer at once with its id; poll GET /jobs/{id}. Asking again while one is waiting or running returns that same job."""
    itv = visible_interview(db, ctx, iid)
    if body.kind not in jobs.HANDLERS:
        raise HTTPException(422, "不支持的任务类型")
    job = jobs.enqueue(db, ctx.org_id, itv.id, body.kind, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "job.start", job.id, kind=body.kind)
    db.commit()
    return jobs.job_out(job)


@router.get("/interviews/{iid}/jobs")
def recent(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    return [jobs.job_out(j) for j in db.scalars(select(Job).where(Job.interview_id == itv.id).order_by(Job.created_at.desc()).limit(RECENT))]


@router.get("/jobs/{jid}")
def get_job(jid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return jobs.job_out(_job(db, ctx, jid))


@router.post("/jobs/{jid}/retry")
def retry(jid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    j = _job(db, ctx, jid)
    if j.status != "failed":
        raise HTTPException(409, "只有失败的任务可以重试")
    jobs.requeue(j)
    audit(db, ctx.org_id, ctx.actor, "job.retry", j.id, kind=j.kind)
    db.commit()
    return jobs.job_out(j)
