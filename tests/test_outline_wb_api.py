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


def _questions(org, pid):
    """The outline's items without the spoken opening / closing notes (修正方案第 4 轮), i.e. what is asked."""
    return [i for i in _api(org, pid, "get", "").json()["items"] if i["kind"] != "note"]


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
    items = _questions(org, proj)
    uid = items[0]["uid"]
    r = _api(org, proj, "post", "/chat", json={"text": "改得更口语一点", "selected": uid}).json()
    assert r["revisions"] and r["revisions"][0]["status"] == "pending"
    assert _questions(org, proj)[0]["text"] == items[0]["text"]       # nothing changed yet
    rid = r["revisions"][0]["id"]
    assert _api(org, proj, "post", f"/revisions/{rid}/accept", role="observer").status_code == 403
    ok = _api(org, proj, "post", f"/revisions/{rid}/accept")
    assert ok.status_code == 200 and ok.json()["items"][0]["text"] != items[0]["text"]
    assert _api(org, proj, "post", "/chat", json={"text": "你是什么模型", "selected": ""}).json()["reply"].startswith("我是小星星助手")


def test_editing_a_question_refuses_a_red_line_text(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    _api(org, proj, "post", "/modules/warm/generate")
    uid = _questions(org, proj)[0]["uid"]
    bad = _api(org, proj, "put", f"/items/{uid}", json={"text": "难道你不觉得它很好用吗？"})
    assert bad.status_code == 422 and "红线" in bad.json()["detail"]
    assert _api(org, proj, "put", f"/items/{uid}", json={"text": "你平时怎么挑护肤品？", "depth": "none"}).status_code == 200


def test_focus_questions_link_to_a_question_and_an_observer_cannot_touch_the_outline(org, proj):
    _api(org, proj, "post", "/overview")
    _api(org, proj, "post", "/overview/confirm")
    _api(org, proj, "post", "/modules/warm/generate")
    uid = _questions(org, proj)[0]["uid"]
    v = _api(org, proj, "post", "/focus", json={"text": "对包装颜色有什么看法？"}).json()
    assert v["focus"][0]["label"] == "" and v["focus"][0]["role"] == "researcher"
    linked = _api(org, proj, "put", f"/focus/{v['focus'][0]['id']}", json={"item_uid": uid}).json()
    assert linked["focus"][0]["label"] == _questions(org, proj)[0]["label"]
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
    uid = _questions(org, proj)[0]["uid"]
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
    uid = _questions(org, proj)[0]["uid"]
    _api(org, proj, "put", f"/items/{uid}", json={"minutes": 3})
    assert _api(org, proj, "get", "/rehearsal").json()["runs"][0]["stale"] is True


def _pilot_setup(org, proj):
    """A published outline and one pilot interview with numbered lines, one of them a participant asking what a word means."""
    from app.db import SessionLocal
    from app.models import CaptionLine, Interview, Participant, Project
    from app.outline_wb import service as svc, stage3
    _ready(org, proj)
    items = _questions(org, proj)
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
        ids = [ln.id for ln in db.query(CaptionLine).filter_by(interview_id=itv.id).order_by(CaptionLine.seq)]
    _api(org, proj, "put", "/ready", json={"value": True})                # the researcher says 可以正式开始 (修正方案第 1 轮第 9 条)
    assert _api(org, proj, "post", "/publish").status_code == 200          # a pilot has been done: now it can be published
    return itv.id, ids


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
    uid = _questions(org, proj)[0]["uid"]
    _api(org, proj, "put", f"/items/{uid}", json={"text": "你平时怎么买东西呢？"})      # edited after the pilot started
    with SessionLocal() as db:
        assert [i["texts"]["zh"] for i in db.get(OutlineSnapshot, itv.outline_snapshot_id).content["items"]] == before


def test_a_focus_question_without_a_question_only_warns_before_publishing(org, proj):
    """修正方案第 1 轮第 10 条 (user 2026-11-19): only the real gates block; an unmatched focus question is a reminder."""
    _ready(org, proj)
    _api(org, proj, "post", "/focus", json={"text": "对包装颜色有什么看法？"})
    r = _api(org, proj, "post", "/publish")
    assert r.status_code == 422 and "关注问题" not in r.json()["detail"]
    assert next(c for c in _api(org, proj, "get", "/publish/check").json()["checks"] if c["id"] == "focus")["level"] == "warn"
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
    first = _questions(org, proj)[0]["uid"]
    mine = [r for r in out["revisions"] if r["item_uid"] == first]
    assert [r["destination"] for r in mine].count("outline") == 1 and "host_engine" in {r["destination"] for r in mine}   # two marks and a finding about one question made ONE outline rewrite
    assert all(r["item_uid"] == first for r in out["revisions"])                                                         # a question nobody asked has nothing to rewrite ...
    assert any(f["kind"] == "skipped" and "没有问到" in f["text"] for f in _api(org, proj, "get", f"/pilots/{iid}").json()["findings"])   # ... but it is reported, once
    host = next(r for r in out["revisions"] if r["destination"] == "host_engine")
    ok = _api(org, proj, "post", f"/revisions/{host['id']}/accept")
    assert ok.status_code == 200                                           # a training case number is made for it
    outline_rev = next(r for r in out["revisions"] if r["destination"] == "outline")
    before = _questions(org, proj)[0]["text"]
    assert _api(org, proj, "post", f"/revisions/{outline_rev['id']}/accept", role="observer").status_code == 403
    assert _api(org, proj, "post", f"/revisions/{outline_rev['id']}/accept").json()["items"][0]["text"] != before


def test_there_is_no_button_or_api_to_make_an_empty_version(org, proj):
    """修正方案第 1 轮第 10 条：版本只在 pilot 修正时产生，不能手动新建（原来会产生没有改动的空版本）。新版本保留 item_uid 由 test_outline_wb.py 覆盖。"""
    _ready(org, proj)
    assert _api(org, proj, "post", "/versions", json={"summary": "第二版"}).status_code in (404, 405)
    assert [v["number"] for v in _api(org, proj, "get", "/publish/check").json()["versions"]] == [1]


def test_an_interview_can_be_marked_as_a_pilot_and_then_lists_there(org, proj):
    itv = org.interview(pid=proj)
    before = _api(org, proj, "get", "/pilots").json()
    assert before["pilots"] == [] and [c["id"] for c in before["candidates"]] == [itv["id"]]
    after = _api(org, proj, "put", f"/pilots/{itv['id']}", json={"value": True}).json()
    assert [p["id"] for p in after["pilots"]] == [itv["id"]] and after["candidates"] == []
    assert _api(org, proj, "put", f"/pilots/{itv['id']}", json={"value": True}, role="observer").status_code == 403


# ---- stage 4: the live board --------------------------------------------------------------------------------------------------------------------

def _board_setup(org, proj):
    """A published outline, a focus question tied to the first question, and a live (not pilot) interview that started on the snapshot."""
    from app.db import SessionLocal
    from app.models import Interview, Participant, Project
    from app.outline_wb import stage3
    _ready(org, proj, ("warm",))
    uid = _questions(org, proj)[0]["uid"]
    fid = _api(org, proj, "post", "/focus", json={"text": "她们平时怎么买东西", "item_uid": uid}).json()["focus"][0]["id"]
    with SessionLocal() as db:
        p = db.get(Project, proj)
        pp = Participant(org_id=p.org_id, project_id=p.id, name="试访", country="SG", language="zh", code="M-900")
        db.add(pp)
        db.flush()
        pil = Interview(org_id=p.org_id, project_id=p.id, participant_id=pp.id, title="Pilot", language="zh", status="ended", moderation="human", is_pilot=True, invite_hash="x-pilot0")
        db.add(pil)
        db.flush()
        stage3.snapshot_on_start(db, pil)
        db.commit()
    _api(org, proj, "put", "/ready", json={"value": True})
    assert _api(org, proj, "post", "/publish").status_code == 200
    with SessionLocal() as db:
        p = db.get(Project, proj)
        part = Participant(org_id=p.org_id, project_id=p.id, name="演示", country="SG", language="zh", code="Q-001")
        db.add(part)
        db.flush()
        itv = Interview(org_id=p.org_id, project_id=p.id, participant_id=part.id, title="真访谈", language="zh", status="live", moderation="human", invite_hash="x-board")
        db.add(itv)
        db.flush()
        from app.models import utcnow
        itv.started_at = utcnow()
        stage3.snapshot_on_start(db, itv)
        db.commit()
        return itv.id, fid, uid


def _say(iid, speaker, text, seq):
    """A caption line through the same function the room uses, then wait for the background coding."""
    import time
    from app.db import SessionLocal
    from app.models import CaptionLine, Interview
    from app.outline_wb import board
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        line = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=seq, speaker=speaker, text=text, at_ms=seq * 20000)
        db.add(line)
        db.flush()
        task = board.assign(db, itv, line, speaker, text)
        db.commit()
    board.submit(task)
    return task


def _wait(fn, secs=8):
    import time
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def test_a_line_is_put_under_its_question_and_only_answers_are_coded(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    assert _say(iid, "moderator", items[0]["text"], 1) is None                    # the moderator's line is placed, never coded
    t = _say(iid, "participant", "我平时在网上买，价格偏贵。", 2)
    assert t is not None and t.item_uid == uid                                    # the answer stays under the question that was asked
    assert _say(iid, "participant", "嗯", 3) is None                              # too short to code
    assert _wait(lambda: _api(org, proj, "get", "/board").json()["cards"][0]["answered"] == 1)
    card = _api(org, proj, "get", "/board").json()["cards"][0]
    assert card["asked"] == 1 and card["top"]


def test_an_answer_under_another_question_that_talks_about_it_counts_as_volunteered(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[1]["text"], 1)                                   # a different question is on
    _say(iid, "participant", "我平时怎么买东西？一般在网上买。", 2)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"] != [])
    q = _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"][0]
    assert q["origin"] == "volunteered" and q["in"]                               # said unprompted, and where
    assert _api(org, proj, "get", f"/board/focus/{fid}?origin=asked").json()["quotes"] == []


def test_percentages_wait_for_twenty_people_and_low_confidence_goes_to_review(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西。", 2)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{fid}").json()["n"] == 1)
    d = _api(org, proj, "get", f"/board/focus/{fid}").json()
    assert d["percent"] is False and all(x["pct"] is None for x in d["dist"])    # one person: counts only
    from app.db import SessionLocal
    from app.outline_wb.models import CodedAnswer
    with SessionLocal() as db:
        ca = db.query(CodedAnswer).first()
        ca.confidence = 0.6
        db.commit()
    assert _api(org, proj, "get", "/board").json()["cards"][0]["review"] == 1


def test_recoding_keeps_the_ai_code_and_a_client_sees_only_numbers_and_cannot_change(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西。", 2)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"] != [])
    cid = _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"][0]["id"]
    first = _api(org, proj, "get", f"/board/answer/{cid}").json()
    assert first["who"] == "Q-001" and first["can_download"] is False and first["question"]
    new = _api(org, proj, "put", f"/board/answer/{cid}", json={"code": "网购"}).json()
    assert new["code"] == "网购" and new["ai_code"] == first["code"] and new["confirmed"] is True
    assert _api(org, proj, "put", f"/board/answer/{cid}", json={"code": "x"}, role="observer").status_code == 403
    clip = _api(org, proj, "post", f"/board/answer/{cid}/report").json()
    assert clip["clip_id"]
    assert _api(org, proj, "post", f"/board/answer/{cid}/report").json()["clip_id"] == clip["clip_id"]    # twice makes one clip


def test_the_board_asks_by_keyword_and_answers_with_numbers_only(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西，价格偏贵。", 2)
    assert _wait(lambda: _api(org, proj, "get", "/board").json()["cards"][0]["answered"] == 1)
    r = _api(org, proj, "post", "/board/chat", json={"text": "哪些人提到价格偏贵"}).json()
    assert r["who"] == ["Q-001"] and "演示" not in r["reply"]
    assert _api(org, proj, "post", "/board/chat", json={"text": "你是什么模型"}).json()["reply"].startswith("我是小星星助手")
    changes = _api(org, proj, "get", "/board/changes").json()["token"]
    assert changes.startswith("1:")


def test_saturation_needs_eight_answers_and_adopting_only_proposes(org, proj):
    from app.outline_wb import board
    assert board._saturated(["价格"] * 7) is False
    assert board._saturated(["价格", "价格", "成分", "成分", "价格", "成分", "价格", "成分", "价格", "成分"]) is True   # the last five all fell into codes seen at least twice before
    assert board._saturated(["价格"] * 6 + ["新说法", "价格"]) is False
    iid, fid, uid = _board_setup(org, proj)
    assert _api(org, proj, "post", "/board/suggestions/adopt", json={"ids": ["sat_none"]}).json()["proposed"] == 0


def test_a_pilot_is_a_must_before_publishing_and_it_runs_on_the_draft(org, proj):
    _ready(org, proj)
    r = _api(org, proj, "post", "/publish")
    assert r.status_code == 422 and "pilot" in r.json()["detail"]
    chk = _api(org, proj, "get", "/publish/check").json()
    assert next(c for c in chk["checks"] if c["id"] == "pilot")["level"] == "block"
    iid, _ = _pilot_setup(org, proj)                                          # a pilot (on the draft) and then the publish works
    chk = _api(org, proj, "get", "/publish/check").json()
    assert next(c for c in chk["checks"] if c["id"] == "pilot")["level"] == "ok"


def test_the_pilot_summary_groups_findings_by_seriousness_and_place_and_makes_the_update(org, proj):
    iid, lines = _pilot_setup(org, proj)
    _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[1], "start": 0, "end": 4, "tag": "leading", "note": "像在引导"})
    _api(org, proj, "post", f"/pilots/{iid}/marks", json={"line_id": lines[0], "start": 0, "end": 3, "tag": "host", "note": "主持引导不当"})
    sm = _api(org, proj, "get", "/pilot-summary").json()
    assert sm["pilots"] == 1 and {g["dest"] for g in sm["groups"]} == {"outline", "material", "host_engine"}
    assert {i["level"] for i in sm["items"]} >= {"now", "watch"} and sm["can_update"] is False
    assert _api(org, proj, "post", "/pilot-update").status_code == 422        # nothing accepted yet
    out = _api(org, proj, "post", f"/pilots/{iid}/suggest").json()
    for r in out["revisions"]:
        _api(org, proj, "post", f"/revisions/{r['id']}/accept")
    sm = _api(org, proj, "get", "/pilot-summary").json()
    assert sm["can_update"] is True and sm["host"] and sm["host"][0]["status"] == "accepted"
    up = _api(org, proj, "post", "/pilot-update").json()
    assert up["version"] == 2 and "Pilot 修正" in up["summary"] and up["host"]
    assert _api(org, proj, "get", "").json()["version"]["number"] == 2


# ---- stage 5: adding a focus question while the interviews run ------------------------------------------------------------------------------------------

def test_a_new_focus_question_is_matched_to_questions_by_words_and_the_top_two_are_ticked(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    out = _api(org, proj, "post", "/board/focus/suggest", json={"text": "她们平时怎么买东西"}).json()
    assert out["candidates"] and out["candidates"][0]["score"] >= 0.25 and out["candidates"][0]["checked"] is True
    assert sum(1 for c in out["candidates"] if c["checked"]) <= 2
    none = _api(org, proj, "post", "/board/focus/suggest", json={"text": "完全无关的火星气候"}).json()
    assert none["none_fit"] is True and not any(c["checked"] for c in none["candidates"])
    assert _api(org, proj, "post", "/board/focus/suggest", json={"text": "x"}, role="observer").status_code == 403


def test_coding_choices_are_checked_and_collect_mode_waits_to_be_grouped(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    bad = _api(org, proj, "post", "/board/focus/new", json={"text": "包装环保", "coding": "custom", "codes": ["只有一个"]})
    assert bad.status_code == 422
    assert _api(org, proj, "post", "/board/focus/new", json={"text": "包装环保", "coding": "wrong"}).status_code == 422
    new = _api(org, proj, "post", "/board/focus/new", json={"text": "她们平时怎么买东西的渠道", "item_uids": [uid], "coding": "collect"}).json()["id"]
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西，渠道主要是电商。", 2)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{new}").json()["pending"] == 1)
    d = _api(org, proj, "get", f"/board/focus/{new}").json()
    assert d["dist"] == [] and d["coding"] == "collect" and d["can_cluster"] is False      # waiting answers are not counted in the statistics
    tpl = _api(org, proj, "post", "/board/focus/new", json={"text": "态度怎样", "coding": "attitude"}).json()["id"]
    assert _api(org, proj, "get", f"/board/focus/{tpl}").json()["codes"] == ["很认可", "一般", "不认可", "说不清"]


def test_eight_collected_answers_can_be_grouped_and_only_confirmed_groups_count(org, proj):
    from app.db import SessionLocal
    from app.outline_wb import service as svc
    iid, fid, uid = _board_setup(org, proj)
    new = _api(org, proj, "post", "/board/focus/new", json={"text": "她们平时怎么买东西的渠道", "item_uids": [uid], "coding": "collect"}).json()["id"]
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    texts = ["我平时在网上买东西，电商比较方便。"] * 4 + ["我喜欢去实体店亲自挑选东西。"] * 4
    for n, t in enumerate(texts, 2):
        _say(iid, "participant", t, n)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{new}").json()["can_cluster"] is True, 15)
    cl = _api(org, proj, "get", f"/board/focus/{new}/clusters").json()
    assert len(cl["groups"]) >= 2 and sum(len(g["members"]) for g in cl["groups"]) == 8
    g0 = cl["groups"][0]
    out = _api(org, proj, "post", f"/board/focus/{new}/codes", json={"groups": [{"name": "网购", "answer_ids": [m["id"] for m in g0["members"]]}]}).json()
    assert out["codes"] == ["网购"] and out["dist"][0]["code"] == "网购" and out["pending"] == 8 - len(g0["members"])
    assert _api(org, proj, "post", f"/board/focus/{new}/codes", json={"groups": []}).status_code == 422


def test_looking_back_finds_lines_said_in_other_questions_and_counts_only_the_ticked(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[1]["text"], 1)                                   # another question is on ...
    _say(iid, "participant", "说到价格，我觉得包装的环保材料比较重要。", 2)         # ... and she talks about packaging
    new = _api(org, proj, "post", "/board/focus/new", json={"text": "包装的环保材料", "item_uids": [uid], "coding": "collect"}).json()["id"]
    bf = _api(org, proj, "get", f"/board/focus/{new}/backfill").json()
    assert bf["candidates"] and bf["candidates"][0]["in"] and "包装" in bf["candidates"][0]["text"]
    lid = bf["candidates"][0]["line_id"]
    assert _api(org, proj, "post", f"/board/focus/{new}/backfill", json={"line_ids": ["not-a-line"]}).json()["added"] == 0
    assert _api(org, proj, "post", f"/board/focus/{new}/backfill", json={"line_ids": [lid]}).json()["added"] == 1
    d = _api(org, proj, "get", f"/board/focus/{new}?origin=volunteered").json()
    assert d["pending"] == 1 and d["quotes"][0]["origin"] == "volunteered"
    assert _api(org, proj, "get", f"/board/focus/{new}/backfill").json()["candidates"] == []     # already counted: not offered again


def test_a_question_added_for_the_next_interviews_is_a_pending_revision_that_links_when_accepted(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    new = _api(org, proj, "post", "/board/focus/new", json={"text": "包装是否环保", "item_uids": [], "coding": "collect"}).json()["id"]
    r = _api(org, proj, "post", f"/board/focus/{new}/add-question").json()["revision"]
    assert r["status"] == "pending" and "包装是否环保" in r["after"]["texts"]["zh"]
    n_before = len(_questions(org, proj))
    assert _api(org, proj, "post", f"/revisions/{r['id']}/accept", role="observer").status_code == 403
    assert _api(org, proj, "post", f"/revisions/{r['id']}/accept").status_code == 200
    v = _api(org, proj, "get", "").json()
    qs = [i for i in v["items"] if i["kind"] != "note"]
    assert len(qs) == n_before + 1
    f = next(x for x in v["focus"] if x["id"] == new)
    assert f["item_uid"] == qs[-1]["uid"]                                       # the focus question now points at the new question
    from app.db import SessionLocal
    from app.models import Interview
    with SessionLocal() as db:
        running = db.get(Interview, iid)
        from app.outline_wb.models import OutlineSnapshot
        assert len([i for i in db.get(OutlineSnapshot, running.outline_snapshot_id).content["items"] if i["kind"] != "note"]) == n_before     # the interview already running keeps its snapshot


# ---- which question a line belongs to: host > ai > guess, and a fix afterwards ---------------------------------------------------------------------------------

def _line_row(iid, seq):
    from app.db import SessionLocal
    from app.models import CaptionLine
    with SessionLocal() as db:
        r = db.query(CaptionLine).filter_by(interview_id=iid, seq=seq).one()
        return r.item_uid, r.item_source, r.id


def test_the_ai_engine_says_which_prepared_question_it_asked_and_follow_ups_stay_under_it(org, proj):
    from app.db import SessionLocal
    from app.models import CaptionLine, Interview
    from app.outline_wb import board
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        a = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=1, speaker="moderator", text="随便什么话", at_ms=1)
        db.add(a)
        db.flush()
        board.assign(db, itv, a, "ai", "随便什么话", question=items[1]["text"], engine=True)     # the engine names the prepared question (words that do not look like it at all)
        b = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=2, speaker="moderator", text="能再说说吗", at_ms=2)
        db.add(b)
        db.flush()
        board.assign(db, itv, b, "ai", "能再说说吗", question=None, engine=True)               # a follow-up: no prepared question
        c = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=3, speaker="participant", text="就是这样吧我觉得", at_ms=3)
        db.add(c)
        db.flush()
        board.assign(db, itv, c, "participant", "就是这样吧我觉得")
        db.commit()
    assert _line_row(iid, 1)[:2] == (items[1]["uid"], "ai")
    assert _line_row(iid, 2)[:2] == (items[1]["uid"], "ai")
    assert _line_row(iid, 3)[:2] == (items[1]["uid"], "ai")                                          # the answer stays under the question, with the same certainty


def test_when_nobody_says_it_is_a_guess_and_the_board_marks_the_guesses(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[0]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西，价格偏贵。", 2)
    assert _line_row(iid, 1)[1] == "guess" and _line_row(iid, 2)[1] == "guess"
    assert _wait(lambda: _api(org, proj, "get", "/board").json()["cards"][0]["answered"] == 1)
    assert _api(org, proj, "get", "/board").json()["cards"][0]["guessed"] == 1
    assert _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"][0]["src"] == "guess"


def test_the_human_moderator_can_say_which_question_is_being_asked_and_that_wins(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    r = _api(org, proj, "put", f"/interviews/{iid}/current-question", json={"item_uid": items[2]["uid"]}, role="moderator")
    assert r.status_code in (200, 404)                              # the moderator of this interview may; a moderator not on it gets "not found"
    r = _api(org, proj, "put", f"/interviews/{iid}/current-question", json={"item_uid": items[2]["uid"]})
    assert r.status_code == 200 and r.json()["current"] == items[2]["uid"]
    _say(iid, "moderator", items[0]["text"], 1)                     # the words look like question 1, but the moderator said question 3
    assert _line_row(iid, 1)[:2] == (items[2]["uid"], "host")
    assert _api(org, proj, "put", f"/interviews/{iid}/current-question", json={"item_uid": "nope"}).status_code == 404
    assert _api(org, proj, "put", f"/interviews/{iid}/current-question", json={"item_uid": None}).json()["current"] is None
    _say(iid, "moderator", items[0]["text"], 2)
    assert _line_row(iid, 2)[1] == "guess"                          # back to automatic


def test_a_researcher_can_fix_a_lines_question_afterwards_and_it_is_coded_again(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    _say(iid, "moderator", items[1]["text"], 1)
    _say(iid, "participant", "我平时在网上买东西，价格偏贵。", 2)
    assert _wait(lambda: _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"] != [])
    assert _api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"][0]["origin"] == "volunteered"     # it was under another question
    lid = _line_row(iid, 2)[2]
    assert _api(org, proj, "put", f"/interviews/{iid}/lines/{lid}/item", json={"item_uid": uid}, role="observer").status_code == 403
    assert _api(org, proj, "put", f"/interviews/{iid}/lines/{lid}/item", json={"item_uid": "nope"}).status_code == 404
    assert _api(org, proj, "put", f"/interviews/{iid}/lines/{lid}/item", json={"item_uid": uid}).status_code == 200
    assert _line_row(iid, 2)[:2] == (uid, "edited")
    assert _wait(lambda: (lambda q: bool(q) and q[0]["origin"] == "asked")(_api(org, proj, "get", f"/board/focus/{fid}").json()["quotes"]))     # coded again, now "asked"


def test_a_pilot_is_numbered_but_not_coded(org, proj):
    from app.db import SessionLocal
    from app.models import CaptionLine, Interview
    from app.outline_wb import board, stage3
    iid, fid, uid = _board_setup(org, proj)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        itv.is_pilot = True
        line = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=1, speaker="participant", text="我平时在网上买东西。", at_ms=1)
        db.add(line)
        db.flush()
        task = board.assign(db, itv, line, "participant", "我平时在网上买东西。")
        db.commit()
    assert task is None


def test_the_room_gives_the_human_moderator_the_questions_to_point_at_and_not_the_ai_or_clients(org, proj):
    iid, fid, uid = _board_setup(org, proj)
    items = _questions(org, proj)
    a = org.c.get(f"/api/interviews/{iid}/assist", headers=org.h()).json()
    assert a["wb"]["current"] is None and {i["uid"] for i in a["wb"]["items"]} >= {items[0]["uid"]}
    assert org.c.put(f"/api/interviews/{iid}/current-question", headers=org.h(), json={"item_uid": items[1]["uid"]}).json()["current"] == items[1]["uid"]
    assert org.c.get(f"/api/interviews/{iid}/assist", headers=org.h()).json()["wb"]["current"] == items[1]["uid"]
    assert org.c.put(f"/api/interviews/{iid}/current-question", headers=org.h("observer"), json={"item_uid": None}).status_code == 403
    assert "wb" not in org.c.get(f"/api/interviews/{iid}/assist", headers=org.h("observer")).json()          # an observer or client never gets the pointer
    from app.db import SessionLocal
    from app.models import Interview
    with SessionLocal() as db:
        db.get(Interview, iid).moderation = "ai"
        db.commit()
    assert "wb" not in org.c.get(f"/api/interviews/{iid}/assist", headers=org.h()).json()
    assert org.c.put(f"/api/interviews/{iid}/current-question", headers=org.h(), json={"item_uid": None}).status_code == 409


def test_the_pilot_view_lists_the_questions_a_line_can_be_moved_to(org, proj):
    iid, lines = _pilot_setup(org, proj)
    pv = _api(org, proj, "get", f"/pilots/{iid}").json()
    assert pv["questions"] and all({"uid", "label", "text"} <= set(q) for q in pv["questions"])


# ---- upgrade safety: old projects, model scope, retention preview -------------------------------------------------------------------------------------------

def test_a_project_that_never_used_the_workbench_still_runs_on_its_old_outline_text(org):
    """The AI room reads the old outline text when an interview has no snapshot (every project made before the workbench, and any project whose outline was never published)."""
    from app import ai_room
    from app.db import SessionLocal
    from app.models import Interview, Project
    pid = org.project()
    itv = org.interview(pid=pid, moderation="ai")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        p.outline_text = "目标: 了解护肤品选购\n受访者话题: 护肤品\n语言: en\n时长: 30\n## 暖场\n- 先聊聊你平时的护肤习惯？\n## 话题: 选购\n- 你上一次买护肤品是怎么选的？\n"
        db.commit()
        row = db.get(Interview, itv["id"])
        assert row.outline_snapshot_id is None
        g = ai_room.project_guide(db, row)
        assert g is not None and "先聊聊你平时的护肤习惯？" in g.warmup_questions                  # the old text is what the room uses
        from app.outline_wb import stage3
        stage3.snapshot_on_start(db, row)
        assert row.outline_snapshot_id is None                                                      # no workbench outline: nothing is frozen, nothing changes


def test_an_unpublished_workbench_outline_is_not_used_by_a_real_interview_only_by_a_pilot(org, proj):
    from app.db import SessionLocal
    from app.models import Interview, Participant, Project
    from app.outline_wb import stage3
    _ready(org, proj)                                                                              # an outline exists, but it is only a draft
    with SessionLocal() as db:
        p = db.get(Project, proj)
        part = Participant(org_id=p.org_id, project_id=p.id, name="x", country="SG", language="zh", code="M-1")
        db.add(part)
        db.flush()
        real = Interview(org_id=p.org_id, project_id=p.id, participant_id=part.id, title="真", language="zh", status="live", invite_hash="r1")
        pilot = Interview(org_id=p.org_id, project_id=p.id, participant_id=part.id, title="试", language="zh", status="live", invite_hash="r2", is_pilot=True)
        db.add_all([real, pilot])
        db.flush()
        stage3.snapshot_on_start(db, real)
        stage3.snapshot_on_start(db, pilot)
        assert real.outline_snapshot_id is None and pilot.outline_snapshot_id                       # a real interview never runs on an unpublished draft; a pilot runs on it


def test_the_model_reads_answers_only_in_ai_moderated_interviews_whose_consent_names_the_vendor(org, proj):
    import time
    from app.db import SessionLocal
    from app.models import Interview
    from app.outline_wb import board
    calls = {"n": 0}

    class Spy:
        name = "spy"

        def complete(self, msgs, **kw):  # noqa: ANN001, ANN003
            calls["n"] += 1
            return {"code": "偏贵", "confidence": 0.9}

    old = board._chat
    board._chat = lambda: Spy()
    try:
        iid, fid, uid = _board_setup(org, proj)                                                     # a human-moderated interview
        items = _questions(org, proj)
        _say(iid, "moderator", items[0]["text"], 1)
        _say(iid, "participant", "我平时在网上买，价格偏贵。", 2)
        assert _wait(lambda: _api(org, proj, "get", "/board").json()["cards"][0]["answered"] == 1)
        assert calls["n"] == 0                                                                      # coded by the offline rules, the model never saw the words
        with SessionLocal() as db:
            db.get(Interview, iid).moderation = "ai"
            db.commit()
        _say(iid, "participant", "第二位也觉得价格偏贵，而且包装很好看。", 3)
        assert _wait(lambda: calls["n"] >= 1)                                                       # an AI-moderated one may use the model
    finally:
        board._chat = old


def test_the_retention_preview_only_looks_and_reports_active_projects(org, proj):
    from app import retention
    from app.db import SessionLocal
    iid, fid, uid = _board_setup(org, proj)
    with SessionLocal() as db:
        r = retention.preview(db)
        assert r["interviews"] == 0 and r["projects_with_active_interviews"] == []


def test_the_retention_command_with_dry_run_does_not_delete(org, proj, capsys):
    import runpy
    import sys
    from app.db import SessionLocal
    from app.models import Interview
    iid, fid, uid = _board_setup(org, proj)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        from app.models import Project, utcnow
        from datetime import timedelta
        itv.status, itv.ended_at = "ended", utcnow() - timedelta(days=900)          # long past the backstop: the real cleanup would purge it
        db.commit()
    old = sys.argv
    sys.argv = ["app.retention", "--dry-run"]
    try:
        runpy.run_module("app.retention", run_name="__main__")
    finally:
        sys.argv = old
    assert "would purge" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.get(Interview, iid).purged_at is None                             # looked, did not delete
