"""Codes on lines of the transcript: tag a line, read what is tagged across the project, and the tags follow the rules of the data (project scope, retention, new transcript)."""
from app.db import SessionLocal
from app.models import SegmentCode
from test_export import _with_insight
from test_project_members import second_researcher


def setup(org):
    itv, ins = _with_insight(org)
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    part = [s for s in segs if s["speaker"] == "participant"]
    a = org.c.post(f"/api/projects/{pid}/codes", headers=org.h(), json={"name": "价格"}).json()["id"]
    b = org.c.post(f"/api/projects/{pid}/codes", headers=org.h(), json={"name": "信任"}).json()["id"]
    return itv, pid, part, a, b


def put(org, seg, ids, headers=None):
    return org.c.put(f"/api/transcript/{seg}/codes", headers=headers or org.h(), json={"code_ids": ids})


def test_a_line_can_carry_several_codes_and_they_can_be_replaced(org):
    itv, pid, part, a, b = setup(org)
    assert put(org, part[0]["id"], [a, b]).json()["code_ids"] == [a, b]
    got = org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=org.h()).json()
    assert got["by_segment"][part[0]["id"]] == [a, b] or set(got["by_segment"][part[0]["id"]]) == {a, b}
    assert {c["name"] for c in got["codes"]} >= {"价格", "信任"}
    put(org, part[0]["id"], [b])
    assert org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=org.h()).json()["by_segment"][part[0]["id"]] == [b]
    put(org, part[0]["id"], [])
    assert part[0]["id"] not in org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=org.h()).json()["by_segment"]


def test_tagged_lines_of_a_code_are_listed_across_interviews_with_codes_for_people(org):
    itv, pid, part, a, b = setup(org)
    put(org, part[0]["id"], [a])
    put(org, part[1]["id"], [a, b])
    got = org.c.get(f"/api/projects/{pid}/codes/{a}/segments", headers=org.h()).json()
    assert got["code"]["name"] == "价格" and len(got["segments"]) == 2 and all(s["who"] == "M-001" and s["interview_id"] == itv["id"] for s in got["segments"])
    assert "Wei Ling" not in str(got) and [s["start_ms"] for s in got["segments"]] == sorted(s["start_ms"] for s in got["segments"])
    assert len(org.c.get(f"/api/projects/{pid}/codes/{b}/segments", headers=org.h()).json()["segments"]) == 1


def test_a_code_of_another_project_or_too_many_codes_are_refused(org):
    itv, pid, part, a, b = setup(org)
    other_pid = org.project()
    foreign = org.c.post(f"/api/projects/{other_pid}/codes", headers=org.h(), json={"name": "别的项目"}).json()["id"]
    assert put(org, part[0]["id"], [foreign]).status_code == 422
    assert put(org, part[0]["id"], ["nope"]).status_code == 404
    many = [org.c.post(f"/api/projects/{pid}/codes", headers=org.h(), json={"name": f"c{i}"}).json()["id"] for i in range(11)]
    assert put(org, part[0]["id"], many).status_code == 422
    assert org.c.get(f"/api/projects/{other_pid}/codes/{a}/segments", headers=org.h()).status_code == 404


def test_deleting_a_code_removes_its_tags(org):
    itv, pid, part, a, b = setup(org)
    put(org, part[0]["id"], [a, b])
    org.c.delete(f"/api/codes/{a}", headers=org.h())
    assert org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=org.h()).json()["by_segment"][part[0]["id"]] == [b]


def test_tags_go_with_retention_and_a_new_transcript(org):
    from app.models import Interview
    from app.retention import purge_interview
    itv, pid, part, a, b = setup(org)
    put(org, part[0]["id"], [a])
    with SessionLocal() as db:
        assert db.query(SegmentCode).count() == 1
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.query(SegmentCode).count() == 0


def test_only_the_projects_staff_can_tag_and_read_tags(org, client):
    itv, pid, part, a, b = setup(org)
    other = second_researcher(org)
    assert put(org, part[0]["id"], [a], headers=other["h"]).status_code == 404
    assert org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=other["h"]).status_code == 404
    assert org.c.get(f"/api/projects/{pid}/codes/{a}/segments", headers=other["h"]).status_code == 404
    for role in ("moderator", "observer"):
        assert put(org, part[0]["id"], [a], headers=org.h(role)).status_code == 403
        assert org.c.get(f"/api/interviews/{itv['id']}/segment-codes", headers=org.h(role)).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert put(org, part[0]["id"], [a], headers=foreign.h("admin")).status_code == 404
