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
    design, listening = over.pop("design", "i"), over.pop("listening", False)  # the methods follow the sub-projects: 仅深访 by default
    cfg["subs"] = [{**s, "design": design, "listening": listening} for s in over.pop("subs", cfg["subs"])]
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
    assert save(org, pid, {**base, "subs": [{**base["subs"][0], "design": "zz"}]}).status_code == 422  # not one of the 8 research orders
    assert save(org, pid, {**base, "retention": 90}).status_code == 422  # only 30 / 60 / 120 / 180
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
    only = save(org, pid, {**cfg, "subs": [{**cfg["subs"][0], "design": "q"}]}).json()  # 仅定量: no interviews
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
    approved(org, pid, listening=True, team=team, subs=subs, langs={"中文": True, "英语": False, "马来语": False, "泰米尔语": False})
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


def test_copying_a_project_makes_a_draft_with_the_same_plan_and_no_confirmations(org):
    pid = draft(org, "原项目")["id"]
    cfg = approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    r = org.c.post(f"/api/projects/{pid}/copy", headers=org.h())
    assert r.status_code == 201 and r.json()["name"] == "原项目（副本）" and r.json()["plan_status"] == "draft" and r.json()["copied_from"] == pid
    new = plan(org, r.json()["id"])
    assert new["status"] == "draft" and new["config"]["n"] == cfg["n"] and new["config"]["subs"] == cfg["subs"]
    assert not any(new["config"]["cons"].values())  # the new project must be confirmed again
    assert org.c.post(f"/api/projects/{org.project()}/copy", headers=org.h()).status_code == 409  # a project without a plan cannot be copied
    assert org.c.post(f"/api/projects/{pid}/copy", headers=org.h("observer")).status_code == 403


# ---------- research orders, retention, unlocking ----------
def test_each_sub_project_has_its_own_research_order_and_the_methods_follow(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    subs = [{**cfg["subs"][0], "design": "fq"}, {"id": "s2", "name": "日本", "lang": "英语", "pm": "", "design": "i", "listening": True, "async_ai": True}]
    got = save(org, pid, {**cfg, "subs": subs, "vars": {**cfg["vars"], "国家": [{"id": "s1", "name": "新加坡"}, {"id": "s2", "name": "日本"}]}}).json()
    assert got["config"]["methods"] == ["depth", "group", "async", "listening", "quant"]  # derived from the sub-projects, whatever the client sends
    assert set(plans.DESIGNS) == {"fq", "iq", "qi", "i", "f", "q", "fi", "if"}
    assert plans.PlanConfig.model_validate({"subs": [{"id": "a", "name": "x", "design": "sw"}]}).subs[0].design == "iq"  # the first version's orders are mapped onto the new list


def test_a_method_nobody_uses_does_not_need_its_quota(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    only_depth = save(org, pid, {**cfg, "subs": [{**cfg["subs"][0], "design": "i"}], "n": {**cfg["n"], "q": 50}}).json()
    assert not any("定量配额" in b for b in only_depth["blockers"])
    with_quant = save(org, pid, {**cfg, "subs": [{**cfg["subs"][0], "design": "iq"}], "n": {**cfg["n"], "q": 50}}).json()
    assert any("定量配额合计 0 与目标 50" in b for b in with_quant["blockers"])


def test_retention_is_chosen_in_the_plan_and_the_consent_text_names_it(org):
    from app.consent import consent_for
    pid = draft(org)["id"]
    approved(org, pid, retention=60)
    pub = org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).json()
    assert pub["retention_days"] == 60 and org.c.get(f"/api/projects/{pid}", headers=org.h("admin")).json()["retention_days"] == 60
    assert "60 days" in consent_for("en", "Acme", retention_days=60)["text"] and "60 天" in consent_for("zh", "Acme", retention_days=60)["text"]
    assert "180 days" in consent_for("en", "Acme")["text"]


def test_an_unlocked_plan_must_be_reviewed_again_and_the_project_keeps_running_on_the_published_version(org):
    pid = draft(org)["id"]
    cfg = approved(org, pid)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    assert org.c.post(f"/api/projects/{pid}/plan/unlock", headers=org.h()).status_code == 403  # a researcher cannot unlock
    open_ = org.c.post(f"/api/projects/{pid}/plan/unlock", headers=org.h("admin")).json()
    assert open_["status"] == "draft" and open_["live"] is True and open_["version"] == 1
    p = org.c.get(f"/api/projects/{pid}", headers=org.h("admin")).json()
    assert p["plan_status"] == "published" and p["editing"] == "draft"  # the project stays usable
    bigger = {**cfg, "n": {**cfg["n"], "i": 9}, "lt": {"dims": ["国家"], "rows": [{"id": "r1", "vs": ["s1"], "t": 0, "i": 9, "f": 0}]}}
    assert save(org, pid, bigger).status_code == 200
    mon = org.c.get(f"/api/projects/{pid}/monitor", headers=org.h()).json()
    assert mon["published"] and mon["i"]["target"] == 5  # monitoring still follows the published version
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).status_code == 409  # not reviewed again
    org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin"))
    assert org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h()).json()["version"] == 2
    assert org.c.get(f"/api/projects/{pid}/monitor", headers=org.h()).json()["i"]["target"] == 9  # now it follows the new one


def test_after_publishing_retention_can_only_be_shortened(org):
    pid = draft(org)["id"]
    cfg = approved(org, pid, retention=120)
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    org.c.post(f"/api/projects/{pid}/plan/unlock", headers=org.h("admin"))
    assert save(org, pid, {**cfg, "retention": 180}).status_code == 422  # people agreed to 120 days
    assert save(org, pid, {**cfg, "retention": 60}).status_code == 200


def test_data_is_deleted_after_each_projects_own_retention_period(org):
    from datetime import timedelta
    from app.db import SessionLocal
    from app.models import Project, utcnow
    from app.retention import days_of, purge_date
    pid = org.project()
    with SessionLocal() as db:
        p = db.get(Project, pid)
        p.retention_days, p.closed_at = 30, utcnow()
        assert days_of(p) == 30 and purge_date(p) - p.closed_at == timedelta(days=30)
        p.retention_days = 999
        assert days_of(p) == 180  # never above the platform's
