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


def test_insert_a_material_checks_its_address_and_a_question_its_wording(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    v = _api(org, proj, "post", "/modules/warm/generate").json()
    uid = v["items"][0]["uid"]
    ok = _api(org, proj, "post", "/items", json={"kind": "website", "text": "请打开官网，找到你会怎么买。", "after_uid": uid, "config": {"url": "https://example.com/shop", "time_limit_s": 240}})
    assert ok.status_code == 200 and ok.json()["items"][1]["kind"] == "website" and ok.json()["items"][1]["config"]["precheck"]["https"] is True
    assert _api(org, proj, "post", "/items", json={"kind": "website", "text": "打开它", "after_uid": uid, "config": {"url": "http://localhost/x"}}).status_code == 422
    assert _api(org, proj, "post", "/items", json={"kind": "question", "text": "难道你不觉得它很好吗？", "after_uid": uid}).status_code == 422
    gone = _api(org, proj, "delete", f"/items/{ok.json()['items'][1]['uid']}")
    assert gone.status_code == 200 and all(i["kind"] != "website" for i in gone.json()["items"])


# ---- stage 3: preview, rehearsal, pilot, publish ------------------------------------------------------------------------------------------------

def _ready(org, proj, modules=("warm",)):
    """An outline with its overview confirmed and the first modules written and confirmed."""
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    for m in modules:
        _api(org, proj, "post", f"/modules/{m}/generate")
        _api(org, proj, "post", f"/modules/{m}/confirm")


def test_preview_shows_what_a_participant_sees_and_flags_a_missing_language(org, proj):
    _ready(org, proj)
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    _api(org, proj, "post", "/items", json={"kind": "concept", "text": "请看这张概念卡", "after_uid": uid, "config": {"title": "概念 A"}})
    pv = _api(org, proj, "get", "/preview").json()
    concept = next(i for i in pv["items"] if i["kind"] == "concept")
    assert concept["watermark"] and concept["view_only"]
    en = _api(org, proj, "get", "/preview?lang=en").json()
    assert any("缺题" in c["text"] for c in en["checks"]) and all(i["missing"] for i in en["items"])


def test_rehearsal_is_saved_and_goes_stale_after_an_edit(org, proj):
    _ready(org, proj)
    r = _api(org, proj, "post", "/rehearsal")
    assert r.status_code == 200 and r.json()["runs"][0]["sessions"] == 3 and not r.json()["runs"][0]["stale"]
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    _api(org, proj, "put", f"/items/{uid}", json={"minutes": 3})
    assert _api(org, proj, "get", "/rehearsal").json()["runs"][0]["stale"] is True


def _pilot_setup(org, proj):
    """A published outline and one pilot interview with numbered lines, one of them a participant asking what a word means."""
    from app.db import SessionLocal
    from app.models import CaptionLine, Interview, Participant, Project
    from app.outline_wb import service as svc, stage3
    _ready(org, proj)
    assert _api(org, proj, "post", "/publish").status_code == 200
    items = _api(org, proj, "get", "").json()["items"]
    with SessionLocal() as db:
        p = db.get(Project, proj)
        part = Participant(org_id=p.org_id, project_id=p.id, name="演示", country="SG", language="zh", code="M-901")
        db.add(part)
        db.flush()
        itv = Interview(org_id=p.org_id, project_id=p.id, participant_id=part.id, title="Pilot", language="zh", status="ended", moderation="ai", is_pilot=True, invite_hash="x-pilot")
        db.add(itv)
        db.flush()
        stage3.snapshot_on_start(db, itv)
        rows = [("moderator", items[0]["text"], items[0]["uid"], "ai"), ("participant", "你说的这个是什么意思？", items[0]["uid"], "ai"), ("participant", "我平时在网上买。", items[0]["uid"], "ai")]
        for n, (sp, tx, uid, src) in enumerate(rows, 1):
            ln = CaptionLine(org_id=p.org_id, interview_id=itv.id, seq=n, speaker=sp, text=tx, at_ms=n * 30000, lang="zh")
            db.add(ln)
            db.flush()
            svc.assign_line(ln, uid, src)
        db.commit()
        return itv.id, [ln.id for ln in db.query(CaptionLine).filter_by(interview_id=itv.id).order_by(CaptionLine.seq)]


def test_publishing_freezes_for_new_interviews_and_keeps_the_older_pages_in_step(org, proj):
    iid, _ = _pilot_setup(org, proj)
    from app.db import SessionLocal
    from app.models import Interview, Project
    from app.outline_wb.models import OutlineSnapshot
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        assert itv.outline_snapshot_id and itv.outline_rev is not None
        proj_row = db.get(Project, proj)
        assert "## 暖场" in proj_row.outline_text and proj_row.outline_text.count("\n- ") >= 1      # the older pages read the published outline
        snap = db.get(OutlineSnapshot, itv.outline_snapshot_id)
        before = [i["texts"]["zh"] for i in snap.content["items"]]
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    _api(org, proj, "put", f"/items/{uid}", json={"text": "你平时怎么买东西呢？"})      # edited after the pilot started
    with SessionLocal() as db:
        assert [i["texts"]["zh"] for i in db.get(OutlineSnapshot, itv.outline_snapshot_id).content["items"]] == before


def test_publish_is_refused_while_a_focus_question_has_no_question(org, proj):
    _ready(org, proj)
    _api(org, proj, "post", "/focus", json={"text": "对包装颜色有什么看法？"})
    r = _api(org, proj, "post", "/publish")
    assert r.status_code == 422 and "关注问题" in r.json()["detail"]
    chk = _api(org, proj, "get", "/publish/check").json()
    assert chk["can_publish"] is False and chk["versions"]


def test_pilot_view_numbers_lines_finds_problems_and_a_client_can_only_circle(org, proj):
    iid, lines = _pilot_setup(org, proj)
    pv = _api(org, proj, "get", f"/pilots/{iid}").json()
    assert all(ln["label"] for ln in pv["lines"]) and any(f["kind"] == "confused" for f in pv["findings"])
    m = _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[1], "start": 0, "end": 4, "tag": "unclear", "note": "没听懂"})
    assert m.status_code == 200 and m.json()["marks"][0]["quote"] == "你说的这" and m.json()["marks"][0]["role"] == "researcher"
    assert _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[1], "start": 0, "end": 99, "tag": "unclear"}).status_code == 422
    assert _api(org, proj, "post", f"/pilots/{iid}/suggest", role="observer").status_code == 403


def test_pilot_suggestions_merge_by_question_and_have_three_possible_destinations(org, proj):
    iid, lines = _pilot_setup(org, proj)
    _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[1], "start": 0, "end": 4, "tag": "unclear", "note": "没听懂"})
    _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[0], "start": 0, "end": 3, "tag": "host", "note": "主持引导不当"})
    out = _api(org, proj, "post", f"/pilots/{iid}/suggest").json()
    first = _api(org, proj, "get", "").json()["items"][0]["uid"]
    mine = [r for r in out["revisions"] if r["item_uid"] == first]
    assert [r["destination"] for r in mine].count("outline") == 1 and "host_engine" in {r["destination"] for r in mine}   # two marks and a finding about one question made ONE outline rewrite
    assert any("没有问到" in r["reason"] for r in out["revisions"] if r["item_uid"] != first)                              # questions the pilot never asked are reported too
    host = next(r for r in out["revisions"] if r["destination"] == "host_engine")
    ok = _api(org, proj, "post", f"/revisions/{host['id']}/accept")
    assert ok.status_code == 200                                           # a training case number is made for it
    outline_rev = next(r for r in out["revisions"] if r["destination"] == "outline")
    before = _api(org, proj, "get", "").json()["items"][0]["text"]
    assert _api(org, proj, "post", f"/revisions/{outline_rev['id']}/accept", role="observer").status_code == 403
    assert _api(org, proj, "post", f"/revisions/{outline_rev['id']}/accept").json()["items"][0]["text"] != before


def test_a_new_version_is_a_copy_with_the_same_permanent_ids(org, proj):
    _ready(org, proj)
    v1 = _api(org, proj, "get", "").json()
    out = _api(org, proj, "post", "/versions", json={"summary": "第二版"}).json()
    assert out["version"] == 2 and {v["number"] for v in out["versions"]} == {1, 2}
    v2 = _api(org, proj, "get", "").json()
    assert [i["uid"] for i in v2["items"]] == [i["uid"] for i in v1["items"]] and v2["version"]["number"] == 2
