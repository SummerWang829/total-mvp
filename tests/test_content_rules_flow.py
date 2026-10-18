"""The rule library at every text entrance: the AI interviewer's chat, the live captions, pre-interview answers and transcripts.

What a participant types or says is hidden BEFORE it is stored, shown to the team or sent to the AI model, and the team is told.
"""
import json

import pytest
from sqlalchemy import select

from app import ai_room, content_rules
from app.db import SessionLocal
from app.models import AiSession, AuditLog, CaptionLine, Interview, PreTaskAnswer, TranscriptSegment
from app.moderator.templates import TEMPLATES
from conftest import Org
from test_ai_room import _participant, _say, _staff, _start, _walk_into_the_room
from test_cues import _cues, _live_group

PHONE = "13812345678"
Z = TEMPLATES["zh"]


class Recorder:
    """Wraps the offline brain and remembers every message that would have gone to the model."""

    name = "recorder"

    def __init__(self):
        from eval.mock_brain import MockBrain

        self.inner, self.sent = MockBrain(), []

    def complete(self, messages, temperature=0.3, seed=None):
        self.sent.append(json.dumps(messages, ensure_ascii=False))
        return self.inner.complete(messages, temperature, seed)


@pytest.fixture
def room(org, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(ai_room, "_chat_model", lambda: rec)
    itv = _walk_into_the_room(org, language="zh")
    assert _start(org, itv).status_code == 200
    return org, itv, rec


def _chat(org, itv):
    return [m for m in _participant(org, itv)["messages"]]


def _db_alert(itv):
    with SessionLocal() as db:
        s = db.get(AiSession, itv["id"])
        return s.alert, s.alert_reason, s.strikes, s.finished


# ---------- the AI interviewer's chat ----------
def test_a_phone_number_is_hidden_never_sent_to_the_model_and_the_ai_reminds_gently(room):
    org, itv, rec = room
    before = len(rec.sent)
    assert _say(org, itv, f"好的，你可以打我电话 {PHONE}，我平时用精华比较多").status_code in (200, 201, 204)
    chat = _chat(org, itv)
    mine = next(m for m in chat if m["sender"] == "participant")
    assert PHONE not in mine["text"] and "[联系方式已隐藏]" in mine["text"] and mine["text"].startswith("好的，你可以打我电话")
    assert all(PHONE not in call for call in rec.sent[before:]) and len(rec.sent) == before  # no model call at all for this turn
    assert Z.contact_reminder.split("。")[0] in chat[-1]["text"]
    alert, reason, strikes, finished = _db_alert(itv)
    assert alert == "yellow" and "联系方式" in reason and strikes == 0 and not finished and PHONE not in reason   # contact details are not a strike


def test_abuse_is_hidden_reminded_counted_and_after_three_the_ai_ends_the_chat_politely(room):
    org, itv, rec = room
    for n in (1, 2):
        _say(org, itv, f"你们这个调查真是傻逼 {n}")
        chat = _chat(org, itv)
        assert "傻逼" not in " ".join(m["text"] for m in chat) and "***" in chat[-2]["text"]
        assert Z.conduct_reminder.split("，")[0] in chat[-1]["text"]
        alert, reason, strikes, finished = _db_alert(itv)
        assert alert == "yellow" and strikes == n and not finished
    _say(org, itv, "傻逼傻逼傻逼")
    chat = _chat(org, itv)
    assert chat[-1]["text"] == Z.conduct_end
    alert, _, strikes, finished = _db_alert(itv)
    assert strikes == 3 and finished
    assert _say(org, itv, "还有话说").status_code == 409  # the chat is closed


def test_hate_or_a_threat_raises_a_red_alert_for_the_team_without_the_words(room):
    org, itv, rec = room
    _say(org, itv, "你们这帮黑鬼公司")
    alert, reason, strikes, _ = _db_alert(itv)
    assert alert == "red" and "歧视" in reason and "黑鬼" not in reason and strikes == 1
    with SessionLocal() as db:
        logged = [a for a in db.scalars(select(AuditLog)) if a.action == "content.hidden"]
        assert logged and "hate" in json.dumps([a.meta for a in logged], ensure_ascii=False) and "黑鬼" not in json.dumps([a.meta for a in logged], ensure_ascii=False)
    assert "黑鬼" not in " ".join(m["text"] for m in _chat(org, itv))


def test_while_a_human_has_taken_over_the_message_is_still_hidden_the_ai_stays_silent_and_the_team_is_told(room):
    org, itv, rec = room
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("moderator")).status_code in (200, 204)
    n_before = len(_chat(org, itv))
    _say(org, itv, f"傻逼，打电话 {PHONE}")
    chat = _chat(org, itv)
    assert len(chat) == n_before + 1 and chat[-1]["sender"] == "participant" and PHONE not in chat[-1]["text"] and "傻逼" not in chat[-1]["text"]
    assert _db_alert(itv)[0] == "yellow"


def test_the_transcript_made_from_the_chat_never_contains_the_hidden_words(room):
    org, itv, rec = room
    _say(org, itv, f"打我电话 {PHONE}，傻逼广告")
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    with SessionLocal() as db:
        text = " ".join(s.text_orig for s in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv["id"])))
    assert text and PHONE not in text and "傻逼" not in text


def test_ordinary_answers_pass_through_untouched_and_still_reach_the_model(room):
    org, itv, rec = room
    before = len(rec.sent)
    _say(org, itv, "我平时先看成分，价格 5000 块以内的我都能接受，牛逼的产品我愿意多花钱")
    assert len(rec.sent) > before and "牛逼的产品" in " ".join(rec.sent[before:])
    assert _db_alert(itv)[0] == "none"


# ---------- live captions (a group, a depth interview) ----------
def test_a_caption_with_contact_details_or_abuse_is_stored_hidden_and_flagged_and_the_moderator_gets_a_card(org):
    iid = _live_group(org, [])
    # write the captions the way the browsers do: through the server's own store function
    from app.routers import concept as C

    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        C._store_caption(db, itv, "p2", C.CaptionIn(text="你可以打我电话 13812345678 联系我", final=True, dur_ms=3000))
        C._store_caption(db, itv, "p3", C.CaptionIn(text="这个广告太傻逼了", final=True, dur_ms=2000))
        C._store_caption(db, itv, "p1", C.CaptionIn(text="我觉得质地很清爽，价格也合理", final=True, dur_ms=3000))
        rows = list(db.scalars(select(CaptionLine).where(CaptionLine.interview_id == iid).order_by(CaptionLine.seq)))
    assert [r.flags for r in rows] == ["contact", "abuse", ""]
    assert "13812345678" not in rows[0].text and "[联系方式已隐藏]" in rows[0].text and "傻逼" not in rows[1].text and rows[2].text == "我觉得质地很清爽，价格也合理"
    cards = _cues(org, iid).json()["cards"]
    assert cards and cards[0]["kind"] == "compliance" and "联系方式" in cards[0]["text"] and "13812345678" not in json.dumps(cards, ensure_ascii=False)
    shown = org.c.get(f"/api/interviews/{iid}/captions?after=0", headers=org.h("researcher")).json()["lines"]
    assert all("13812345678" not in c["text"] and "傻逼" not in c["text"] for c in shown)


# ---------- pre-interview answers and uploaded transcripts ----------
def test_pre_interview_answers_are_hidden_too(org):
    from test_pretask import join_info, set_pretask, submit
    from test_week1 import give_consent, let_in

    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "你最近一次买精华的经历？", "required": True}])
    give_consent(org, itv)
    let_in(org, itv)
    key = join_info(org, itv)["pretask"]["questions"][0]["key"]
    assert submit(org, itv, {key: "加我微信 abc12345，傻逼"}).status_code == 201
    with SessionLocal() as db:
        answers = [a.text for a in db.scalars(select(PreTaskAnswer))]
    assert answers and "abc12345" not in answers[0] and "傻逼" not in answers[0]


def test_an_uploaded_transcript_is_hidden_as_well(org):
    itv = org.interview()
    body = {"segments": [{"idx": 0, "speaker": "participant", "start_ms": 0, "end_ms": 4000, "text_orig": f"打电话 {PHONE} 吧，傻逼", "text_zh": "", "lang": "zh"}]}
    r = org.c.post(f"/api/interviews/{itv['id']}/transcript", headers=org.h(), json=body)
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        t = db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv["id"])).one().text_orig
    assert PHONE not in t and "傻逼" not in t and "打电话" in t
