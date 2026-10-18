"""Quantitative results: project keys, the keyed ingest API (people and aggregated survey results), spreadsheet import, and who may do what."""
import pytest

from app import quant, ratelimit
from app.db import SessionLocal
from app.models import IngestKey, Participant
from test_project_members import second_researcher

SURVEY = {"survey_id": "S-2026-10", "title": "品牌认知问卷", "respondents": 500, "questions": [
    {"key": "q1", "text": "你听说过 Acme 吗？", "type": "single", "results": [{"option": "听说过", "count": 320}, {"option": "没听说过", "count": 180}]},
    {"key": "q2", "text": "总体满意度（1-5）", "type": "scale", "mean": 3.84, "results": [{"option": "5", "count": 100}, {"option": "4", "count": 200}]},
    {"key": "q3", "text": "还有什么想说的？", "type": "open"}]}
PEOPLE = {"participants": [
    {"external_id": "P1", "name": "林小雨", "email": "lin@example.com", "country": "sg", "language": "zh", "tags": ["女性"], "answers": [{"question": "年龄", "answer": "22"}]},
    {"external_id": "P2", "name": "Amir", "country": "SG", "language": "en"}]}


def make_key(org, pid, name="问卷平台", headers=None):
    r = org.c.post(f"/api/projects/{pid}/quant/keys", headers=headers or org.h(), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def _limits():
    ratelimit.reset()
    yield
    ratelimit.reset()


# ---------- keys ----------
def test_a_key_is_shown_once_stored_as_a_hash_and_works_until_revoked(org):
    pid = org.project()
    k = make_key(org, pid)
    assert k["key"].startswith("xtk_") and k["prefix"] == k["key"][:8]
    with SessionLocal() as db:
        row = db.query(IngestKey).one()
        assert row.key_hash != k["key"] and k["key"] not in row.key_hash and row.key_hash == quant.hash_key(k["key"])
    listed = org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()
    assert "key" not in str(listed["keys"]) and listed["keys"][0]["prefix"] == k["prefix"] and listed["keys"][0]["revoked"] is False
    assert org.c.get("/api/ingest/ping", headers=bearer(k["key"])).json() == {"ok": True, "project": "P1"}
    assert org.c.delete(f"/api/projects/{pid}/quant/keys/{k['id']}", headers=org.h()).status_code == 204
    assert org.c.get("/api/ingest/ping", headers=bearer(k["key"])).status_code == 401
    assert org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["keys"][0]["revoked"] is True


def test_wrong_missing_and_foreign_keys_get_401(org):
    assert org.c.get("/api/ingest/ping").status_code == 401
    assert org.c.get("/api/ingest/ping", headers=bearer("xtk_nope")).status_code == 401
    assert org.c.get("/api/ingest/ping", headers=bearer("garbage")).status_code == 401
    assert org.c.post("/api/ingest/surveys", headers=bearer("xtk_x"), json=SURVEY).status_code == 401


def test_only_owners_and_admins_manage_keys_and_a_project_is_limited_to_ten_live_keys(org):
    pid = org.project()
    other = second_researcher(org)
    org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]})
    assert org.c.post(f"/api/projects/{pid}/quant/keys", headers=other["h"], json={"name": "x"}).status_code == 403
    k = make_key(org, pid, headers=org.h("admin"))
    assert org.c.delete(f"/api/projects/{pid}/quant/keys/{k['id']}", headers=other["h"]).status_code == 403
    for i in range(9):
        make_key(org, pid, name=f"k{i}")
    assert org.c.post(f"/api/projects/{pid}/quant/keys", headers=org.h(), json={"name": "eleventh"}).status_code == 409


# ---------- people ----------
def test_people_from_the_survey_tool_are_created_then_updated_by_external_id(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    r = org.c.post("/api/ingest/participants", headers=bearer(key), json=PEOPLE)
    assert r.status_code == 200 and r.json() == {"created": 2, "updated": 0}
    people = {p["name"]: p for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert people["林小雨"]["country"] == "SG" and people["林小雨"]["tags"] == ["女性"] and people["林小雨"]["screener"] == 1 and people["Amir"]["language"] == "en"
    again = {"participants": [{**PEOPLE["participants"][0], "name": "林小雨（更新）", "answers": [{"question": "年龄", "answer": "23"}, {"question": "城市", "answer": "新加坡"}]}]}
    assert org.c.post("/api/ingest/participants", headers=bearer(key), json=again).json() == {"created": 0, "updated": 1}
    people = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()
    assert len(people) == 2 and next(p for p in people if "更新" in p["name"])["screener"] == 2


def test_people_who_asked_to_be_erased_are_not_brought_back(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    org.c.post("/api/ingest/participants", headers=bearer(key), json=PEOPLE)
    pid1 = next(p["id"] for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json() if p["name"] == "Amir")
    org.c.delete(f"/api/participants/{pid1}/data", headers=org.h())
    r = org.c.post("/api/ingest/participants", headers=bearer(key), json=PEOPLE).json()
    assert r["created"] == 0
    with SessionLocal() as db:
        erased = db.get(Participant, pid1)
        assert erased.anonymized_at is not None and erased.name != "Amir"


def test_rules_apply_to_people_who_arrive_through_the_api(org):
    pid = org.project()
    org.c.put(f"/api/projects/{pid}/recruiting/rules", headers=org.h(), json={"rules": [{"question": "年龄", "op": "gte", "value": 18, "effect": "require", "note": "成年"}]})
    key = make_key(org, pid)["key"]
    org.c.post("/api/ingest/participants", headers=bearer(key), json={"participants": [{"external_id": "A", "name": "成年人", "answers": [{"question": "年龄", "answer": "30"}]},
                                                                                      {"external_id": "B", "name": "未成年", "answers": [{"question": "年龄", "answer": "15"}]}]})
    got = {p["name"]: p["screen_result"] for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert got == {"成年人": "qualified", "未成年": "disqualified"}


def test_bad_people_payloads_are_refused_with_the_position_and_store_nothing(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    for people in ([], [{"name": "no id"}], [{"external_id": "x"}], [{"external_id": "x", "name": "a", "email": "bad"}], [{"external_id": "x", "name": "a", "language": "fr"}],
                   [{"external_id": "x", "name": "a", "answers": [{"question": "q"}]}], [{"external_id": str(i), "name": "n"} for i in range(201)], "nope"):
        assert org.c.post("/api/ingest/participants", headers=bearer(key), json={"participants": people}).status_code == 422, str(people)[:60]
    assert org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json() == []


# ---------- survey results ----------
def test_results_are_stored_and_the_same_survey_id_replaces_them(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    assert org.c.post("/api/ingest/surveys", headers=bearer(key), json=SURVEY).json()["created"] is True
    newer = {**SURVEY, "respondents": 650}
    assert org.c.post("/api/ingest/surveys", headers=bearer(key), json=newer).json()["created"] is False
    surveys = org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["surveys"]
    assert len(surveys) == 1 and surveys[0]["respondents"] == 650 and [q["type"] for q in surveys[0]["questions"]] == ["single", "scale", "open"]
    assert surveys[0]["questions"][1]["mean"] == 3.84 and surveys[0]["source"] == "api"
    assert org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["keys"][0]["last_used_at"]


def test_bad_survey_payloads_are_refused(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    base = SURVEY
    bad = [{**base, "questions": []}, {**base, "questions": [{"text": "", "type": "single", "results": [{"option": "a", "count": 1}]}]},
           {**base, "questions": [{"text": "q", "type": "weird", "results": [{"option": "a", "count": 1}]}]},
           {**base, "questions": [{"text": "q", "type": "single", "results": [{"option": "a", "count": -1}]}]},
           {**base, "questions": [{"text": "q", "type": "single", "results": [{"option": "a", "count": "x"}]}]},
           {**base, "questions": [{"text": "q", "type": "single"}]},
           {**base, "questions": [{"text": "q", "type": "single", "results": [{"option": str(i), "count": 1} for i in range(61)]}]},
           {**base, "respondents": -1}, {**base, "survey_id": ""}]
    for body in bad:
        assert org.c.post("/api/ingest/surveys", headers=bearer(key), json=body).status_code == 422, str(body)[:80]


def test_the_ingest_api_is_rate_limited_per_key(org):
    pid = org.project()
    key = make_key(org, pid)["key"]
    codes = [org.c.get("/api/ingest/ping", headers=bearer(key)).status_code for _ in range(122)]
    assert codes[:120] == [200] * 120 and codes[120:] == [429, 429]
    other_key = make_key(org, pid, name="另一把")["key"]
    assert org.c.get("/api/ingest/ping", headers=bearer(other_key)).status_code == 200


# ---------- spreadsheet import ----------
def test_a_results_spreadsheet_becomes_a_survey_and_importing_again_replaces_it(org):
    pid = org.project()
    csv_text = "题目,选项,人数\n你听说过 Acme 吗？,听说过,\"1,320\"\n你听说过 Acme 吗？,没听说过,180\n购买渠道,线上,300\n购买渠道,线下,100\n"
    r = org.c.post(f"/api/projects/{pid}/quant/surveys/import", headers=org.h(), json={"title": "手工导入", "csv": csv_text, "respondents": 1500})
    assert r.status_code == 201 and r.json()["source"] == "csv" and [q["text"] for q in r.json()["questions"]] == ["你听说过 Acme 吗？", "购买渠道"]
    assert r.json()["questions"][0]["results"][0] == {"option": "听说过", "count": 1320}
    org.c.post(f"/api/projects/{pid}/quant/surveys/import", headers=org.h(), json={"title": "手工导入", "csv": "question,option,count\nQ,a,1\n", "respondents": 1})
    surveys = org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["surveys"]
    assert len(surveys) == 1 and surveys[0]["respondents"] == 1
    assert org.c.delete(f"/api/quant/surveys/{surveys[0]['id']}", headers=org.h()).status_code == 204
    assert org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["surveys"] == []


def test_bad_spreadsheets_are_refused(org):
    pid = org.project()
    for text in ("a,b\n1,2\n", "question,option,count\nQ,a,many\n", "   \n"):
        r = org.c.post(f"/api/projects/{pid}/quant/surveys/import", headers=org.h(), json={"title": "x", "csv": text, "respondents": 1})
        assert r.status_code == 422, text


# ---------- who ----------
def test_other_researchers_roles_and_companies_are_kept_out_of_the_page(org, client):
    pid = org.project()
    key = make_key(org, pid)["key"]
    org.c.post("/api/ingest/surveys", headers=bearer(key), json=SURVEY)
    sid = org.c.get(f"/api/projects/{pid}/quant", headers=org.h()).json()["surveys"][0]["id"]
    other = second_researcher(org)
    for method, url, body in [("get", f"/api/projects/{pid}/quant", None), ("post", f"/api/projects/{pid}/quant/keys", {"name": "x"}),
                              ("post", f"/api/projects/{pid}/quant/surveys/import", {"title": "x", "csv": "question,option,count\nQ,a,1"}), ("delete", f"/api/quant/surveys/{sid}", None)]:
        assert getattr(org.c, method)(url, headers=other["h"], **({"json": body} if body is not None else {})).status_code == 404, url
        for role in ("moderator", "observer"):
            assert getattr(org.c, method)(url, headers=org.h(role), **({"json": body} if body is not None else {})).status_code == 403, (role, url)
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/projects/{pid}/quant", headers=foreign.h("admin")).status_code == 404
    assert foreign.c.delete(f"/api/quant/surveys/{sid}", headers=foreign.h("admin")).status_code == 404
