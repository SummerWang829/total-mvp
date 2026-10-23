"""Two more roles: a recruiter (works with the project's people only) and a viewer (the client: reads findings, the report, monitoring and survey results only).
Both belong to projects as members; nothing else of the project opens for them."""
import pytest

from conftest import H, Org
from test_export import _with_insight
from test_project_members import second_researcher


def person(org, role, name=None):
    email = f"{role}x@acme.com"
    extra = {}
    if role == "viewer":  # a client is always added under a brand
        extra["brand_id"] = org.c.post("/api/brands", headers=org.h("admin"), json={"name": "品牌甲"}).json()["id"]
    r = org.c.post("/api/users", headers=org.h("admin"), json={"name": name or role, "email": email, "password": "password123", "role": role, **extra})
    assert r.status_code == 201, r.text
    login = org.c.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    return {"id": r.json()["id"], "h": H(login["token"])}


def project_with(org, role, member=True):
    itv, ins = _with_insight(org)
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    who = person(org, role)
    if member:
        assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": who["id"]}).status_code == 201
    org.c.post(f"/api/projects/{pid}/pool/notes", headers=org.h(), json={"theme": "内部假设", "text": "团队内部笔记，客户不应看到"})
    sid = org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json={"kind": "mock", "name": "m", "config": {"brand": "Acme", "lang": "en"}}).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": ["depth", "async", "listening", "quant"]})
    key = org.c.post(f"/api/projects/{pid}/quant/keys", headers=org.h(), json={"name": "k"}).json()["key"]
    org.c.post("/api/ingest/surveys", headers={"Authorization": f"Bearer {key}"}, json={"survey_id": "S", "title": "问卷", "respondents": 10, "questions": [
        {"text": "q", "type": "single", "results": [{"option": "a", "count": 7}, {"option": "b", "count": 3}]}]})
    return itv, pid, who


def status(org, who, method, url, body=None):
    return getattr(org.c, method)(url, headers=who["h"], **({"json": body} if body is not None else {})).status_code


def denied(org, who, method, url, body=None):
    """Refused: 403 for pages of the project they are not allowed into, 404 for an interview (which they must not even know exists)."""
    want = 404 if "/api/interviews/" in url else 403
    got = status(org, who, method, url, body)
    assert got == want, (method, url, got)


# ---------- the viewer (client, read only) ----------
def test_a_viewer_reads_findings_report_monitoring_and_results_of_their_project(org):
    itv, pid, v = project_with(org, "viewer")
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=v["h"]).json()]
    assert "summary" not in org.c.get("/api/projects", headers=v["h"]).json()[0]          # no staff cards
    assert org.c.get(f"/api/projects/{pid}", headers=v["h"]).json()["id"] == pid
    got = org.c.get(f"/api/projects/{pid}/pool", headers=v["h"]).json()
    assert got["counts"]["note"] == 0 and "内部假设" not in str(got) and all(i["interview_id"] is None for f in got["findings"] for i in f["items"])
    assert got["counts"]["interview"] == 1
    rep = org.c.get(f"/api/projects/{pid}/report?format=json", headers=v["h"])
    assert rep.status_code == 200 and "团队内部笔记" not in str(rep.json()) and "Wei Ling" not in str(rep.json())
    assert org.c.get(f"/api/projects/{pid}/report", headers=v["h"]).status_code == 200
    mentions = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=v["h"]).json()
    assert mentions["total"] == 6 and all(m["author"] is None for m in mentions["mentions"])
    assert org.c.get(f"/api/projects/{pid}/listening", headers=v["h"]).json()["summary"]["total"] == 6
    quant = org.c.get(f"/api/projects/{pid}/quant", headers=v["h"]).json()
    assert quant["keys"] == [] and quant["can_manage"] is False and quant["surveys"][0]["title"] == "问卷"
    assert "团队内部笔记" in str(org.c.get(f"/api/projects/{pid}/pool", headers=org.h()).json())   # the team still sees its own notes


def test_a_viewer_can_do_nothing_else(org):
    itv, pid, v = project_with(org, "viewer")
    part = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()[0]["id"]
    forbidden = [("get", f"/api/projects/{pid}/participants"), ("post", f"/api/projects/{pid}/participants", {"name": "x"}), ("get", f"/api/participants/{part}/screener"),
                 ("post", f"/api/projects/{pid}/participants/import", {"csv": "name\nx"}), ("get", f"/api/projects/{pid}/recruiting"),
                 ("get", f"/api/interviews/{itv['id']}"), ("get", f"/api/interviews/{itv['id']}/transcript"), ("get", f"/api/projects/{pid}/overview"),
                 ("get", f"/api/projects/{pid}/members"), ("post", "/api/projects", {"name": "n", "methods": ["depth"]}), ("get", f"/api/projects/{pid}/studies"),
                 ("get", "/api/repo/search?q=halal"), ("post", f"/api/projects/{pid}/listening/sources", {"kind": "mock", "name": "x", "config": {}}),
                 ("post", f"/api/projects/{pid}/pool/notes", {"theme": "a", "text": "b"}), ("post", f"/api/projects/{pid}/quant/keys", {"name": "k"}),
                 ("post", f"/api/projects/{pid}/interviews/batch", {"participant_ids": [part], "title": "t"}), ("put", f"/api/projects/{pid}/methods", {"methods": ["depth"]}),
                 ("get", f"/api/projects/{pid}/insights/export"), ("get", "/api/users"), ("get", "/api/audit")]
    for method, url, *body in forbidden:
        denied(org, v, method, url, body[0] if body else None)
    assert org.c.get(f"/api/projects/{pid}/interviews", headers=v["h"]).json() == []   # the list is filtered per interview: a client never sees one
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=v["h"]).status_code == 404


def test_a_viewer_does_not_see_projects_they_were_not_added_to(org):
    itv, pid, v = project_with(org, "viewer", member=False)
    assert org.c.get("/api/projects", headers=v["h"]).json() == []
    for url in (f"/api/projects/{pid}", f"/api/projects/{pid}/pool", f"/api/projects/{pid}/report", f"/api/projects/{pid}/listening", f"/api/projects/{pid}/quant"):
        assert status(org, v, "get", url) == 404, url


def test_removing_a_viewer_from_the_project_closes_it_at_once(org):
    itv, pid, v = project_with(org, "viewer")
    assert status(org, v, "get", f"/api/projects/{pid}/pool") == 200
    assert org.c.delete(f"/api/projects/{pid}/members/{v['id']}", headers=org.h()).status_code == 200
    assert status(org, v, "get", f"/api/projects/{pid}/pool") == 404 and org.c.get("/api/projects", headers=v["h"]).json() == []


# ---------- the recruiter ----------
def test_a_recruiter_works_with_the_projects_people_and_nothing_else(org):
    itv, pid, r = project_with(org, "recruiter")
    assert org.c.get("/api/projects", headers=r["h"]).json()[0]["id"] == pid
    assert org.c.post(f"/api/projects/{pid}/participants", headers=r["h"], json={"name": "新人", "country": "SG", "language": "zh"}).status_code == 201
    imp = org.c.post(f"/api/projects/{pid}/participants/import", headers=r["h"], json={"csv": "name,country,年龄\n导入的人,SG,30\n"})
    assert imp.status_code == 200 and imp.json()["created"] == 1
    people = org.c.get(f"/api/projects/{pid}/participants", headers=r["h"]).json()
    assert {p["name"] for p in people} >= {"新人", "导入的人"}
    one = next(p["id"] for p in people if p["name"] == "导入的人")
    assert org.c.get(f"/api/participants/{one}/screener", headers=r["h"]).json()["items"][0]["answer"] == "30"
    assert org.c.put(f"/api/participants/{one}/screener", headers=r["h"], json={"text": "年龄：31"}).status_code == 200
    assert org.c.put(f"/api/participants/{one}/tags", headers=r["h"], json={"tags": ["a"]}).status_code == 200
    assert org.c.put(f"/api/projects/{pid}/recruiting/rules", headers=r["h"], json={"rules": [{"question": "年龄", "op": "gte", "value": 18, "effect": "require"}]}).status_code == 200
    assert org.c.put(f"/api/projects/{pid}/recruiting/quotas", headers=r["h"], json={"quotas": [{"field": "country", "value": "SG", "target": 5}]}).status_code == 200
    assert org.c.get(f"/api/projects/{pid}/recruiting", headers=r["h"]).json()["rules_version"] == 1
    assert org.c.post(f"/api/projects/{pid}/recruiting/apply", headers=r["h"]).status_code == 200


def test_a_recruiter_cannot_reach_findings_sessions_or_erase_people(org):
    itv, pid, r = project_with(org, "recruiter")
    part = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()[0]["id"]
    forbidden = [("get", f"/api/projects/{pid}/pool"), ("get", f"/api/projects/{pid}/report"), ("get", f"/api/projects/{pid}/listening"), ("get", f"/api/projects/{pid}/quant"),
                 ("get", f"/api/projects/{pid}/studies"), ("get", "/api/repo/search?q=halal"), ("get", f"/api/interviews/{itv['id']}/transcript"),
                 ("get", f"/api/projects/{pid}/overview"), ("post", f"/api/projects/{pid}/interviews/batch", {"participant_ids": [part], "title": "t"}),
                 ("post", f"/api/projects/{pid}/interviews", {"participant_id": part, "title": "t"}), ("delete", f"/api/participants/{part}/data"),
                 ("get", f"/api/projects/{pid}/members"), ("put", f"/api/projects/{pid}/methods", {"methods": ["depth"]}), ("get", "/api/users")]
    for method, url, *body in forbidden:
        denied(org, r, method, url, body[0] if body else None)
    assert org.c.get(f"/api/projects/{pid}/interviews", headers=r["h"]).json() == []
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=r["h"]).status_code == 404


def test_a_recruiter_only_reaches_projects_they_were_added_to(org):
    itv, pid, r = project_with(org, "recruiter", member=False)
    for method, url, body in [("get", f"/api/projects/{pid}/participants", None), ("post", f"/api/projects/{pid}/participants", {"name": "x"}),
                              ("post", f"/api/projects/{pid}/participants/import", {"csv": "name\nx"}), ("get", f"/api/projects/{pid}/recruiting", None)]:
        assert status(org, r, method, url, body) == 404, url


# ---------- membership and accounts ----------
def test_only_these_roles_can_be_added_to_a_project_and_only_by_owners_and_admins(org):
    itv, pid, v = project_with(org, "viewer")
    mod = org.users["moderator"]
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": mod}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": org.users["observer"]}).status_code == 422
    rec = person(org, "recruiter", "招募")
    assert org.c.post(f"/api/projects/{pid}/members", headers=v["h"], json={"user_id": rec["id"]}).status_code == 403
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": rec["id"]}).status_code == 201
    names = {m["role"] for m in org.c.get(f"/api/projects/{pid}/members", headers=org.h()).json()["members"]}
    assert names == {"researcher", "viewer", "recruiter"}


def test_the_new_roles_can_be_invited_and_a_bad_role_is_refused(org):
    brand = org.c.post("/api/brands", headers=org.h("admin"), json={"name": "品牌乙"}).json()["id"]
    for role in ("viewer", "recruiter"):
        assert org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": f"{role}@client.com", "role": role, "brand_id": brand}).status_code == 201
    assert org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": "x@client.com", "role": "boss"}).status_code == 422
    assert org.c.post("/api/users", headers=org.h("admin"), json={"name": "x", "email": "x2@client.com", "password": "password123", "role": "boss"}).status_code == 422


def test_viewer_in_another_company_gets_nothing(org, client):
    itv, pid, v = project_with(org, "viewer")
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/projects/{pid}/pool", headers=foreign.h("admin")).status_code == 404
    assert second_researcher(org)["id"]


# ---------- researcher kinds, technical support, clients under brands ----------
def test_a_client_needs_a_brand_and_carries_a_title_and_contact(org):
    h = org.h("admin")
    base = {"name": "客户甲", "email": "jia@client.com", "password": "password123", "role": "viewer"}
    assert org.c.post("/api/users", headers=h, json=base).status_code == 422  # no brand
    assert org.c.post("/api/users", headers=h, json={**base, "brand_id": "nope"}).status_code == 422
    b = org.c.post("/api/brands", headers=h, json={"name": "雪花"}).json()
    assert org.c.post("/api/brands", headers=h, json={"name": "雪花"}).status_code == 409
    r = org.c.post("/api/users", headers=h, json={**base, "brand_id": b["id"], "job_title": "品牌经理", "phone": "+65 8000 0000"})
    assert r.status_code == 201 and r.json()["brand_id"] == b["id"] and r.json()["job_title"] == "品牌经理" and r.json()["phone"] == "+65 8000 0000"
    assert org.c.get("/api/brands", headers=h).json()[0]["clients"] == 1
    assert org.c.delete(f"/api/brands/{b['id']}", headers=h).status_code == 409  # a client is still under it
    assert org.c.post("/api/brands", headers=org.h("researcher"), json={"name": "x"}).status_code == 403  # only admins add brands


def test_a_researcher_has_a_kind_and_only_researchers(org):
    h = org.h("admin")
    mk = lambda email, **kw: org.c.post("/api/users", headers=h, json={"name": email, "email": email, "password": "password123", **kw})
    assert mk("q@a.com", role="researcher", research_kind="quant").json()["research_kind"] == "quant"
    assert mk("d@a.com", role="researcher").json()["research_kind"] == "all"
    assert mk("m@a.com", role="moderator", research_kind="quant").json()["research_kind"] == "all"  # a kind means nothing for other roles
    assert mk("x@a.com", role="researcher", research_kind="zzz").status_code == 422
    uid = mk("p@a.com", role="researcher").json()["id"]
    assert org.c.patch(f"/api/users/{uid}", headers=h, json={"research_kind": "qual"}).json()["research_kind"] == "qual"


def test_changing_someone_into_a_client_needs_a_brand(org):
    h = org.h("admin")
    uid = org.c.post("/api/users", headers=h, json={"name": "u", "email": "u@a.com", "password": "password123", "role": "researcher"}).json()["id"]
    assert org.c.patch(f"/api/users/{uid}", headers=h, json={"role": "viewer"}).status_code == 422
    b = org.c.post("/api/brands", headers=h, json={"name": "B"}).json()["id"]
    r = org.c.patch(f"/api/users/{uid}", headers=h, json={"role": "viewer", "brand_id": b, "job_title": "总监"})
    assert r.status_code == 200 and r.json()["role"] == "viewer" and r.json()["brand_id"] == b


def test_technical_support_works_like_a_researcher_in_their_projects_only(org):
    sup = person(org, "support")
    pid = org.project()
    assert pid not in [p["id"] for p in org.c.get("/api/projects", headers=sup["h"]).json()]  # not added: does not even see it
    assert org.c.get(f"/api/projects/{pid}/plan", headers=sup["h"]).status_code == 404
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": sup["id"]}).status_code == 201
    assert pid in [p["id"] for p in org.c.get("/api/projects", headers=sup["h"]).json()]
    assert org.c.get(f"/api/projects/{pid}/participants", headers=sup["h"]).status_code == 200
    d = org.c.post("/api/projects/draft", headers=sup["h"], json={"name": "支持建的"}).json()
    assert org.c.post(f"/api/projects/{d['id']}/plan/approve", headers=sup["h"]).status_code == 403  # legal approval stays with admins


def test_the_project_manager_can_unlock_a_plan_but_never_sees_contact_details(org):
    from test_plans import approved, draft
    pm = person(org, "recruiter")
    pid = draft(org)["id"]
    approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    assert org.c.post(f"/api/projects/{pid}/plan/unlock", headers=pm["h"]).status_code == 404  # not a member of this project yet
    org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": pm["id"]})
    assert org.c.post(f"/api/projects/{pid}/plan/unlock", headers=pm["h"]).json()["status"] == "draft"
    people = org.c.get(f"/api/projects/{pid}/plan/people", headers=pm["h"]).json()
    assert people and all(set(x) == {"id", "name", "role", "active", "research_kind"} for x in people)  # names and roles, no email or phone
    assert org.c.get("/api/users", headers=pm["h"]).status_code == 403
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=pm["h"]).status_code == 403


def test_a_client_can_be_added_with_a_brand_typed_by_name(org):
    h = org.h("admin")
    base = {"name": "客户乙", "email": "yi@client.com", "password": "password123", "role": "viewer"}
    assert org.c.post("/api/users", headers=h, json=base).status_code == 422  # neither a brand id nor a name
    a = org.c.post("/api/users", headers=h, json={**base, "brand_name": "雪花"}).json()
    assert a["brand_id"] and [b["name"] for b in org.c.get("/api/brands", headers=h).json()] == ["雪花"]  # a new name creates the brand
    b = org.c.post("/api/users", headers=h, json={**base, "name": "客户丙", "email": "bing@client.com", "brand_name": "  雪花 "}).json()
    assert b["brand_id"] == a["brand_id"] and len(org.c.get("/api/brands", headers=h).json()) == 1  # the same name reuses it
    c = org.c.post("/api/users/invites", headers=h, json={"email": "ding@client.com", "role": "viewer", "brand_name": "新品牌"})
    assert c.status_code == 201 and len(org.c.get("/api/brands", headers=h).json()) == 2
    uid = org.c.post("/api/users", headers=h, json={"name": "研", "email": "yan@a.com", "password": "password123", "role": "researcher"}).json()["id"]
    r = org.c.patch(f"/api/users/{uid}", headers=h, json={"role": "viewer", "brand_name": "雪花"})
    assert r.status_code == 200 and r.json()["brand_id"] == a["brand_id"]
    assert org.c.delete(f"/api/brands/{a['brand_id']}", headers=h).status_code == 409  # still protected on the server
