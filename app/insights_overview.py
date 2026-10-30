"""The 洞察与片段 page's first tab (受访者数据): one row per interview with what the team wants at a glance, and a richer card for the row the mouse is on.

Everything is counted from what is already stored; nothing is written by a model. A participant appears only as a code; a client observer gets no tags, source,
moderator or device (the team's own details), the same as the interview list.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Clip, Consent, Evidence, Insight, Interview, TranscriptSegment

MAX_QUOTES = 2
MAX_SUMMARY = 2
SUMMARY_CHARS = 60
STEP_NAMES = ["邀请", "同意", "进房", "结束", "转写", "洞察"]


def _minutes(itv: Interview) -> int | None:
    if itv.started_at and itv.ended_at:
        return max(0, round((itv.ended_at - itv.started_at).total_seconds() / 60))
    return None


def _insights(db: Session, iid: str) -> list[Insight]:
    rows = db.scalars(select(Insight).where(Insight.interview_id == iid).order_by(Insight.created_at)).all()
    return sorted(rows, key=lambda i: i.status != "approved")  # confirmed ones first


def _quotes(db: Session, ins: list[Insight]) -> list[dict]:
    out = []
    for i in ins:
        for ev in sorted(i.evidence, key=lambda e: e.start_ms):
            if len(out) < MAX_QUOTES:
                out.append({"text": ev.quote, "at_ms": ev.start_ms})
    return out


def _talk(db: Session, iid: str) -> tuple[int, list[int]]:
    """(characters the participant said, [moderator %, participant %] of the speaking time)."""
    segs = db.execute(select(TranscriptSegment.speaker, TranscriptSegment.start_ms, TranscriptSegment.end_ms, func.length(TranscriptSegment.text_orig)).where(TranscriptSegment.interview_id == iid)).all()
    t = {"moderator": 0, "participant": 0}
    words = 0
    for sp, a, b, n in segs:
        key = "moderator" if sp == "moderator" else "participant"
        t[key] += max(0, b - a)
        if key == "participant":
            words += n or 0
    total = t["moderator"] + t["participant"]
    share = [round(t["moderator"] * 100 / total), round(t["participant"] * 100 / total)] if total else [0, 0]
    return words, share


def row(db: Session, itv: Interview, code: str, team: bool, moderator_name: str) -> dict:
    """One interview as the table row plus the card. `team` False (a client observer) leaves out tags, source, moderator and device."""
    ins = _insights(db, itv.id)
    clips = db.scalar(select(func.count()).select_from(Clip).where(Clip.interview_id == itv.id)) or 0
    segs = db.scalar(select(func.count()).select_from(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id)) or 0
    consent = db.scalar(select(Consent.id).where(Consent.interview_id == itv.id).limit(1)) is not None
    words, share = _talk(db, itv.id)
    p = itv.participant
    steps = [True, consent, itv.entered_at is not None, itv.status == "ended", segs > 0, len(ins) > 0]
    out = {"id": itv.id, "code": code, "status": itv.status, "kind": itv.kind, "started_at": (itv.started_at or itv.scheduled_at).isoformat() if (itv.started_at or itv.scheduled_at) else None,
           "scheduled": itv.started_at is None, "minutes": _minutes(itv), "purged": itv.purged_at is not None,
           "insights": len(ins), "approved": sum(1 for i in ins if i.status == "approved"), "review": sum(1 for i in ins if i.status == "needs_review"), "clips": clips,
           "summary": [i.statement[:SUMMARY_CHARS] for i in ins[:MAX_SUMMARY]],
           "card": {"loc": f"{p.country or '—'} · {p.language or itv.language}", "words": words, "talk": share, "steps": steps, "step_names": STEP_NAMES,
                    "quotes": _quotes(db, ins), "scheduled_at": itv.scheduled_at.isoformat() if itv.scheduled_at else None}}
    out["transcript_state"] = "ready" if segs else ("waiting" if itv.status == "ended" and itv.entered_at else "none")
    if team:
        out["tags"] = p.tags or []
        out["card"].update({"source": p.source_name or p.source or "", "moderation": itv.moderation, "moderator": moderator_name,
                            "device": "麦克风检测通过" if itv.device_ok_at else "尚未检测"})
    return out


def kpis(rows: list[dict]) -> dict:
    """The three numbers on top: finished interviews, the average length, the share of insights a researcher has approved."""
    ended = [r for r in rows if r["status"] == "ended"]
    mins = [r["minutes"] for r in ended if r["minutes"] is not None]
    ins = sum(r["insights"] for r in rows)
    ok = sum(r["approved"] for r in rows)
    return {"completed": len(ended), "upcoming": sum(1 for r in rows if r["status"] == "scheduled"), "live": sum(1 for r in rows if r["status"] == "live"),
            "avg_minutes": round(sum(mins) / len(mins)) if mins else None, "insights": ins, "approved": ok, "approval_rate": round(ok * 100 / ins) if ins else None}
