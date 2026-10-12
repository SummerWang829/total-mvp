"""Project home (progress, next step, recent activity) and the admin's "clear demo data" button."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles
from ..models import (AuditLog, Clip, Consent, Evidence, Insight, Interview, InterviewObserver, Participant, Project, Recording,
                      RecordingTrack, ShareLink, TranscriptSegment, User, utcnow)
from ..retention import _delete_file
from ..media import purge_cache
from .projects import get_project

router = APIRouter(prefix="/api", tags=["overview"])

DEMO_DOMAIN = "@demo.test"  # reserved TLD: no real person can have this address, so an org made only of these accounts is the demo


def project_summary(db: Session, p: Project) -> dict:
    its = db.scalars(select(Interview).where(Interview.project_id == p.id)).all()
    now = utcnow()
    upcoming = sorted(i.scheduled_at for i in its if i.status != "ended" and i.scheduled_at and i.scheduled_at >= now - timedelta(hours=1))
    ins = db.execute(select(Insight.status, func.count()).join(Interview, Interview.id == Insight.interview_id)
                     .where(Interview.project_id == p.id).group_by(Insight.status)).all()
    by = dict(ins)
    return {
        "participants": db.scalar(select(func.count()).select_from(Participant).where(Participant.project_id == p.id)) or 0,
        "sessions": len(its),
        "done": sum(1 for i in its if i.status == "ended" and i.entered_at),
        "ended": sum(1 for i in its if i.status == "ended"),
        "live": sum(1 for i in its if i.status == "live"),
        "upcoming": sum(1 for i in its if i.status == "scheduled"),
        "waiting": sum(1 for i in its if i.status != "ended" and i.waiting_seen_at and not i.admitted_at and (now - i.waiting_seen_at).total_seconds() <= 10),
        "transcribed": len({i for (i,) in db.execute(select(TranscriptSegment.interview_id).join(Interview, Interview.id == TranscriptSegment.interview_id)
                                                     .where(Interview.project_id == p.id).distinct())}),
        "insights": sum(by.values()), "approved": by.get("approved", 0), "to_review": by.get("draft", 0) + by.get("needs_review", 0),
        "next_at": upcoming[0].isoformat() if upcoming else None,
    }


def next_step(s: dict, closed: bool) -> dict:
    """One plain sentence about what to do next, and which page does it."""
    if closed:
        return {"text": "项目已结项。洞察结论会保留，受访者数据到期自动删除。", "page": "insights"}
    if not s["participants"]:
        return {"text": "先添加受访者（问卷平台接入后会自动进来）。", "page": "people"}
    if not s["sessions"]:
        return {"text": "给受访者新建场次，并把邀请链接发给他们。", "page": "sessions"}
    if s["waiting"]:
        return {"text": f"有 {s['waiting']} 位受访者在候场室，进入房间放行。", "page": "rooms"}
    if s["live"]:
        return {"text": f"有 {s['live']} 场访谈正在进行。", "page": "rooms"}
    if s["done"] > s["transcribed"]:
        return {"text": f"有 {s['done'] - s['transcribed']} 场已完成的访谈还没有转写。", "page": "insights"}
    if s["to_review"]:
        return {"text": f"有 {s['to_review']} 条洞察等待审核。", "page": "insights"}
    if s["upcoming"]:
        return {"text": f"还有 {s['upcoming']} 场待开始，留意受访者的邀请进度。", "page": "sessions"}
    return {"text": "所有场次都已完成。可以在“洞察与片段”里整理结论。", "page": "insights"}


ACTION_ZH = {
    "project.create": "创建了项目", "project.close": "结项", "project.reopen": "重新打开项目", "participant.create": "添加了受访者",
    "participant.erase": "删除了一位受访者的数据", "interview.create": "新建了场次", "interview.invite.rotate": "重新生成了邀请链接",
    "invite.open": "打开了邀请", "device.check": "完成了设备检测", "consent.give": "同意了隐私说明", "consent.blocked_region": "所在地区暂未开放",
    "participant.admit": "放行了受访者", "participant.hold": "请受访者稍等", "room.token": "进入了房间", "room.leave": "离开了房间",
    "interview.start": "开始了访谈", "interview.end": "结束了访谈", "recording.start": "开始录制", "recording.stop": "停止录制",
    "recording.ready": "录像合成完成", "recording.failed": "录像合成失败", "transcript.transcribe": "完成了转写", "transcript.edit": "校对了转写",
    "insight.generate": "生成了 AI 洞察", "insight.create": "写了一条洞察", "insight.approve": "通过了一条洞察", "insight.delete": "删除了一条洞察",
    "clip.create": "生成了片段", "share.create": "创建了分享链接", "share.revoke": "撤销了分享链接", "share.view": "打开了分享链接",
    "interview.purge": "按保留期删除了数据",
}


@router.get("/projects/{pid}/overview")
def overview(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = get_project(db, ctx, pid)
    s = project_summary(db, p)
    its = db.scalars(select(Interview).where(Interview.project_id == p.id)).all()
    titles = {i.id: i.title for i in its}
    targets = {p.id, *titles}
    targets |= {x for (x,) in db.execute(select(Participant.id).where(Participant.project_id == p.id))}
    targets |= {x for (x,) in db.execute(select(Insight.id).where(Insight.interview_id.in_(titles)))} if titles else set()
    targets |= {x for (x,) in db.execute(select(Clip.id).where(Clip.interview_id.in_(titles)))} if titles else set()
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == ctx.org_id, AuditLog.target.in_(targets), AuditLog.action.in_(ACTION_ZH))
                      .order_by(AuditLog.at.desc()).limit(80)).all()
    # the team opening a room is not project progress; a participant entering is
    rows = [r for r in rows if not (r.action == "room.token" and not r.actor.startswith("participant:"))][:24]
    names = {u.id: u.name for u in db.scalars(select(User).where(User.org_id == ctx.org_id))}

    def who(actor: str) -> str:
        kind, _, ref = actor.partition(":")
        return {"participant": "受访者", "share": "客户", "system": "系统", "livekit": "视频服务器"}.get(kind) or names.get(ref, "团队成员")

    def what(r) -> str:
        if r.action == "device.check" and not (r.meta or {}).get("microphone"):
            return "设备检测未通过"
        return ACTION_ZH[r.action]

    activity = []  # no IPs, tokens or meta; the same thing repeated in a row (e.g. testing the device twice) shows once
    for r in rows:
        item = {"at": r.at.isoformat(), "who": who(r.actor), "what": what(r), "session": titles.get(r.target)}
        if activity and {k: v for k, v in activity[-1].items() if k != "at"} == {k: v for k, v in item.items() if k != "at"}:
            continue
        activity.append(item)
    return {"summary": s, "next": next_step(s, p.closed_at is not None), "activity": activity[:12]}


@router.post("/admin/demo/reset")
def reset_demo(ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Clear the demo company's sessions, participants and recordings so `python -m app.seed` can build a tidy set again.
    Refused for any organisation with a real account in it."""
    emails = [e for (e,) in db.execute(select(User.email).where(User.org_id == ctx.org_id))]
    if not emails or not all(e.lower().endswith(DEMO_DOMAIN) for e in emails):
        raise HTTPException(403, "只有演示公司（所有账号都是 @demo.test）才能清理演示数据")
    its = db.scalars(select(Interview).where(Interview.org_id == ctx.org_id)).all()
    ids = [i.id for i in its]
    if ids:
        for rec in db.scalars(select(Recording).where(Recording.interview_id.in_(ids))):
            _delete_file(rec.storage_key)
            purge_cache(rec.id)
            db.execute(delete(RecordingTrack).where(RecordingTrack.recording_id == rec.id))
        ins_ids = [x for (x,) in db.execute(select(Insight.id).where(Insight.interview_id.in_(ids)))]
        clip_ids = [x for (x,) in db.execute(select(Clip.id).where(Clip.interview_id.in_(ids)))]
        if clip_ids:
            db.execute(delete(ShareLink).where(ShareLink.clip_id.in_(clip_ids)))
        db.execute(delete(Clip).where(Clip.interview_id.in_(ids)))
        if ins_ids:
            db.execute(delete(Evidence).where(Evidence.insight_id.in_(ins_ids)))
        for model in (Insight, TranscriptSegment, Recording, Consent, InterviewObserver):
            db.execute(delete(model).where(model.interview_id.in_(ids)))
        db.execute(delete(Interview).where(Interview.id.in_(ids)))
    n_parts = db.execute(delete(Participant).where(Participant.org_id == ctx.org_id)).rowcount
    audit(db, ctx.org_id, ctx.actor, "demo.reset", ctx.org_id, interviews=len(ids), participants=n_parts)
    db.commit()
    return {"interviews": len(ids), "participants": n_parts}
