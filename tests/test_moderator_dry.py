"""A participant with nothing more to say (task from the user, 2026-10-13): ask once more from a fresh angle, then move on gently;
near the end, thank and finish. The moderator never pads the time with questions that get nothing."""
import json

import pytest

from app.moderator.constants import PacingAction, Operator, Phase, TransitionReason
from app.moderator.fsm import InterviewStateMachine, TurnUpdate
from app.moderator.operators import choose_operator, counts_as_followup
from app.moderator.prompts import load_prompts
from app.moderator.schemas import RespondentInputAnalysis
from eval.models import load_guide
from app.moderator.pacing import PacingConfig
from tests.test_moderator_engine import GUIDE, engine, say, started
from tests.test_moderator_fsm import ANSWERED, guide, machine, to_core

DRY = TurnUpdate(dry=True, information_gain=0.05)
RICH = TurnUpdate(information_gain=0.6, covered_item_ids=[])


def _dry_turns(fsm: InterviewStateMachine, n: int):
    d = None
    for _ in range(n):
        d = fsm.record_turn(DRY)
    return d


# ---------- the state machine ----------
def test_by_default_one_empty_answer_earns_exactly_one_more_question_and_the_next_empty_one_moves_on():
    fsm, _ = machine()
    to_core(fsm)
    d = fsm.record_turn(DRY)
    assert d.action is PacingAction.LAST_TRY and d.phase is Phase.CORE
    d = fsm.record_turn(DRY)
    assert d.action is PacingAction.NEXT_TOPIC and d.reason is TransitionReason.NO_NEW_INFO


def test_by_default_an_answer_with_substance_after_the_extra_question_keeps_the_topic_going():
    fsm, _ = machine()
    to_core(fsm)
    fsm.record_turn(DRY)
    assert fsm.record_turn(RICH).action is not PacingAction.NEXT_TOPIC


def test_in_the_recovery_every_missed_item_is_still_asked_once_even_when_the_answers_are_empty():
    fsm, _ = machine()
    to_core(fsm)
    for _ in range(4):
        d = fsm.record_turn(DRY)  # both topics are left (one extra question each)
    assert d.phase is Phase.RECOVERY
    asked = [d.missed_item.id]
    missed = {i.id for i in fsm.missed_items() if i.id not in fsm.asked_items} | set(asked)  # the first one is being asked right now
    while d.phase is Phase.RECOVERY:
        d = fsm.record_turn(DRY)  # empty answers do not end the recovery
        if d.phase is Phase.RECOVERY:
            asked.append(d.missed_item.id)
    assert set(asked) == missed and len(asked) == len(set(asked))  # all asked, none twice
    assert d.phase is Phase.WRAPUP


def test_when_the_last_try_brings_nothing_either_the_next_topic_starts():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    _dry_turns(fsm, 2)
    d = fsm.record_turn(DRY)
    assert d.action is PacingAction.NEXT_TOPIC and d.reason is TransitionReason.NO_NEW_INFO and d.topic_id == "barriers"
    assert d.question == guide().topics[1].questions[0]


def test_an_answer_with_something_new_resets_the_count():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    fsm.record_turn(DRY)
    fsm.record_turn(RICH)  # something new: the streak starts again
    assert fsm.record_turn(DRY).action is PacingAction.CONTINUE
    assert fsm.record_turn(DRY).action is PacingAction.LAST_TRY


def test_a_last_try_that_works_lets_the_topic_go_on_and_there_is_only_one_per_topic():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    _dry_turns(fsm, 2)  # the last try is asked
    assert fsm.record_turn(RICH).action is not PacingAction.NEXT_TOPIC  # it worked: the topic goes on
    fsm.record_turn(DRY)
    d = fsm.record_turn(DRY)  # empty again in the same topic: no second last try
    assert d.action is PacingAction.NEXT_TOPIC and d.reason is TransitionReason.NO_NEW_INFO


def test_a_new_topic_starts_with_a_clean_count():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    _dry_turns(fsm, 3)  # leaves the first topic
    assert fsm.record_turn(DRY).action is PacingAction.CONTINUE
    assert fsm.record_turn(DRY).action is PacingAction.LAST_TRY  # the new topic has its own last try


def test_the_warm_up_is_left_the_same_way():
    fsm, _ = machine(dry_streak_limit=2)
    fsm.start()
    fsm.participant_ready()
    assert fsm.record_turn(DRY).action is PacingAction.CONTINUE
    assert fsm.record_turn(DRY).action is PacingAction.LAST_TRY
    d = fsm.record_turn(DRY)
    assert d.phase is Phase.CORE and d.reason is TransitionReason.NO_NEW_INFO and d.topic_id == "discovery"


def test_the_last_topic_running_dry_still_ends_in_order():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    _dry_turns(fsm, 3)  # topic 1 -> topic 2
    d = _dry_turns(fsm, 3)  # topic 2 runs dry too
    assert d.phase in (Phase.RECOVERY, Phase.WRAPUP)  # unasked items are asked once in the recovery; with none, straight to the wrap-up


def test_a_participant_who_keeps_giving_content_is_never_cut_short():
    fsm, _ = machine(dry_streak_limit=2)
    to_core(fsm)
    for _ in range(6):
        assert fsm.record_turn(TurnUpdate(information_gain=0.5, was_followup=False)).reason is not TransitionReason.NO_NEW_INFO


def test_random_walks_with_empty_answers_never_break_the_machine(dry_streak_limit=2):
    import random

    for seed in range(60):
        rng = random.Random(seed)
        fsm, clock = machine(dry_streak_limit=2)
        fsm.start()
        fsm.participant_ready()
        for _ in range(150):
            if fsm.phase is Phase.ENDED:
                break
            clock.advance(rng.choice([5, 20, 60]))
            fsm.record_turn(TurnUpdate(dry=rng.random() < 0.6, was_followup=rng.random() < 0.3, saturation=rng.choice([0.1, 0.5, 0.9]),
                                       information_gain=rng.choice([0.0, 0.2, 0.7]), question_answered=rng.random() < 0.4,
                                       covered_item_ids=rng.sample(["d_channels", "d_trust", "b_cert", "nope"], rng.randint(0, 1))))
        assert fsm.phase is Phase.ENDED or fsm.snapshot().elapsed_s > 0


# ---------- the operator and the prompts ----------
def _decision(action: PacingAction):
    fsm, _ = machine()
    to_core(fsm)
    d = fsm.record_turn(DRY) if action is PacingAction.LAST_TRY else fsm.record_turn(ANSWERED)
    assert d.action is action
    return d


def test_the_last_try_uses_the_fresh_angle_operator_and_still_counts_as_a_probe():
    analysis = RespondentInputAnalysis.model_validate({"category": "too_short"})
    assert choose_operator(analysis, _decision(PacingAction.LAST_TRY), "不知道").operator is Operator.FRESH_ANGLE
    assert counts_as_followup(Operator.FRESH_ANGLE)  # honest accounting: it is a probe and is scored as one


@pytest.mark.parametrize("category, expected", [("emotional", Operator.REASSURE), ("question_back", Operator.REDIRECT), ("not_understood", Operator.REPHRASE)])
def test_a_participant_who_needs_something_else_still_gets_it_at_the_last_try(category, expected):
    analysis = RespondentInputAnalysis.model_validate({"category": category})
    assert choose_operator(analysis, _decision(PacingAction.LAST_TRY), "我有点难过").operator is expected


def test_prompt_v004_tells_the_model_how_to_move_on_and_what_a_fresh_angle_is():
    v4, v3 = load_prompts("v004"), load_prompts("v003")
    assert load_prompts().version == "v008"
    assert 'reason is "no_new_info"' in v4.speak and "这一块我们先聊到这里" in v4.speak and "never mention that their answers were short" in v4.speak
    assert "different angle" in v4.operators[Operator.FRESH_ANGLE] and Operator.FRESH_ANGLE not in v3.operators
    assert json.dumps(v4.speak) != json.dumps(v3.speak)


# ---------- the engine decides what counts as an empty answer ----------
def _engine():
    e, _ = started([{"category": "too_short", "signals": {"information_gain": 0.05}}], [say("您平时用什么护肤品？")], PacingConfig())
    return e


def test_only_short_answers_that_add_nothing_and_cover_nothing_are_empty():
    e = _engine()

    def analysis(gain=0.05, covered=()):
        return RespondentInputAnalysis.model_validate({"category": "answered", "signals": {"information_gain": gain}, "covered_item_ids": list(covered)})

    assert e._is_dry(analysis(), "不知道。")
    assert not e._is_dry(analysis(0.5), "不知道。")  # it added something
    assert not e._is_dry(analysis(covered=["d_price"]), "几十块。")  # it covered an item
    assert not e._is_dry(analysis(), "我觉得吧，其实也不是不想说，主要是平时没怎么留意过这些事情，所以一下子说不上来什么具体的。")  # long: a dodge, not silence


def test_in_english_the_length_is_counted_in_words():
    e, _ = engine([{"category": "too_short"}], [say("What do you use?")], guide=load_guide("sg_food_delivery_en"), config=PacingConfig())
    a = RespondentInputAnalysis.model_validate({"category": "too_short", "signals": {"information_gain": 0.0}})
    assert e._is_dry(a, "Not sure, really.") and not e._is_dry(a, "I really have not thought about it that much before, to be honest with you.")


def test_a_silent_participant_gets_one_fresh_angle_then_a_move_on_in_the_conversation():
    short = {"category": "too_short", "ladder_level": "fact", "signals": {"information_gain": 0.05}}
    e, _ = started([short], [say("您平时用什么护肤品？"), say("那最近一次买的是什么？"), say("上周有没有逛过护肤品的店？"), say("您一般在哪里看评价？")], PacingConfig())
    seen = []
    for answer in ("面霜。", "保湿的。", "不记得。", "没有。", "嗯。", "不知道。", "没了。", "随便。", "不清楚。", "没有。"):
        p = e.handle_participant(answer)
        if p.plan:  # the closing statement has no plan
            seen.append((p.plan.decision.action, p.plan.decision.reason, p.plan.operator))
        if e.fsm.phase is Phase.ENDED:
            break
    last_try = [i for i, (a, _, o) in enumerate(seen) if a is PacingAction.LAST_TRY]
    moved_on = [i for i, (_, r, _) in enumerate(seen) if r is TransitionReason.NO_NEW_INFO]
    assert last_try and all(seen[i][2] is Operator.FRESH_ANGLE for i in last_try)
    assert moved_on and min(moved_on) > min(last_try)  # the move-on comes after the one extra try, never before


# ---------- the evaluation's time metric ----------
def _early_result(scenario, elapsed_s: float):
    from app.moderator.constants import EndReason
    from eval.session import SessionResult

    return SessionResult(scenario_id=scenario.id, guide_id=scenario.guide_id, language="zh", intro="x", turns=[], end_reason=EndReason.COMPLETED,
                         elapsed_s=elapsed_s, planned_s=1000.0, covered=[], asked=[], must_cover=[], topic_ids=[], transitions=[])


def _scenario(persona):
    from eval.scenarios.generate import load_scenarios

    return next(s for s in load_scenarios(None) if s.persona is persona)


def test_finishing_early_is_excused_only_for_participants_with_nothing_to_say_and_only_by_their_persona():
    from eval.checkers import check_session
    from eval.constants import PersonaType
    from eval.models import load_guide

    silent, cooperative = _scenario(PersonaType.SILENT), _scenario(PersonaType.COOPERATIVE)
    g = load_guide(silent.guide_id)
    assert check_session(_early_result(silent, 500.0), silent, g).excused_under_run
    assert not check_session(_early_result(cooperative, 500.0), cooperative, g).excused_under_run  # a cooperative participant had more to give
    assert not check_session(_early_result(silent, 1300.0), silent, g).excused_under_run  # running over is never excused
    assert not check_session(_early_result(silent, 900.0), silent, g).excused_under_run  # within the gate anyway: nothing to excuse


def test_the_report_counts_the_excused_runs_and_still_shows_the_strict_figure():
    from eval.checkers import SessionCheck
    from eval.report import _metrics

    def check(sid, dev, excused=False):
        return SessionCheck(scenario_id=sid, coverage=1.0, repeats=0, followups=0, followup_hits=0, core_topics_reached=0, deep_topics=0,
                            time_deviation=dev, excused_under_run=excused, latencies=[], fallbacks=0, completed=True)

    rows = [check("a", 0.05), check("b", 0.5, excused=True), check("c", 0.4)]  # c is a real miss: a cooperative participant cut short
    metric = next(m for m in _metrics(rows, {}, None) if m.name.startswith("时间偏差"))
    assert abs(metric.value - 2 / 3) < 1e-9 and metric.passed is False
    assert "1 场" in metric.note and "33.3%" in metric.note  # excused count and the strict figure (1 of 3) are both on the report


# ---------- the wrap-up keeps enough time for a talkative participant ----------
def test_a_participant_with_long_answers_gets_a_longer_wrap_up_reserve_so_the_interview_ends_on_time():
    fsm, clock = machine()
    fsm.start()
    fsm.participant_ready()
    planned = fsm.budget.phase_s[Phase.WRAPUP]
    assert fsm._wrapup_reserve() == planned  # nothing said yet: the plan
    for _ in range(4):
        clock.advance(300.0)  # five-minute exchanges
        fsm.record_turn(TurnUpdate(information_gain=0.6))
    assert fsm._wrapup_reserve() > planned and fsm._wrapup_reserve() >= 2 * fsm.snapshot().elapsed_s / 5
