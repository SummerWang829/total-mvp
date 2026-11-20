"""修正方案第 1 轮第 3 批: 已经知道的 shown in Chinese and each one editable (what was made earlier is flagged "需要重新生成", never overwritten);
重新开始 (only my answers / everything, refused on a version that was used or is in a pilot) with 撤销清除 within 10 minutes; the focus questions' "对应到" with several questions,
新增想法 (a pending new question) and 暂不对应."""
from datetime import timedelta

from test_outline_wb_api import _api, _ready, proj  # noqa: F401 - the shared fixture and helpers

from app.db import SessionLocal
from app.outline_wb import flow
from app.outline_wb.models import StudySetting

CODES = {"usage", "journey", "none", "skip", "after_concept", "first", "concept", "video", "website", "image"}


def test_known_values_read_in_chinese_never_as_codes(org, proj):
    _api(org, proj, "post", "/answer", json={"slot": "materials", "value": ["none"]})
    _api(org, proj, "post", "/answer", json={"slot": "price", "value": "skip"})
    v = _api(org, proj, "get", "").json()
    for k, s in v["slots"].items():
        assert s["text"] and not (set(s["text"].replace("、", " ").split()) & CODES), (k, s["text"])
    assert v["slots"]["materials"]["text"] == "不看素材" and v["slots"]["price"]["text"] == "不谈价格"
    assert v["slots"]["research_type"]["edit"] == "choice" and {"value": "journey", "text": "购买旅程"} in v["slots"]["research_type"]["options"]
    assert flow.slot_text("research_type", "made_up") == "其他"   # a code missing from the dictionary is never shown as itself


def test_changing_a_known_value_marks_what_was_made_and_keeps_it(org, proj):
    _ready(org, proj)
    before = _api(org, proj, "get", "").json()
    assert before["stale"] is False and before["items"]
    after = _api(org, proj, "post", "/answer", json={"slot": "research_type", "value": "usability"}).json()
    assert after["stale"] is True and [i["uid"] for i in after["items"]] == [i["uid"] for i in before["items"]]   # flagged, nothing overwritten
    assert _api(org, proj, "post", "/overview/keep").json()["stale"] is False
    bg = _api(org, proj, "put", "/background", json={"text": "换一个研究背景：看她们怎么比较价格"}).json()
    assert bg["context"]["background"].startswith("换一个研究背景") and bg["stale"] is True
    assert _api(org, proj, "post", "/overview").json()["stale"] is False   # made again from the new settings


def test_restart_answers_only_then_undo(org, proj):
    _ready(org, proj)
    _api(org, proj, "post", "/answer", json={"slot": "price", "value": "skip"})
    items = _api(org, proj, "get", "").json()["items"]
    v = _api(org, proj, "post", "/reset", json={"mode": "answers"}).json()
    assert v["gates"] == {} and v["slots"]["price"]["source"] != "answer" and len(v["items"]) == len(items)   # the questions stay
    assert 590 <= v["reset"]["undo_left_s"] <= 600 and v["reset"]["mode"] == "answers"
    u = _api(org, proj, "post", "/reset/undo").json()
    assert u["gates"].get("overview") and u["slots"]["price"]["value"] == "skip" and u["reset"]["undo_left_s"] == 0
    assert _api(org, proj, "post", "/reset/undo").status_code == 409


def test_restart_everything_clears_and_undo_puts_it_back(org, proj):
    _ready(org, proj)
    d = _api(org, proj, "get", "").json()
    uid = d["items"][0]["uid"]
    fid = _api(org, proj, "post", "/focus", json={"text": "她们怎么比较价格"}).json()["focus"][0]["id"]
    _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": [uid], "state": "linked"})
    rev = d["version"]["rev"]
    v = _api(org, proj, "post", "/reset", json={"mode": "all"}).json()
    assert not v["items"] and not v["overview"]["modules"] and v["focus"][0]["state"] == "" and v["version"]["number"] == d["version"]["number"]   # no new version
    u = _api(org, proj, "post", "/reset/undo").json()
    assert [i["uid"] for i in u["items"]] == [i["uid"] for i in d["items"]] and u["overview"]["modules"] and u["focus"][0]["item_uids"] == [uid] and u["version"]["rev"] > rev


def test_restart_everything_is_refused_on_a_version_in_use_and_undo_expires(org, proj):
    _ready(org, proj)
    part = org.participant(proj)
    _api(org, proj, "post", "/pilot-rounds", json={"participant_id": part["id"], "watcher_ids": [], "send_email": False})
    v = _api(org, proj, "get", "").json()
    assert v["reset"]["all_ok"] is False and "Pilot" in v["reset"]["why"]
    r = _api(org, proj, "post", "/reset", json={"mode": "all"})
    assert r.status_code == 409 and "只能清除你的回答" in r.json()["detail"]
    assert _api(org, proj, "post", "/reset", json={"mode": "answers"}).status_code == 200
    with SessionLocal() as db:
        st = db.query(StudySetting).one()
        st.reset_at -= timedelta(minutes=11)
        db.commit()
    assert _api(org, proj, "get", "").json()["reset"]["undo_left_s"] == 0
    r = _api(org, proj, "post", "/reset/undo")
    assert r.status_code == 409 and "10 分钟" in r.json()["detail"]


def test_focus_links_to_several_questions_a_new_idea_or_none(org, proj):
    _ready(org, proj, modules=("warm", "close"))
    d = _api(org, proj, "get", "").json()
    uids = [i["uid"] for i in d["items"]][:2]
    fid = _api(org, proj, "post", "/focus", json={"text": "用户平时喜欢哪些广告"}).json()["focus"][0]["id"]
    f = _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": uids, "state": "linked"}).json()["focus"][0]
    assert f["item_uids"] == uids and f["state"] == "linked" and len(f["labels"]) == 2
    f = _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": [], "state": "none"}).json()["focus"][0]
    assert f["state"] == "none" and not f["item_uids"]
    chk = {c["id"]: c for c in _api(org, proj, "get", "/publish/check").json()["checks"]}
    assert chk["focus"]["level"] == "ok" and chk["focus_none"]["level"] == "warn"   # 暂不对应 counts as handled, and is said
    out = _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": [], "state": "new_idea"}).json()
    f = out["focus"][0]
    assert f["state"] == "new_idea" and f["idea"]["text"] and len(out["items"]) == len(d["items"])   # pending: nothing written yet
    assert _api(org, proj, "get", "/publish/check").json()["checks"][-1]["level"] in ("warn", "ok")
    again = _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": [], "state": "new_idea"}).json()["focus"][0]
    assert again["idea"]["id"] == f["idea"]["id"]   # not drafted twice
    mine = _api(org, proj, "put", f"/focus/{fid}/idea", json={"text": "最近一次让你记住的广告是什么？"}).json()["focus"][0]
    assert mine["idea"]["text"] == "最近一次让你记住的广告是什么？" and mine["idea"]["id"] != f["idea"]["id"]
    acc = _api(org, proj, "post", f"/revisions/{mine['idea']['id']}/accept", json={}).json()
    f = acc["focus"][0]
    assert f["state"] == "linked" and f["idea"] is None and len(acc["items"]) == len(d["items"]) + 1 and f["item_uids"][-1] in {i["uid"] for i in acc["items"]}
    assert _api(org, proj, "put", f"/focus/{fid}/link", json={"item_uids": ["nope"], "state": "linked"}).status_code == 404
