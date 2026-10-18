"""Disagreement map of a focus group: positions with verbatim quotes, conflicts, changes of mind with what was said before, consensus and dissent; every claim checked against the transcript and only a suggestion until a researcher accepts it."""
import io
import zipfile

import pytest

from app import group_analysis
from app.db import SessionLocal
from app.models import Claim, Interview, TranscriptSegment
from test_groups import _create_group

TALK = [("moderator", "大家怎么看这款面霜的香味？"),
        ("p1", "我喜欢它的香味，很放心。"),                     # 1: p1 pro fragrance
        ("p2", "我不喜欢，香精会让我过敏。"),                   # 2: p2 con fragrance
        ("p3", "我觉得价格太贵了。"),                           # 3: p3 con price
        ("moderator", "p1 你能说说为什么吗？"),
        ("p1", "天然的香味让我放心，不过听了她说的，我也不确定了。"),   # 5: p1 unsure fragrance (after p2's con? p2 was con) -> shift pro -> unsure
        ("p2", "包装不错，我推荐。"),                           # 6: p2 pro packaging
        ("p3", "包装不错，也推荐。"),                           # 7: p3 pro packaging
        ("p1", "包装不错。")]                                   # 8: p1 pro packaging


def group_with_transcript(org, talk=TALK):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(3)]
    r = _create_group(org, pid, pids).json()
    iid = r["id"]
    with SessionLocal() as db:
        oid = db.get(Interview, iid).org_id
        for i, (spk, text) in enumerate(talk):
            db.add(TranscriptSegment(org_id=oid, interview_id=iid, idx=i, speaker=spk, start_ms=i * 6000, end_ms=i * 6000 + 5000, text_orig=text, lang="zh"))
        db.commit()
    return iid


def analyze(org, iid, headers=None):
    r = org.c.post(f"/api/interviews/{iid}/disagreement/analyze", headers=headers or org.h())
    assert r.status_code == 200, r.text
    return r.json()


def cell(v, topic, key):
    return next(m for m in v["matrix"] if m["topic"] == topic)["cells"].get(key, [])


def test_the_map_shows_each_persons_stance_with_the_quote_copied_from_the_transcript(org):
    iid = group_with_transcript(org)
    v = analyze(org, iid)
    assert v["analysed"] is True and {m["topic"] for m in v["matrix"]} == {"香味", "价格", "包装"} and [s["label"] for s in v["speakers"]] == ["参与者 ·01", "参与者 ·02", "参与者 ·03"]
    c = cell(v, "香味", "p2")[0]
    assert c["stance"] == "con" and c["stance_zh"] == "反对" and c["quotes"] == [{"text": "我不喜欢，香精会让我过敏。", "at_ms": 12000}] and c["status"] == "suggested"
    assert cell(v, "价格", "p3")[0]["stance_zh"] == "反对" and cell(v, "包装", "p1")[0]["stance"] == "pro"
    assert "Wei Ling" not in str(v)


def test_people_on_opposite_sides_are_a_conflict_pair_and_agreement_is_a_consensus(org):
    iid = group_with_transcript(org)
    v = analyze(org, iid)
    fragrance = [c for c in v["conflicts"] if c["topic"] == "香味"]
    assert fragrance == [] or all(c["a"]["stance"] == "pro" and c["b"]["stance"] == "con" for c in fragrance)    # p1 moved to "unsure", so the pair no longer opposes
    assert any(c["topic"] == "包装" and set(c["speakers"]) == {"参与者 ·01", "参与者 ·02", "参与者 ·03"} and c["stance_zh"] == "赞成" for c in v["consensus"])


def test_a_conflict_is_listed_when_two_people_still_disagree(org):
    iid = group_with_transcript(org, [("p1", "我喜欢这个香味。"), ("p2", "我不喜欢这个香味，会过敏。"), ("p3", "我也不喜欢香味。")])
    v = analyze(org, iid)
    assert [(c["a"]["speaker"], c["b"]["speaker"]) for c in v["conflicts"]] == [("参与者 ·01", "参与者 ·02"), ("参与者 ·01", "参与者 ·03")]
    assert v["dissent"] and v["dissent"][0]["who"] == "参与者 ·01" and v["dissent"][0]["stance_zh"] == "赞成"      # one against two: the minority is not averaged away


def test_a_change_of_mind_is_found_with_a_possible_trigger_and_marked_for_review_when_weak(org):
    iid = group_with_transcript(org, [("p1", "我喜欢这个香味。"), ("p2", "我不喜欢香味，会过敏。"), ("p1", "听她这么说，我对香味也不确定了。"), ("p3", "价格太贵了。"), ("p3", "其实价格还不错。")])
    v = analyze(org, iid)
    sh = {(e["speaker"], e["topic"]): e for e in v["shifts"]}
    a = sh[("参与者 ·01", "香味")]
    assert (a["from"], a["to"]) == ("赞成", "犹豫") and a["trigger"] is None and a["needs_review"] is True and a["status"] == "suggested"   # p2 said "con", p1 moved to "unsure": no matching trigger
    b = sh[("参与者 ·03", "价格")]
    assert (b["from"], b["to"]) == ("反对", "赞成")


def test_a_trigger_that_took_the_stance_the_person_moved_to_raises_the_confidence(org):
    iid = group_with_transcript(org, [("p1", "我喜欢这个香味。"), ("p2", "我不喜欢香味，会过敏。"), ("p1", "我现在也不喜欢香味了。")])
    e = analyze(org, iid)["shifts"][0]
    assert e["speaker"] == "参与者 ·01" and (e["from"], e["to"]) == ("赞成", "反对") and e["needs_review"] is False and e["confidence"] == 0.6
    assert e["trigger"]["speaker"] == "参与者 ·02" and e["trigger"]["quotes"][0]["text"] == "我不喜欢香味，会过敏。"


def test_claims_that_cite_the_wrong_speaker_unknown_lines_or_bad_stances_are_dropped(org, monkeypatch):
    iid = group_with_transcript(org)

    class Liar:
        def extract_claims(self, lines):
            return [{"speaker": "p1", "topic": "香味", "stance": "pro", "reason": "ok", "lines": [2]},               # line 2 is p1's
                    {"speaker": "p2", "topic": "香味", "stance": "con", "reason": "wrong speaker", "lines": [2]},   # p2 did not say line 2
                    {"speaker": "p1", "topic": "香味", "stance": "pro", "reason": "no such line", "lines": [99]},
                    {"speaker": "p1", "topic": "香味", "stance": "angry", "reason": "bad stance", "lines": [2]},
                    {"speaker": "moderator", "topic": "香味", "stance": "pro", "reason": "not a participant", "lines": [1]},
                    {"speaker": "p1", "topic": "", "stance": "pro", "reason": "no topic", "lines": [2]}, {"speaker": "p1", "topic": "香味", "stance": "pro", "lines": []}, "junk"]
    monkeypatch.setattr("app.routers.group_analysis.get_llm", lambda: Liar())
    v = analyze(org, iid)
    assert v["claims"] == 1 and v["dropped"] == 7 and cell(v, "香味", "p1")[0]["quotes"][0]["text"] == "我喜欢它的香味，很放心。"


def test_similar_topic_names_are_merged_and_the_number_of_topics_is_capped(org, monkeypatch):
    iid = group_with_transcript(org)

    class Wordy:
        def extract_claims(self, lines):
            return [{"speaker": "p1", "topic": "香味", "stance": "pro", "reason": "a", "lines": [2]}, {"speaker": "p2", "topic": "香味的问题", "stance": "con", "reason": "b", "lines": [3]}]
    monkeypatch.setattr("app.routers.group_analysis.get_llm", lambda: Wordy())
    v = analyze(org, iid)
    assert [m["topic"] for m in v["matrix"]] == ["香味"] and len(v["conflicts"]) == 1
    many = [{"speaker": "p1", "topic": f"议题{chr(0x4e00 + i * 7)}{chr(0x4e01 + i * 7)}", "stance": "pro", "reason": "x", "lines": [2]} for i in range(20)]
    assert len({c["topic"] for c in group_analysis.clean_claims(many, group_analysis.lines_of(SessionLocal(), SessionLocal().get(Interview, iid)))}) == group_analysis.MAX_TOPICS


def test_suggestions_become_part_of_the_map_only_when_accepted_and_rejected_ones_disappear(org):
    iid = group_with_transcript(org)
    v = analyze(org, iid)
    c = cell(v, "香味", "p2")[0]
    assert org.c.post(f"/api/claims/{c['id']}/accept", headers=org.h()).json()["status"] == "accepted"
    assert cell(org.c.get(f"/api/interviews/{iid}/disagreement", headers=org.h()).json(), "香味", "p2")[0]["status"] == "accepted"
    other = cell(v, "价格", "p3")[0]
    org.c.post(f"/api/claims/{other['id']}/reject", headers=org.h())
    again = org.c.get(f"/api/interviews/{iid}/disagreement", headers=org.h()).json()
    assert "价格" not in [m["topic"] for m in again["matrix"]]
    v2 = analyze(org, iid)        # analysing again keeps accepted claims and rebuilds the rest (the rejected one comes back as a fresh suggestion)
    assert cell(v2, "香味", "p2")[0]["id"] == c["id"] and cell(v2, "香味", "p2")[0]["status"] == "accepted" and "价格" in [m["topic"] for m in v2["matrix"]]
    with SessionLocal() as db:
        assert db.query(Claim).filter_by(interview_id=iid, speaker="p2", topic="香味").count() == 1
    ev = v2["shifts"][0]
    assert org.c.post(f"/api/persuasion/{ev['id']}/accept", headers=org.h()).json()["status"] == "accepted"
    assert org.c.post(f"/api/claims/{c['id']}/delete", headers=org.h()).status_code == 404


def test_only_focus_groups_with_a_transcript_can_be_analysed(org):
    depth = org.interview()
    assert org.c.post(f"/api/interviews/{depth['id']}/disagreement/analyze", headers=org.h()).status_code == 409
    assert org.c.get(f"/api/interviews/{depth['id']}/disagreement", headers=org.h()).status_code == 409
    pid = org.project()
    r = _create_group(org, pid, [org.participant(pid)["id"] for _ in range(2)]).json()
    assert org.c.post(f"/api/interviews/{r['id']}/disagreement/analyze", headers=org.h()).status_code == 409     # no transcript yet


def test_a_model_that_fails_gives_a_calm_error_and_changes_nothing(org, monkeypatch):
    import httpx
    iid = group_with_transcript(org)
    analyze(org, iid)

    class Down:
        def extract_claims(self, lines):
            raise httpx.ConnectError("down")
    monkeypatch.setattr("app.routers.group_analysis.get_llm", lambda: Down())
    r = org.c.post(f"/api/interviews/{iid}/disagreement/analyze", headers=org.h())
    assert r.status_code == 502 and "稍后再试" in r.json()["detail"]
    assert org.c.get(f"/api/interviews/{iid}/disagreement", headers=org.h()).json()["claims"] > 0


def test_the_word_export_has_the_matrix_quotes_and_the_caveat(org):
    iid = group_with_transcript(org)
    analyze(org, iid)
    r = org.c.get(f"/api/interviews/{iid}/disagreement/export", headers=org.h())
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "<w:tbl>" in xml and "我不喜欢，香精会让我过敏。" in xml and "参与者 ·02" in xml and "需要研究员确认" in xml and "Wei Ling" not in xml


def test_the_map_goes_with_retention_and_is_for_the_projects_staff_only(org, client):
    from app.retention import purge_interview
    from test_project_members import second_researcher
    iid = group_with_transcript(org)
    v = analyze(org, iid)
    cid = cell(v, "香味", "p2")[0]["id"]
    other = second_researcher(org)
    for method, url in (("post", f"/api/interviews/{iid}/disagreement/analyze"), ("get", f"/api/interviews/{iid}/disagreement"), ("get", f"/api/interviews/{iid}/disagreement/export"), ("post", f"/api/claims/{cid}/accept")):
        assert getattr(org.c, method)(url, headers=other["h"]).status_code == 404, url
        for role in ("moderator", "observer"):
            assert getattr(org.c, method)(url, headers=org.h(role)).status_code in (403, 404), (role, url)
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.post(f"/api/claims/{cid}/accept", headers=foreign.h("admin")).status_code == 404
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, iid), "test")
        db.commit()
        assert db.query(Claim).filter_by(interview_id=iid).count() == 0
