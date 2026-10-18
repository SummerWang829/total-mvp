"""The insight pool: approved interview insights, public-opinion topics, survey questions and team notes grouped into findings; findings backed by more than one kind of source are triangulated."""
from app import pool
from app.db import SessionLocal
from app.models import Insight, Interview, utcnow
from conftest import H
from test_export import _with_insight
from test_project_members import second_researcher


def approved_insight(org, theme, statement="受访者的说法"):
    itv, ins = _with_insight(org)
    with SessionLocal() as db:
        row = db.get(Insight, ins["id"])
        row.theme, row.statement, row.status = theme, statement, "approved"
        db.commit()
    return itv, ins


def pool_of(org, pid, headers=None):
    r = org.c.get(f"/api/projects/{pid}/pool", headers=headers or org.h())
    assert r.status_code == 200, r.text
    return r.json()


def run_listening(org, pid):
    sid = org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json={"kind": "mock", "name": "m", "config": {"brand": "Acme", "lang": "zh"}}).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())


def push_survey(org, pid, question="配送速度满意度"):
    org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": ["depth", "quant"]})
    key = org.c.post(f"/api/projects/{pid}/quant/keys", headers=org.h(), json={"name": "k"}).json()["key"]
    org.c.post("/api/ingest/surveys", headers={"Authorization": f"Bearer {key}"}, json={"survey_id": "S", "title": "满意度调查", "respondents": 400, "questions": [
        {"text": question, "type": "single", "results": [{"option": "不满意", "count": 300}, {"option": "满意", "count": 100}]}]})


def pid_of(org, itv):
    return org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]


def test_an_empty_project_has_an_empty_pool(org):
    pid = org.project()
    assert pool_of(org, pid) == {"counts": {"interview": 0, "sentiment": 0, "survey": 0, "note": 0}, "triangulated": 0, "findings": []}


def test_only_approved_interview_insights_enter_the_pool(org):
    itv, ins = _with_insight(org)           # a draft
    pid = pid_of(org, itv)
    assert pool_of(org, pid)["counts"]["interview"] == 0
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    got = pool_of(org, pid)
    assert got["counts"]["interview"] == 1 and got["findings"][0]["items"][0]["interview_id"] == itv["id"] and got["findings"][0]["kinds"] == ["interview"]


def test_the_same_theme_from_interviews_public_opinion_and_a_survey_is_one_triangulated_finding(org):
    itv, _ = approved_insight(org, "配送太慢", "受访者普遍抱怨配送太慢")
    pid = pid_of(org, itv)
    run_listening(org, pid)
    push_survey(org, pid)
    got = pool_of(org, pid)
    top = got["findings"][0]
    assert got["triangulated"] >= 1 and top["triangulated"] is True and set(top["kinds"]) == {"interview", "sentiment", "survey"}
    assert {i["kind"] for i in top["items"]} == {"interview", "sentiment", "survey"} and "配送" in top["theme"]
    survey = next(i for i in top["items"] if i["kind"] == "survey")
    assert "不满意 75%" in survey["text"] and "400 份回答" in survey["text"]
    senti = next(i for i in top["items"] if i["kind"] == "sentiment")
    assert "条提到" in senti["text"] and senti["weight"] >= 1


def test_unrelated_themes_stay_separate_and_the_better_supported_finding_comes_first(org):
    itv, _ = approved_insight(org, "信任与认证")
    pid = pid_of(org, itv)
    run_listening(org, pid)
    push_survey(org, pid, "配送速度满意度")
    findings = pool_of(org, pid)["findings"]
    themes = [f["theme"] for f in findings]
    assert "信任与认证" in themes and any("配送" in t for t in themes)
    assert len(findings[0]["kinds"]) >= len(findings[-1]["kinds"]) and findings[0]["triangulated"] is True
    assert next(f for f in findings if f["theme"] == "信任与认证")["triangulated"] is False


def test_team_notes_join_the_finding_with_the_same_theme_and_can_be_removed(org):
    itv, _ = approved_insight(org, "价格敏感")
    pid = pid_of(org, itv)
    r = org.c.post(f"/api/projects/{pid}/pool/notes", headers=org.h(), json={"theme": "价格敏感", "text": "假设：折扣会放大这个效应"})
    assert r.status_code == 201
    f = pool_of(org, pid)["findings"][0]
    assert set(f["kinds"]) == {"interview", "note"} and f["triangulated"] is True
    assert org.c.delete(f"/api/pool/notes/{r.json()['id']}", headers=org.h()).status_code == 204
    assert pool_of(org, pid)["counts"]["note"] == 0
    assert org.c.post(f"/api/projects/{pid}/pool/notes", headers=org.h(), json={"theme": " ", "text": "x"}).status_code == 422


def test_purged_interviews_leave_the_pool(org):
    itv, _ = approved_insight(org, "价格敏感")
    pid = pid_of(org, itv)
    assert pool_of(org, pid)["counts"]["interview"] == 1
    with SessionLocal() as db:
        db.get(Interview, itv["id"]).purged_at = utcnow()
        db.commit()
    assert pool_of(org, pid)["counts"]["interview"] == 0


def test_grouping_helper_joins_close_themes_only():
    items = [pool.Item("interview", "配送太慢", "a", "1"), pool.Item("sentiment", "配送", "b", "配送"), pool.Item("survey", "价格是否合理", "c", "2"), pool.Item("note", "Delivery speed", "d", "3"),
             pool.Item("survey", "delivery speed rating", "e", "4")]
    groups = pool.group(items)
    by = {tuple(sorted(i.ref for i in g.items)) for g in groups}
    assert ("1", "配送") in by and ("2",) in by and ("3", "4") in by
    assert pool.group([]) == []


def test_who_may_see_the_pool(org, client):
    itv, _ = approved_insight(org, "价格敏感")
    pid = pid_of(org, itv)
    other = second_researcher(org)
    assert org.c.get(f"/api/projects/{pid}/pool", headers=other["h"]).status_code == 404
    assert org.c.post(f"/api/projects/{pid}/pool/notes", headers=other["h"], json={"theme": "a", "text": "b"}).status_code == 404
    note = org.c.post(f"/api/projects/{pid}/pool/notes", headers=org.h(), json={"theme": "a", "text": "b"}).json()["id"]
    assert org.c.delete(f"/api/pool/notes/{note}", headers=other["h"]).status_code == 404
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/projects/{pid}/pool", headers=org.h(role)).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/projects/{pid}/pool", headers=foreign.h("admin")).status_code == 404
    assert foreign.c.delete(f"/api/pool/notes/{note}", headers=foreign.h("admin")).status_code == 404
    assert org.c.get(f"/api/projects/{pid}/pool", headers=org.h("admin")).status_code == 200
