"""The project plan (立项向导): draft projects, the compliance list, review, publish and lock, and the quota monitor."""
import copy

import pytest

from app import plans
from conftest import Org, H


def draft(org: Org, name: str = "新项目", role: str = "researcher") -> dict:
    r = org.c.post("/api/projects/draft", headers=org.h(role), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def plan(org: Org, pid: str, role: str = "researcher") -> dict:
    r = org.c.get(f"/api/projects/{pid}/plan", headers=org.h(role))
    assert r.status_code == 200, r.text
    return r.json()


def good_config(base: dict, **over) -> dict:
    """A config that passes every check: confirmations ticked, quotas add up, the sending language matches."""
    cfg = copy.deepcopy(base)
    cfg["methods"] = over.pop("methods", ["depth", "group"])
    cfg["cons"] = {k: True for k in cfg["cons"]}
    cfg["n"] = {"q": 0, "i": 5, "f": 0, "fs": 6}
    cfg["lt"] = {"dims": ["国家"], "rows": [{"id": "r1", "vs": ["s1"], "t": 0, "i": 5, "f": 0}]}
    cfg.update(over)
    return cfg


def save(org: Org, pid: str, cfg: dict, role: str = "researcher"):
    return org.c.put(f"/api/projects/{pid}/plan", headers=org.h(role), json={"config": cfg})


def approved(org: Org, pid: str, **over) -> dict:
    cfg = good_config(plan(org, pid)["config"], **over)
    assert save(org, pid, cfg).status_code == 200
    assert org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h()).status_code == 200
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin")).status_code == 200
    return cfg


# ---------- drafts ----------
def test_a_draft_project_exists_with_a_default_plan_and_shows_its_status(org):
    p = draft(org, "东南亚研究")
    assert p["plan_status"] == "draft" and p["name"] == "东南亚研究"
    listed = {x["id"]: x for x in org.c.get("/api/projects", headers=org.h("admin")).json()}
    assert listed[p["id"]]["plan_status"] == "draft"
    cfg = plan(org, p["id"])["config"]
    assert cfg["split"] == "country" and cfg["subs"][0]["name"] == "新加坡" and "国家" in cfg["vars"]


def test_only_the_research_team_makes_drafts(org):
    for role in ("observer", "moderator"):
        assert org.c.post("/api/projects/draft", headers=org.h(role), json={"name": "x"}).status_code == 403
    assert org.c.post("/api/projects/draft", headers=org.h(), json={}).status_code == 422


def test_a_project_made_the_old_way_has_no_plan_and_counts_as_published(org):
    pid = org.project()
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["plan_status"] == ""
    assert plan(org, pid) == {"status": "", "legacy": True, "name": "P1", "brief": ""}
    assert save(org, pid, plans.default_config()).status_code == 409


# ---------- the config ----------
def test_unknown_keys_and_impossible_numbers_are_refused(org):
    pid = draft(org)["id"]
    base = plan(org, pid)["config"]
    assert save(org, pid, {**base, "surprise": 1}).status_code == 422
    assert save(org, pid, {**base, "n": {**base["n"], "fs": 2}}).status_code == 422  # a group needs at least 3 people
    assert save(org, pid, {**base, "lt": {"dims": ["不存在的变量"], "rows": []}}).status_code == 422
    assert save(org, pid, {**base, "lt": {"dims": ["国家"], "rows": [{"id": "r", "vs": ["nope"], "i": 1}]}}).status_code == 422
    assert save(org, pid, {**base, "methods": []}).status_code == 422
    assert save(org, pid, {**base, "split": "single", "subs": base["subs"] * 2}).status_code == 422


def test_saving_updates_the_name_and_the_brief(org):
    pid = draft(org)["id"]
    r = org.c.put(f"/api/projects/{pid}/plan", headers=org.h(), json={"name": "改了名", "brief": "了解选购", "config": plan(org, pid)["config"]})
    assert r.status_code == 200 and r.json()["name"] == "改了名" and org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["brief"] == "了解选购"


# ---------- compliance ----------
def test_the_compliance_list_has_14_items_and_4_more_when_interviews_are_used(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    assert len(plan(org, pid)["compliance"]) == 18  # depth is on by default
    only = save(org, pid, {**cfg, "methods": ["listening"]}).json()
    assert len(only["compliance"]) == 14 and not any(c["id"].startswith("q") for c in only["compliance"])


def test_items_the_system_can_judge_follow_the_config(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    by = lambda r: {c["id"]: c["passed"] for c in r["compliance"]}
    assert by(plan(org, pid))["c9"] is True and by(plan(org, pid))["c12"] is True
    no_unsub = save(org, pid, {**cfg, "tq": "您好，邀请您参加调研。目的：{{purpose}}。"}).json()
    assert by(no_unsub)["c9"] is False  # a template without the unsubscribe placeholder
    wrong_lang = save(org, pid, {**cfg, "langs": {"中文": True, "英语": False, "马来语": False, "泰米尔语": False}}).json()
    assert by(wrong_lang)["c12"] is False  # the sub-project speaks English but English is not ticked
    assert by(plan(org, pid))["c6"] is False and by(plan(org, pid))["q1"] is False  # confirmations a person must give


def test_submitting_is_refused_with_reasons_until_everything_passes_and_adds_up(org):
    pid = draft(org)["id"]
    r = org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    assert r.status_code == 409 and "合规项未通过" in r.json()["detail"]
    cfg = good_config(plan(org, pid)["config"])
    cfg["n"]["i"] = 9  # the table says 5
    save(org, pid, cfg)
    r = org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    assert r.status_code == 409 and "深访配额合计 5 与目标 9 不一致" in r.json()["detail"]


# ---------- review ----------
def test_review_runs_draft_review_approved_published_and_only_an_admin_approves(org):
    pid = draft(org)["id"]
    save(org, pid, good_config(plan(org, pid)["config"]))
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin")).status_code == 409  # not submitted yet
    sub = org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h()).json()
    assert sub["status"] == "review" and len(sub["review_hash"]) == 16
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h()).status_code == 403  # a researcher cannot approve
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).status_code == 409  # not approved yet
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin")).json()["status"] == "approved"
    pub = org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).json()
    assert pub["status"] == "published" and pub["version"] == 1 and pub["project"]["plan_status"] == "published"


def test_a_change_after_submission_sends_the_plan_back_to_draft(org):
    pid = draft(org)["id"]
    cfg = good_config(plan(org, pid)["config"])
    save(org, pid, cfg)
    org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    r = save(org, pid, {**cfg, "gift": 300}).json()
    assert r["status"] == "draft" and "重新提交" in r["review_note"]
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin")).status_code == 409


def test_an_admin_can_send_it_back_with_a_reason(org):
    pid = draft(org)["id"]
    save(org, pid, good_config(plan(org, pid)["config"]))
    org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    r = org.c.post(f"/api/projects/{pid}/plan/return", headers=org.h("admin"), json={"note": "跨境条款还要补"}).json()
    assert r["status"] == "draft" and r["review_note"] == "跨境条款还要补"
    assert org.c.post(f"/api/projects/{pid}/plan/return", headers=org.h("admin"), json={}).status_code == 409  # nothing to send back


def test_publishing_applies_methods_language_and_team_and_then_locks_the_plan(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    team = [{"user_id": org.users["researcher"], "q": False, "i": True, "f": False}, {"user_id": org.users["moderator"], "q": False, "i": True, "f": False}]
    subs = [{**cfg["subs"][0], "lang": "中文"}]
    approved(org, pid, methods=["depth", "listening"], team=team, subs=subs, langs={"中文": True, "英语": False, "马来语": False, "泰米尔语": False})
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).status_code == 200
    p = org.c.get(f"/api/projects/{pid}", headers=org.h("admin")).json()
    assert p["methods"] == ["depth", "listening"] and p["language"] == "zh" and p["plan_status"] == "published"
    members = [m["user_id"] for m in org.c.get(f"/api/projects/{pid}/members", headers=org.h("admin")).json()["members"]]
    assert org.users["researcher"] in members and org.users["moderator"] not in members  # moderators are assigned per interview, not per project
    assert save(org, pid, cfg).status_code == 409  # locked


def test_published_plans_cannot_be_published_or_approved_again(org):
    pid = draft(org)["id"]
    approved(org, pid)
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).status_code == 200
    for step in ("submit", "approve", "publish"):
        assert org.c.post(f"/api/projects/{pid}/plan/{step}", headers=org.h("admin")).status_code == 409


# ---------- who can see it ----------
def test_another_organisation_and_non_staff_cannot_see_or_change_a_plan(client, org):
    pid = draft(org)["id"]
    other = Org(client, name="Other", email="o@other.com")
    assert client.get(f"/api/projects/{pid}/plan", headers=other.h("admin")).status_code == 404
    assert client.put(f"/api/projects/{pid}/plan", headers=other.h("admin"), json={"config": plan(org, pid)["config"]}).status_code == 404
    for role in ("observer", "moderator"):
        assert client.get(f"/api/projects/{pid}/plan", headers=org.h(role)).status_code == 403


def test_a_researcher_who_is_not_a_member_cannot_open_the_plan(client, org):
    pid = org.c.post("/api/projects/draft", headers=org.h("admin"), json={"name": "只有管理员"}).json()["id"]
    assert client.get(f"/api/projects/{pid}/plan", headers=org.h("researcher")).status_code == 404


def test_plan_actions_are_in_the_audit_log(org):
    pid = draft(org)["id"]
    approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    actions = {a["action"] for a in org.c.get("/api/audit?limit=100", headers=H(org.admin)).json()}
    assert {"project.draft", "plan.save", "plan.submit", "plan.approve", "plan.publish"} <= actions


# ---------- helpers: tags and the monitor ----------
def test_matching_tags_uses_the_projects_own_labels_and_counts_people(org):
    pid = draft(org)["id"]
    for name, tags in (("甲", ["小店主", "线上"]), ("乙", ["小店主"]), ("丙", ["学生"])):
        r = org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": name, "country": "SG", "language": "en"})
        org.c.put(f"/api/participants/{r.json()['id']}/tags", headers=org.h(), json={"tags": tags})
    cfg = plan(org, pid)["config"]
    save(org, pid, {**cfg, "pop": "新加坡的小店主，主要在线上进货"})
    out = org.c.post(f"/api/projects/{pid}/plan/tags", headers=org.h()).json()
    assert out["tags"] == ["小店主", "线上"] and out["reach"] == 1 and out["pool"] == 3


def test_the_monitor_counts_people_against_the_quota_rows(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    assert org.c.get(f"/api/projects/{pid}/monitor", headers=org.h()).json() == {"published": False}
    approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": "甲", "country": "SG", "language": "en"})
    org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": "乙", "country": "MY", "language": "en"})
    m = org.c.get(f"/api/projects/{pid}/monitor", headers=org.h()).json()
    row = m["i"]["rows"][0]
    assert row["label"] == "新加坡" and row["target"] == 5 and row["ready"] == 1 and row["gap"] == 4  # only the Singapore person fits
    assert m["i"]["target"] == 5 and m["i"]["ready"] == 1 and m["f"]["rows"] == [] and cfg["n"]["fs"] == m["fs"]


def test_saving_the_same_content_again_does_not_undo_a_submission(org):
    pid = draft(org)["id"]
    cfg = good_config(plan(org, pid)["config"])
    save(org, pid, cfg)
    org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    again = save(org, pid, cfg).json()  # the page saves before every action, whether or not anything changed
    assert again["status"] == "review"
    assert org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin")).status_code == 200
