"""The 洞察与片段 page's respondent-data tab: the numbers, the rows, the hover card; what a client observer does not get; other companies."""
from conftest import Org
from test_week1 import transcribed


def _setup(org):
    itv, segs = transcribed(org)
    seg = [s for s in segs if s["speaker"] == "participant"][1]
    org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json={"theme": "信任", "statement": "怕假货", "evidence": [{"segment_id": seg["id"], "quote": "clear halal certification"}]})
    return itv, org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]


def test_rows_numbers_and_card(org):
    itv, pid = _setup(org)
    d = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()
    r = next(x for x in d["rows"] if x["id"] == itv["id"])
    assert r["code"] == "M-001" and r["insights"] == 1 and r["summary"] == ["怕假货"] and "Wei Ling" not in str(d)
    c = r["card"]
    assert c["words"] > 0 and sum(c["talk"]) in (99, 100) and c["step_names"] == ["邀请", "同意", "进房", "结束", "转写", "洞察"] and c["steps"][4] is True and c["steps"][5] is True
    assert c["quotes"] and c["quotes"][0]["text"] == "clear halal certification" and c["moderation"] in ("human", "ai")
    assert d["kpi"]["insights"] == 1 and d["kpi"]["approval_rate"] in (0, 100)


def test_a_planned_interview_has_no_numbers_yet(org):
    itv = org.interview()
    d = org.c.get(f"/api/projects/{itv['project_id']}/insights/overview", headers=org.h()).json()
    r = d["rows"][0]
    assert r["status"] == "scheduled" and r["insights"] == 0 and r["card"]["steps"][3] is False and d["kpi"]["completed"] == 0 and d["kpi"]["avg_minutes"] is None


def test_client_observer_gets_no_team_details(org):
    itv, pid = _setup(org)
    d = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h("observer")).json()
    r = d["rows"][0]
    assert "tags" not in r and not {"source", "moderator", "device"} & set(r["card"])


def test_other_companies_cannot_read_it(org):
    itv, pid = _setup(org)
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.get(f"/api/projects/{pid}/insights/overview", headers=other.h()).status_code == 404
