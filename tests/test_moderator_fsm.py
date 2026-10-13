"""Sprint 1: the AI moderator's state machine, time budget, saturation, follow-up cap, overtime, missed items, context."""
import json
import logging
import pathlib

import pytest
from pydantic import ValidationError

from app.moderator.clock import ManualClock
from app.moderator.constants import CONTEXT_ASKED_QUESTIONS, DIGEST_MAX_CHARS, EndReason, PacingAction, Phase, Speaker, TransitionReason
from app.moderator.fsm import (ALLOWED_TRANSITIONS, IllegalTransitionError, InterviewStateError, InterviewStateMachine,
                               TurnUpdate)
from app.moderator.knowledge import InterviewGuide
from app.moderator.memory import ConversationMemory
from app.moderator.pacing import PacingConfig, TimeBudget

GUIDE_FILE = pathlib.Path("eval/guides/sg_skincare_zh.json")
MIN = 60.0


def guide(**changes) -> InterviewGuide:
    data = json.loads(GUIDE_FILE.read_text(encoding="utf-8"))
    data.update(changes)
    return InterviewGuide.model_validate(data)


def machine(g: InterviewGuide | None = None, **cfg) -> tuple[InterviewStateMachine, ManualClock]:
    clock = ManualClock()
    return InterviewStateMachine(g or guide(), PacingConfig(**cfg), clock), clock


def to_core(fsm: InterviewStateMachine) -> None:
    """Start, say ready, answer both warm-up questions."""
    fsm.start()
    fsm.participant_ready()
    fsm.record_turn(TurnUpdate(question_answered=True))
    d = fsm.record_turn(TurnUpdate(question_answered=True))
    assert d.phase is Phase.CORE and d.topic_id == "discovery"


ANSWERED = TurnUpdate(question_answered=True)
STAY = TurnUpdate()


# ---------- the project guide ----------
def test_demo_guide_is_valid():
    g = guide()
    assert g.language == "zh" and [t.id for t in g.topics] == ["discovery", "barriers"]
    assert g.topic("barriers").weight == 1.2
    with pytest.raises(KeyError):
        g.topic("nope")


@pytest.mark.parametrize("change", [
    {"language": "jp"},                                   # Singapore first: en/zh/ms/ta only
    {"forbidden_topics": ["政治", "  "]},                 # a blank forbidden topic would match everything
    {"topics": []},
    {"warmup_questions": []},
    {"duration_min": 5},
])
def test_bad_guides_are_refused(change):
    with pytest.raises(ValidationError):
        guide(**change)


def test_duplicate_ids_are_refused():
    data = json.loads(GUIDE_FILE.read_text(encoding="utf-8"))
    data["topics"][1]["must_cover"][0]["id"] = "d_channels"
    with pytest.raises(ValidationError):
        InterviewGuide.model_validate(data)
    data = json.loads(GUIDE_FILE.read_text(encoding="utf-8"))
    data["topics"][1]["id"] = "discovery"
    with pytest.raises(ValidationError):
        InterviewGuide.model_validate(data)


# ---------- time budget ----------
def test_budget_for_60_minutes_without_stimuli():
    b = TimeBudget.for_guide(guide())
    assert b.total_s == 60 * MIN
    assert b.phase_s[Phase.INTRO] == 4 * MIN and b.phase_s[Phase.WARMUP] == 6.5 * MIN and b.phase_s[Phase.WRAPUP] == 4 * MIN
    assert b.phase_s[Phase.STIMULI] == 0  # no materials: the time goes to the core
    assert b.phase_s[Phase.CORE] == pytest.approx((60 - 4 - 6.5 - 1.5 - 4) * MIN)
    assert b.topic_s["barriers"] / b.topic_s["discovery"] == pytest.approx(1.2)  # split by weight
    assert sum(b.topic_s.values()) == pytest.approx(b.phase_s[Phase.CORE])


def test_budget_scales_with_planned_length():
    half = TimeBudget.for_guide(guide(duration_min=30))
    full = TimeBudget.for_guide(guide())
    for phase in (Phase.INTRO, Phase.WARMUP, Phase.WRAPUP, Phase.CORE):
        assert half.phase_s[phase] == pytest.approx(full.phase_s[phase] / 2)


def test_stimuli_get_time_when_there_are_materials():
    g = guide(stimuli=[{"id": "s1", "description": "包装照片", "questions": ["看到这个包装，您的第一感觉是什么？"]}])
    assert TimeBudget.for_guide(g).phase_s[Phase.STIMULI] == 11 * MIN


# ---------- the transition gate ----------
@pytest.mark.parametrize("src, dst", [
    (Phase.INTRO, Phase.CORE), (Phase.INTRO, Phase.RECOVERY), (Phase.WARMUP, Phase.INTRO), (Phase.CORE, Phase.WARMUP),
    (Phase.WRAPUP, Phase.CORE), (Phase.RECOVERY, Phase.CORE), (Phase.STIMULI, Phase.CORE), (Phase.ENDED, Phase.INTRO),
    (Phase.ENDED, Phase.WRAPUP),
])
def test_illegal_transitions_are_blocked(src, dst):
    assert InterviewStateMachine.can_transition(src, dst) is False
    fsm, _ = machine()
    fsm._phase = src  # put the machine in `src` directly to exercise the single gate
    with pytest.raises(IllegalTransitionError):
        fsm._transition(dst, TransitionReason.IN_PROGRESS)
    assert fsm.phase is src  # nothing changed


def test_every_running_phase_can_stop_and_be_wrapped_up():
    for phase, targets in ALLOWED_TRANSITIONS.items():
        if phase is not Phase.ENDED:
            assert Phase.ENDED in targets
        if phase not in (Phase.ENDED, Phase.WRAPUP):
            assert Phase.WRAPUP in targets  # forced wrap-up on overtime from anywhere
    assert ALLOWED_TRANSITIONS[Phase.ENDED] == frozenset()


def test_actions_out_of_order_are_refused():
    fsm, _ = machine()
    with pytest.raises(InterviewStateError):
        fsm.record_turn(STAY)  # before start
    with pytest.raises(InterviewStateError):
        fsm.participant_ready()
    fsm.start()
    with pytest.raises(InterviewStateError):
        fsm.start()
    fsm.participant_ready()
    with pytest.raises(InterviewStateError):
        fsm.participant_ready()  # only from intro
    fsm.stop(EndReason.PARTICIPANT_STOPPED)
    for act in (lambda: fsm.record_turn(STAY), lambda: fsm.stop(EndReason.CRISIS)):
        with pytest.raises(InterviewStateError):
            act()


# ---------- intro and warm-up ----------
def test_intro_waits_for_the_participant():
    fsm, clock = machine()
    d = fsm.start()
    assert d.action is PacingAction.ENTER_PHASE and d.phase is Phase.INTRO and not d.followup_allowed
    clock.advance(10 * MIN)
    assert fsm.record_turn(ANSWERED).action is PacingAction.CONTINUE and fsm.phase is Phase.INTRO
    d = fsm.participant_ready()
    assert d.phase is Phase.WARMUP and d.question == guide().warmup_questions[0]


def test_warmup_walks_its_questions_then_enters_the_first_topic():
    fsm, _ = machine()
    fsm.start(); fsm.participant_ready()
    assert fsm.record_turn(STAY).action is PacingAction.CONTINUE
    d = fsm.record_turn(ANSWERED)
    assert d.action is PacingAction.NEXT_QUESTION and d.question == guide().warmup_questions[1]
    d = fsm.record_turn(ANSWERED)
    assert d.action is PacingAction.ENTER_PHASE and d.phase is Phase.CORE and d.question == guide().topics[0].questions[0]


def test_warmup_ends_when_its_time_is_up():
    fsm, clock = machine()
    fsm.start(); fsm.participant_ready()
    clock.advance(6.5 * MIN)
    d = fsm.record_turn(STAY)
    assert d.phase is Phase.CORE and d.reason is TransitionReason.PHASE_TIME_UP


# ---------- follow-up cap ----------
def test_three_follow_ups_per_question_then_move_on():
    fsm, _ = machine()
    to_core(fsm)
    for used in range(1, 3):
        d = fsm.record_turn(TurnUpdate(was_followup=True))
        assert d.action is PacingAction.CONTINUE and fsm.snapshot().followups_used == used and d.followup_allowed
    d = fsm.record_turn(TurnUpdate(was_followup=True))  # the third follow-up has been answered
    assert d.action is PacingAction.NEXT_QUESTION and d.reason is TransitionReason.FOLLOWUP_LIMIT
    assert d.question == guide().topics[0].questions[1] and d.followup_allowed and fsm.snapshot().followups_used == 0


def test_follow_up_cap_is_configurable_and_never_exceeded():
    fsm, _ = machine(max_followups_per_question=1)
    to_core(fsm)
    assert fsm.followup_allowed()
    d = fsm.record_turn(TurnUpdate(was_followup=True))
    assert d.reason is TransitionReason.FOLLOWUP_LIMIT


# ---------- saturation, turn and time limits ----------
def test_saturation_threshold_decides_the_transition():
    fsm, _ = machine()
    to_core(fsm)
    assert fsm.record_turn(TurnUpdate(saturation=0.79)).action is PacingAction.CONTINUE
    d = fsm.record_turn(TurnUpdate(saturation=0.8))
    assert d.action is PacingAction.NEXT_TOPIC and d.reason is TransitionReason.SATURATED and d.topic_id == "barriers"
    assert d.question == guide().topics[1].questions[0]


def test_topic_turn_limit():
    fsm, _ = machine(max_turns_per_topic=4)
    to_core(fsm)
    reasons = [fsm.record_turn(STAY).reason for _ in range(4)]
    assert reasons[-1] is TransitionReason.TOPIC_TURN_LIMIT and fsm.snapshot().topic_id == "barriers"


def test_topic_time_limit():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(fsm.budget.topic_s["discovery"])
    d = fsm.record_turn(STAY)
    assert d.reason is TransitionReason.TOPIC_TIME_UP and d.topic_id == "barriers"


def test_topic_ends_when_questions_and_items_are_used_up():
    fsm, _ = machine()
    to_core(fsm)
    fsm.record_turn(ANSWERED)
    fsm.record_turn(TurnUpdate(question_answered=True, covered_item_ids=["d_channels", "d_trust"]))  # -> probe d_price
    d = fsm.record_turn(ANSWERED)  # d_price asked, nothing left in this topic
    assert d.reason is TransitionReason.QUESTIONS_DONE and d.topic_id == "barriers"


# ---------- missed items, wrap-up, end ----------
def _finish_core(fsm, covered=()):
    to_core(fsm)
    fsm.record_turn(TurnUpdate(saturation=0.9, covered_item_ids=list(covered)))
    return fsm.record_turn(TurnUpdate(saturation=0.9))


def test_missed_items_are_asked_once_before_wrap_up():
    fsm, _ = machine()
    d = _finish_core(fsm, covered=["d_channels", "d_trust", "d_price", "b_authentic", "b_cert"])
    assert d.action is PacingAction.ASK_MISSED_ITEM and d.phase is Phase.RECOVERY and d.missed_item.id == "b_return"
    assert not d.followup_allowed  # asked once, no follow-ups
    d = fsm.record_turn(STAY)
    assert d.missed_item.id == "b_delivery"
    d = fsm.record_turn(STAY)
    assert d.phase is Phase.WRAPUP and d.action is PacingAction.ENTER_PHASE
    d = fsm.record_turn(STAY)
    assert d.action is PacingAction.END and d.end_reason is EndReason.COMPLETED and fsm.phase is Phase.ENDED


def test_items_covered_meanwhile_are_not_asked_again():
    fsm, _ = machine()
    d = _finish_core(fsm, covered=["d_channels", "d_trust", "d_price", "b_authentic", "b_cert"])
    assert d.missed_item.id == "b_return"
    d = fsm.record_turn(TurnUpdate(covered_item_ids=["b_return", "b_delivery"]))  # the answer covered both
    assert d.phase is Phase.WRAPUP and fsm.missed_items() == []


def test_nothing_missed_goes_straight_to_wrap_up():
    fsm, _ = machine()
    all_items = [i.id for t in guide().topics for i in t.must_cover]
    d = _finish_core(fsm, covered=all_items)
    assert d.phase is Phase.WRAPUP and d.reason is TransitionReason.SATURATED


def test_stimuli_come_after_the_core():
    g = guide(stimuli=[{"id": "s1", "description": "包装照片", "questions": ["看到这个包装，您的第一感觉是什么？", "会想买吗？"]}])
    fsm, _ = machine(g)
    d = _finish_core(fsm)
    assert d.phase is Phase.STIMULI and d.question == "看到这个包装，您的第一感觉是什么？"
    assert fsm.record_turn(ANSWERED).question == "会想买吗？"
    assert fsm.record_turn(ANSWERED).phase is Phase.RECOVERY


def test_overtime_forces_wrap_up_without_chasing_missed_items():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(60 * MIN * 1.15)
    d = fsm.record_turn(STAY)
    assert d.phase is Phase.WRAPUP and d.reason is TransitionReason.OVERTIME and fsm.missed_items()  # items remain missed
    assert fsm.snapshot().overtime


@pytest.mark.parametrize("reason, why", [(EndReason.CRISIS, TransitionReason.CRISIS),
                                         (EndReason.PARTICIPANT_STOPPED, TransitionReason.PARTICIPANT_STOPPED)])
def test_stop_ends_at_once_from_any_phase(reason, why):
    for steps in (0, 1, 3):
        fsm, _ = machine()
        fsm.start()
        if steps:
            fsm.participant_ready()
        for _ in range(steps - 1):
            fsm.record_turn(ANSWERED)
        d = fsm.stop(reason)
        assert d.action is PacingAction.END and fsm.phase is Phase.ENDED and fsm.end_reason is reason and d.reason is why


def test_unknown_item_ids_from_the_model_are_ignored_and_logged(caplog):
    fsm, _ = machine()
    to_core(fsm)
    with caplog.at_level(logging.WARNING):
        fsm.record_turn(TurnUpdate(covered_item_ids=["d_trust", "made_up"]))
    assert "unknown must-cover" in caplog.text and [i.id for i in fsm.missed_items()][:2] == ["d_channels", "d_price"]


def test_model_scores_are_validated():
    with pytest.raises(ValidationError):
        TurnUpdate(saturation=1.5)
    with pytest.raises(ValidationError):
        TurnUpdate(saturation=-0.1)


# ---------- clock, snapshot, transition log ----------
def test_snapshot_shows_the_clock_and_position():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(5 * MIN)
    fsm.record_turn(TurnUpdate(was_followup=True, saturation=0.4, covered_item_ids=["d_channels"]))
    s = fsm.snapshot()
    assert s.phase is Phase.CORE and s.topic_title == "发现与评估新品牌" and s.elapsed_s == 5 * MIN
    assert s.remaining_s == 55 * MIN and s.topic_elapsed_s == 5 * MIN and s.saturation == 0.4
    assert (s.followups_used, s.followups_left) == (1, 2)
    assert s.uncovered_items == ["判断可信度的依据", "对价格的预期"] and not s.overtime


def test_every_phase_change_is_logged_with_a_reason():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(30)
    fsm.record_turn(TurnUpdate(saturation=0.85))
    log = [(t.from_phase, t.to_phase, t.reason, t.topic_id) for t in fsm.transitions]
    assert log == [(Phase.INTRO, Phase.WARMUP, TransitionReason.PARTICIPANT_READY, None),
                   (Phase.WARMUP, Phase.CORE, TransitionReason.QUESTIONS_DONE, "discovery"),
                   (Phase.CORE, Phase.CORE, TransitionReason.SATURATED, "barriers")]
    assert fsm.transitions[-1].at_s == 30


def test_manual_clock_never_runs_backwards():
    with pytest.raises(ValueError):
        ManualClock().advance(-1)


def _simulate_hour(turn_s: float, saturation_step: float = 0.06) -> tuple[InterviewStateMachine, ManualClock]:
    """A whole 60-minute interview: every answer takes turn_s seconds, topic saturation rises by saturation_step a turn."""
    fsm, clock = machine()
    fsm.start()
    clock.advance(3 * MIN)
    fsm.participant_ready()
    saturation, turns = 0.0, 0
    while fsm.phase is not Phase.ENDED:
        clock.advance(turn_s)
        turns += 1
        saturation = 0.0 if fsm.phase is not Phase.CORE else min(1.0, saturation + saturation_step)
        d = fsm.record_turn(TurnUpdate(saturation=saturation, was_followup=fsm.followup_allowed()))
        if d.action is PacingAction.NEXT_TOPIC:
            saturation = 0.0
        assert turns < 300, "the interview must always end"
    return fsm, clock


def test_talkative_participant_ends_within_the_time_gate():
    fsm, clock = _simulate_hour(turn_s=120)
    assert 0.85 * 60 * MIN <= clock.now() <= 1.15 * 60 * MIN
    assert all(t.reason is not TransitionReason.OVERTIME for t in fsm.transitions)  # planned, not forced


def test_brief_participant_never_runs_over_and_topics_use_their_items():
    fsm, clock = _simulate_hour(turn_s=40)
    assert clock.now() <= 60 * MIN
    reasons = [t.reason for t in fsm.transitions]
    assert TransitionReason.TOPIC_TIME_UP not in reasons and TransitionReason.PHASE_TIME_UP not in reasons  # no topic was cut short for time


def test_no_item_is_asked_twice():
    fsm, _ = machine(max_turns_per_topic=100)
    to_core(fsm)
    asked = []
    while fsm.phase is not Phase.ENDED:
        d = fsm.record_turn(ANSWERED)
        if d.missed_item:
            asked.append(d.missed_item.id)
    assert len(asked) == len(set(asked)) == 7  # every item asked exactly once (probe in topic, none left for recovery)


def test_probing_moves_on_when_the_item_gets_covered():
    fsm, _ = machine()
    to_core(fsm)
    fsm.record_turn(ANSWERED)
    d = fsm.record_turn(ANSWERED)  # both prepared questions answered: explore uncovered items
    assert d.action is PacingAction.PROBE_ITEM and d.missed_item.id == "d_channels" and d.followup_allowed
    assert fsm.record_turn(STAY).action is PacingAction.CONTINUE
    d = fsm.record_turn(TurnUpdate(covered_item_ids=["d_channels"]))
    assert d.action is PacingAction.PROBE_ITEM and d.reason is TransitionReason.ITEM_COVERED and d.missed_item.id == "d_trust"


# ---------- conversation memory / context ----------
def _memory_with(n: int, window: int = 4) -> ConversationMemory:
    m = ConversationMemory(recent_turns_verbatim=window)
    for i in range(n):
        m.add_turn(Speaker.AI if i % 2 == 0 else Speaker.PARTICIPANT, f"line {i}", at_s=float(i))
    return m


def test_context_carries_whispers_once_and_marks_hypotheses_internal():
    fsm, _ = machine()
    d = fsm.start()
    m = _memory_with(2)
    m.add_whisper("  问问她退货遇到过什么问题  ", at_s=2)
    ctx = m.build_context(guide(), fsm.snapshot(), d)
    assert ctx.whispers == ["问问她退货遇到过什么问题"] and ctx.hypotheses_internal == guide().hypotheses
    assert ctx.forbidden_topics == ["政治", "宗教信仰本身的评价", "收入具体数字"] and ctx.brand_disclosure_allowed is False
    assert m.build_context(guide(), fsm.snapshot(), d).whispers == []  # a whisper is used once
    assert len(m.whisper_log) == 1  # but kept for the audit trail


def test_long_whispers_and_blank_lines_are_refused():
    m = ConversationMemory()
    with pytest.raises(ValidationError):
        m.add_whisper("x" * 501, at_s=0)
    with pytest.raises(ValidationError):
        m.add_turn(Speaker.PARTICIPANT, "   ", at_s=0)


def test_turns_must_be_in_time_order():
    m = _memory_with(2)
    with pytest.raises(ValueError):
        m.add_turn(Speaker.PARTICIPANT, "late", at_s=0.5)


def test_older_turns_are_condensed_and_the_context_has_a_bounded_size():
    fsm, _ = machine()
    d = fsm.start()
    m = _memory_with(10, window=4)
    assert [t.index for t in m.turns_to_summarize()] == list(range(6))
    ctx = m.build_context(guide(), fsm.snapshot(), d)
    assert [t.index for t in ctx.recent_turns] == [6, 7, 8, 9]  # only the window is word for word
    assert [line.split(": ", 1)[1] for line in ctx.summary.split("\n")] == ["line 1", "line 3", "line 5"]  # older participant answers, one short line each
    m.set_summary("受访者常在 TikTok 看评价。", upto_index=6)
    ctx = m.build_context(guide(), fsm.snapshot(), d)
    assert ctx.summary == "受访者常在 TikTok 看评价。" and [t.index for t in ctx.recent_turns] == [6, 7, 8, 9]
    assert m.turns_to_summarize() == []
    with pytest.raises(ValueError):
        m.set_summary("x", upto_index=3)  # never goes backwards


def test_a_very_long_interview_never_grows_the_context():
    fsm, _ = machine()
    d = fsm.start()
    sizes = []
    for n in (20, 60, 200, 400):
        m = ConversationMemory()
        for i in range(n):
            m.add_turn(Speaker.AI if i % 2 == 0 else Speaker.PARTICIPANT, f"这是第 {i} 句话，" + "很长的回答" * 30, at_s=float(i))
        ctx = m.build_context(guide(), fsm.snapshot(), d)
        sizes.append(len(ctx.model_dump_json()))
        assert len(ctx.recent_turns) == 12 and len(ctx.summary) <= DIGEST_MAX_CHARS
    assert max(sizes) < 1.2 * sizes[1]  # a 400-turn interview costs about the same per turn as a 60-turn one
    assert len(ctx.asked_questions) == CONTEXT_ASKED_QUESTIONS and len(m.asked_questions()) == 200  # the model sees 20; the repeat guard sees all


def test_the_digest_keeps_the_newest_lines_whole_when_it_has_to_cut():
    m = ConversationMemory(recent_turns_verbatim=2)
    for i in range(300):
        m.add_turn(Speaker.PARTICIPANT, f"回答编号{i:03d}" + "字" * 60, at_s=float(i))
    digest = m.history_digest(len(m.turns) - 2)
    assert len(digest) <= DIGEST_MAX_CHARS and "回答编号297" in digest and "回答编号000" not in digest
    assert all(line.split(": ", 1)[1].startswith("回答编号") for line in digest.split("\n"))  # no line was cut in half

def test_asked_questions_cover_ai_and_human_moderators():
    m = ConversationMemory()
    m.add_turn(Speaker.AI, "您好", 0)
    m.add_turn(Speaker.PARTICIPANT, "你好", 1)
    m.add_turn(Speaker.HUMAN, "我接一下", 2)
    assert m.asked_questions() == ["您好", "我接一下"]


# ---------- exploring: dig deeper while a topic has time and the answers still add information ----------
GAIN = 0.6  # an answer that added plenty of new information


def _used_up_topic(**cfg):
    """A machine at the end of the first core topic: every prepared question and must-cover item has been asked."""
    fsm, clock = machine(**cfg)
    to_core(fsm)
    fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    while d.action is PacingAction.PROBE_ITEM:
        d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    return fsm, clock, d


def test_a_topic_with_time_left_gets_deeper_questions_instead_of_moving_on():
    fsm, _, d = _used_up_topic()
    assert d.action is PacingAction.EXPLORE and d.reason is TransitionReason.EXPLORING and d.explore_index == 0 and d.topic_id == "discovery"
    assert d.question is None and d.followup_allowed  # no prepared question: the engine writes one about what was said
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert d.action is PacingAction.EXPLORE and d.explore_index == 1


def test_exploring_is_capped_per_topic():
    fsm, _, d = _used_up_topic(max_explore_per_topic=2)
    asked = 0
    while d.action is PacingAction.EXPLORE:
        asked += 1
        d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert asked == 2 and d.topic_id == "barriers" and d.reason is TransitionReason.QUESTIONS_DONE


def test_exploring_stops_when_the_answers_stop_adding_information():
    fsm, _, d = _used_up_topic()
    assert d.action is PacingAction.EXPLORE
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.1))  # nothing new: do not pad the time
    assert d.action is PacingAction.NEXT_TOPIC and d.topic_id == "barriers"


def test_exploring_stops_near_the_end_of_the_topic_time():
    fsm, clock, d = _used_up_topic()
    clock.advance(fsm.snapshot().topic_budget_s * 0.9)
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert d.action is PacingAction.NEXT_TOPIC


def test_saturation_with_time_left_explores_and_without_it_moves_on():
    fsm, clock = machine()
    to_core(fsm)
    d = fsm.record_turn(TurnUpdate(saturation=0.9, information_gain=GAIN))
    assert d.action is PacingAction.EXPLORE
    clock.advance(fsm.snapshot().topic_budget_s * 0.9)
    d = fsm.record_turn(TurnUpdate(saturation=0.9, information_gain=GAIN))
    assert d.reason is TransitionReason.SATURATED and d.topic_id == "barriers"


def test_exploring_can_be_switched_off_and_never_beats_the_hard_limits():
    fsm, _, d = _used_up_topic(max_explore_per_topic=0)
    assert d.action is PacingAction.NEXT_TOPIC
    fsm, _ = machine(max_turns_per_topic=3)
    to_core(fsm)
    reasons = [fsm.record_turn(TurnUpdate(information_gain=GAIN)).reason for _ in range(3)]
    assert reasons[-1] is TransitionReason.TOPIC_TURN_LIMIT


def test_time_a_topic_did_not_use_goes_to_the_topics_after_it():
    fsm, clock = machine()
    to_core(fsm)
    planned = fsm.snapshot().topic_budget_s
    clock.advance(planned * 0.25)  # the first topic finishes early
    fsm.record_turn(TurnUpdate(saturation=0.9, information_gain=0.0))
    s = fsm.snapshot()
    assert s.topic_id == "barriers" and s.topic_budget_s > fsm.budget.topic_s["barriers"]
    assert s.topic_budget_s == pytest.approx(fsm.budget.phase_s[Phase.CORE] - planned * 0.25)  # all that is left, the last topic gets it all


def test_a_topic_that_ran_over_leaves_the_next_one_less():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(fsm.snapshot().topic_budget_s)  # the whole budget used up
    fsm.record_turn(TurnUpdate())
    s = fsm.snapshot()
    assert s.topic_id == "barriers" and s.topic_budget_s == pytest.approx(fsm.budget.phase_s[Phase.CORE] - fsm.budget.topic_s["discovery"])


def test_prepared_questions_still_come_in_order_after_exploring_and_probing():
    """Eval run 20261012-085748: IndexError in 2 of 40 interviews. Explore and probe questions advanced the shared counter,
    so the next prepared question was looked up past the end of the list."""
    fsm, _ = machine()
    to_core(fsm)
    topic = guide().topics[0]
    d = fsm.record_turn(TurnUpdate(saturation=0.9, information_gain=GAIN))  # saturated early: an extra deeper question, prepared ones unasked
    assert d.action is PacingAction.EXPLORE
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert d.action is PacingAction.NEXT_QUESTION and d.question == topic.questions[1]  # the second prepared question, not an IndexError
    d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert d.action is not PacingAction.NEXT_QUESTION  # the prepared questions are used up now: items, exploring or the next topic


def test_every_mixture_of_probes_explores_and_questions_stays_inside_the_guide():
    """Random but repeatable walks through the state machine: it must never raise and never leave the guide's questions."""
    import random

    for seed in range(60):
        rng = random.Random(seed)
        fsm, clock = machine()
        fsm.start()
        fsm.participant_ready()
        for _ in range(120):
            if fsm.phase is Phase.ENDED:
                break
            clock.advance(rng.choice([5, 20, 60, 150]))
            d = fsm.record_turn(TurnUpdate(
                was_followup=rng.random() < 0.3, saturation=rng.choice([0.1, 0.5, 0.9]), information_gain=rng.choice([0.0, 0.2, 0.7]),
                question_answered=rng.random() < 0.6, dry=rng.random() < 0.3,
                covered_item_ids=rng.sample(["d_channels", "d_trust", "b_cert", "b_return", "nope"], rng.randint(0, 2))))
            assert d.question is None or d.question in {*guide().warmup_questions, *(q for t in guide().topics for q in t.questions)}
        assert fsm.phase is Phase.ENDED or fsm.snapshot().elapsed_s > 0


def test_behind_schedule_the_items_come_before_more_follow_ups():
    fsm, clock = machine()
    to_core(fsm)
    d = fsm.record_turn(TurnUpdate(was_followup=False))  # early in the topic: normal follow-up flow
    assert d.action is PacingAction.CONTINUE and not fsm.hurrying()
    clock.advance(fsm.snapshot().topic_budget_s * 0.5)  # half the topic's time is gone, three items still unasked
    assert fsm.hurrying()
    d = fsm.record_turn(TurnUpdate(information_gain=GAIN))  # a plain answer, no "answered" flag: it would normally get a follow-up
    assert d.action is PacingAction.PROBE_ITEM and d.reason is TransitionReason.BEHIND_SCHEDULE and d.missed_item.id == "d_channels"


def test_items_that_are_covered_or_asked_stop_the_hurry():
    fsm, clock = machine()
    to_core(fsm)
    clock.advance(fsm.snapshot().topic_budget_s * 0.6)
    assert fsm.hurrying()
    fsm.record_turn(TurnUpdate(covered_item_ids=["d_channels", "d_trust", "d_price"]))
    assert not fsm.hurrying()  # nothing left to be anxious about: back to the normal flow (and exploring, if the time allows)


def test_the_hurry_can_be_moved_and_only_applies_in_the_core():
    fsm, clock = machine(hurry_after_share=0.9)
    to_core(fsm)
    clock.advance(fsm.snapshot().topic_budget_s * 0.6)
    assert not fsm.hurrying()
    fsm2, _ = machine()
    fsm2.start()
    assert not fsm2.hurrying()  # intro / warm-up: no topic yet


def test_the_exploring_operators_take_turns():
    from app.moderator.constants import Operator
    from app.moderator.operators import EXPLORE_ROTATION, choose_operator
    from app.moderator.schemas import RespondentInputAnalysis

    fsm, _, d = _used_up_topic()
    seen = []
    while d.action is PacingAction.EXPLORE:
        seen.append(choose_operator(RespondentInputAnalysis(), d, "一段足够长的回答，里面有原因。").operator)
        d = fsm.record_turn(TurnUpdate(question_answered=True, information_gain=GAIN))
    assert seen == list(EXPLORE_ROTATION)[:len(seen)] and len(set(seen)) == len(seen) and Operator.REFLECT not in seen
