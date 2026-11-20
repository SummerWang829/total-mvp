"""修正方案第 1 轮第 5 条: the outline reads length, format and languages from the 立项 sub-project it is bound to; a change in the outline goes into the 立项 when it
is not locked, otherwise it becomes an unlock request; the two pages and publishing share one mismatch check."""
from test_plans import approved, draft, good_config, plan, save

from conftest import Org


def _wb(org: Org, pid: str, method: str, path: str, role: str = "researcher", **kw):
    return getattr(org.c, method)(f"/api/projects/{pid}/outline-wb{path}", headers=org.h(role), **kw)


def _two_subs(org: Org, pid: str) -> dict:
    cfg = good_config(plan(org, pid)["config"])
    cfg["subs"] = [{**cfg["subs"][0], "minutes": 45, "langs": ["中文", "英语"], "lang": "中文"}, {**cfg["subs"][0], "id": "s2", "name": "马来西亚", "minutes": 60, "lang": "英语", "langs": ["英语"]}]
    assert save(org, pid, cfg).status_code == 200, save(org, pid, cfg).text
    return cfg


def test_the_outline_reads_its_sub_project_and_shows_where_others_differ(org):
    pid = draft(org)["id"]
    _two_subs(org, pid)
    v = _wb(org, pid, "get", "").json()
    sy = v["context"]["sync"]
    assert sy["sub_id"] == "s1" and v["context"]["minutes"] == 45 and v["slots"]["minutes"]["source"] == "project"
    assert sy["values"]["languages"]["text"] == "中文、English"
    rows = {(m["sub_id"], m["field"]) for m in sy["mismatch"]}
    assert ("s2", "minutes") in rows and ("s2", "languages") in rows and not any(m["bound"] for m in sy["mismatch"])
    assert org.c.get(f"/api/projects/{pid}/plan/outline-sync", headers=org.h()).json()["mismatch"] == sy["mismatch"]   # one check for both pages
    b = _wb(org, pid, "put", "/sub", json={"sub_id": "s2"}).json()
    assert b["context"]["minutes"] == 60 and b["context"]["sync"]["sub_name"] == "马来西亚"


def test_a_change_goes_into_an_unlocked_plan(org):
    pid = draft(org)["id"]
    _two_subs(org, pid)
    r = _wb(org, pid, "put", "/sync", json={"field": "minutes", "value": 60}).json()
    assert r["sync_result"] == "plan" and r["context"]["minutes"] == 60
    assert next(s for s in plan(org, pid)["config"]["subs"] if s["id"] == "s1")["minutes"] == 60
    r = _wb(org, pid, "put", "/sync", json={"field": "format", "value": "audio"}).json()
    assert r["context"]["sync"]["values"]["format"]["text"] == "纯音频"
    assert _wb(org, pid, "put", "/sync", json={"field": "minutes", "value": 999}).status_code == 422


def test_a_locked_plan_gets_an_unlock_request_and_publishing_waits(org):
    pid = draft(org)["id"]
    approved(org, pid)
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).status_code == 200
    r = _wb(org, pid, "put", "/sync", json={"field": "minutes", "value": 60}).json()
    assert r["sync_result"] == "requested" and r["context"]["minutes"] == 60
    assert any(m["bound"] and m["field"] == "minutes" for m in r["context"]["sync"]["mismatch"])
    req = plan(org, pid)["unlock_request"]
    assert req["changes"][0] == {"sub_id": "s1", "field": "minutes", "value": 60, "text": "60 分钟"}
    chk = {c["id"]: c for c in _wb(org, pid, "get", "/publish/check").json()["checks"]}
    assert chk["sync"]["level"] == "block"
    assert _wb(org, pid, "post", "/publish/skip-pilot").status_code == 422   # skipping the pilot does not skip this either
    assert org.c.post(f"/api/projects/{pid}/plan/unlock?with_request=true", headers=org.h("researcher")).status_code == 403   # only an admin or the project manager
    out = org.c.post(f"/api/projects/{pid}/plan/unlock?with_request=true", headers=org.h("admin")).json()
    assert out["status"] == "draft" and out["unlock_request"] is None and next(s for s in out["config"]["subs"] if s["id"] == "s1")["minutes"] == 60


def test_the_unlock_request_can_be_declined(org):
    pid = draft(org)["id"]
    approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    _wb(org, pid, "put", "/sync", json={"field": "languages", "value": ["en", "zh"]})
    out = org.c.post(f"/api/projects/{pid}/plan/unlock-request/dismiss", headers=org.h("admin")).json()
    assert out["unlock_request"] is None and out["status"] == "published"


def test_a_project_without_a_plan_keeps_reading_the_project(org):
    pid = org.project(brief="背景")
    v = _wb(org, pid, "get", "").json()
    assert v["context"]["sync"] is None and v["context"]["minutes"] in (None, 45)
    assert _wb(org, pid, "post", "/answer", json={"slot": "minutes", "value": 30}).json()["slots"]["minutes"]["value"] == 30


def test_the_dialog_changes_the_length_in_the_plan(org):
    """修正方案第 4 轮: “访谈时长改为30分钟” in the dialog does what 改 → 同步改立项 does."""
    pid = draft(org)["id"]
    _two_subs(org, pid)
    r = _wb(org, pid, "post", "/chat", json={"text": "访谈时长改为30分钟", "step": "people"}).json()
    assert r["changed"] == ["minutes"]
    assert next(s for s in plan(org, pid)["config"]["subs"] if s["id"] == "s1")["minutes"] == 30
