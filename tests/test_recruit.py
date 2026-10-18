"""Recruiting: spreadsheet import, tags and filters, eligibility rules with reasons, quotas with progress, and inviting many at once."""
import pytest

from app import recruit
from app.db import SessionLocal
from app.models import Participant
from test_project_members import second_researcher

CSV = ("name,email,country,language,tags,年龄,是否使用过 Acme\n"
       "林小雨,lin@example.com,SG,zh,女性;学生,22,是\n"
       "Amir,amir@example.com,sg,en,男性,35,否\n"
       "王大明,,SG,zh,,17,是\n"
       "No Country,nc@example.com,,en,,40,是\n")


def imp(org, pid, text=CSV, dry=False, headers=None):
    return org.c.post(f"/api/projects/{pid}/participants/import", headers=headers or org.h(), json={"csv": text, "dry_run": dry})


def people(org, pid, **params):
    return org.c.get(f"/api/projects/{pid}/participants", headers=org.h(), params=params).json()


# ---------- import ----------
def test_import_creates_people_with_tags_and_turns_extra_columns_into_screening_answers(org):
    pid = org.project()
    r = imp(org, pid)
    assert r.status_code == 200 and r.json()["created"] == 4 and r.json()["skipped"] == []
    got = {p["name"]: p for p in people(org, pid)}
    assert got["林小雨"]["tags"] == ["女性", "学生"] and got["林小雨"]["country"] == "SG" and got["Amir"]["country"] == "SG" and got["林小雨"]["screener"] == 2
    answers = org.c.get(f"/api/participants/{got['林小雨']['id']}/screener", headers=org.h()).json()["items"]
    assert {"question": "年龄", "answer": "22"} in answers and {"question": "是否使用过 Acme", "answer": "是"} in answers


def test_a_dry_run_changes_nothing_and_repeating_the_import_skips_people_already_there(org):
    pid = org.project()
    d = imp(org, pid, dry=True).json()
    assert d["dry_run"] is True and d["created"] == 4 and people(org, pid) == []
    imp(org, pid)
    again = imp(org, pid).json()
    assert again["created"] == 0 and len(again["skipped"]) == 4 and all("已经在" in s["why"] for s in again["skipped"])
    assert len(people(org, pid)) == 4


def test_bad_rows_are_reported_with_their_line_and_the_rest_still_go_in(org):
    pid = org.project()
    bad = "name,email,country,language\nOK One,ok@example.com,SG,en\n,noname@example.com,SG,en\nBad Mail,not-an-email,SG,en\nBad Country,b@example.com,SGP,en\nBad Lang,l@example.com,SG,fr\n"
    r = imp(org, pid, bad).json()
    assert r["created"] == 1 and {s["line"] for s in r["skipped"]} == {3, 4, 5, 6}
    assert [p["name"] for p in people(org, pid)] == ["OK One"]


def test_unusable_files_are_refused_clearly(org):
    pid = org.project()
    assert imp(org, pid, "email,country\na@b.co,SG\n").status_code == 422     # no name column
    assert imp(org, pid, "   ").status_code == 422
    big = "name\n" + "\n".join(f"P{i}" for i in range(501))
    assert imp(org, pid, big).status_code == 422 and "500" in imp(org, pid, big).json()["detail"]
    assert imp(org, pid, "﻿name\tcountry\nTabbed\tSG\n").json()["created"] == 1   # a BOM and tab separation (spreadsheet paste) both work


def test_people_from_regions_not_yet_open_are_stored_but_not_invitable(org):
    pid = org.project()
    imp(org, pid, "name,country\nGerman Guest,DE\nLocal,SG\n")
    got = {p["name"]: p for p in people(org, pid)}
    assert got["German Guest"]["screening"] == "blocked_region" and got["Local"]["screening"] == "eligible"


# ---------- tags and filters ----------
def test_tags_can_be_edited_and_people_filtered(org):
    pid = org.project()
    imp(org, pid)
    by = {p["name"]: p["id"] for p in people(org, pid)}
    r = org.c.put(f"/api/participants/{by['Amir']}/tags", headers=org.h(), json={"tags": [" VIP ", "VIP", "", "x" * 50]})
    assert r.json()["tags"] == ["VIP", "x" * 30]
    assert [p["name"] for p in people(org, pid, tag="VIP")] == ["Amir"]
    assert {p["name"] for p in people(org, pid, country="sg")} == {"林小雨", "Amir", "王大明"}
    assert {p["name"] for p in people(org, pid, language="en")} == {"Amir", "No Country"}
    assert len(people(org, pid)) == 4


# ---------- eligibility rules ----------
RULES = [{"question": "年龄", "op": "between", "value": [18, 65], "effect": "require", "note": "年龄 18–65"},
         {"question": "是否使用过", "op": "equals", "value": "是", "effect": "require", "note": "用过 Acme"},
         {"field": "country", "op": "in", "value": ["SG"], "effect": "require", "note": "新加坡"}]


def put_rules(org, pid, rules=RULES, headers=None):
    return org.c.put(f"/api/projects/{pid}/recruiting/rules", headers=headers or org.h(), json={"rules": rules})


def test_rules_decide_who_qualifies_and_say_why(org):
    pid = org.project()
    imp(org, pid)
    r = put_rules(org, pid)
    assert r.status_code == 200 and r.json()["rules_version"] == 1 and r.json()["counts"] == {"qualified": 1, "disqualified": 2, "review": 1, "not_screened": 0}
    got = {p["name"]: p for p in people(org, pid)}
    assert got["林小雨"]["screen_result"] == "qualified" and got["林小雨"]["screen_reason"] == ""
    assert got["Amir"]["screen_result"] == "disqualified" and "用过 Acme" in got["Amir"]["screen_reason"]
    assert got["王大明"]["screen_result"] == "disqualified" and "年龄 18–65" in got["王大明"]["screen_reason"]
    assert got["No Country"]["screen_result"] == "review" and "缺少" in got["No Country"]["screen_reason"] and "新加坡" in got["No Country"]["screen_reason"]   # country unknown yet: a person decides
    assert [p["name"] for p in people(org, pid, screen="qualified")] == ["林小雨"]


def test_exclude_flag_and_missing_answers_work_and_changing_rules_makes_a_new_version(org):
    pid = org.project()
    imp(org, pid, "name,country,职业\nA,SG,医生\nB,SG,学生\nC,SG,\n")
    r = put_rules(org, pid, [{"question": "职业", "op": "contains", "value": "医", "effect": "exclude", "note": "医疗行业"}, {"question": "职业", "op": "answered", "effect": "require", "note": "填了职业"}])
    got = {p["name"]: p for p in people(org, pid)}
    assert got["A"]["screen_result"] == "disqualified" and "医疗行业" in got["A"]["screen_reason"]
    assert got["B"]["screen_result"] == "qualified" and got["C"]["screen_result"] == "review" and "缺少" in got["C"]["screen_reason"]
    flag = put_rules(org, pid, [{"question": "职业", "op": "equals", "value": "学生", "effect": "flag", "note": "学生要人工看"}]).json()
    assert flag["rules_version"] == 2
    got = {p["name"]: p for p in people(org, pid)}
    assert got["B"]["screen_result"] == "review" and got["A"]["screen_result"] == "qualified"
    assert put_rules(org, pid, []).json()["counts"]["qualified"] == 3     # no rules: everyone qualifies


def test_numbers_are_read_out_of_answers_like_two_hundred_dollars_a_month(org):
    pid = org.project()
    imp(org, pid, "name,country,月花费\nA,SG,\"大约 S$1,200 元\"\nB,SG,300\nC,SG,不确定\n")
    put_rules(org, pid, [{"question": "月花费", "op": "gte", "value": 500, "effect": "require", "note": "花费≥500"}])
    got = {p["name"]: p["screen_result"] for p in people(org, pid)}
    assert got == {"A": "qualified", "B": "disqualified", "C": "disqualified"}


def test_bad_rules_are_refused_with_the_rule_number(org):
    pid = org.project()
    for rules in ([{"op": "equals", "value": "x", "effect": "require"}],                                  # neither a field nor a question
                  [{"field": "country", "question": "x", "op": "equals", "value": "x", "effect": "require"}],   # both
                  [{"field": "salary", "op": "equals", "value": "x", "effect": "require"}],
                  [{"question": "q", "op": "magic", "value": "x", "effect": "require"}],
                  [{"question": "q", "op": "gte", "value": "abc", "effect": "require"}],
                  [{"question": "q", "op": "between", "value": [1], "effect": "require"}],
                  [{"question": "q", "op": "in", "value": [], "effect": "require"}],
                  [{"question": "q", "op": "equals", "value": "x", "effect": "maybe"}], ["not a rule"], [{}] * 31):
        r = put_rules(org, pid, rules)
        assert r.status_code == 422, rules
    assert "第 1 条" in put_rules(org, pid, [{"question": "q", "op": "magic", "value": "x", "effect": "require"}]).json()["detail"]


def test_new_people_are_screened_at_import_when_rules_exist(org):
    pid = org.project()
    put_rules(org, pid)
    imp(org, pid)
    assert {p["name"]: p["screen_result"] for p in people(org, pid)}["林小雨"] == "qualified"
    assert org.c.post(f"/api/projects/{pid}/recruiting/apply", headers=org.h()).json()["counts"]["qualified"] == 1


# ---------- quotas ----------
def test_quotas_show_how_many_there_are_against_the_target_and_ignore_the_disqualified(org):
    pid = org.project()
    imp(org, pid)
    put_rules(org, pid)
    r = org.c.put(f"/api/projects/{pid}/recruiting/quotas", headers=org.h(), json={"quotas": [
        {"field": "country", "value": "sg", "target": 2, "name": "新加坡"}, {"field": "tag", "value": "女性", "target": 1},
        {"field": "answer", "question": "年龄", "value": "22", "target": 3}]})
    assert r.status_code == 200
    q = {x["name"]: x for x in r.json()["quotas"]}
    assert q["新加坡"]["have"] == 1 and q["新加坡"]["target"] == 2 and q["新加坡"]["full"] is False       # only the qualified one counts
    assert q["tag=女性"]["have"] == 1 and q["tag=女性"]["full"] is True and q["answer=22"]["have"] == 1
    assert org.c.get(f"/api/projects/{pid}/recruiting", headers=org.h()).json()["quotas"][0]["have"] == 1


def test_bad_quotas_are_refused(org):
    pid = org.project()
    for q in ([{"field": "shoe", "value": "x", "target": 1}], [{"field": "country", "value": "", "target": 1}], [{"field": "country", "value": "SG", "target": 0}],
              [{"field": "country", "value": "SG", "target": "many"}], [{"field": "answer", "value": "x", "target": 1}], ["x"]):
        assert org.c.put(f"/api/projects/{pid}/recruiting/quotas", headers=org.h(), json={"quotas": q}).status_code == 422, q


# ---------- inviting many ----------
def test_batch_invite_makes_a_session_and_link_per_person_and_skips_the_disqualified(org):
    pid = org.project()
    imp(org, pid)
    put_rules(org, pid)
    ids = [p["id"] for p in people(org, pid)]
    r = org.c.post(f"/api/projects/{pid}/interviews/batch", headers=org.h(), json={"participant_ids": ids, "title": "第一批深访", "language": "zh"})
    assert r.status_code == 201
    body = r.json()
    assert [i["name"] for i in body["invites"]] == ["林小雨", "No Country"] and body["invites"][0]["invite_path"].startswith("/join.html?t=")   # "review" people may be invited: a person decided
    assert len(body["skipped"]) == 2 and all("不符合条件" in s["why"] for s in body["skipped"])
    assert len(org.c.get(f"/api/projects/{pid}/interviews", headers=org.h()).json()) == 2


def test_batch_invite_reports_people_who_cannot_be_invited_and_rejects_strangers(org):
    pid = org.project()
    imp(org, pid, "name,country\nGerman Guest,DE\nLocal,SG\n")
    ids = [p["id"] for p in people(org, pid)]
    body = org.c.post(f"/api/projects/{pid}/interviews/batch", headers=org.h(), json={"participant_ids": ids, "title": "批量"}).json()
    assert [i["name"] for i in body["invites"]] == ["Local"] and body["skipped"][0]["name"] == "German Guest"
    other = org.participant(org.project())
    assert org.c.post(f"/api/projects/{pid}/interviews/batch", headers=org.h(), json={"participant_ids": [other["id"]], "title": "x"}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/interviews/batch", headers=org.h(), json={"participant_ids": [], "title": "x"}).status_code == 422


# ---------- who may ----------
def test_only_the_projects_staff_can_recruit(org, client):
    pid = org.project()
    imp(org, pid)
    pid_person = people(org, pid)[0]["id"]
    other = second_researcher(org)
    calls = [("post", f"/api/projects/{pid}/participants/import", {"csv": "name\nX"}), ("get", f"/api/projects/{pid}/recruiting", None),
             ("put", f"/api/projects/{pid}/recruiting/rules", {"rules": []}), ("post", f"/api/projects/{pid}/recruiting/apply", None),
             ("put", f"/api/projects/{pid}/recruiting/quotas", {"quotas": []}), ("put", f"/api/participants/{pid_person}/tags", {"tags": ["a"]}),
             ("post", f"/api/projects/{pid}/interviews/batch", {"participant_ids": [pid_person], "title": "x"})]
    for method, url, body in calls:
        r = getattr(org.c, method)(url, headers=other["h"], **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
        for role in ("moderator", "observer"):
            assert getattr(org.c, method)(url, headers=org.h(role), **({"json": body} if body is not None else {})).status_code == 403
    assert recruit.clean_tags("a; b|c，a") == ["a", "b", "c"]
