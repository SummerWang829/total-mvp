"""What each person said to each outline question: their own words from the transcript, one column per question, labels only from a model that may be used."""
from types import SimpleNamespace

from app import interview_answers, respondent_table
from app.db import SessionLocal
from app.models import ScreenerAnswer, TranscriptSegment
from app.moderator.providers import ChatError
from test_week1 import transcribed

OUTLINE = """目标: 了解消费者如何选购护肤品
受访者话题: 护肤品选购
语言: en
时长: 30
## 暖场
- Could you tell me how you usually buy skincare products?
## 话题: 顾虑
- What makes you hesitate to try a new brand from overseas?
- How important is the packaging to you?
- Would you pay more for an organic label?
"""


def _seg(i, speaker, text):
    return SimpleNamespace(id=f"s{i}", idx=i, speaker=speaker, text_orig=text, start_ms=i * 1000)


def test_the_answer_is_the_participants_own_words_up_to_the_next_question():
    segs = [_seg(0, "moderator", "How do you usually buy skincare products?"), _seg(1, "participant", "I watch reviews first."), _seg(2, "moderator", "And why those?"),
            _seg(3, "participant", "They feel honest."), _seg(4, "moderator", "How important is the packaging to you?"), _seg(5, "participant", "Quite important.")]
    items = interview_answers.extract(["How do you usually buy skincare products?", "How important is the packaging to you?", "Would you pay more for an organic label?"], segs)
    assert items[0]["a"] == "I watch reviews first. They feel honest." and items[0]["seg"] == ["s1", "s3"] and items[0]["at_ms"] == 1000   # the follow-up belongs to the answer
    assert items[1]["a"] == "Quite important."
    assert items[2] == {"q": "Would you pay more for an organic label?", "a": "", "main": "", "followups": [], "asked": False, "seg": [], "at_ms": None}   # never asked: left empty, nothing invented


def test_theme_labels_are_added_by_the_model_but_never_replace_the_words():
    class Chat:
        def complete(self, messages, temperature=0.2, seed=None):
            return {"themes": [["reviews", "price", "a very long label that goes on and on", "extra"]]}
    items = [{"q": "q1", "a": "I watch reviews first.", "seg": [], "at_ms": 0}, {"q": "q2", "a": "", "seg": [], "at_ms": None}]
    interview_answers.add_themes(Chat(), items, "en")
    assert items[0]["a"] == "I watch reviews first." and 1 <= len(items[0]["themes"]) <= 3 and "themes" not in items[1]


def test_a_failing_model_leaves_the_answers_without_labels(org):
    class Broken:
        def complete(self, *a, **k):
            raise ChatError("down")
    itv, _ = transcribed(org)
    org.c.put(f"/api/projects/{itv['project_id']}/outline", headers=org.h(), json={"text": OUTLINE})
    with SessionLocal() as db:
        from app.models import Interview
        i = db.get(Interview, itv["id"])
        out = interview_answers.generate(db, i, ["Could you tell me how you usually buy skincare products?"], Broken(), "en")
        assert out == {"answered": 1, "themes": False, "by_model": 0}


def test_endpoint_table_columns_and_filter_kinds(org):
    itv, _ = transcribed(org)
    pid = itv["project_id"]
    assert org.c.post(f"/api/interviews/{itv['id']}/answers/generate", headers=org.h()).status_code == 409   # no outline yet
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    with SessionLocal() as db:
        person = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["participant"]["id"]
        org_id = db.query(TranscriptSegment).first().org_id
        for pos, (q, a) in enumerate([("多久用一次？", "每天"), ("在哪里买？", "网店、超市"), ("为什么选它？", "朋友推荐了一个很长很长的理由，说了很多很多的话，远远超过一个选项该有的长度，所以它是开放题。")]):
            db.add(ScreenerAnswer(org_id=org_id, participant_id=person, position=pos, question=q, answer=a))
        db.commit()
    r = org.c.post(f"/api/interviews/{itv['id']}/answers/generate", headers=org.h())
    assert r.status_code == 200 and r.json()["answered"] >= 2 and r.json()["themes"] is False   # the offline mock model gives no labels
    d = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()
    cols = {c["id"]: c for c in d["columns"]}
    assert cols["s0"]["type"] == "choice" and cols["s0"]["options"] == ["每天"] and cols["s1"]["multi"] is True and set(cols["s1"]["options"]) == {"网店", "超市"}
    assert cols["s2"]["type"] == "text" and cols["s2"]["options"] == []   # an open answer only gets a search box
    labels = [c["label"] for c in d["columns"] if c["group"] == "answer"]
    assert labels[0].startswith("Could you tell me") and labels[1].endswith("· 主题")
    row = next(x for x in d["rows"] if x["id"] == itv["id"])
    assert row["cells"]["s0"] == "每天" and "TikTok" in row["cells"]["a0"] and d["can_generate"] is True


def test_not_ended_and_client_observer_and_other_companies(org):
    from conftest import Org
    itv = org.interview()
    org.c.put(f"/api/projects/{itv['project_id']}/outline", headers=org.h(), json={"text": OUTLINE})
    assert org.c.post(f"/api/interviews/{itv['id']}/answers/generate", headers=org.h()).status_code == 409
    d = org.c.get(f"/api/projects/{itv['project_id']}/insights/overview", headers=org.h("observer")).json()
    assert d["columns"] == [] and "cells" not in d["rows"][0] and d["can_generate"] is False
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.post(f"/api/interviews/{itv['id']}/answers/generate", headers=other.h()).status_code == 404


def test_raw_answers_are_saved_automatically_when_an_ended_interview_gets_a_transcript(org):
    itv = org.interview()
    pid = itv["project_id"]
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h())
    body = {"segments": [
        {"idx": 0, "speaker": "moderator", "start_ms": 0, "end_ms": 900, "text_orig": "How do you usually buy skincare products?", "lang": "en"},
        {"idx": 1, "speaker": "participant", "start_ms": 1000, "end_ms": 2200, "text_orig": "I compare reviews and ingredients.", "lang": "en"},
    ]}
    assert org.c.post(f"/api/interviews/{itv['id']}/transcript", headers=org.h(), json=body).status_code == 200
    overview = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()
    row = next(x for x in overview["rows"] if x["id"] == itv["id"])
    assert row["cells"]["a0"] == "I compare reviews and ingredients."
    assert row["transcript_state"] == "ready"


def test_the_first_answer_and_each_follow_up_with_its_reply_are_kept_apart():
    segs = [_seg(0, "moderator", "How do you usually buy skincare products?"), _seg(1, "participant", "I watch reviews first."), _seg(2, "moderator", "Which reviews?"),
            _seg(3, "participant", "TikTok ones."), _seg(4, "participant", "Mostly."), _seg(5, "moderator", "And then?"), _seg(6, "participant", "I compare prices."),
            _seg(7, "moderator", "How important is the packaging to you?"), _seg(8, "participant", "Quite important.")]
    it = interview_answers.extract(["How do you usually buy skincare products?", "How important is the packaging to you?"], segs)[0]
    assert it["main"] == "I watch reviews first." and it["asked"] is True
    assert it["followups"] == [{"ask": "Which reviews?", "a": "TikTok ones. Mostly.", "at_ms": 3000}, {"ask": "And then?", "a": "I compare prices.", "at_ms": 6000}]
    assert it["a"] == "I watch reviews first. TikTok ones. Mostly. I compare prices."   # the whole answer is still there for the themes and the report


def test_a_question_that_was_asked_but_not_answered_differs_from_one_never_found():
    segs = [_seg(0, "moderator", "How do you usually buy skincare products?"), _seg(1, "moderator", "Hello? Are you there?")]
    a, b = interview_answers.extract(["How do you usually buy skincare products?", "Would you pay more for an organic label?"], segs)
    assert a["asked"] is True and a["a"] == "" and b["asked"] is False


def test_a_question_in_another_language_is_found_by_the_model_from_the_moderators_words_only():
    sent = []

    class Chat:
        def complete(self, messages, temperature=0.0, seed=None):
            sent.append(messages[1]["content"])
            return {"matches": {"1": 1, "2": 99, "x": 3}}   # a turn that does not exist and a bad key are dropped
    segs = [_seg(0, "moderator", "简单说，您每天早晚会往脸上涂些什么？"), _seg(1, "participant", "秘密的受访者原话"), _seg(2, "moderator", "再说说价格")]
    qs = ["How do you usually buy skincare products?", "Would you pay more for an organic label?"]
    asks = interview_answers.rule_match(qs, segs)
    assert asks == {}   # wording alone finds nothing across languages
    got = interview_answers.model_match(Chat(), qs, segs, asks)
    assert got == {0: 0} and "秘密的受访者原话" not in sent[0] and "再说说价格" in sent[0]


def test_the_model_matching_failing_leaves_the_wording_result(org):
    from app.moderator.providers import ChatError

    class Broken:
        def complete(self, *a, **k):
            raise ChatError("down")
    segs = [_seg(0, "moderator", "简单说，您每天早晚会往脸上涂些什么？")]
    assert interview_answers.model_match(Broken(), ["How do you usually buy skincare products?"], segs, {}) == {}
