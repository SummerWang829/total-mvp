"""Cue cards for the moderator of a focus group: the five rules, the limits that keep them from becoming noise, and who may see them."""
import json
from datetime import timedelta

import pytest

from app.cues import constants as K
from app.cues import rules, service
from app.cues.rules import Candidate, Line, Snapshot, candidates
from app.cues.schedule import Existing, choose, expired, noisy
from app.db import SessionLocal
from app.group_metrics.compute import Sentence, compute
from app.models import CaptionLine, CueCard, Interview, InterviewPlan, RoomState, Stimulus, utcnow
from conftest import Org
from test_groups import _group

LONG = "我觉得这个精华的质地很清爽不黏腻用了之后皮肤很舒服"
STEPS = [{"title": "开场", "minutes": 1, "questions": ["平时的护肤步骤是怎样的？"], "must_cover": ["护肤习惯"], "probes": [], "show": None},
         {"title": "概念 A 精华", "minutes": 2, "questions": ["这个概念价格合理吗？"], "must_cover": ["价格感知"], "probes": [], "show": "concept_a"},
         {"title": "收尾", "minutes": 2, "questions": ["会买吗？"], "must_cover": ["购买意向"], "probes": [], "show": None}]


def sent(speaker, end_s, dur_s=5, text=LONG):
    return Sentence(speaker, text, int(end_s * 1000), int(dur_s * 1000))


def snap(sentences, now_s, seats=(1, 2, 3), steps=None, profiles=None, position=None):
    lines = [Line(i + 1, s) for i, s in enumerate(sentences)]
    return Snapshot(int(now_s * 1000), compute(sentences, list(seats), int(now_s * 1000)), lines, steps, profiles or {}, position)


def kinds(cands):
    return [c.kind for c in cands]


# ---------- the five rules ----------
@pytest.mark.parametrize("said, what", [("我去年确诊了抑郁，吃了很久的药", "健康"), ("我现在欠着很多贷款，压力很大", "财务"),
                                        ("你可以打我电话 9123 4567", "联系方式"), ("my email is lin@example.com if you need it", "联系方式"),
                                        ("I was diagnosed with eczema as a child", "健康")])
def test_something_very_personal_in_the_last_minute_makes_a_compliance_card(said, what):
    c = rules.compliance(snap([sent("p2", 100, 5, said)], 110))
    assert len(c) == 1 and c[0].kind is K.Kind.COMPLIANCE and what in c[0].text and "参与者 ·02" in c[0].text and c[0].evidence == (1,)


def test_everyday_skincare_talk_is_not_a_compliance_card():
    for said in ("我对香精过敏，用了就起红疹", "激素脸的事我听说过", "价格上我的收入有限，所以更看重性价比"):
        assert rules.compliance(snap([sent("p1", 100, 5, said)], 110)) == []


def test_old_sentences_and_the_moderators_own_words_are_not_flagged():
    assert rules.compliance(snap([sent("p1", 10, 5, "我确诊了抑郁")], 200)) == []   # 190 s ago
    assert rules.compliance(snap([sent("moderator", 100, 5, "你有没有确诊过什么病")], 110)) == []


def test_someone_with_far_more_than_their_share_makes_a_control_card_naming_the_two_who_spoke_least():
    s = [sent("p1", 100, 90), sent("p1", 200, 90), sent("p2", 230, 10), sent("p3", 250, 5)]
    c = rules.dominant(snap(s, 260))
    assert len(c) == 1 and "参与者 ·01" in c[0].text and "参与者 ·03" in c[0].text and "参与者 ·02" in c[0].text and c[0].seat == 1 and c[0].ask
    assert rules.dominant(snap([sent("p1", 100, 30), sent("p2", 130, 30), sent("p3", 160, 30)], 170)) == []


def test_a_long_silence_makes_a_card_and_a_matching_screening_answer_is_quoted():
    s = [sent("p1", 100, 60), sent("p2", 200, 60, "我平时用的精华里香精味道不能太重，不然会不舒服")]
    profiles = {3: ["对香精的态度 → 尽量避开，闻着太重会不舒服", "肤质 → 混合偏油"]}
    c = rules.silent(snap(s, 400, profiles=profiles))
    assert len(c) == 1 and c[0].seat == 3 and "参与者 ·03" in c[0].text and "还没发过言" in c[0].text
    assert "问卷里写过" in c[0].text and "香精" in c[0].text


def test_without_a_matching_screening_answer_the_card_does_not_invent_one():
    s = [sent("p1", 100, 60), sent("p2", 200, 60, "价格我觉得偏贵不过可以接受")]
    c = rules.silent(snap(s, 400, profiles={3: ["肤质 → 混合偏油"]}))
    assert len(c) == 1 and "问卷" not in c[0].text


def test_the_section_that_ran_long_makes_a_time_card_using_the_moderators_real_position():
    inside = 4 * 60_000  # concept A (planned 2 min) has been on screen for 4 minutes
    c = rules.time_check(snap([], 400, steps=STEPS, position=(1, inside)))
    assert len(c) == 1 and "概念 A 精华" in c[0].text and "收尾" in c[0].text and c[0].dedupe_key == "time:1"
    assert rules.time_check(snap([], 400, steps=STEPS, position=(1, 2.2 * 60_000))) == []   # not over by enough yet
    assert rules.time_check(snap([], 30, steps=STEPS)) == []
    assert rules.time_check(snap([], 30, steps=None)) == []   # no script: no time card


def test_the_last_section_can_run_over_even_without_a_material_on_screen():
    c = rules.time_check(snap([], 8 * 60, steps=STEPS))   # clock position: the last section, 3 minutes past the planned 5 total
    assert len(c) == 1 and "收尾" in c[0].text and "建议收尾" in c[0].text


def test_three_informative_sentences_that_miss_the_section_make_an_off_topic_card():
    elsewhere = ["上个月我去了一趟泰国旅行，拍了很多照片回来", "我的猫最近生病了，一直在看兽医，花了不少钱", "周末我打算和朋友一起去爬山，天气应该不错"]
    s = [sent("p1", 100 + 10 * i, 5, t) for i, t in enumerate(elsewhere)]
    c = rules.offtopic(snap(s, 140, steps=STEPS, position=(1, 30_000)))
    assert len(c) == 1 and "概念 A 精华" in c[0].text and c[0].ask == "这个概念价格合理吗？" and len(c[0].evidence) == 2
    on_topic = s[:2] + [sent("p1", 130, 5, "这个概念的价格我觉得合理，但不确定它值不值这个价")]
    assert rules.offtopic(snap(on_topic, 140, steps=STEPS, position=(1, 30_000))) == []
    assert rules.offtopic(snap(s, 140, steps=None)) == []


def _said(seq, speaker, end_s, text, tags=(), senti=0.0):
    return Line(seq, sent(speaker, end_s, 5, text), tags=tuple(tags), senti=senti)


def _snap_with(lines, now_s):
    base = snap([ln.sentence for ln in lines], now_s)
    return Snapshot(base.now_ms, base.metrics, lines, None, {}, None)


def test_a_long_pause_slow_speech_or_a_strongly_negative_answer_makes_a_hint_card():
    lines = [_said(1, "p2", 100, "这个我还得想一想吧不太确定怎么说", ["hesitation", "slow", "quiet"])]
    c = rules.how_said(_snap_with(lines, 110))
    assert len(c) == 1 and c[0].kind is K.Kind.SIGNAL and "参与者 ·02" in c[0].text and "回答前停顿长" in c[0].text and "语速偏慢" in c[0].text
    assert "仅供参考" in c[0].text and "不代表" in c[0].text and c[0].evidence == (1,) and c[0].ask
    strong = [_said(1, "p1", 100, "我真的很失望这个太糟糕了", ["negative"], -0.8)]
    assert rules.how_said(_snap_with(strong, 110))


def test_ordinary_answers_old_answers_and_the_moderator_make_no_hint_card():
    assert rules.how_said(_snap_with([_said(1, "p1", 100, "我平时一般先看看成分表", [])], 110)) == []
    assert rules.how_said(_snap_with([_said(1, "p1", 100, "嗯", ["slow"])], 110)) == []   # one mild tag alone is not worth a card
    assert rules.how_said(_snap_with([_said(1, "p1", 100, "停了很久", ["hesitation"])], 400)) == []   # 300 s ago
    assert rules.how_said(_snap_with([_said(1, "moderator", 100, "你觉得呢", ["hesitation"])], 110)) == []


def test_the_hint_card_never_names_a_feeling_as_a_fact():
    text = rules.how_said(_snap_with([_said(1, "p1", 100, "不太确定", ["hesitation", "slow"])], 110))[0].text
    assert not any(w in text for w in ("紧张", "生气", "不满", "害怕", "说谎", "不诚实"))


def test_candidates_come_most_urgent_first():
    s = [sent("p1", 100, 90), sent("p1", 200, 90), sent("p2", 230, 10, "我去年确诊了抑郁，现在在吃药治疗"), sent("p3", 250, 5)]
    assert kinds(candidates(snap(s, 260)))[0] is K.Kind.COMPLIANCE


# ---------- the limits ----------
NOW = utcnow()


def card(kind, seat=None, ago=1000, status="pending", key=None):
    return Existing(kind.value, seat, key or f"{kind.value}:{seat}:{ago}", status, NOW - timedelta(seconds=ago))


def cand(kind, seat=None, key=None):
    return Candidate(kind, "x", key or f"{kind.value}:{seat}:new", seat)


def test_a_card_is_shown_when_nothing_holds_it_back():
    assert choose([cand(K.Kind.SILENT, 3)], [], NOW, None).seat == 3


def test_at_most_one_new_card_every_45_seconds_except_personal_data():
    existing = [card(K.Kind.TIME, ago=20, status="used")]
    assert choose([cand(K.Kind.SILENT, 3)], existing, NOW, None) is None
    assert choose([cand(K.Kind.COMPLIANCE, 2, "compliance:9")], existing, NOW, None) is not None
    assert choose([cand(K.Kind.COMPLIANCE, 2, "compliance:9")], [card(K.Kind.TIME, ago=5, status="used")], NOW, None) is None


def test_no_card_right_after_the_moderator_spoke_except_personal_data():
    assert choose([cand(K.Kind.SILENT, 3)], [], NOW, 3.0) is None
    assert choose([cand(K.Kind.SILENT, 3)], [], NOW, 20.0) is not None
    assert choose([cand(K.Kind.COMPLIANCE, 2, "compliance:1")], [], NOW, 3.0) is not None


def test_three_unanswered_cards_stop_new_ones():
    waiting = [card(K.Kind.TIME, ago=100 + i * 50, key=f"k{i}") for i in range(K.MAX_PENDING)]
    assert choose([cand(K.Kind.OFFTOPIC)], waiting, NOW, None) is None
    assert choose([cand(K.Kind.OFFTOPIC)], waiting[:2], NOW, None) is not None


def test_the_same_kind_and_the_same_person_wait_a_while():
    assert choose([cand(K.Kind.SILENT, 3)], [card(K.Kind.SILENT, 2, ago=120, status="used")], NOW, None) is None   # kind cooldown (300 s)
    assert choose([cand(K.Kind.SILENT, 3)], [card(K.Kind.SILENT, 2, ago=400, status="used")], NOW, None) is not None
    assert choose([cand(K.Kind.DOMINANT, 3)], [card(K.Kind.SILENT, 3, ago=100, status="used")], NOW, None) is None   # the same person was just named
    assert choose([cand(K.Kind.DOMINANT, 1)], [card(K.Kind.SILENT, 3, ago=100, status="used")], NOW, None) is not None


def test_a_card_that_already_exists_is_not_made_again():
    assert choose([cand(K.Kind.TIME, None, "time:1")], [card(K.Kind.TIME, ago=1000, status="used", key="time:1")], NOW, None) is None


def test_a_kind_the_moderator_keeps_dismissing_waits_twice_as_long():
    dismissed = [card(K.Kind.SILENT, i, ago=2000 + i * 100, status="dismissed") for i in range(3)]
    assert noisy(K.Kind.SILENT, dismissed) and not noisy(K.Kind.TIME, dismissed)
    recent = dismissed + [card(K.Kind.SILENT, 4, ago=400, status="dismissed")]   # 400 s ago: past 300 s, inside 600 s
    assert choose([cand(K.Kind.SILENT, 5)], recent, NOW, None) is None
    assert choose([cand(K.Kind.SILENT, 5)], [c for c in recent if c.created_at < NOW - timedelta(seconds=1500)] + [card(K.Kind.SILENT, 4, ago=700, status="dismissed")], NOW, None) is not None


def test_unanswered_cards_expire_after_five_minutes():
    assert expired([card(K.Kind.TIME, ago=301, key="a"), card(K.Kind.TIME, ago=100, key="b"), card(K.Kind.TIME, ago=900, status="used", key="c")], NOW) == ["a"]


# ---------- on the database, through the API ----------
def _live_group(org, captions, started_min_ago=12):
    iid, _, _ = _group(org, 3)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        itv.status, itv.started_at = "live", utcnow() - timedelta(minutes=started_min_ago)
        for n, (speaker, end_s, dur_s, text) in enumerate(captions, start=1):
            db.add(CaptionLine(org_id=itv.org_id, interview_id=iid, seq=n, speaker=speaker, text=text, at_ms=end_s * 1000, dur_ms=dur_s * 1000))
        db.commit()
    return iid


def _cues(org, iid, role="researcher"):
    return org.c.get(f"/api/interviews/{iid}/cues", headers=org.h(role))


def test_a_personal_remark_becomes_a_card_with_the_participants_own_words_as_evidence(org):
    iid = _live_group(org, [("p2", 11 * 60 + 40, 6, "我去年确诊了抑郁，现在每天都在吃药")])
    r = _cues(org, iid)
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    assert len(cards) == 1 and cards[0]["kind"] == "compliance" and cards[0]["status"] == "pending"
    assert cards[0]["evidence"] == [{"seq": 1, "who": "参与者 ·02", "text": "我去年确诊了抑郁，现在每天都在吃药"}]
    assert len(_cues(org, iid).json()["cards"]) == 1   # asking again does not make the same card twice


def test_the_moderator_can_use_or_dismiss_a_card_and_it_is_audited(org):
    iid = _live_group(org, [("p2", 11 * 60 + 40, 6, "我去年确诊了抑郁，现在每天都在吃药")])
    iid_mod = iid
    with SessionLocal() as db:   # make the moderator one of this interview's team
        itv = db.get(Interview, iid)
        itv.moderator_id = org.users["moderator"]
        db.commit()
    cid = _cues(org, iid_mod, "moderator").json()["cards"][0]["id"]
    r = org.c.patch(f"/api/cues/{cid}", headers=org.h("moderator"), json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["cards"][0]["status"] == "dismissed"
    assert org.c.patch(f"/api/cues/{cid}", headers=org.h("moderator"), json={"status": "used"}).json()["cards"][0]["status"] == "dismissed"  # answered once
    assert org.c.patch(f"/api/cues/{cid}", headers=org.h("moderator"), json={"status": "maybe"}).status_code == 422
    log = org.c.get("/api/audit?limit=20", headers=org.h("admin")).json()
    assert any(e["action"] == "cue.dismissed" for e in log)


def test_observers_participants_other_companies_and_depth_interviews_get_nothing(org, client):
    iid = _live_group(org, [("p2", 11 * 60 + 40, 6, "我去年确诊了抑郁，现在每天都在吃药")])
    assert _cues(org, iid, "observer").status_code == 403   # a card may quote a participant's screening answer
    assert client.get(f"/api/interviews/{iid}/cues").status_code == 401
    other = Org(client, "Other", "b@other.com")
    assert other.c.get(f"/api/interviews/{iid}/cues", headers=other.h()).status_code == 404
    cid = _cues(org, iid).json()["cards"][0]["id"]
    assert org.c.patch(f"/api/cues/{cid}", headers=org.h("observer"), json={"status": "used"}).status_code == 403
    assert other.c.patch(f"/api/cues/{cid}", headers=other.h(), json={"status": "used"}).status_code == 404
    depth = org.interview()
    assert _cues(org, depth["id"]).status_code == 409


def test_nothing_is_made_before_the_interview_is_live_and_old_unanswered_cards_expire(org):
    iid = _live_group(org, [("p2", 11 * 60 + 40, 6, "我去年确诊了抑郁，现在每天都在吃药")])
    with SessionLocal() as db:
        db.get(Interview, iid).status = "scheduled"
        db.commit()
    assert _cues(org, iid).json()["cards"] == []
    with SessionLocal() as db:
        db.get(Interview, iid).status = "live"
        db.commit()
    assert len(_cues(org, iid).json()["cards"]) == 1
    with SessionLocal() as db:
        db.scalars(__import__("sqlalchemy").select(CueCard)).first().created_at = utcnow() - timedelta(seconds=K.EXPIRE_S + 5)
        db.commit()
    shown = _cues(org, iid).json()["cards"]   # (a newer card about the silent seats may now be waiting in front of it)
    assert next(c for c in shown if c["kind"] == "compliance")["status"] == "expired"


def test_the_moderators_position_comes_from_the_material_on_screen(org):
    iid = _live_group(org, [], started_min_ago=2)   # early enough that nobody counts as silent yet
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        db.add(InterviewPlan(interview_id=iid, org_id=itv.org_id, template="t", title="t", script_json=json.dumps({"steps": STEPS})))
        stim = Stimulus(org_id=itv.org_id, interview_id=iid, key="concept_a", kind="image", title="概念 A", url="/concepts/concept_a.svg", position=1)
        db.add(stim)
        db.flush()
        db.add(RoomState(interview_id=iid, org_id=itv.org_id, stimulus_id=stim.id, shown_at=utcnow() - timedelta(minutes=5), shown_by="x"))
        db.commit()
    cards = _cues(org, iid).json()["cards"]
    assert [c["kind"] for c in cards] == ["time"] and "概念 A 精华" in cards[0]["text"]


def test_cards_are_deleted_with_the_interview(org):
    from app.retention import purge_interview

    iid = _live_group(org, [("p2", 11 * 60 + 40, 6, "我去年确诊了抑郁，现在每天都在吃药")])
    _cues(org, iid)
    with SessionLocal() as db:
        assert db.query(CueCard).count() == 1
        purge_interview(db, db.get(Interview, iid), "test", "tester")
        db.commit()
        assert db.query(CueCard).count() == 0
