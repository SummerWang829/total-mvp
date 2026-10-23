"""Project membership: a researcher sees and touches only the projects they were added to; admins see every project; nobody loses access on the day it arrives."""
import pytest

from app import project_members
from app.db import SessionLocal
from app.models import Clip, Interview, ProjectMember, ShareLink, utcnow
from conftest import H, Org
from test_export import _with_insight


def second_researcher(org, name="Second", email=None):
    """Another researcher of the same company (not on the first one's projects)."""
    domain = org.users and "acme.com"
    email = email or f"{name.lower()}@{domain}"
    r = org.c.post("/api/users", headers=org.h("admin"), json={"name": name, "email": email, "password": "password123", "role": "researcher"})
    assert r.status_code == 201, r.text
    login = org.c.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    return {"id": r.json()["id"], "h": H(login["token"])}


def members(org, pid, role="researcher", headers=None):
    return org.c.get(f"/api/projects/{pid}/members", headers=headers or org.h(role))


# ---------- who sees what ----------
def test_the_creator_is_an_owner_and_another_researcher_does_not_even_see_the_project(org):
    pid = org.project()
    other = second_researcher(org)
    mine = [p["id"] for p in org.c.get("/api/projects", headers=org.h()).json()]
    assert pid in mine
    assert pid not in [p["id"] for p in org.c.get("/api/projects", headers=other["h"]).json()]
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=org.h("admin")).json()]  # admins see every project
    first = members(org, pid).json()
    assert [m["role"] for m in first["members"]] == ["researcher"] and first["members"][0]["is_owner"] is True and first["can_manage"] is True


def test_a_researcher_outside_the_project_gets_404_on_everything_in_it(org):
    itv = org.interview()
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    part = itv["participant"]["id"]
    other = second_researcher(org)
    h = other["h"]
    for method, url, body in [
        ("get", f"/api/projects/{pid}", None), ("get", f"/api/projects/{pid}/participants", None), ("get", f"/api/projects/{pid}/interviews", None),
        ("get", f"/api/interviews/{itv['id']}", None), ("get", f"/api/interviews/{itv['id']}/transcript", None), ("get", f"/api/interviews/{itv['id']}/insights", None),
        ("get", f"/api/projects/{pid}/insights/export?format=csv", None), ("get", f"/api/projects/{pid}/codes", None),
        ("post", f"/api/projects/{pid}/participants", {"name": "X", "country": "SG", "language": "en"}),
        ("post", f"/api/projects/{pid}/interviews", {"participant_id": part, "title": "x"}),
        ("get", f"/api/participants/{part}/screener", None), ("put", f"/api/participants/{part}/screener", {"text": "a：b"}),
        ("delete", f"/api/participants/{part}/data", None), ("post", f"/api/projects/{pid}/close", None),
        ("get", f"/api/projects/{pid}/members", None),
    ]:
        r = getattr(org.c, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code, r.text[:80])
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).status_code == 200   # the owner is untouched


def test_insights_segments_clips_and_shares_by_id_are_closed_to_outsiders_too(org):
    itv, ins = _with_insight(org)
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    with SessionLocal() as db:
        clip = Clip(org_id=db.get(Interview, itv["id"]).org_id, interview_id=itv["id"], title="c", start_ms=0, end_ms=1000)
        db.add(clip)
        db.flush()
        share = ShareLink(org_id=clip.org_id, clip_id=clip.id, token_hash="h" * 64, watermark="w", expires_at=utcnow())
        db.add(share)
        db.commit()
        clip_id, share_id = clip.id, share.id
    h = second_researcher(org)["h"]
    calls = [("post", f"/api/insights/{ins['id']}/approve", None), ("delete", f"/api/insights/{ins['id']}", None),
             ("patch", f"/api/transcript/{segs[0]['id']}", {"text_orig": "changed"}), ("post", f"/api/clips/{clip_id}/preview", None),
             ("post", f"/api/clips/{clip_id}/share", {"watermark": "w"}), ("get", f"/api/clips/{clip_id}/shares", None), ("delete", f"/api/shares/{share_id}", None)]
    for method, url, body in calls:
        r = getattr(org.c, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    assert org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h()).status_code == 200   # the project's own researcher still can


def test_a_code_of_another_project_cannot_be_used_or_deleted(org):
    pid = org.project()
    code = org.c.post(f"/api/projects/{pid}/codes", headers=org.h(), json={"name": "价格"}).json()
    h = second_researcher(org)["h"]
    assert org.c.delete(f"/api/codes/{code['id']}", headers=h).status_code == 404
    assert org.c.get(f"/api/projects/{pid}/codes", headers=h).status_code == 404


def test_other_companies_still_get_404(org, client):
    pid = org.project()
    other = Org(client, "Other", "b@other.com")
    assert other.c.get(f"/api/projects/{pid}", headers=other.h()).status_code == 404
    assert other.c.get(f"/api/projects/{pid}/members", headers=other.h("admin")).status_code == 404


def test_admins_reach_every_project_and_moderators_and_observers_keep_their_interview_assignments(org):
    itv = org.interview()
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    assert org.c.get(f"/api/projects/{pid}", headers=org.h("admin")).status_code == 200
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=org.h("moderator")).status_code == 200
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=org.h("observer")).status_code == 200
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=org.h("moderator")).json()]
    assert org.c.get(f"/api/projects/{pid}/members", headers=org.h("moderator")).status_code == 403   # members are the research team's business


# ---------- managing members ----------
def test_an_owner_adds_a_researcher_who_then_sees_the_project_and_removing_takes_it_away_at_once(org):
    itv = org.interview()
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    other = second_researcher(org)
    assert org.c.get(f"/api/projects/{pid}", headers=other["h"]).status_code == 404
    r = org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]})
    assert r.status_code == 201 and len(r.json()["members"]) == 2
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]}).json()["members"].__len__() == 2   # again: no duplicate
    assert org.c.get(f"/api/projects/{pid}", headers=other["h"]).status_code == 200 and org.c.get(f"/api/interviews/{itv['id']}", headers=other["h"]).status_code == 200
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=other["h"]).json()]
    assert org.c.delete(f"/api/projects/{pid}/members/{other['id']}", headers=org.h()).status_code == 200
    assert org.c.get(f"/api/projects/{pid}", headers=other["h"]).status_code == 404 and org.c.get(f"/api/interviews/{itv['id']}", headers=other["h"]).status_code == 404
    log = [e["action"] for e in org.c.get("/api/audit?limit=50", headers=org.h("admin")).json()]
    assert "project.member.add" in log and "project.member.remove" in log


def test_a_plain_member_cannot_add_or_remove_anyone_until_made_an_owner(org):
    pid = org.project()
    a, b = second_researcher(org, "Anna"), second_researcher(org, "Ben")
    org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": a["id"]})
    assert members(org, pid, headers=a["h"]).json()["can_manage"] is False
    assert org.c.post(f"/api/projects/{pid}/members", headers=a["h"], json={"user_id": b["id"]}).status_code == 403
    assert org.c.put(f"/api/projects/{pid}/members/{a['id']}/owner", headers=a["h"], json={"is_owner": True}).status_code == 403
    assert org.c.put(f"/api/projects/{pid}/members/{a['id']}/owner", headers=org.h(), json={"is_owner": True}).status_code == 200
    assert org.c.post(f"/api/projects/{pid}/members", headers=a["h"], json={"user_id": b["id"]}).status_code == 201


def test_an_admin_manages_members_of_any_project_without_being_a_member(org):
    pid = org.project()
    other = second_researcher(org)
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h("admin"), json={"user_id": other["id"]}).status_code == 201
    assert org.c.delete(f"/api/projects/{pid}/members/{other['id']}", headers=org.h("admin")).status_code == 200


def test_only_active_researchers_of_the_company_can_be_added(org, client):
    pid = org.project()
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": org.users["moderator"]}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": org.users["observer"]}).status_code == 422
    admin_id = org.c.get("/api/me", headers=org.h("admin")).json()["id"]
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": admin_id}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": "nobody"}).status_code == 404
    foreign = Org(client, "Other", "b@other.com").users["researcher"]
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": foreign}).status_code == 404
    gone = second_researcher(org, "Gone")
    org.c.patch(f"/api/users/{gone['id']}", headers=org.h("admin"), json={"active": False})
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": gone["id"]}).status_code == 404


def test_the_last_owner_cannot_be_removed_by_an_owner_but_an_admin_may(org):
    pid = org.project()
    me = next(m for m in members(org, pid).json()["members"])
    assert org.c.delete(f"/api/projects/{pid}/members/{me['user_id']}", headers=org.h()).status_code == 409
    assert org.c.put(f"/api/projects/{pid}/members/{me['user_id']}/owner", headers=org.h(), json={"is_owner": False}).status_code == 409
    assert org.c.delete(f"/api/projects/{pid}/members/{me['user_id']}", headers=org.h("admin")).status_code == 200
    assert org.c.get(f"/api/projects/{pid}", headers=org.h("admin")).status_code == 200   # the project is still there for admins


# ---------- the day it arrives ----------
def test_existing_projects_without_members_give_every_active_researcher_access_and_it_runs_only_once(org):
    pid = org.project()
    other = second_researcher(org)
    with SessionLocal() as db:   # an "old" database: the project has no member rows at all
        db.query(ProjectMember).delete()
        db.commit()
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).status_code == 404   # (that is why the backfill must run)
    with SessionLocal() as db:
        assert project_members.backfill(db) == 2   # both researchers
        assert project_members.backfill(db) == 0   # nothing left to do
        assert db.query(ProjectMember).filter_by(project_id=pid, is_owner=True).count() == 0
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).status_code == 200 and org.c.get(f"/api/projects/{pid}", headers=other["h"]).status_code == 200


def test_the_backfill_leaves_a_project_that_already_has_members_alone(org):
    pid = org.project()
    second_researcher(org)
    with SessionLocal() as db:
        assert project_members.backfill(db) == 0
        assert db.query(ProjectMember).filter_by(project_id=pid).count() == 1   # only the creator: the second researcher was not added behind anyone's back


# ---------- research stages per member ----------
def test_members_have_research_stages_by_role_and_can_have_several(org):
    from test_plans import approved, draft
    pid = draft(org)["id"]
    approved(org, pid, design="iq", listening=True)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    d = org.c.get(f"/api/projects/{pid}/members", headers=org.h()).json()
    assert [s["key"] for s in d["stages"]] == ["depth", "quant", "listening"] and d["stages"][0]["where"] == ["新加坡"]
    quant = org.c.post("/api/users", headers=org.h("admin"), json={"name": "量", "email": "liang@a.com", "password": "password123", "role": "researcher", "research_kind": "quant"}).json()["id"]
    qual = org.c.post("/api/users", headers=org.h("admin"), json={"name": "性", "email": "xing@a.com", "password": "password123", "role": "researcher", "research_kind": "qual"}).json()["id"]
    pm = org.c.post("/api/users", headers=org.h("admin"), json={"name": "经", "email": "jing@a.com", "password": "password123", "role": "recruiter"}).json()["id"]
    for uid in (quant, qual, pm):
        assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": uid}).status_code == 201
    got = {m["user_id"]: m["scopes"] for m in org.c.get(f"/api/projects/{pid}/members", headers=org.h()).json()["members"]}
    assert got[quant] == ["quant", "listening"] and got[qual] == ["depth", "listening"] and got[pm] == []  # by role, limited to the stages the project has
    r = org.c.put(f"/api/projects/{pid}/members/{pm}/scopes", headers=org.h(), json={"scopes": ["quant", "depth"]})
    assert r.status_code == 200 and {m["user_id"]: m["scopes"] for m in r.json()["members"]}[pm] == ["depth", "quant"]  # several at once, in the standard order
    assert org.c.put(f"/api/projects/{pid}/members/{pm}/scopes", headers=org.h(), json={"scopes": ["group"]}).status_code == 422  # this project has no focus groups
    assert org.c.put(f"/api/projects/{pid}/members/nobody/scopes", headers=org.h(), json={"scopes": []}).status_code == 404


def test_choosing_stages_when_adding_and_the_plan_seeds_them_at_publish(org):
    from test_plans import approved, draft
    pid = draft(org)["id"]
    team = [{"user_id": org.users["researcher"], "q": True, "i": True, "f": False, "l": False}]
    approved(org, pid, design="iq", team=team)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    got = {m["user_id"]: m["scopes"] for m in org.c.get(f"/api/projects/{pid}/members", headers=org.h()).json()["members"]}
    assert got[org.users["researcher"]] == ["depth", "quant"]  # what the plan's people table ticked
    who = org.c.post("/api/users", headers=org.h("admin"), json={"name": "x", "email": "x@a.com", "password": "password123", "role": "support"}).json()["id"]
    r = org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": who, "scopes": ["depth"]})
    assert {m["user_id"]: m["scopes"] for m in r.json()["members"]}[who] == ["depth"]
    other = org.c.post("/api/users", headers=org.h("admin"), json={"name": "y", "email": "y@a.com", "password": "password123", "role": "support"}).json()["id"]
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other, "scopes": ["zzz"]}).status_code == 422
