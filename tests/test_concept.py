"""Concept test: the script and materials, what is on the participant's screen, live captions (and the signed speech-recognition address),
quotes and follow-up questions. Permissions, tenant isolation, consent, and that quotes are copied by the server."""
import base64
import hashlib
import hmac
from urllib.parse import parse_qsl, quote, urlsplit

import pytest

from app import asr_live, concept
from app.config import settings
from app.db import SessionLocal
from app.models import CaptionLine, Highlight, Interview
from app.retention import purge_interview
from conftest import H, Org

TEMPLATE = "loreal_skincare_concept"


def _live_room(org: Org, **kw) -> dict:
    """A human-moderated interview that is live, whose participant consented to recording, was let in and entered."""
    itv = org.interview(**kw)
    t = itv["invite_token"]
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": True, "speaker": True})
    r = org.c.post(f"/api/public/join/{t}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": True})
    assert r.status_code == 201, r.text
    org.c.post(f"/api/interviews/{itv['id']}/start", headers=org.h("moderator"))
    org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True})
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200
    return itv


def _install(org: Org, itv: dict, role: str = "researcher"):
    return org.c.post(f"/api/interviews/{itv['id']}/concept/template", headers=org.h(role), json={"template": TEMPLATE})


def _stimuli(org: Org, itv: dict) -> dict:
    return {s["key"]: s for s in org.c.get(f"/api/interviews/{itv['id']}/concept", headers=org.h("moderator")).json()["stimuli"]}


def _caption(org: Org, itv: dict, text: str, final: bool = True, who: str = "moderator"):
    if who == "participant":
        return org.c.post(f"/api/public/join/{itv['invite_token']}/captions", json={"text": text, "final": final})
    return org.c.post(f"/api/interviews/{itv['id']}/captions", headers=org.h("moderator"), json={"text": text, "final": final})


def _lines(org: Org, itv: dict, after: int = 0, role: str = "moderator") -> dict:
    return org.c.get(f"/api/interviews/{itv['id']}/captions?after={after}", headers=org.h(role)).json()


# ---------- the script and the materials ----------
def test_only_staff_install_a_template_and_the_script_has_ten_minutes(org):
    itv = org.interview()
    assert _install(org, itv, "moderator").status_code == 403
    assert _install(org, itv, "observer").status_code == 403
    r = _install(org, itv)
    assert r.status_code == 200
    plan = r.json()
    assert plan["template"] == TEMPLATE and sum(s["minutes"] for s in plan["script"]["steps"]) == 10
    assert {s["key"] for s in plan["stimuli"]} == {"ad_film", "concept_a", "concept_b", "ad_film_2"}
    assert org.c.post(f"/api/interviews/{itv['id']}/concept/template", headers=org.h(), json={"template": "nope"}).status_code == 422


def test_installing_twice_replaces_instead_of_duplicating(org):
    itv = org.interview()
    _install(org, itv)
    _install(org, itv)
    assert len(org.c.get(f"/api/interviews/{itv['id']}/concept", headers=org.h("moderator")).json()["stimuli"]) == 4


def test_the_script_is_for_the_team_not_for_observers_or_other_companies(org, client):
    itv = org.interview()
    _install(org, itv)
    assert org.c.get(f"/api/interviews/{itv['id']}/concept", headers=org.h("observer")).status_code == 403
    other = Org(client, "Other", "o@other.com")
    assert other.c.get(f"/api/interviews/{itv['id']}/concept", headers=other.h()).status_code == 404
    assert other.c.post(f"/api/interviews/{itv['id']}/concept/template", headers=other.h(), json={"template": TEMPLATE}).status_code == 404


def test_every_material_address_is_from_the_reviewed_list():
    for s in concept.LOREAL_SKINCARE["stimuli"]:
        assert s["url"].startswith("https://player.bilibili.com/player.html?bvid=BV") or s["url"].startswith("/concepts/"), s
        assert s["source_note"]


# ---------- what is on the participant's screen ----------
def test_the_moderator_shows_a_material_and_only_people_in_the_room_see_it(org):
    itv = _live_room(org)
    _install(org, itv)
    s = _stimuli(org, itv)
    assert org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("observer"), json={"stimulus_id": s["concept_a"]["id"]}).status_code == 403
    r = org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": s["concept_a"]["id"]})
    assert r.status_code == 200 and r.json()["stimulus"]["key"] == "concept_a"
    seen = org.c.get(f"/api/public/join/{itv['invite_token']}/live").json()["stimulus"]
    assert seen == {"id": s["concept_a"]["id"], "kind": "image", "url": "/concepts/concept_a.svg"}  # no title, no source note: the title could name the brand
    team = org.c.get(f"/api/interviews/{itv['id']}/live", headers=org.h("observer")).json()
    assert team["stimulus"]["key"] == "concept_a" and team["elapsed_ms"] is not None
    org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": None})
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/live").json()["stimulus"] is None


def test_a_participant_who_is_not_let_in_sees_no_material(org):
    itv = _live_room(org)
    _install(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": _stimuli(org, itv)["ad_film"]["id"]})
    org.c.post(f"/api/public/join/{itv['invite_token']}/leave")  # leaving withdraws the admission
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/live").json()["stimulus"] is None


def test_showing_needs_a_live_interview_and_a_material_of_this_interview(org):
    waiting = org.interview()
    _install(org, waiting)
    sid = _stimuli(org, waiting)["concept_a"]["id"]
    assert org.c.post(f"/api/interviews/{waiting['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": sid}).status_code == 409
    live = _live_room(org)
    assert org.c.post(f"/api/interviews/{live['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": sid}).status_code == 404  # another interview's material


# ---------- live captions ----------
def test_captions_need_a_live_interview_a_consenting_participant_who_was_let_in(org):
    itv = org.interview()
    t = itv["invite_token"]
    assert _caption(org, itv, "还没开始").status_code == 409
    assert _caption(org, itv, "还没开始", who="participant").status_code == 409
    org.c.post(f"/api/interviews/{itv['id']}/start", headers=org.h("moderator"))  # live and admitted, but the participant never consented
    org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True})
    assert _caption(org, itv, "我不同意录制", who="participant").status_code == 409  # no consent to recording: nothing is captured
    assert _caption(org, itv, "主持人的话").status_code == 204


def test_finished_sentences_are_stored_in_order_and_the_sentence_in_progress_is_not(org):
    itv = _live_room(org)
    assert _caption(org, itv, "你平时怎么挑精华？", who="moderator").status_code == 204
    assert _caption(org, itv, "我先看", final=False, who="participant").status_code == 204
    cur = _lines(org, itv)
    assert [ln["text"] for ln in cur["lines"]] == ["你平时怎么挑精华？"] and cur["partial"] == {"participant": "我先看"}
    _caption(org, itv, "我先看成分，再看价格。", who="participant")
    after = _lines(org, itv)
    assert [(ln["seq"], ln["speaker"]) for ln in after["lines"]] == [(1, "moderator"), (2, "participant")] and after["partial"] == {}
    assert _lines(org, itv, after=1)["lines"][0]["text"] == "我先看成分，再看价格。"
    assert all(ln["at_ms"] >= 0 for ln in after["lines"])
    with SessionLocal() as db:
        assert db.query(CaptionLine).count() == 2


def test_blank_text_is_ignored_and_long_text_is_refused(org):
    itv = _live_room(org)
    assert _caption(org, itv, "   ").status_code == 204
    assert _lines(org, itv)["lines"] == []
    assert _caption(org, itv, "字" * 601).status_code == 422


def test_observers_read_captions_but_cannot_write_them_and_participants_cannot_read_them(org):
    itv = _live_room(org)
    _caption(org, itv, "大家好")
    assert _lines(org, itv, role="observer")["lines"][0]["text"] == "大家好"
    assert org.c.post(f"/api/interviews/{itv['id']}/captions", headers=org.h("observer"), json={"text": "x"}).status_code == 403
    assert org.c.get(f"/api/interviews/{itv['id']}/captions").status_code == 401  # a participant's invitation does not open the team's caption feed


def test_captions_do_not_cross_companies(org, client):
    itv = _live_room(org)
    _caption(org, itv, "机密")
    other = Org(client, "Other", "o@other.com")
    assert other.c.get(f"/api/interviews/{itv['id']}/captions", headers=other.h()).status_code == 404


def test_the_line_cap_protects_the_database(org, monkeypatch):
    from app.routers import concept as router
    monkeypatch.setattr(router, "MAX_LINES_PER_INTERVIEW", 2)
    itv = _live_room(org)
    assert [_caption(org, itv, f"第{i}句").status_code for i in range(3)] == [204, 204, 429]


# ---------- the signed speech-recognition address ----------
@pytest.fixture
def tencent(monkeypatch):
    monkeypatch.setattr(settings, "tencent_asr_appid", "1250000000")
    monkeypatch.setattr(settings, "tencent_asr_secret_id", "AKIDexample")
    monkeypatch.setattr(settings, "tencent_asr_secret_key", "secret-key-example")
    monkeypatch.setattr(settings, "asr_live_ws_base", "wss://asr.cloud.tencent.com")


def test_captions_are_off_and_say_why_until_tencent_is_configured(org):
    itv = _live_room(org)
    r = org.c.get(f"/api/interviews/{itv['id']}/asr", headers=org.h("moderator")).json()
    assert r["enabled"] is False and "未开启" in r["reason"] and "url" not in r


def test_the_signed_address_follows_the_documented_algorithm(org, tencent):
    itv = _live_room(org, language="zh")
    r = org.c.get(f"/api/interviews/{itv['id']}/asr", headers=org.h("moderator")).json()
    assert r["enabled"] and r["sample_rate"] == 16000 and r["frame_ms"] == 200
    parts = urlsplit(r["url"])
    assert parts.scheme == "wss" and parts.netloc == "asr.cloud.tencent.com" and parts.path == "/asr/v2/1250000000"
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    sig = q.pop("signature")
    assert q["engine_model_type"] == "16k_zh" and q["voice_format"] == "1" and q["needvad"] == "1" and q["secretid"] == "AKIDexample"
    assert int(q["expired"]) > int(q["timestamp"]) and len(q["voice_id"]) <= 128 and 1 <= int(q["nonce"]) < 10**10
    raw = "asr.cloud.tencent.com/asr/v2/1250000000?" + "&".join(f"{k}={q[k]}" for k in sorted(q))  # the doc: sort by name, no scheme
    expect = quote(base64.b64encode(hmac.new(b"secret-key-example", raw.encode(), hashlib.sha1).digest()).decode(), safe="")
    assert quote(sig, safe="") == expect
    assert "secret-key-example" not in r["url"]  # the secret key never leaves the server


def test_every_connect_gets_its_own_voice_id():
    a = parse_qsl(urlsplit(_signed(settings)).query)
    b = parse_qsl(urlsplit(_signed(settings)).query)
    assert dict(a)["voice_id"] != dict(b)["voice_id"]


def _signed(s) -> str:
    s.tencent_asr_appid, s.tencent_asr_secret_id, s.tencent_asr_secret_key = "1", "id", "key"
    try:
        return asr_live.signed_url("zh")
    finally:
        s.tencent_asr_appid = s.tencent_asr_secret_id = s.tencent_asr_secret_key = ""


def test_malay_and_tamil_interviews_have_no_captions(org, tencent):
    itv = _live_room(org, language="ms")
    r = org.c.get(f"/api/interviews/{itv['id']}/asr", headers=org.h("moderator")).json()
    assert r["enabled"] is False and "语言" in r["reason"]


def test_only_the_team_and_an_admitted_consenting_participant_get_an_address(org, tencent):
    itv = _live_room(org, language="zh")
    assert org.c.get(f"/api/interviews/{itv['id']}/asr", headers=org.h("observer")).status_code == 403
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/asr").json()["enabled"] is True
    org.c.post(f"/api/public/join/{itv['invite_token']}/leave")
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/asr").json()["enabled"] is False
    assert org.c.get("/api/public/join/not-a-token/asr").status_code == 404


# ---------- quotes and follow-up questions ----------
def _highlight(org: Org, itv: dict, role: str = "observer", **body):
    return org.c.post(f"/api/interviews/{itv['id']}/highlights", headers=org.h(role), json=body)


def test_a_quote_is_copied_from_the_caption_by_the_server(org):
    itv = _live_room(org)
    _caption(org, itv, "我最担心买到假货。", who="participant")
    line = _lines(org, itv)["lines"][0]
    r = _highlight(org, itv, kind="quote", caption_id=line["id"], text="我被篡改了")
    assert r.status_code == 201
    h = r.json()
    assert h["text"] == "我最担心买到假货。" and h["speaker"] == "participant" and h["at_ms"] == line["at_ms"]  # the browser's text is ignored


def test_a_quote_must_come_from_a_caption_but_a_follow_up_may_be_typed(org):
    itv = _live_room(org)
    assert _highlight(org, itv, kind="quote", text="凭空捏造").status_code == 422
    assert _highlight(org, itv, kind="quote").status_code == 422
    assert _highlight(org, itv, kind="probe", text="问问退货顾虑").status_code == 201
    assert _highlight(org, itv, kind="probe").status_code == 422


def test_a_highlight_remembers_the_material_that_was_on_screen(org):
    itv = _live_room(org)
    _install(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": _stimuli(org, itv)["concept_b"]["id"]})
    assert _highlight(org, itv, kind="probe", text="B 和 A 比呢？").json()["stimulus_key"] == "concept_b"


def test_captions_of_another_interview_or_company_cannot_be_quoted(org, client):
    a, b = _live_room(org), _live_room(org)
    _caption(org, a, "只在 A 里")
    cap = _lines(org, a)["lines"][0]["id"]
    assert _highlight(org, b, kind="quote", caption_id=cap).status_code == 404
    other = Org(client, "Other", "o@other.com")
    assert other.c.post(f"/api/interviews/{a['id']}/highlights", headers=other.h(), json={"kind": "probe", "text": "x"}).status_code == 404
    assert _highlight(org, {"id": "does-not-exist"}, kind="probe", text="x").status_code == 404


def test_the_moderator_works_through_follow_ups_and_observers_cannot(org):
    itv = _live_room(org)
    hid = _highlight(org, itv, kind="probe", text="追问价格").json()["id"]
    assert [h["status"] for h in org.c.get(f"/api/interviews/{itv['id']}/highlights?kind=probe", headers=org.h("moderator")).json()] == ["open"]
    assert org.c.patch(f"/api/highlights/{hid}", headers=org.h("observer"), json={"status": "done"}).status_code == 403
    assert org.c.patch(f"/api/highlights/{hid}", headers=org.h("moderator"), json={"status": "done"}).json()["status"] == "done"


def test_an_observer_deletes_only_their_own_marks(org):
    itv = _live_room(org)
    mine = _highlight(org, itv, kind="probe", text="我的").json()["id"]
    theirs = _highlight(org, itv, role="moderator", kind="probe", text="主持人的").json()["id"]
    assert org.c.delete(f"/api/highlights/{theirs}", headers=org.h("observer")).status_code == 403
    assert org.c.delete(f"/api/highlights/{mine}", headers=org.h("observer")).status_code == 204
    assert org.c.delete(f"/api/highlights/{theirs}", headers=org.h("moderator")).status_code == 204


# ---------- retention ----------
def test_captions_and_quotes_are_deleted_with_the_interview(org):
    itv = _live_room(org)
    _caption(org, itv, "个人数据")
    _highlight(org, itv, kind="quote", caption_id=_lines(org, itv)["lines"][0]["id"])
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.query(CaptionLine).count() == 0 and db.query(Highlight).count() == 0
    assert _caption(org, itv, "删除后").status_code == 404
