"""修正方案第 3 轮（生成大纲）: material files uploaded into a 概念卡 / 视频片段 (checked by their bytes, kept in order, rotated or in order, opened only through a
signed address, removed with the project's data), 已经知道的 is never said to change when it did not, and the preview offers only the interview's languages."""
import os

from test_outline_wb_api import _api, proj  # noqa: F401

from app.config import settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _concept(org, proj):
    v = _api(org, proj, "post", "/overview").json()
    _api(org, proj, "post", "/overview/confirm")
    m = v["overview"]["modules"][0]["id"]
    v = _api(org, proj, "post", f"/modules/{m}/generate").json()
    uid = v["overview"]["modules"][0]["items"][0]["uid"]
    v = _api(org, proj, "post", "/items", json={"kind": "concept", "text": "请看这张概念卡。", "after_uid": uid}).json()
    return next(i for i in v["items"] if i["kind"] == "concept")["uid"]


def _up(org, proj, uid, data, name="a.png", mode="add"):
    return org.c.post(f"/api/projects/{proj}/outline-wb/items/{uid}/media", headers=org.h(), files={"file": (name, data)}, data={"mode": mode})


def test_material_is_uploaded_ordered_and_served_only_by_a_signed_address(org, proj):
    uid = _concept(org, proj)
    assert _up(org, proj, uid, MP4, "x.mp4").status_code == 415          # a 概念卡 takes pictures and PDF, not video
    assert _up(org, proj, uid, b"<svg></svg>", "x.png").status_code == 415  # the bytes decide, not the name
    r = _up(org, proj, uid, PNG, "一.png")
    assert r.status_code == 200, r.text
    v = _up(org, proj, uid, PNG, "二.png").json()
    it = next(i for i in v["items"] if i["uid"] == uid)
    assert [m["name"] for m in it["media"]] == ["一.png", "二.png"]
    url = it["media"][0]["url"]
    got = org.c.get(url)
    assert got.status_code == 200 and got.content == PNG
    assert org.c.get(url.replace("s=", "s=0")).status_code == 404       # a changed signature opens nothing
    ids = [m["id"] for m in it["media"]]
    v = _api(org, proj, "put", f"/items/{uid}/media", json={"ids": ids[::-1], "order": "random"}).json()
    it = next(i for i in v["items"] if i["uid"] == uid)
    assert [m["name"] for m in it["media"]] == ["二.png", "一.png"] and it["config"]["order"] == "random"
    v = _up(org, proj, uid, PNG, "三.png", mode="replace").json()
    it = next(i for i in v["items"] if i["uid"] == uid)
    assert [m["name"] for m in it["media"]] == ["三.png"]
    folder = os.path.join(settings.media_dir, "outline", proj)
    assert len(os.listdir(folder)) == 1                                 # the replaced files are gone from the disk too
    pv = _api(org, proj, "get", "/preview").json()
    assert next(i for i in pv["items"] if i["uid"] == uid)["media"][0]["name"] == "三.png"
    assert pv["languages"][0] == pv["language"] and "ms" not in pv["languages"] and "ta" not in pv["languages"]   # only the interview's own languages


def test_the_dialog_does_not_claim_a_change_it_did_not_make(org, proj):
    r = _api(org, proj, "post", "/chat", json={"text": "你好", "step": "people"}).json()
    assert r["changed"] == [] and "没有改动" in r["reply"]


def test_the_dialog_is_kept_on_the_server_for_each_person(org, proj):
    """用户 2026-11-20: 存服务器，不可以换电脑丢失 — and nobody reads another person's dialog."""
    msgs = [{"me": True, "text": "物料改为看素材"}, {"me": False, "text": "已经知道的没有改动。", "revs": [{"id": "r1"}]}]
    assert _api(org, proj, "put", "/chat-log", json={"messages": msgs}).json()["kept"] == 2
    got = _api(org, proj, "get", "/chat-log").json()["messages"]
    assert [m["text"] for m in got] == ["物料改为看素材", "已经知道的没有改动。"] and got[1]["revs"] == [{"id": "r1"}]
    assert _api(org, proj, "get", "/chat-log", role="admin").json()["messages"] == []


def test_the_dialog_changes_the_length(org, proj):
    """修正方案第 4 轮: “访谈时长改为30分钟” in the dialog changes 已经知道的 (it used to say nothing changed)."""
    r = _api(org, proj, "post", "/chat", json={"text": "访谈时长改为30分钟", "step": "people"}).json()
    assert r["changed"] == ["minutes"] and "30" in r["reply"]
    assert _api(org, proj, "get", "").json()["slots"]["minutes"]["value"] == 30


def test_the_warm_up_opens_with_an_introduction_and_the_closing_thanks(org, proj):
    """修正方案第 4 轮: 暖场 starts with a spoken opening, 收尾 ends with thanks (both editable notes)."""
    v = _api(org, proj, "post", "/overview").json()
    _api(org, proj, "post", "/overview/confirm")
    mods = {m["kind"]: m["id"] for m in v["overview"]["modules"]}
    w = next(m for m in _api(org, proj, "post", f"/modules/{mods['warm']}/generate").json()["overview"]["modules"] if m["id"] == mods["warm"])
    assert w["items"][0]["kind"] == "note" and "感谢" in w["items"][0]["text"] and "没有对错" in w["items"][0]["text"]
    c = next(m for m in _api(org, proj, "post", f"/modules/{mods['close']}/generate").json()["overview"]["modules"] if m["id"] == mods["close"])
    assert c["items"][-1]["kind"] == "note" and "感谢" in c["items"][-1]["text"]
