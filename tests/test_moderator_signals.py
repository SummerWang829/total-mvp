"""The signals reach the AI model, and the AI never tells a participant it noticed anything about them (red line H14)."""
import json

import pytest

from app import ai_room, signals
from app.db import SessionLocal
from app.models import AiSession, ChatMessage
from app.moderator.constants import RedLine
from app.moderator.guardrails import GuardrailContext, check_utterance
from conftest import Org
from test_ai_room import _participant, _say, _start, _walk_into_the_room
from test_content_rules_flow import Recorder
from test_moderator_engine import ANSWER, GUIDE, say, started

HARMLESS = [
    "您对价格担心吗？", "这个味道您觉得怎么样？", "您当时有什么顾虑？", "Are you worried about the price?", "How did you feel about the packaging?",
    "您更在意价格还是成分？", "What made you hesitate before buying it?", "您会因为什么犹豫？", "Would you say the cream felt comfortable on your skin?",
    "如果慢慢想，您觉得哪一点最重要？", "Take your time. What matters most to you here?", "不着急，您可以慢慢说。您最近买过什么？",
]
CLAIMS = [
    "I noticed your voice got quieter. What did you buy?", "You sound nervous. How do you choose a serum?", "我注意到您停顿了很久。您更看重什么？", "您听起来有点紧张。您最近买过什么？",
    "I can tell you are hesitant. Which brand did you pick?", "您的语速突然变慢了。是不是有什么顾虑？",
]


def _h14(text):
    return [v for v in check_utterance(text, GuardrailContext(guide=GUIDE)) if v.red_line is RedLine.H14_NO_FEELING_CLAIMS]


@pytest.mark.parametrize("text", HARMLESS)
def test_asking_about_worry_or_hesitation_in_the_participants_own_terms_is_fine(text):
    assert not _h14(text)


@pytest.mark.parametrize("text", CLAIMS)
def test_claiming_to_have_noticed_how_the_participant_sounds_or_feels_is_not(text):
    assert _h14(text)


def test_the_model_sees_the_hints_in_both_calls_and_not_when_there_are_none():
    e, model = started([ANSWER], [say("您平时在哪里买精华？")])
    e.handle_participant("我真的很失望，这个太糟糕了", ["the words are clearly negative (strong)", "much shorter than this person's usual answer"])
    for call in (model.analysis_calls[-1], model.draft_calls[-1]):
        assert json.loads(call[-1]["content"])["PARTICIPANT_SIGNALS"] == ["the words are clearly negative (strong)", "much shorter than this person's usual answer"]
    e.handle_participant("我一般在屈臣氏买")
    assert json.loads(model.draft_calls[-1][-1]["content"])["PARTICIPANT_SIGNALS"] == []   # hints belong to one answer only


def test_at_most_three_hints_are_passed_on():
    e, model = started([ANSWER], [say("您平时在哪里买精华？")])
    e.handle_participant("好的", ["a", "b", "c", "d", "e"])
    assert json.loads(model.draft_calls[-1][-1]["content"])["PARTICIPANT_SIGNALS"] == ["a", "b", "c"]


def test_a_draft_that_claims_to_have_noticed_the_participants_mood_is_regenerated():
    e, model = started([ANSWER], [say("我注意到您的声音变小了。您平时在哪里买精华？"), say("不着急，您可以慢慢说。您平时在哪里买精华？")])
    out = e.handle_participant("嗯……我想想", ["took much longer than usual to answer"])
    assert out.attempts == 2 and out.rejected[0][0].red_line is RedLine.H14_NO_FEELING_CLAIMS and "声音" not in out.spoken_response


def test_the_prompt_tells_the_model_to_use_the_hints_quietly():
    from app.moderator.prompts import load_prompts

    p = load_prompts()
    assert "PARTICIPANT_SIGNALS" in p.speak and "NEVER mention" in p.speak and "PARTICIPANT_SIGNALS" in p.analyze


# ---------- the hints a typed answer gets ----------
def test_typed_hints_compare_with_the_persons_own_earlier_answers():
    usual = [60, 72, 55, 68, 64]
    assert "much shorter than this person's usual answer" in signals.chat.hints("还行吧", usual)
    assert signals.chat.hints("还行吧", usual[:2]) == []   # too few earlier answers to know what is usual
    assert "took much longer than usual to answer" in signals.chat.hints("我想想", usual, 60.0, [8.0, 9.0, 7.0, 10.0, 8.0])
    assert "took much longer than usual to answer" not in signals.chat.hints("我想想", usual, 12.0, [8.0, 9.0, 7.0, 10.0, 8.0])
    assert signals.chat.hints("我真的很失望，完全不值", usual)[0] == "the words are clearly negative (strong)" or "negative" in signals.chat.hints("我真的很失望，完全不值", usual)[0]
    assert signals.chat.hints("这个我很喜欢！", usual + [10])[0].startswith("the words are clearly positive")
    assert any("😕" in h for h in signals.chat.hints("嗯", usual, reactions=["😕", "😕"])) and len(signals.chat.hints("x", usual, reactions=["👍", "❤️", "😊", "😢"])) == 3


# ---------- in the live chat ----------
@pytest.fixture
def room(org, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(ai_room, "_chat_model", lambda: rec)
    itv = _walk_into_the_room(org, language="zh")
    assert _start(org, itv).status_code == 200
    return org, itv, rec


def _signals_sent(rec):
    """PARTICIPANT_SIGNALS of the last speaking call the model received."""
    for raw in reversed(rec.sent):
        for msg in json.loads(raw):
            if msg["role"] == "user":
                data = json.loads(msg["content"])
                if "PLAN" in data:
                    return data["PARTICIPANT_SIGNALS"]
    return None


def test_a_negative_typed_answer_reaches_the_model_with_a_hint_and_the_participant_never_sees_it(room):
    org, itv, rec = room
    _say(org, itv, "好的，可以开始")
    _say(org, itv, "我真的很失望，这个产品太糟糕了，完全不值")
    assert _signals_sent(rec) and "negative" in _signals_sent(rec)[0]
    shown = json.dumps(_participant(org, itv)["messages"], ensure_ascii=False)
    assert "PARTICIPANT_SIGNALS" not in shown and "negative" not in shown   # the participant's chat never carries a hint


def test_a_reaction_sent_since_the_last_question_is_passed_on_too(room):
    org, itv, rec = room
    _say(org, itv, "好的，可以开始")
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/reaction", json={"emoji": "😕"}).status_code == 201
    _say(org, itv, "嗯，我平时一般先看成分表再决定买不买")
    assert any("😕" in h for h in _signals_sent(rec))
