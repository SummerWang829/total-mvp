"""Emotion signals: hints about how something was said (pace, pauses, volume, tone of the words) and emoji reactions. Hints only: always against the
speaker's own habits, for the team only, never a conclusion."""
import pytest
from sqlalchemy import select

from app import signals
from app.db import SessionLocal
from app.models import CaptionLine, Interview, Reaction
from app.retention import purge_interview
from app.routers import concept as C
from app.signals import constants as K
from conftest import Org
from test_concept import _live_room

STEADY = [signals.Past(rate=4.0, vol_db=-30.0, gap_ms=1500)] * 6   # a speaker who usually says 4 characters a second at -30 dB after a 1.5 s pause


# ---------- the tone of the words ----------
@pytest.mark.parametrize("text, sign", [
    ("这个精华我非常喜欢，很好用！", 1), ("I really love it", 1), ("质地很清爽，价格也划算", 1), ("今天天气不错", 1), ("it works great 👍", 1),
    ("不太满意，有点失望", -1), ("it is not great, kind of disappointing", -1), ("我不喜欢这个味道", -1), ("太贵了，不值", -1), ("I don't like the smell 😕", -1),
    ("not bad at all", 1), ("这个不难用", 1),
])
def test_the_tone_of_the_words_goes_the_right_way(text, sign):
    score, strength = signals.tone(text)
    assert score * sign > 0 and 0 < strength <= 1, (text, score)


@pytest.mark.parametrize("text", ["我平时先看成分再看价格", "I usually check the label first", "", "一共 128 块"])
def test_neutral_sentences_have_no_tone(text):
    assert signals.tone(text) == (0.0, 0.0)


def test_negation_flips_and_an_intensifier_strengthens():
    assert signals.tone("很喜欢")[0] > signals.tone("喜欢")[0]
    assert signals.tone("不喜欢")[0] < 0 < signals.tone("喜欢")[0]


def test_emoji_alone_carry_a_tone():
    assert signals.tone("😭")[0] < -0.5 and signals.tone("😍")[0] > 0.5 and signals.tone("🎉")[0] > 0


# ---------- pace, volume, pauses: only against the person's own habits ----------
def test_nothing_is_compared_until_there_are_enough_earlier_sentences():
    few = [signals.Past(rate=4.0, vol_db=-30.0, gap_ms=1500)] * (K.BASELINE_MIN_SENTENCES - 1)
    got = signals.analyze("这个我还得想一想吧不太确定怎么说", 8000, -45.0, 2000, few)
    assert "slow" not in got.tags and "quiet" not in got.tags


def test_a_much_slower_and_quieter_sentence_after_a_long_pause_is_tagged():
    got = signals.analyze("这个我还得想一想吧不太确定怎么说", 8000, -38.0, 7000, STEADY)   # 15 characters in 8 s = 1.9 per second
    assert set(got.tags) >= {"slow", "quiet", "hesitation"} and signals.is_notable(got.tags, got.senti)


def test_a_fast_loud_sentence_is_tagged_too():
    got = signals.analyze("对对对这个我特别清楚我每次都买这个牌子的", 3000, -22.0, 800, STEADY)   # 19 characters in 3 s
    assert "fast" in got.tags and "loud" in got.tags and "hesitation" not in got.tags


def test_a_sentence_like_the_speakers_usual_gets_no_tags():
    assert signals.analyze("我平时一般先看看成分表再决定买不买", 4200, -30.5, 1600, STEADY).tags == ()


def test_a_long_pause_counts_only_against_the_speakers_own_usual_pause():
    slowtalker = [signals.Past(rate=4.0, vol_db=-30.0, gap_ms=5000)] * 6   # this person always waits about five seconds
    assert "hesitation" not in signals.analyze("嗯这个嘛我觉得还可以吧", 3000, -30.0, 6000, slowtalker).tags
    assert "hesitation" in signals.analyze("嗯这个嘛我觉得还可以吧", 3000, -30.0, 14000, slowtalker).tags


def test_a_silence_that_long_is_not_hesitation_and_short_sentences_give_no_pace():
    assert "hesitation" not in signals.analyze("好的", 1000, None, 90_000, STEADY).tags   # a break or a technical problem
    assert signals.rate_of("好的", 800) is None and signals.rate_of("一二三", 5000) is None and signals.rate_of("一二三四五六七八", 2000) == 4.0
    assert signals.rate_of("one two three four five six", 3000) == 2.0


def test_a_strongly_negative_sentence_is_notable_without_any_voice_data():
    got = signals.analyze("我真的很失望，这个太糟糕了，完全不值", 5000, None, None, [])
    assert "negative" in got.tags and signals.is_notable(got.tags, got.senti)


def test_the_labels_are_plain_chinese_in_a_fixed_order():
    assert signals.labels(["quiet", "hesitation", "slow"]) == ["回答前停顿长", "语速偏慢", "声音偏轻"]


# ---------- stored with the caption, shown only to the operating team ----------
def _say(db, itv, speaker, text, dur_ms, vol_db=None):
    C._store_caption(db, itv, speaker, C.CaptionIn(text=text, final=True, dur_ms=dur_ms, vol_db=vol_db))


def test_a_spoken_sentence_is_stored_with_its_hints_and_only_the_team_sees_them(org):
    itv = _live_room(org)
    with SessionLocal() as db:
        row = db.get(Interview, itv["id"])
        for _ in range(6):
            _say(db, row, "participant", "我平时一般先看看成分表再决定买不买", 4200, -30.0)
        _say(db, row, "moderator", "那你对这个价格有什么看法呢", 2000, -25.0)
        _say(db, row, "participant", "这个我还得想一想吧不太确定怎么说失望", 12000, -41.0)
        last = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv["id"]).order_by(CaptionLine.seq.desc())).first()
    assert last.vol_db10 == -410 and last.rate_x10 is not None and last.rate_x10 < 30 and last.tags.split(",")[0] in {"slow", "quiet", "negative"}
    assert {"slow", "quiet"} <= set(last.tags.split(","))
    got = org.c.get(f"/api/interviews/{itv['id']}/captions?after=0", headers=org.h("researcher")).json()["lines"]
    assert got[-1]["hints"] and "语速偏慢" in got[-1]["hints"]
    seen = org.c.get(f"/api/interviews/{itv['id']}/captions?after=0", headers=org.h("observer")).json()["lines"]
    assert all("hints" not in c for c in seen)   # hidden client observers get the captions, not the hints


def test_the_pause_before_an_answer_is_measured_from_the_other_speakers_last_sentence(org):
    itv = _live_room(org)
    with SessionLocal() as db:
        row = db.get(Interview, itv["id"])
        db.add(CaptionLine(org_id=row.org_id, interview_id=row.id, seq=1, speaker="moderator", text="你觉得怎么样", at_ms=0, dur_ms=1000))
        db.commit()
        _say(db, row, "participant", "我觉得还可以吧", 1500)
        got = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == row.id, CaptionLine.speaker == "participant")).one()
    assert got.gap_ms is not None and got.gap_ms >= 0   # (the clock is the test's own: a small number is fine, it just must exist)


def test_the_volume_field_must_be_a_plausible_level(org):
    itv = _live_room(org)
    body = {"text": "hello there everyone", "final": True, "dur_ms": 2000}
    assert org.c.post(f"/api/interviews/{itv['id']}/captions", headers=org.h("moderator"), json={**body, "vol_db": -35.5}).status_code == 204
    assert org.c.post(f"/api/interviews/{itv['id']}/captions", headers=org.h("moderator"), json={**body, "vol_db": 12}).status_code == 422
    assert org.c.post(f"/api/interviews/{itv['id']}/captions", headers=org.h("moderator"), json={**body, "vol_db": -400}).status_code == 422


# ---------- emoji reactions ----------
def _react(org, itv, emoji="😕"):
    return org.c.post(f"/api/public/join/{itv['invite_token']}/reaction", json={"emoji": emoji})


def test_a_participant_sends_an_emoji_and_the_whole_team_sees_it_with_the_moment_and_the_material(org):
    itv = _live_room(org)
    org.c.post(f"/api/interviews/{itv['id']}/concept/template", headers=org.h(), json={"template": "loreal_skincare_concept"})
    stims = {s["key"]: s["id"] for s in org.c.get(f"/api/interviews/{itv['id']}/concept", headers=org.h()).json()["stimuli"]}
    org.c.post(f"/api/interviews/{itv['id']}/stimulus/show", headers=org.h("moderator"), json={"stimulus_id": stims["concept_a"]})
    assert _react(org, itv, "😕").status_code == 201
    for role in ("moderator", "researcher", "observer"):
        got = org.c.get(f"/api/interviews/{itv['id']}/reactions", headers=org.h(role)).json()["reactions"]
        assert len(got) == 1 and got[0]["emoji"] == "😕" and got[0]["glyph"] == "😕" and got[0]["label"] and got[0]["who"] == "受访者" and got[0]["stimulus"] == "concept_a"
    assert org.c.get(f"/api/interviews/{itv['id']}/reactions?after=1", headers=org.h("moderator")).json()["reactions"] == []


def test_only_a_participant_who_is_in_the_room_can_react_and_only_with_a_real_emoji(org, client):
    waiting = org.interview()
    assert _react(org, waiting).status_code == 409   # not live / not let in / no consent: nothing is recorded
    itv = _live_room(org)
    assert _react(org, itv, "party").status_code == 422 and _react(org, itv, "").status_code == 422
    assert org.c.post(f"/api/public/join/nonsense/reaction", json={"emoji": "👍"}).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}/reactions").status_code == 401


def test_a_participant_cannot_flood_the_feed(org):
    itv = _live_room(org)
    assert _react(org, itv, "👍").status_code == 201
    assert _react(org, itv, "❤️").status_code == 429   # less than a second later


def test_the_overview_for_the_team_has_tone_hints_moments_and_reactions_per_material(org):
    itv = _live_room(org)
    with SessionLocal() as db:
        row = db.get(Interview, itv["id"])
        for _ in range(6):
            _say(db, row, "participant", "我平时一般先看看成分表再决定买不买", 4200, -30.0)
        _say(db, row, "participant", "我真的很失望，这个太糟糕了，完全不值", 5000, -31.0)
    _react(org, itv, "👎")
    r = org.c.get(f"/api/interviews/{itv['id']}/signals", headers=org.h("researcher"))
    assert r.status_code == 200, r.text
    d = r.json()
    me = d["speakers"][0]
    assert me["who"] == "受访者" and me["sentences"] == 7 and me["tags"].get("negative") == 1 and me["reactions"] == {"👎": 1}
    assert d["moments"] and d["moments"][-1]["hints"] == ["偏负面"] and "仅供参考" in d["note"]
    assert org.c.get(f"/api/interviews/{itv['id']}/signals", headers=org.h("observer")).status_code == 403   # hints are not for client observers


def test_other_companies_cannot_read_them_and_the_data_goes_with_the_interview(org, client):
    itv = _live_room(org)
    _react(org, itv)
    other = Org(client, "Other", "b@other.com")
    assert other.c.get(f"/api/interviews/{itv['id']}/signals", headers=other.h()).status_code == 404
    assert other.c.get(f"/api/interviews/{itv['id']}/reactions", headers=other.h()).status_code == 404
    with SessionLocal() as db:
        assert db.query(Reaction).count() == 1
        purge_interview(db, db.get(Interview, itv["id"]), "test", "tester")
        db.commit()
        assert db.query(Reaction).count() == 0


# ---------- any real emoji, like a phone keyboard; nothing else ----------
from app.signals import emoji as E   # noqa: E402


@pytest.mark.parametrize("glyph", ["😀", "😕", "👍", "👍🏽", "❤️", "❤", "🥹", "🙏", "🇸🇬", "👨‍👩‍👧‍👦", "🧋", "🧴", "💄", "🫶", "😮‍💨"])
def test_real_emoji_are_accepted_with_or_without_the_emoji_marker_and_skin_tone(glyph):
    assert E.is_valid(glyph) and E.label_zh(glyph)


@pytest.mark.parametrize("text", ["", "abc", "👍 great", "😀😀", "like", "<b>", "1", "😀" * 40, "🖕", "🖕🏽", "🍆", "🍑", "💦", "🔞"])
def test_free_text_several_emoji_and_the_insulting_ones_are_refused(text):
    assert not E.is_valid(text)


def test_the_tone_of_an_emoji_comes_from_its_kind():
    assert E.tone("😡") < -0.7 and E.tone("😢") < -0.4 and E.tone("😍") > 0.8 and E.tone("🥰") > 0.5 and E.tone("👍") > 0.5 and E.tone("👎") < -0.5
    assert E.tone("🧴") == 0.0 and E.tone("🇸🇬") == 0.0 and E.tone("😐") <= 0.0   # objects and flags carry no tone
    assert signals.tone("好用 🥰")[0] > signals.tone("好用")[0] and signals.tone("太贵了 😤")[0] <= signals.tone("太贵了")[0] < 0


def test_any_emoji_typed_in_a_message_counts_for_the_tone_not_only_the_quick_ones():
    assert signals.tone("😠")[0] < -0.5 and signals.tone("🤩")[0] > 0.3 and signals.tone("🧴")[0] == 0.0


def test_a_participant_can_send_an_emoji_that_is_not_in_the_quick_row(org):
    itv = _live_room(org)
    assert _react(org, itv, "🧋").status_code == 201
    got = org.c.get(f"/api/interviews/{itv['id']}/reactions", headers=org.h("moderator")).json()["reactions"][0]
    assert got["glyph"] == "🧋" and got["label"]
    assert _react(org, itv, "🖕").status_code == 422


# ---------- the consent says it ----------
@pytest.mark.parametrize("lang", ["en", "zh", "ms", "ta"])
def test_the_consent_tells_every_participant_that_pace_pauses_volume_and_emoji_are_noted(lang):
    from app.consent import SIGNALS_TEXTS, consent_for

    for ai in (False, True):
        c = consent_for(lang, "Acme", ai=ai)
        assert SIGNALS_TEXTS[lang] in c["text"] and c["version"] == "2026-10-draft-6"
