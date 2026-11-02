"""The participant's phone screen: the conversation so far (words of both speakers only) and typed answers that become the participant's own lines."""
from conftest import Org
from test_concept import _caption, _live_room, _live_room_not_entered


def _read(org: Org, itv: dict, after: int = 0):
    return org.c.get(f"/api/public/join/{itv['invite_token']}/captions?after={after}")


def test_participant_reads_both_speakers_words_only(org):
    itv = _live_room(org)
    _caption(org, itv, "请先介绍一下你平时怎么选护肤品。")
    _caption(org, itv, "我先看成分，再看价格。", who="participant")
    r = _read(org, itv)
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert [(x["speaker"], x["text"]) for x in lines] == [("moderator", "请先介绍一下你平时怎么选护肤品。"), ("participant", "我先看成分，再看价格。")]
    assert all(set(x) == {"seq", "speaker", "text", "at_ms"} for x in lines)   # no translation, no hints, no flags
    assert _read(org, itv, after=lines[0]["seq"]).json()["lines"][0]["text"] == "我先看成分，再看价格。"


def test_participant_typed_answer_is_one_of_their_lines(org):
    itv = _live_room(org)
    assert _caption(org, itv, "因为我皮肤比较敏感。", who="participant").status_code == 204   # what the phone input sends
    mine = [x for x in _read(org, itv).json()["lines"] if x["speaker"] == "participant"]
    assert mine and mine[0]["text"] == "因为我皮肤比较敏感。"
    staff = org.c.get(f"/api/interviews/{itv['id']}/captions", headers=org.h("moderator")).json()["lines"]
    assert any(x["speaker"] == "participant" and x["text"] == "因为我皮肤比较敏感。" for x in staff)   # the moderator sees it with the spoken ones


def test_captions_for_the_participant_need_the_room(org):
    itv = _live_room_not_entered(org)
    assert _read(org, itv).status_code == 409   # consented and live, but not in the room yet
    assert org.c.get("/api/public/join/not-a-token/captions").status_code == 404


def test_enter_says_the_session_is_one_to_one(org):
    itv = _live_room(org)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").json()["one_to_one"] is True
