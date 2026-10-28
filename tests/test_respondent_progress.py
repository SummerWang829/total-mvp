"""The respondent table lists finished interviews; one that was cut off midway carries how many outline questions it got through."""
import json

from app import respondent_table
from app.db import SessionLocal
from app.models import Interview, Project

OUTLINE = """目标: 了解消费者如何选购护肤品
受访者话题: 护肤品选购
语言: en
时长: 30
## 暖场
- Could you tell me how you usually buy skincare products?
## 话题: 顾虑
- What makes you hesitate to try a new brand from overseas?
- How important is the packaging to you?
- Would you pay more for an organic label?
"""
QS = ["Could you tell me how you usually buy skincare products?", "What makes you hesitate to try a new brand from overseas?", "How important is the packaging to you?", "Would you pay more for an organic label?"]


def _finish(itv_id, answered):
    with SessionLocal() as db:
        row = db.get(Interview, itv_id)
        row.status = "ended"
        row.answers_json = json.dumps({"v": 1, "items": [{"q": q, "a": f"answer {i}" if i < answered else "", "seg": [], "at_ms": 0} for i, q in enumerate(QS)]})
        db.commit()


def test_a_cut_off_interview_says_how_many_questions_it_got_through(org):
    itv = org.interview()
    pid = itv["project_id"]
    assert org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE}).status_code == 200
    _finish(itv["id"], 2)
    r = next(x for x in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()["rows"] if x["id"] == itv["id"])
    assert r["progress"] == {"answered": 2, "asked": 4}


def test_a_complete_interview_and_an_unprocessed_one(org):
    itv = org.interview()
    pid = itv["project_id"]
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    _finish(itv["id"], 4)
    rows = {x["id"]: x for x in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()["rows"]}
    assert rows[itv["id"]]["progress"] == {"answered": 4, "asked": 4}
    other = org.interview(pid=pid)
    with SessionLocal() as db:
        db.get(Interview, other["id"]).status = "ended"   # finished, answers not worked out yet: nothing can be said
        db.commit()
    rows = {x["id"]: x for x in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()["rows"]}
    assert rows[other["id"]]["progress"] is None


def test_planned_interviews_and_clients_get_no_progress(org):
    itv = org.interview()
    pid = itv["project_id"]
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    r = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()["rows"][0]
    assert r["status"] == "scheduled" and "progress" not in r
    _finish(itv["id"], 1)
    assert "progress" not in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h("observer")).json()["rows"][0]


def test_progress_needs_questions_and_answers():
    class Itv:
        answers_json = ""
    assert respondent_table.progress(QS, Itv()) is None and respondent_table.progress([], Itv()) is None
