"""The speaking-fairness panel: the arithmetic (pure functions of the caption sentences) and the endpoint that serves it to the team."""
import time
from datetime import timedelta

import pytest

from app.db import SessionLocal
from app.group_metrics import constants as K
from app.group_metrics.compute import Sentence, compute, fairness, fairness_word, gini, is_informative, overlaps
from app.models import CaptionLine, Interview, utcnow
from conftest import Org
from test_groups import _group

LONG = "我觉得这个精华的质地很清爽不黏腻用了之后皮肤很舒服"


def S(speaker, end_s, dur_s=5, text=LONG):
    return Sentence(speaker, text, int(end_s * 1000), int(dur_s * 1000))


def row(result, seat):
    return next(r for r in result["participants"] if r["seat"] == seat)


# ---------- the arithmetic ----------
def test_talk_time_share_turns_and_silence():
    r = compute([S("p1", 60, 20), S("p1", 70, 5), S("p2", 100, 10), S("moderator", 120, 5)], [1, 2, 3], 300_000)
    p1, p2, p3 = row(r, 1), row(r, 2), row(r, 3)
    assert (p1["talk_s"], p2["talk_s"], p3["talk_s"]) == (25, 10, 0)
    assert p1["share"] == pytest.approx(25 / 35, abs=0.001) and p3["share"] == 0
    assert p1["turns"] == 2 and p1["sentences"] == 2 and p3["turns"] == 0  # 40..60 and 65..70: a 5 s gap starts a new turn
    assert p1["silent_s"] == 230 and p3["silent_s"] is None
    assert r["moderator"]["talk_s"] == 5 and r["expected_share"] == pytest.approx(0.333, abs=0.001)


def test_sentences_of_one_turn_are_merged_when_close_together():
    r = compute([S("p1", 10, 4), S("p1", 15, 3), S("p1", 19, 2)], [1], 60_000)
    assert row(r, 1)["turns"] == 1  # the gaps are 1 s and 1 s


def test_overlapping_speech_of_one_person_is_counted_once_in_talk_time():
    r = compute([S("p1", 20, 10), S("p1", 22, 10)], [1], 60_000)  # 10..20 and 12..22
    assert row(r, 1)["talk_s"] == 12


def test_a_sentence_without_a_duration_is_estimated_from_its_length():
    zh = Sentence("p1", "一二三四五六七八九十一二", 20_000, 0)  # 12 characters at 4 per second
    assert zh.length_ms == 3000 and zh.start_ms == 17_000
    assert Sentence("p1", "one two three four five", 20_000, 0).length_ms == 2000  # 5 words at 2.5 per second
    assert Sentence("p1", "嗯", 5000, 0).length_ms == K.MIN_SENTENCE_MS


def test_interruptions_name_who_began_later_and_who_was_talked_over():
    sentences = [S("p1", 20, 10), S("p2", 24, 6), S("moderator", 40, 3), S("p3", 60, 2)]  # p2 starts at 18, inside p1's 10..20
    assert [(a, b) for a, b, _ in overlaps(sentences)] == [("p2", "p1")]
    r = compute(sentences, [1, 2, 3], 120_000)
    assert row(r, 2)["interrupts"] == 1 and row(r, 1)["interrupted"] == 1 and r["moderator"]["interrupts"] == 0


def test_the_moderator_talking_over_a_participant_is_counted_for_the_moderator():
    r = compute([S("p1", 20, 10), S("moderator", 24, 6)], [1], 60_000)
    assert r["moderator"]["interrupts"] == 1 and row(r, 1)["interrupted"] == 1


def test_a_short_overlap_is_not_an_interruption():
    assert overlaps([S("p1", 20, 10), Sentence("p2", LONG, 30_500, 11_000)]) == []  # p2 starts at 19.5: 0.5 s of overlap
    assert overlaps([S("p1", 20, 10), Sentence("p2", LONG, 30_000, 11_000)]) == []  # starts at 19: 1 s, still within the delay between two recognisers
    assert len(overlaps([S("p1", 20, 10), Sentence("p2", LONG, 29_000, 11_000)])) == 1  # starts at 18: 2 s of real talking over each other


def test_nobody_is_judged_too_early():
    r = compute([S("p1", 30, 25)], [1, 2, 3], 60_000)
    assert [p["state"] for p in r["participants"]] == ["ok", "ok", "ok"] and r["fairness"] is None and r["fairness_word"] == "数据还少"


def test_a_silent_participant_turns_yellow_then_red_and_is_named_in_the_hints():
    spoke = [S("p1", 100, 60), S("p2", 200, 60), S("p3", 120, 10)]
    assert row(compute(spoke, [1, 2, 3], 120_000 + 250_000), 3)["state"] == "quiet"  # silent 250 s
    red = compute(spoke, [1, 2, 3], 120_000 + 500_000)
    assert row(red, 3)["state"] == "silent" and any("参与者 ·03" in h and "没发言" in h for h in red["hints"])


def test_someone_who_never_spoke_is_silent_after_five_minutes():
    r = compute([S("p1", 100, 60), S("p2", 200, 60)], [1, 2, 3], 360_000)
    assert row(r, 3)["state"] == "silent" and any("还没发过言" in h for h in r["hints"])


def test_one_person_talking_most_of_the_time_is_flagged_high():
    r = compute([S("p1", 100, 90), S("p1", 200, 90), S("p2", 230, 10), S("p3", 250, 8)], [1, 2, 3], 260_000)
    assert row(r, 1)["state"] == "high" and any("占" in h and "%" in h for h in r["hints"])
    assert r["fairness_word"] in {"偏集中", "严重偏集中"}


def test_gini_and_fairness_bounds():
    assert gini([5, 5, 5]) == 0 and gini([0, 0, 9]) == pytest.approx(1.0)
    assert fairness([100_000, 100_000, 100_000]) == 1.0
    assert fairness([180_000, 0, 0]) == 0.0
    assert fairness([30_000, 20_000]) is None  # too little talk to mean anything
    assert fairness_word(0.9) == "均衡" and fairness_word(0.6) == "偏集中" and fairness_word(0.3) == "严重偏集中"


def test_the_fairness_curve_has_a_point_every_two_minutes_once_there_is_enough_talk():
    sentences = [S("p1", 50, 40), S("p2", 100, 40), S("p1", 200, 30), S("p2", 230, 30)]
    series = compute(sentences, [1, 2], 300_000)["fairness_series"]
    assert series and all(0 <= v <= 1 for _, v in series) and [m for m, _ in series] == sorted({m for m, _ in series})


def test_informative_sentences_leave_out_nods():
    assert is_informative(LONG) and is_informative("I really like how light this cream feels on my skin")
    for nod in ("嗯", "对", "对对对！", "好的", "yeah", "I agree", "OK.", "短句子"):
        assert not is_informative(nod), nod


def test_the_last_five_minutes_chart_counts_seconds_per_minute():
    r = compute([S("p1", 290, 20)], [1], 300_000)
    assert sum(row(r, 1)["spark"]) == 20 and len(row(r, 1)["spark"]) == K.SPARK_BUCKETS


def test_a_two_hour_interview_is_computed_fast_enough_to_poll():
    sentences = [S(f"p{1 + i % 6}", 10 + i * 12, 8) for i in range(600)]
    t0 = time.monotonic()
    compute(sentences, [1, 2, 3, 4, 5, 6], 7_300_000)
    assert time.monotonic() - t0 < 2.0


# ---------- the endpoint ----------
def _fill(itv_id, lines, started_min_ago=30, ended=False):
    with SessionLocal() as db:
        itv = db.get(Interview, itv_id)
        itv.status = "ended" if ended else "live"
        itv.started_at = utcnow() - timedelta(minutes=started_min_ago)
        if ended:
            itv.ended_at = itv.started_at + timedelta(minutes=30)
        for n, (speaker, end_s, dur_s) in enumerate(lines, start=1):
            db.add(CaptionLine(org_id=itv.org_id, interview_id=itv.id, seq=n, speaker=speaker, text=LONG, at_ms=end_s * 1000, dur_ms=dur_s * 1000))
        db.commit()


def test_the_team_reads_the_panel_and_a_depth_interview_has_none(org):
    iid, _, _ = _group(org, 3)
    _fill(iid, [("p1", 100, 60), ("p2", 200, 30), ("moderator", 220, 5)], ended=True)
    r = org.c.get(f"/api/interviews/{iid}/group/metrics", headers=org.h("researcher"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert [p["label"] for p in d["participants"]] == ["参与者 ·01", "参与者 ·02", "参与者 ·03"] and d["elapsed_ms"] == 30 * 60_000
    assert row(d, 1)["talk_s"] == 60 and d["moderator"]["talk_s"] == 5
    assert all("name" not in p and "email" not in p for p in d["participants"])  # numbers only, never a name
    depth = org.interview()
    assert org.c.get(f"/api/interviews/{depth['id']}/group/metrics", headers=org.h("researcher")).status_code == 409


def test_other_companies_and_logged_out_callers_are_refused(org, client):
    iid, _, _ = _group(org, 2)
    assert client.get(f"/api/interviews/{iid}/group/metrics").status_code == 401
    other = Org(client, "Other", "b@other.com")
    assert other.c.get(f"/api/interviews/{iid}/group/metrics", headers=other.h()).status_code == 404


def test_a_participant_link_is_not_a_login(org):
    iid, _, tokens = _group(org, 2)
    assert org.c.get(f"/api/interviews/{iid}/group/metrics", headers={"Authorization": "Bearer " + tokens[0]}).status_code == 401
