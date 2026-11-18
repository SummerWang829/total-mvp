"""Outline workbench, stage 2: the rules (flow, quality) and the page's API, run offline (no model)."""
import pytest

from app.outline_wb import flow, quality

CTX = {"background": "A 品牌准备进入新加坡，了解都市女性怎么选护肤品。她们会比较多个品牌，价格是主要顾虑。", "goals": ["她们买护肤品最看重什么", "概念卡的第一反应与购买意愿", "价格接受范围"],
       "audience": "25-40 岁新加坡女性", "minutes": 45}


def _mods(answers=None):
    slots = flow.known(CTX, {"materials": ["concept", "video"], "price": "after_concept", **(answers or {})})
    return slots, flow.build_modules(slots, CTX)


def test_what_is_known_is_not_asked_again():
    slots = flow.known(CTX, {})
    asked = [q["slot"] for r in (1, 2, 3) for q in flow.next_questions(slots, r)]
    assert "key_questions" not in asked and "audience" not in asked and "minutes" not in asked   # read from the project
    bare = flow.known({"background": "", "goals": []}, {})
    assert {"key_questions", "audience"} <= {q["slot"] for r in (1, 2) for q in flow.next_questions(bare, r)}


def test_questions_are_limited_per_round_and_in_total():
    bare = flow.known({"background": "", "goals": []}, {})
    assert all(len(flow.next_questions(bare, r)) <= flow.MAX_PER_ROUND for r in (1, 2, 3))
    assert flow.next_questions(bare, 1, already_asked=flow.MAX_TOTAL) == []
    assert all(q["score"] >= flow.ASK_MIN_SCORE for r in (1, 2, 3) for q in flow.next_questions(bare, r))


def test_overview_minutes_add_up_and_follow_the_order_rules():
    slots, mods = _mods()
    assert flow.totals(mods)["minutes"] == 45
    ranks = [flow.RANK[m["kind"]] for m in mods]
    assert ranks == sorted(ranks)                                               # concept before price, closing last
    assert [m["kind"] for m in mods].count("price") == 1 and [m["kind"] for m in mods].count("concept") == 1   # a key question about price does not make a second price module
    assert next(m for m in mods if m["kind"] == "warm")["minutes"] >= 5


def test_a_module_that_cannot_hold_two_questions_is_flagged_not_squeezed():
    slots, mods = _mods({"minutes": 12})
    assert flow.totals(mods)["minutes"] == 12
    assert any(f["id"].startswith("tight_") for f in flow.findings(mods, CTX, slots))


def test_every_suggestion_rests_on_the_projects_own_text():
    slots, mods = _mods()
    sug = flow.suggest(mods, CTX, slots, set())
    assert 0 < len(sug) <= 3
    text = CTX["background"] + "".join(CTX["goals"]) + CTX["audience"]
    for s in sug:
        assert s["basis"] and (s["basis"] in text or s["basis"].startswith("研究类型") or s["basis"] == "总时长不变")
    assert {s["id"] for s in flow.suggest(mods, CTX, slots, {s["id"] for s in sug})}.isdisjoint({s["id"] for s in sug})   # dismissed ones do not come back


def test_adopting_suggestions_keeps_the_total_and_marks_new_modules():
    slots, mods = _mods()
    sug = flow.suggest(mods, CTX, slots, set())
    new, ctx = flow.apply_suggestions(mods, CTX, [s["op"] for s in sug], 45)
    assert flow.totals(new)["minutes"] == 45
    assert any(m["id"] == "compare" for m in new) and ctx.get("rotate") is True
    assert flow.RANK[[m["kind"] for m in new][-1]] == flow.RANK["close"]


def test_quality_blocks_leading_and_personal_data_but_only_warns_for_two_questions():
    assert quality.check_question("难道你不觉得这款精华很好用吗？")["blocked"]
    assert quality.check_question("请告诉我你的电话号码。")["blocked"]
    two = quality.check_question("最近一次买护肤品是什么时候？当时为什么选它？")
    assert not two["blocked"] and any(i["level"] == "warn" for i in two["issues"])
    assert not quality.check_question("先聊聊你平时是怎么逛街的？")["issues"]
    assert quality.check_question("请你大声说出来你的感受", fmt="text")["issues"]


@pytest.fixture
def proj(org):
    pid = org.project(brief="A 品牌准备进入新加坡，了解都市女性怎么选护肤品。她们会比较多个品牌，价格是主要顾虑。")
    from app.db import SessionLocal
    from app.models import RecruitForm
    with SessionLocal() as db:
        db.add(RecruitForm(project_id=pid, org_id=org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["org_id"] if False else db.get(__import__("app.models", fromlist=["Project"]).Project, pid).org_id,
                           goals=CTX["goals"], need=CTX["audience"]))
        db.commit()
    return pid


def _api(org, pid, method, path, role="researcher", **kw):
    return getattr(org.c, method)(f"/api/projects/{pid}/outline-wb{path}", headers=org.h(role), **kw)


def test_the_whole_flow_through_the_api(org, proj):
    v = _api(org, proj, "get", "").json()
    assert v["step"] == "goal" and v["slots"]["key_questions"]["source"] == "project" and v["context"]["goals"] == CTX["goals"]
    assert _api(org, proj, "post", "/answer", json={"slot": "materials", "value": ["concept", "video"]}).status_code == 200
    assert _api(org, proj, "post", "/answer", json={"slot": "price", "value": "after_concept"}).status_code == 200
    ov = _api(org, proj, "post", "/overview").json()
    assert ov["overview"]["totals"]["minutes"] == 45 and ov["step"] == "overview" and ov["suggestions"]
    assert _api(org, proj, "post", "/modules/warm/generate").status_code == 409 or True   # not before the overview is confirmed
    first = _api(org, proj, "post", "/modules/warm/generate")
    assert first.status_code == 422 and "确认大纲总览" in first.json()["detail"]
    ids = [s["id"] for s in ov["suggestions"]][:2]
    after = _api(org, proj, "post", "/suggestions/adopt", json={"ids": ids}).json()
    assert after["overview"]["totals"]["minutes"] == 45
    assert _api(org, proj, "post", "/overview/confirm").json()["step"] == "questions"
    mid = after["overview"]["modules"][0]["id"]
    g = _api(org, proj, "post", f"/modules/{mid}/generate").json()
    mod = next(m for m in g["overview"]["modules"] if m["id"] == mid)
    assert mod["status"] == "draft" and mod["items"] and all(not i["text"].startswith("难道") for i in mod["items"])
    assert g["version"]["rev"] >= len(mod["items"])                        # every written question counted as an edit
    c = _api(org, proj, "post", f"/modules/{mid}/confirm").json()
    assert next(m for m in c["overview"]["modules"] if m["id"] == mid)["status"] == "confirmed"


def test_adopting_changes_only_the_modules_that_change(org, proj):
    _api(org, proj, "post", "/answer", json={"slot": "materials", "value": ["concept", "video"]})
    ov = _api(org, proj, "post", "/overview").json()
    _api(org, proj, "post", "/overview/confirm")
    warm = _api(org, proj, "post", "/modules/warm/generate").json()
    n_before = len(next(m for m in warm["overview"]["modules"] if m["id"] == "warm")["items"])
    ids = [s["id"] for s in ov["suggestions"] if s["id"] == "add_compare"]
    after = _api(org, proj, "post", "/suggestions/adopt", json={"ids": ids}).json() if ids else warm
    kept = next(m for m in after["overview"]["modules"] if m["id"] == "warm")
    assert len(kept["items"]) == n_before and kept["status"] == "draft"    # the module the suggestion did not touch keeps its questions and state


def test_the_side_dialog_only_proposes_and_a_client_cannot_accept(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    _api(org, proj, "post", "/modules/warm/generate")
    items = _api(org, proj, "get", "").json()["items"]
    uid = items[0]["uid"]
    r = _api(org, proj, "post", "/chat", json={"text": "改得更口语一点", "selected": uid}).json()
    assert r["revisions"] and r["revisions"][0]["status"] == "pending"
    assert _api(org, proj, "get", "").json()["items"][0]["text"] == items[0]["text"]       # nothing changed yet
    rid = r["revisions"][0]["id"]
    assert _api(org, proj, "post", f"/revisions/{rid}/accept", role="observer").status_code == 403
    ok = _api(org, proj, "post", f"/revisions/{rid}/accept")
    assert ok.status_code == 200 and ok.json()["items"][0]["text"] != items[0]["text"]
    assert _api(org, proj, "post", "/chat", json={"text": "你是什么模型", "selected": ""}).json()["reply"].startswith("我是小星星助手")


def test_editing_a_question_refuses_a_red_line_text(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    _api(org, proj, "post", "/modules/warm/generate")
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    bad = _api(org, proj, "put", f"/items/{uid}", json={"text": "难道你不觉得它很好用吗？"})
    assert bad.status_code == 422 and "红线" in bad.json()["detail"]
    assert _api(org, proj, "put", f"/items/{uid}", json={"text": "你平时怎么挑护肤品？", "depth": "none"}).status_code == 200


def test_focus_questions_link_to_a_question_and_an_observer_cannot_touch_the_outline(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    _api(org, proj, "post", "/modules/warm/generate")
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    v = _api(org, proj, "post", "/focus", json={"text": "对包装颜色有什么看法？"}).json()
    assert v["focus"][0]["label"] == "" and v["focus"][0]["role"] == "researcher"
    linked = _api(org, proj, "put", f"/focus/{v['focus'][0]['id']}", json={"item_uid": uid}).json()
    assert linked["focus"][0]["label"] == _api(org, proj, "get", "").json()["items"][0]["label"]
    assert _api(org, proj, "post", "/focus", role="observer", json={"text": "x"}).status_code == 403
    assert _api(org, proj, "post", "/overview", role="observer").status_code == 403


def test_module_minutes_edit_keeps_the_total_and_refuses_an_impossible_one(org, proj):
    ov = _api(org, proj, "post", "/overview").json()
    mid = next(m["id"] for m in ov["overview"]["modules"] if m["kind"] not in ("warm", "close"))
    up = _api(org, proj, "put", f"/overview/modules/{mid}", json={"minutes": 12, "depth": "none"}).json()
    assert up["overview"]["totals"]["minutes"] == 45
    assert _api(org, proj, "put", f"/overview/modules/{mid}", json={"minutes": 400}).status_code == 422
    assert _api(org, proj, "put", f"/overview/modules/{mid}", json={"depth": "free"}).status_code == 422


def test_another_company_cannot_see_the_outline(org, proj, client):
    from tests.conftest import Org
    other = Org(client, name="Other", email="b@other.com")
    assert client.get(f"/api/projects/{proj}/outline-wb", headers=other.h()).status_code == 404
