"""Research methods per project: chosen when the project is made (with the team), changeable by the owner, shown as a star map on the project home."""
import pytest

from app import methods
from app.db import SessionLocal
from app.models import Project
from test_project_members import second_researcher


def make(org, **body):
    return org.c.post("/api/projects", headers=org.h(), json={"name": "向导项目", "market": "SG", "language": "en", **body})


def test_a_project_made_without_saying_uses_every_method_and_older_projects_do_too(org):
    pid = org.project()
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["methods"] == ["depth", "group", "async", "listening"]
    with SessionLocal() as db:
        db.get(Project, pid).methods = ""   # an old row from before methods existed
        db.commit()
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["methods"] == list(methods.DEFAULT)


def test_the_wizard_chooses_methods_and_adds_researchers_to_the_team(org):
    other = second_researcher(org)
    r = make(org, methods=["listening", "depth", "depth"], member_ids=[other["id"]])
    assert r.status_code == 201 and r.json()["methods"] == ["depth", "listening"]       # standard order, no repeats
    pid = r.json()["id"]
    members = org.c.get(f"/api/projects/{pid}/members", headers=org.h()).json()["members"]
    assert sorted(m["name"] for m in members) == sorted(["researcher", "Second"]) and sum(m["is_owner"] for m in members) == 1
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=other["h"]).json()]


def test_bad_wizard_input_is_refused_and_nothing_is_made(org):
    for body in ({"methods": []}, {"methods": ["depth", "telepathy"]}, {"member_ids": ["nobody"]}, {"member_ids": [org.users["moderator"]]}):
        assert make(org, **body).status_code == 422, body
    gone = second_researcher(org, "Gone")
    org.c.patch(f"/api/users/{gone['id']}", headers=org.h("admin"), json={"active": False})
    assert make(org, member_ids=[gone["id"]]).status_code == 422
    assert [p["name"] for p in org.c.get("/api/projects", headers=org.h()).json()] == []


def test_only_owners_and_admins_change_methods_and_the_data_stays(org):
    pid = org.project()
    org.interview(pid=pid)
    other = second_researcher(org)
    org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]})
    assert org.c.put(f"/api/projects/{pid}/methods", headers=other["h"], json={"methods": ["listening"]}).status_code == 403   # a plain member
    r = org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": ["listening", "async"]})
    assert r.status_code == 200 and r.json()["methods"] == ["async", "listening"]
    assert org.c.put(f"/api/projects/{pid}/methods", headers=org.h("admin"), json={"methods": ["depth"]}).json()["methods"] == ["depth"]
    assert len(org.c.get(f"/api/projects/{pid}/interviews", headers=org.h()).json()) == 1       # switching a method off hides, never deletes
    assert org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": []}).status_code == 422
    assert org.c.put(f"/api/projects/{pid}/methods", headers=org.h("moderator"), json={"methods": ["depth"]}).status_code == 403


def test_the_star_map_has_one_node_per_enabled_method_with_real_numbers(org):
    pid = org.project()
    org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": ["depth", "listening", "async"]})
    nodes = {n["key"]: n for n in org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()["starmap"]}
    assert set(nodes) == {"depth", "async", "listening"} and nodes["depth"]["big"] == 0 and nodes["depth"]["started"] is False and nodes["depth"]["page"] == "people"
    org.interview(pid=pid)
    org.c.post(f"/api/projects/{pid}/studies", headers=org.h(), json={"title": "研究", "goal": "g", "language": "zh", "duration_min": 20})
    org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json={"kind": "mock", "name": "m", "config": {"brand": "A", "lang": "en"}})
    sid = org.c.get(f"/api/projects/{pid}/listening", headers=org.h()).json()["sources"][0]["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    nodes = {n["key"]: n for n in org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()["starmap"]}
    assert nodes["depth"]["started"] is True and "共 1 场" in nodes["depth"]["sub"] and nodes["depth"]["page"] == "rooms"
    assert nodes["async"]["started"] and "1 项研究" in nodes["async"]["sub"]
    assert nodes["listening"]["big"] == 6 and "负面" in nodes["listening"]["sub"] and nodes["listening"]["page"] == "listening"


def test_async_sessions_are_not_counted_as_depth_interviews_on_the_map(org):
    from test_studies import invited
    pid, st, part, tok, iid = invited(org)
    nodes = {n["key"]: n for n in org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()["starmap"]}
    assert nodes["depth"]["started"] is False and nodes["async"]["sub"] == "1 项研究，1 人受邀"


def test_the_methods_helpers():
    assert methods.clean(["listening", "depth"]) == ["depth", "listening"]
    for bad in ([], None, ["x"]):
        with pytest.raises(methods.MethodsError):
            methods.clean(bad)
