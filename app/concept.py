"""Concept-test templates: the moderator's script and the materials that go on the participant's screen.

The materials are fixed here on purpose. Participants' browsers load them (a player page in an iframe, or a picture), so the addresses
must come from this reviewed list - never from a form field.  Videos are the embedded player of a public page, never a file we host.
"""
import importlib.util
import json
import os
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Interview, InterviewPlan, RoomState, Stimulus, utcnow

TEMPLATES: dict[str, dict] = {}   # the product has no built-in script: a team writes its own; the automated tests load a sample below
if os.getenv("CONCEPT_TEST_FIXTURE") == "1":
    _spec = importlib.util.spec_from_file_location("concept_fixture", os.path.join(os.path.dirname(__file__), "..", "tests", "concept_fixture.py"))
    _fixture = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_fixture)
    TEMPLATES[_fixture.SAMPLE["key"]] = _fixture.SAMPLE


class TemplateError(Exception):
    """The requested template does not exist."""


def at_ms(itv: Interview) -> int:
    """Milliseconds since the interview started, on OUR clock (never taken from a browser); 0 before it starts."""
    if not itv.started_at:
        return 0
    return max(0, int((utcnow() - itv.started_at) / timedelta(milliseconds=1)))


def public_script(template: dict) -> dict:
    """The part of a template that is stored as the interview's script (the stimuli are stored as their own rows)."""
    return {k: template[k] for k in ("key", "title", "language", "duration_min", "research_goal", "steps")}


def install(db: Session, org_id: str, interview_id: str, key: str) -> InterviewPlan:
    """Give an interview the template's script and materials (replacing any earlier ones and clearing the screen).

    Raises:
        TemplateError: the template name is unknown.
    """
    tpl = TEMPLATES.get(key)
    if not tpl:
        raise TemplateError("没有这个模板：" + key)
    db.execute(delete(Stimulus).where(Stimulus.interview_id == interview_id))
    db.execute(delete(RoomState).where(RoomState.interview_id == interview_id))
    db.execute(delete(InterviewPlan).where(InterviewPlan.interview_id == interview_id))
    db.flush()
    for pos, s in enumerate(tpl["stimuli"]):
        db.add(Stimulus(org_id=org_id, interview_id=interview_id, key=s["key"], position=pos, kind=s["kind"], title=s["title"], url=s["url"], source_note=s["source_note"]))
    plan = InterviewPlan(interview_id=interview_id, org_id=org_id, template=key, title=tpl["title"], script_json=json.dumps(public_script(tpl), ensure_ascii=False))
    db.add(plan)
    return plan


def plan_out(db: Session, interview_id: str) -> dict | None:
    plan = db.get(InterviewPlan, interview_id)
    if not plan:
        return None
    return {"template": plan.template, "title": plan.title, "script": json.loads(plan.script_json), "stimuli": stimuli_out(db, interview_id, staff=True)}


def stimuli_out(db: Session, interview_id: str, staff: bool) -> list[dict]:
    rows = db.scalars(select(Stimulus).where(Stimulus.interview_id == interview_id).order_by(Stimulus.position)).all()
    return [stimulus_out(s, staff) for s in rows]


def stimulus_out(s: Stimulus, staff: bool) -> dict:
    """Staff get the title, key and source note; the participant only gets what is needed to draw it (no title: it could name the brand)."""
    if not staff:
        return {"id": s.id, "kind": s.kind, "url": s.url}
    return {"id": s.id, "key": s.key, "kind": s.kind, "title": s.title, "url": s.url, "source_note": s.source_note}


def current_stimulus(db: Session, interview_id: str, staff: bool) -> dict | None:
    """What is on the participant's screen right now (None: nothing)."""
    st = db.get(RoomState, interview_id)
    if not st or not st.stimulus_id:
        return None
    s = db.get(Stimulus, st.stimulus_id)
    return stimulus_out(s, staff) if s and s.interview_id == interview_id else None
