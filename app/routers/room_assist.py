"""The moderator's side panels in the room: outline progress, pace, who talks, follow-up hints and chapter summaries (app/room_assist.py). Read-only; for the operating team."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import concept, outline, room_assist
from ..db import get_db
from ..deps import Ctx, require_roles, visible_interview
from ..models import CaptionLine, Project

router = APIRouter(prefix="/api", tags=["room"])
OPERATE = ("admin", "researcher", "support", "moderator")
MAX_ASKED = 40


@router.get("/interviews/{iid}/assist")
def assist(iid: str, asked: str = Query("", max_length=1200), ctx: Ctx = Depends(require_roles(*OPERATE)), db: Session = Depends(get_db)):
    """Everything the room's panels need in one call. `asked` lists the hint keys already used or dismissed (comma separated) so they are not offered again."""
    itv = visible_interview(db, ctx, iid)
    proj = db.get(Project, itv.project_id)
    summary = outline.summary_of(proj.outline_text or "")
    lines = [{"id": c.id, "speaker": c.speaker, "text": c.text, "at_ms": c.at_ms}
             for c in db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq).limit(2000)).all()]
    planned = (summary["minutes"] if summary else None) or itv.duration_min or 45
    out = {"has_outline": bool(summary), "planned_min": planned, "talk": room_assist.talk_share(lines)}
    seen = {k for k in asked.split(",") if k}
    cov = room_assist.coverage(summary, lines) if summary else None
    out["probes"] = room_assist.probe_suggestions(lines, set(list(seen)[:MAX_ASKED]))[:room_assist.probe_limit(summary, cov)]
    if summary:
        mins = room_assist.part_minutes(summary["parts"], planned)
        out["outline"] = {"parts": [{"title": p["title"], "minutes": mins[i], "questions": [{"text": q["text"], "covered": next((x["covered"] for x in cov["questions"] if x["part"] == i and x["q"] == q["text"]), False)} for q in p["questions"] if not q.get("cover")]} for i, p in enumerate(summary["parts"])],
                          "covered": cov["covered"], "total": cov["total"], "current": cov["current"]}
        out["pacing"] = room_assist.pacing(cov, concept.at_ms(itv) if itv.status == "live" else None, planned)
        out["chapters"] = room_assist.chapter_summaries(summary, lines, cov)
    return out
