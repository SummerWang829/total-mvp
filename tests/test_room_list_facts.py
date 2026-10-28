"""The live-room list: the planned length beside the real one, and the questionnaire answers shown on the row's card."""
from app.db import SessionLocal
from app.models import ScreenerAnswer
from test_week1 import transcribed


def test_planned_and_actual_length_and_key_answers(org):
    itv, _ = transcribed(org)
    pid = itv["project_id"]
    with SessionLocal() as db:
        part = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["participant"]["id"]
        for i, (q, a) in enumerate([("多久用一次？", "每天"), ("在哪里买？", "网店"), ("预算？", "200 以内")]):
            db.add(ScreenerAnswer(org_id=org_org(db, part), participant_id=part, position=i, question=q, answer=a))
        db.commit()
    rows = org.c.get(f"/api/projects/{pid}/interviews", headers=org.h()).json()
    r = next(x for x in rows if x["id"] == itv["id"])
    assert r["duration_min"] == 45 and r["actual_min"] is not None and r["actual_min"] >= 0
    assert [k["a"] for k in r["key_answers"]] == ["每天", "网店", "200 以内"]


def test_not_ended_has_no_actual_length_and_observer_gets_no_answers(org):
    itv = org.interview()
    r = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()
    assert r["actual_min"] is None
    o = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h("observer")).json()
    assert "actual_min" not in o and "key_answers" not in o


def org_org(db, participant_id):
    from app.models import Participant
    return db.get(Participant, participant_id).org_id
