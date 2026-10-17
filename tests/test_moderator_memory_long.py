"""Memory in a two-hour interview (user, 2026-10-17): what the participant said at minute 5 must still reach the model at minute 100.

Three pieces keep it: key facts (taken from the analysis call, kept all interview), a rolling summary (refreshed as lines leave the
window), and recall (older lines that match what is being discussed now come back word for word). The model sees a bounded amount.
"""
import json

from app.moderator.clock import ManualClock
from app.moderator.constants import FACT_CARD_CHARS, FACT_CARDS_MAX, FACTS_CONTEXT_CHARS, SUMMARY_EVERY_TURNS, SUMMARY_MAX_CHARS, SUMMARY_RETRY_AFTER_TURNS, Phase, Speaker
from app.moderator.engine import ModeratorEngine
from app.moderator.knowledge import MustCoverItem
from app.moderator.memory import ConversationMemory
from app.moderator.providers import ChatError, MockModeratorChat
from app.moderator.recall import recall
from tests.test_moderator_engine import GUIDE, NO_DRY_RULE, PROMPTS, SHALLOW, say

ALLERGY = "我对香精过敏，用过一次就起了红疹"
ALLERGY_FACT = "对香精过敏，用过会起红疹"


# ---------- recall ----------
LINES = [(3, ALLERGY), (5, "平时在超市买，很少网购"), (7, "预算大概一百块以内")]


def test_recall_brings_back_the_old_line_about_the_same_thing():
    assert recall("这个产品有香味吗，香精会不会让我过敏", LINES) == [(3, ALLERGY)]


def test_recall_brings_back_nothing_when_the_subject_is_new():
    assert recall("今天的天气怎么样", LINES) == []
    assert recall("", LINES) == []


def test_recall_works_for_english_too():
    old = [(1, "I am allergic to fragrance, it gave me a rash"), (2, "I usually buy in the supermarket")]
    assert recall("is there any fragrance in this cream", old) == [(1, old[0][1])]


# ---------- the fact list ----------
def test_facts_are_deduplicated_cut_and_capped():
    m = ConversationMemory()
    assert m.add_facts([ALLERGY_FACT, ALLERGY_FACT, "  ", "x" * 500], 60) == 2
    assert all(len(f) <= FACT_CARD_CHARS for _, f in m.facts)
    m.add_facts([f"事实{i}" for i in range(FACT_CARDS_MAX + 20)], 120)
    assert len(m.facts) == FACT_CARDS_MAX and m.facts[-1][1] == f"事实{FACT_CARDS_MAX + 19}"


def test_facts_shown_to_the_model_are_bounded_and_keep_the_newest():
    m = ConversationMemory()
    for i in range(FACT_CARDS_MAX):
        m.add_facts([f"第{i}条事实" + "字" * 70], i * 60)
    shown = m.facts_digest()
    assert sum(len(x) for x in shown) <= FACTS_CONTEXT_CHARS + 90 and shown[-1].endswith("字" * 70) and f"第{FACT_CARDS_MAX - 1}条" in shown[-1]


def test_the_summary_is_cut_and_only_due_after_enough_lines_leave_the_window():
    m = ConversationMemory(recent_turns_verbatim=4)
    for i in range(4 + SUMMARY_EVERY_TURNS - 1):
        m.add_turn(Speaker.PARTICIPANT, f"第{i}句", float(i))
    assert not m.summary_due()
    m.add_turn(Speaker.PARTICIPANT, "再来一句", 99.0)
    assert m.summary_due()
    m.set_summary("长" * (SUMMARY_MAX_CHARS + 500), len(m.turns_to_summarize()))
    assert len(m.summary) == SUMMARY_MAX_CHARS and not m.summary_due()


# ---------- a whole long interview through the engine ----------
class LongInterview:
    """A scripted model that records what it was shown; the participant states one fact early and the interview runs ~100 exchanges."""

    def __init__(self, summary_fails: bool = False) -> None:
        self.summary_fails = summary_fails
        self.shown: list[dict] = []     # the speaking calls' user messages, in order
        self.summary_calls = 0
        self.chat = MockModeratorChat(self._reply)

    def _reply(self, messages: list[dict]) -> dict:
        system, user = messages[0]["content"], json.loads(messages[-1]["content"])
        if system == PROMPTS.analyze:
            facts = [ALLERGY_FACT] if ALLERGY in user["ANSWER"] else []
            return {**SHALLOW, "new_facts": facts, "question_answered": False, "category": "answered"}
        if system == PROMPTS.summarize:
            self.summary_calls += 1
            if self.summary_fails:
                raise ChatError("summary service down")
            return {"summary": "已聊过：渠道与购买习惯。" + "；".join(x["text"][:12] for x in user["NEW_LINES"] if x["speaker"] == "participant")[:300]}
        self.shown.append(user)
        return say(f"关于第{len(self.shown)}个话题，你还有什么想补充的细节？")


def long_guide():
    """A guide with enough topics for a two-hour interview (the shipped one has two)."""
    base = GUIDE.topics[0]
    topics = [base.model_copy(update={"id": f"t{i}", "title": f"第{i}个方面", "questions": [f"你在第{i}个方面有过什么使用经历？"],
                                      "must_cover": [MustCoverItem(id=f"m{i}", text=f"第{i}个方面的使用场景")]}) for i in range(24)]
    return GUIDE.model_copy(update={"duration_min": 180, "topics": topics})


def run_interview(model: LongInterview, exchanges: int, final_answer: str) -> ModeratorEngine:
    guide = long_guide()
    clock = ManualClock()
    engine = ModeratorEngine(guide, model.chat, prompts=PROMPTS, config=NO_DRY_RULE, clock=clock)
    engine.open()
    engine.handle_participant("好的，可以开始")
    for i in range(exchanges):
        clock.advance(60)
        answer = ALLERGY if i == 2 else f"第{i}回，我一般先看网上的评价再决定要不要买，因为担心{i}号成分不合适"
        engine.handle_participant(answer)
        assert engine.fsm.phase is not Phase.ENDED
    clock.advance(60)
    engine.handle_participant(final_answer)
    return engine


def test_the_early_fact_and_the_old_line_reach_the_model_at_minute_100():
    model = LongInterview()
    run_interview(model, 100, "你们这个产品的香精味道重不重，会不会让人过敏")
    last = model.shown[-1]
    assert any(ALLERGY_FACT in f for f in last["KEY_FACTS"])  # the fact stated at minute 3 is still in front of the model
    assert any(ALLERGY in r for r in last["RECALLED_EARLIER"])  # and so are the participant's own words
    assert ALLERGY not in json.dumps(last["RECENT_TURNS"], ensure_ascii=False)  # they are far outside the verbatim window
    assert model.summary_calls >= 5 and last["SUMMARY"]  # the summary was refreshed along the way


def test_what_the_model_sees_does_not_grow_with_the_length_of_the_interview():
    model = LongInterview()
    run_interview(model, 100, "这个产品适合敏感肌吗")
    sizes = [len(json.dumps(u, ensure_ascii=False)) for u in model.shown]
    early, late = max(sizes[30:40]), max(sizes[-10:])
    assert late < early * 1.35 and late < 14000


def test_a_failing_summary_service_does_not_break_the_interview_or_hammer_the_service():
    model = LongInterview(summary_fails=True)
    engine = run_interview(model, 60, "这个产品适合敏感肌吗")
    assert engine.memory.summary == "" and engine.memory.facts  # the rule-based digest and the facts still work
    assert model.summary_calls <= 2 * (2 * 60 // SUMMARY_RETRY_AFTER_TURNS + 2)  # a try (2 attempts) only every few lines, not every turn
