"""Sprint 2: the AI moderator's per-turn pipeline with a scripted (mock) model. No real model is ever called."""
import json
import logging
import pathlib

import pytest

from app.moderator.clock import ManualClock
from app.moderator.constants import OPERATORS_ADDED_IN, AlertLevel, EndReason, Operator, Phase, RedLine, Speaker
from app.moderator.engine import EngineStateError, ModeratorEngine
from app.moderator.fsm import TurnUpdate
from app.moderator.knowledge import InterviewGuide
from app.moderator.prompts import CURRENT_PROMPT_VERSION, load_prompts
from app.moderator.pacing import PacingConfig
from app.moderator.providers import ChatError, MockModeratorChat
from app.moderator.templates import TEMPLATES

GUIDE = InterviewGuide.model_validate(json.loads(pathlib.Path("eval/guides/sg_skincare_zh.json").read_text(encoding="utf-8")))
PROMPTS = load_prompts()
ZH = TEMPLATES["zh"]
READY = {"category": "answered", "ready_to_start": True}
ANSWER = {"category": "answered", "ladder_level": "reason", "saturation": 0.2, "signals": {"information_gain": 0.25}}  # a full answer: the reason is given, nothing left to dig for
SHALLOW = {"category": "answered", "ladder_level": "fact", "saturation": 0.2, "question_answered": True, "signals": {"information_gain": 0.25}}  # a bare fact: the ladder says ask for more
DEEP = {"category": "answered", "ladder_level": "reason", "saturation": 0.3,
        "signals": {"information_gain": 0.9, "emotion": 0.8, "pain_point": 0.9, "contradiction": 0.2, "business_value": 0.8}}


class Model:
    """Scripted model: analysis replies and draft replies come from separate queues (the last one repeats)."""

    def __init__(self, analyses: list, drafts: list) -> None:
        self.analyses, self.drafts = analyses, drafts
        self.analysis_calls: list[list[dict]] = []
        self.draft_calls: list[list[dict]] = []
        self.chat = MockModeratorChat(self._reply)

    def _reply(self, messages: list[dict]) -> dict:
        is_analysis = messages[0]["content"] == PROMPTS.analyze
        calls, queue = (self.analysis_calls, self.analyses) if is_analysis else (self.draft_calls, self.drafts)
        calls.append(messages)
        item = queue[min(len(calls) - 1, len(queue) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.analysis_calls) + len(self.draft_calls)


def say(text: str) -> dict:
    return {"spoken_response": text, "note": {"research_purpose": "了解渠道", "expected_information": "具体平台", "probe_logic": "先事实后原因"}}


# The empty-answer rule (test_moderator_dry.py) is switched off for the tests about other things: their plain fixture answers are short
# and score low, which would trigger it. Tests of the rule pass PacingConfig() (the real defaults).
NO_DRY_RULE = PacingConfig(dry_streak_limit=10)


def engine(analyses: list, drafts: list, guide: InterviewGuide = GUIDE, config: PacingConfig = NO_DRY_RULE) -> tuple[ModeratorEngine, Model]:
    model = Model(analyses, drafts)
    e = ModeratorEngine(guide, model.chat, prompts=PROMPTS, config=config, clock=ManualClock())
    return e, model


def started(analyses: list, drafts: list, config: PacingConfig = NO_DRY_RULE) -> tuple[ModeratorEngine, Model]:
    """Opened and past the intro (now on the first warm-up question)."""
    e, model = engine([READY, *analyses], [say(GUIDE.warmup_questions[0]), *drafts], config=config)
    e.open()
    e.handle_participant("好的，可以开始")
    return e, model


# ---------- opening ----------
def test_open_discloses_the_ai_without_asking_the_model():
    e, model = engine([READY], [say("x？")])
    p = e.open()
    assert "AI 主持人" in p.spoken_response and "不是真人" in p.spoken_response and "护肤品选购" in p.spoken_response
    assert model.calls == 0 and e.fsm.phase is Phase.INTRO
    with pytest.raises(EngineStateError):
        e.open()


def test_turns_before_open_are_refused():
    e, _ = engine([READY], [say("x？")])
    with pytest.raises(EngineStateError):
        e.handle_participant("你好")


def test_unsupported_language_is_refused_up_front():
    with pytest.raises(ValueError):
        engine([READY], [say("x?")], guide=GUIDE.model_copy(update={"language": "ms"}))


def test_guide_questions_that_break_red_lines_are_refused_before_the_interview():
    bad = GUIDE.model_copy(update={"warmup_questions": ["您最近买了什么？在哪里买的？", "您是不是很喜欢网购？"]})
    with pytest.raises(ValueError) as e:
        engine([READY], [say("x？")], guide=bad)
    assert "H1" in str(e.value) and "H2" in str(e.value)


def test_intro_waits_until_the_participant_is_ready():
    e, _ = engine([{"category": "question_back"}, READY], [say("大约二十分钟。您准备好了就告诉我，可以吗？"), say(GUIDE.warmup_questions[0])])
    e.open()
    p = e.handle_participant("这个要多久？")
    assert e.fsm.phase is Phase.INTRO and p.spoken_response.endswith("？")
    p = e.handle_participant("好的")
    assert e.fsm.phase is Phase.WARMUP and p.plan.operator is Operator.TRANSITION and p.attempts == 1 and not p.used_fallback


# ---------- normal turns ----------
def test_deep_answer_climbs_the_ladder_and_keeps_the_internal_note():
    e, _ = started([DEEP], [say("当时您是怎么想的，让您最后决定不买？")])
    p = e.handle_participant("我看了很多评价，但担心是假货，最后没买。")
    assert p.plan.deep_dive and p.plan.operator is Operator.EMOTION and p.note.research_purpose == "了解渠道"
    assert p.analysis.signals.pain_point == 0.9 and p.latency_s >= 0


def test_shallow_answer_asks_for_a_concrete_example():
    e, _ = started([ANSWER], [say("最近一次是在哪里买的？")])
    assert e.handle_participant("网上买").plan.operator in (Operator.NUDGE, Operator.SPECIFY)


def test_follow_ups_are_counted_for_the_cap():
    e, _ = started([ANSWER], [say("最近一次是在哪里买的？")])
    e.handle_participant("我一般在网上买，也会去商场看看。")
    assert e.fsm.snapshot().followups_used == 0  # counted when the answer to the follow-up comes in
    e.handle_participant("Shopee 上买得比较多。")
    assert e.fsm.snapshot().followups_used == 1


# ---------- red-line gate: regenerate, then fall back ----------
def test_violating_draft_is_regenerated_and_the_reason_fed_back():
    e, model = started([ANSWER], [say("说得好！您为什么这样选？"), say("您为什么这样选？")])
    p = e.handle_participant("我一般在网上买护肤品。")
    assert p.spoken_response == "您为什么这样选？" and p.attempts == 2 and [v.red_line for v in p.rejected[0]] == [RedLine.H7_EVALUATIVE]
    assert "H7" in model.draft_calls[-1][1]["content"]  # the model is told what to fix
    assert p.alert is AlertLevel.NONE


def test_three_failing_drafts_fall_back_and_alert_then_red_on_the_second_turn():
    bad = say("您是不是觉得太贵了？")
    e, model = started([ANSWER], [bad])
    p = e.handle_participant("我一般在网上买护肤品。")
    assert p.used_fallback and p.spoken_response == ZH.followup and p.alert is AlertLevel.YELLOW and len(p.rejected) == 3
    assert p.spoken_response not in [d["spoken_response"] for d in [bad]]  # the violating draft is never said
    p = e.handle_participant("还是网上多。")
    assert p.alert is AlertLevel.RED and "建议接管" in p.alert_reason


def test_fallback_asks_the_question_the_state_machine_wanted():
    e, _ = started([{**ANSWER, "question_answered": True}], [say("您是不是常买？")])
    p = e.handle_participant("我每周都会护肤两次。")
    assert p.used_fallback and p.spoken_response == GUIDE.warmup_questions[1]


@pytest.mark.parametrize("broken", [{"not": "a draft"}, {"spoken_response": ""}, ChatError("down")])
def test_unusable_model_output_falls_back(broken):
    e, model = started([ANSWER], [broken])
    p = e.handle_participant("我一般在网上买。")
    assert p.used_fallback and p.spoken_response == ZH.followup
    assert len(model.draft_calls) == 1 + 1 + 1  # intro-ready draft, then 1 try + 1 retry; no endless loop


def test_one_bad_field_in_the_analysis_is_dropped_not_the_whole_reading(caplog):
    e, model = started([{"category": "nonsense", "saturation": 0.6, "covered_item_ids": ["d_channels"]}], [say("能再说说吗？")])
    p = e.handle_participant("网上买，主要看 TikTok。")
    assert p.analysis.category.value == "answered" and p.analysis.saturation == 0.6 and p.analysis.covered_item_ids == ["d_channels"]
    assert len(model.analysis_calls) == 2  # intro + this turn: no retry needed
    assert "fields: category" in caplog.text and "TikTok" not in caplog.text  # field names only, never the participant's words


def test_unusable_analysis_becomes_a_neutral_reading():
    e, model = started([ChatError("down")], [say("能再说说吗？")])
    p = e.handle_participant("网上买。")
    assert p.analysis.category.value == "answered" and p.spoken_response == "能再说说吗？" and len(model.analysis_calls) == 1 + 2


@pytest.mark.parametrize("text", ["好的", "可以开始", "OK", "没问题，开始吧", "Sure, let's begin"])
def test_ready_is_recognised_even_if_the_model_misses_it(text):
    e, _ = engine([{"category": "answered"}], [say(GUIDE.warmup_questions[0])])
    e.open()
    e.handle_participant(text)
    assert e.fsm.phase is Phase.WARMUP


# ---------- safety: stop and crisis never depend on the model ----------
@pytest.mark.parametrize("text", ["我不想活了", "有时候真想自杀", "I want to end my life"])
def test_crisis_stops_with_help_numbers_and_red_alert(text):
    e, model = started([ANSWER], [say("x？")])
    before = model.calls
    p = e.handle_participant(text)
    assert p.spoken_response == ZH.crisis and "1767" in p.spoken_response and p.alert is AlertLevel.RED
    assert e.fsm.phase is Phase.ENDED and e.fsm.end_reason is EndReason.CRISIS and model.calls == before
    with pytest.raises(EngineStateError):
        e.handle_participant("还在吗")


def test_crisis_seen_only_by_the_model_also_stops():
    e, _ = started([{"category": "crisis"}], [say("x？")])
    p = e.handle_participant("最近什么都没意义")
    assert p.spoken_response == ZH.crisis and e.fsm.end_reason is EndReason.CRISIS


@pytest.mark.parametrize("text", ["我不想继续了", "我想停止", "结束访谈吧", "I want to stop"])
def test_stop_request_ends_politely_without_a_question(text):
    e, model = started([ANSWER], [say("x？")])
    p = e.handle_participant(text)
    assert p.spoken_response == ZH.stop and "？" not in p.spoken_response and e.fsm.end_reason is EndReason.PARTICIPANT_STOPPED


def test_are_you_ai_gets_an_honest_answer_even_when_the_model_dodges():
    e, _ = started([ANSWER], [say("这不重要。您平时在哪里买？")])
    p = e.handle_participant("你是真人吗？")
    assert p.used_fallback and p.spoken_response.startswith(ZH.ai_answer) and RedLine.H3_AI_DISCLOSURE in [v.red_line for v in p.rejected[0]]


def test_are_you_ai_answered_by_the_model_passes():
    e, _ = started([{**ANSWER, "asks_if_ai": True}], [say("我是 AI 主持人，不是真人。您平时在哪里买？")])
    p = e.handle_participant("你是机器人吗")
    assert not p.used_fallback and p.spoken_response.startswith("我是 AI 主持人")


# ---------- human take-over (H11) and whispers ----------
def test_ai_is_silent_after_take_over_and_resumes_with_what_was_said():
    e, model = started([ANSWER], [say("您最近一次买是什么时候？")])
    e.take_over()
    with pytest.raises(EngineStateError):
        e.take_over()
    before = model.calls
    for text in ("我一般在网上买。", "Shopee 比较多。"):
        p = e.handle_participant(text)
        assert p.spoken_response is None and p.plan is None
    e.add_human_turn("那您最近一次买是什么时候？")
    assert model.calls == before  # the model is not even asked while a human is in control
    e.hand_back()
    e.handle_participant("上个月。")
    sent = model.draft_calls[-1][1]["content"]
    assert "那您最近一次买是什么时候？" in sent and "human_moderator" in sent
    with pytest.raises(EngineStateError):
        e.hand_back()


def test_whisper_reaches_the_next_draft_once_and_never_the_analysis():
    e, model = started([ANSWER], [say("关于退货，您遇到过什么情况？")])
    e.add_whisper("问问她退货的经历")
    e.handle_participant("我一般在网上买。")
    assert "问问她退货的经历" in model.draft_calls[-1][1]["content"]
    assert "问问她退货的经历" not in model.analysis_calls[-1][1]["content"]
    e.handle_participant("Shopee 比较多。")
    assert "问问她退货的经历" not in model.draft_calls[-1][1]["content"]


# ---------- wrap-up and the end ----------
def test_wrap_up_answer_ends_with_the_closing_statement():
    covered = [i.id for t in GUIDE.topics for i in t.must_cover]
    analyses = [{**ANSWER, "question_answered": True}, {**ANSWER, "question_answered": True},
                {**ANSWER, "saturation": 0.9, "covered_item_ids": covered}, {**ANSWER, "saturation": 0.9}, ANSWER]
    drafts = [say(GUIDE.warmup_questions[1]), say(GUIDE.topics[0].questions[0]), say(GUIDE.topics[1].questions[0]),
              say("您提到了渠道和退货。还有什么重要的事，是我们今天没聊到的吗？")]
    e, _ = started(analyses, drafts)
    phases = [e.handle_participant(t) and e.fsm.phase for t in ("每天护肤", "上周在网上买的", "TikTok 上看到的", "最担心假货")]
    assert phases == [Phase.WARMUP, Phase.CORE, Phase.CORE, Phase.WRAPUP]
    p = e.handle_participant("没有了")
    assert p.spoken_response == ZH.closing and e.fsm.end_reason is EndReason.COMPLETED


def test_every_turn_is_logged_and_memory_has_both_sides():
    e, _ = started([ANSWER], [say("最近一次是在哪里买的？")])
    e.handle_participant("网上买的比较多。")
    assert len(e.turn_log) == 2 and [t.speaker for t in e.memory.turns] == [Speaker.AI, Speaker.PARTICIPANT, Speaker.AI, Speaker.PARTICIPANT, Speaker.AI]


def test_prompt_versions_are_complete():
    assert PROMPTS.version == CURRENT_PROMPT_VERSION and set(PROMPTS.operators) == set(Operator)
    with pytest.raises(FileNotFoundError):
        load_prompts("v999")


@pytest.mark.parametrize("note, kept", [("只是一句话的备注", "只是一句话的备注"), (["a", "b"], ""), ({"research_purpose": "x" * 900}, "x" * 400),
                                         ({"research_purpose": 5, "probe_logic": "ok"}, ""), (None, ""), (7, "")])
def test_a_badly_formatted_internal_note_never_costs_the_reply(note, kept):
    e, _ = started([ANSWER], [{"spoken_response": "最近一次是在哪里买的？", "note": note}])
    p = e.handle_participant("我一般在网上买护肤品。")
    assert p.spoken_response == "最近一次是在哪里买的？" and not p.used_fallback and p.attempts == 1
    assert p.note.research_purpose == kept  # what is usable is kept (cut to 400), the rest is dropped


# ---- permanent cases from eval run 20261012-055934 (first DeepSeek baseline) ----
@pytest.mark.parametrize("text", ["你是不是 AI？我不想继续了。", "我想停止。对了，你是真人吗？"])
def test_stop_and_are_you_an_ai_in_one_message_gets_both_answers(text):
    e, _ = started([ANSWER], [say("x？")])
    p = e.handle_participant(text)
    assert p.spoken_response.startswith(ZH.ai_answer) and p.spoken_response.endswith(ZH.stop) and "？" not in p.spoken_response
    assert e.fsm.end_reason is EndReason.PARTICIPANT_STOPPED


def test_crisis_message_that_also_asks_if_ai_still_gets_help_numbers_and_an_honest_answer():
    e, _ = started([ANSWER], [say("x？")])
    p = e.handle_participant("我不想活了，你是机器人吗")
    assert p.spoken_response.startswith(ZH.ai_answer) and "1767" in p.spoken_response and p.alert is AlertLevel.RED


def test_the_closing_statement_answers_honestly_when_the_last_message_asked_if_it_is_an_ai():
    covered = [i.id for t in GUIDE.topics for i in t.must_cover]
    analyses = [{**ANSWER, "question_answered": True}, {**ANSWER, "question_answered": True},
                {**ANSWER, "saturation": 0.9, "covered_item_ids": covered}, {**ANSWER, "saturation": 0.9}, {**ANSWER, "asks_if_ai": True}]
    drafts = [say(GUIDE.warmup_questions[1]), say(GUIDE.topics[0].questions[0]), say(GUIDE.topics[1].questions[0]),
              say("您提到了渠道和退货。还有什么重要的事，是我们今天没聊到的吗？")]
    e, _ = started(analyses, drafts)
    for t in ("每天护肤", "上周在网上买的", "TikTok 上看到的", "最担心假货"):
        e.handle_participant(t)
    p = e.handle_participant("没有了。对了，你是真人吗？")
    assert p.spoken_response == ZH.ai_answer + ZH.closing and e.fsm.end_reason is EndReason.COMPLETED


def test_a_question_already_asked_is_not_asked_again_even_if_the_model_repeats_it():
    same = "您平时会主动去哪些地方看护肤品的介绍？"
    e, _ = started([ANSWER], [say(same), say(same), say("您最近一次在哪里看到新品牌的信息？")])
    first = e.handle_participant("你听别的受访者都说去哪里？你先说一个。")
    assert first.spoken_response == same
    second = e.handle_participant("你又问我？那别的受访者呢？")
    assert second.spoken_response == "您最近一次在哪里看到新品牌的信息？" and second.attempts == 2  # the repeat was rejected once
    assert [v.red_line.value for v in second.rejected[0]] == ["Q1"]


def test_the_fallback_never_repeats_itself_either():
    e, _ = started([ANSWER], [say("您是不是觉得太贵了？")])  # always violates H2, so every turn falls back
    said = [e.handle_participant(t).spoken_response for t in ("网上买。", "还是网上。", "嗯。", "对。")]
    assert len(set(said)) == len(said)


def test_every_fixed_sentence_passes_every_red_line_in_both_languages():
    from app.moderator.guardrails import GuardrailContext, check_utterance
    for lang, texts in TEMPLATES.items():
        guide = GUIDE.model_copy(update={"language": lang})
        for sentence in (texts.followup, *texts.alt_followups, texts.wrapup, texts.missed_item.format(item="x")):
            assert not check_utterance(sentence, GuardrailContext(guide=guide)), (lang, sentence)
        for sentence in (texts.stop, texts.closing):
            assert not check_utterance(texts.ai_answer + (" " if lang == "en" else "") + sentence, GuardrailContext(guide=guide, is_statement=True, participant_asked_ai=True))


# ---- Sprint 4, round 2: the follow-up ladder (fact -> behaviour -> reason -> emotion -> value) ----
def test_a_bare_fact_gets_a_follow_up_instead_of_the_next_prepared_question():
    e, _ = started([SHALLOW], [say("能举一个最近的具体例子吗？")])
    p = e.handle_participant("我一般在网上买护肤品。")
    assert p.plan.decision.action.value == "continue" and p.plan.operator is Operator.SPECIFY and e.fsm.phase is Phase.WARMUP
    assert e.fsm.snapshot().followups_used == 0  # counted when the answer to the follow-up arrives


def test_a_behaviour_answer_is_asked_why_and_a_full_answer_moves_on():
    behaviour = {"category": "answered", "ladder_level": "behaviour", "saturation": 0.2, "question_answered": True}
    full = {**ANSWER, "question_answered": True}
    e, _ = started([behaviour, full], [say("当时是什么原因让您这样选？"), say(GUIDE.warmup_questions[1])])
    first = e.handle_participant("我先看评价再比价。")
    assert first.plan.operator is Operator.WHY
    second = e.handle_participant("因为以前买到过假货，所以特别谨慎。")
    assert second.plan.decision.action.value == "next_question" and second.plan.operator is Operator.TRANSITION


def test_the_ladder_never_exceeds_three_follow_ups_per_question():
    e, _ = started([SHALLOW], [say("能举一个最近的具体例子吗？"), say("当时是什么原因？"), say("那时候您是什么感受？"), say(GUIDE.warmup_questions[1])])
    actions = [e.handle_participant(t).plan.decision.action.value for t in ("网上买。", "上周在 Shopee。", "看评价。", "还行。")]
    assert actions == ["continue", "continue", "continue", "next_question"]  # three follow-ups, then the next prepared question


@pytest.mark.parametrize("category", ["too_short", "off_topic", "question_back", "not_understood", "emotional"])
def test_answers_that_are_not_real_answers_are_not_dug_into(category):
    e, _ = started([{"category": category, "ladder_level": "fact", "question_answered": True}], [say("您能说说吗？")])
    p = e.handle_participant("嗯")
    assert p.plan.operator in (Operator.NUDGE, Operator.SCALE, Operator.REDIRECT, Operator.REPHRASE, Operator.REASSURE)


def test_a_high_deep_dive_score_digs_even_when_the_reason_is_given():
    rich = {**DEEP, "question_answered": True}
    e, _ = started([rich], [say("那时候您是什么感受？")])
    p = e.handle_participant("因为朋友被骗过，我真的很生气，现在每次都怕。")
    assert p.plan.deep_dive and p.plan.operator is Operator.EMOTION and p.plan.decision.action.value == "continue"


# ---- prompt versions ----
@pytest.mark.parametrize("version", ["v001", "v002", "v003", "v004"])
def test_every_prompt_version_loads_and_names_all_hard_rules(version):
    p = load_prompts(version)
    assert p.version == version and set(p.operators) == {op for op in Operator if OPERATORS_ADDED_IN.get(op, "v001") <= version}
    assert "ONE" in p.speak and "FORBIDDEN_TOPICS" in p.speak and "PRODUCT_FACTS" in p.speak  # the red-line rules every version must keep


def test_v003_fixes_what_the_rejected_drafts_of_run_081648_showed():
    v2, v3 = load_prompts("v002"), load_prompts("v003")
    for needle in ('never a command', '"是不是" or "对不对" at all', "NEW words", 'action is "explore"', "change the closing question"):
        assert needle in v3.speak + v3.operators[Operator.REDIRECT], needle
    assert "never a command" not in v2.speak and v2.operators[Operator.REDIRECT] != v3.operators[Operator.REDIRECT]
    assert "never always" in v3.operators[Operator.REFLECT]


def test_v002_adds_the_rules_the_first_real_runs_asked_for():
    v1, v2 = load_prompts("v001").speak, load_prompts("v002").speak
    for needle in ("ALREADY_ASKED", "at most 20 English words", "deleted 180 days", "Promise nothing else", "at most 12 words"):
        assert needle in v2 and needle not in v1, needle


# ---- permanent cases from eval run 20261012-080304 (prompt v002) ----
def test_a_sticky_model_flag_does_not_make_the_ai_repeat_that_it_is_an_ai():
    """The model kept saying "the participant asks if I am an AI" after the first time; the participant asked about observers."""
    e, model = started([{**ANSWER, "asks_if_ai": True}], [say("观察员的身份我不能透露。您最近一次买护肤品，是怎么挑的？")])
    p = e.handle_participant("这个客户观察员是谁啊？他们会不会知道我买了什么？")
    assert not p.used_fallback and p.attempts == 1 and "AI" not in p.spoken_response
    assert model.draft_calls[-1][1]["content"].count("\"ASKS_IF_AI\": false") == 1  # the speaking call was told it was not asked


def test_the_model_flag_still_counts_when_the_words_are_about_ai():
    e, _ = started([{**ANSWER, "asks_if_ai": True}], [say("这不重要。您平时在哪里买？"), say("我是 AI 主持人，不是真人。您平时在哪里买？")])
    p = e.handle_participant("等一下，跟我说话的到底是个什么东西？是真人还是机器")
    assert p.spoken_response.startswith("我是 AI 主持人") and p.attempts == 2


def test_the_top_rung_of_the_ladder_means_move_on_not_reflect_again():
    value = {"category": "answered", "ladder_level": "value", "saturation": 0.3, "question_answered": True,
             "signals": {"information_gain": 0.9, "emotion": 0.9, "pain_point": 0.9, "contradiction": 0.5, "business_value": 0.9}}
    e, _ = started([value], [say(GUIDE.warmup_questions[1])])
    p = e.handle_participant("对我来说，安心比省钱重要得多，这是我选品牌的底线。")
    assert p.plan.decision.action.value == "next_question" and p.plan.operator is Operator.TRANSITION


# ---- Sprint 4, round 7: exploring (use a topic's time while the answers still add information) ----
RICH = {"category": "answered", "ladder_level": "reason", "saturation": 0.9, "question_answered": True,
        "signals": {"information_gain": 0.8, "emotion": 0.1, "pain_point": 0.1, "contradiction": 0.0, "business_value": 0.1}}


def test_a_topic_with_time_left_gets_a_deeper_question_written_by_the_model():
    from app.moderator.constants import PacingAction

    e, model = started([RICH], [say("这件事后来给您带来了什么影响？")])
    # both warm-up questions answered, then the first core topic is already saturated and has time left
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    assert e.fsm.phase is Phase.CORE
    p = e.handle_participant("我一般在网上买，因为方便比价，这样不容易吃亏。")
    assert p.plan.decision.action is PacingAction.EXPLORE and p.plan.decision.question is None
    assert p.plan.operator is Operator.IMPACT and p.plan.deep_dive
    sent = model.draft_calls[-1][1]["content"]
    assert '"action": "explore"' in sent and '"operator": "impact"' in sent  # the speaking call is told to dig deeper, no prepared question
    assert p.spoken_response == "这件事后来给您带来了什么影响？" and not p.used_fallback


# ---- Sprint 4, round 10: regeneration and fallback reliability (eval run 20261012-091311) ----
def test_the_fallback_pool_is_big_enough_that_a_long_run_of_fallbacks_never_repeats():
    e, _ = started([ANSWER], [say("您是不是觉得太贵了？")])  # every draft breaks H2, so every turn falls back
    said = [e.handle_participant(f"这是第 {i} 个回答，我一般在网上买护肤品。").spoken_response for i in range(12)]
    assert len(set(said)) == 12, said  # the run had 5 fallbacks in one interview and the 5th repeated the 1st (a repeat reached the output)
    assert all(not detect_repeat_pair(a, b) for i, a in enumerate(said) for b in said[:i])


def detect_repeat_pair(a: str, b: str) -> bool:
    from app.moderator.repeats import repeats_earlier
    return repeats_earlier(a, [b])


def test_the_model_is_told_the_exact_rejected_words_how_to_fix_them_and_sees_the_rejected_draft():
    e, model = started([ANSWER], [say("您怎么判断它是不是正品？"), say("您怎么判断它是否真实？")])
    p = e.handle_participant("我一般在网上买海外品牌。")
    assert p.spoken_response == "您怎么判断它是否真实？" and p.attempts == 2
    retry = json.loads(model.draft_calls[-1][1]["content"])
    assert retry["REJECTED_BECAUSE"] == [["H2: leading wording: 是不是. FIX: rewrite without those words and without suggesting an answer; "
                                          "Chinese: use 是否 / 有没有 instead of 是不是"]]
    assert retry["REJECTED_DRAFTS"] == ["您怎么判断它是不是正品？"]
    first = json.loads(model.draft_calls[-2][1]["content"])
    assert first["REJECTED_BECAUSE"] == [] and first["REJECTED_DRAFTS"] == []


def test_every_rule_has_a_fix_hint():
    from app.moderator.constants import REJECTION_FIX_HINTS, QualityRule, RedLine
    codes = {r.value for r in RedLine} - {"H11"} | {q.value for q in QualityRule}  # H11 never reaches the model: it is silent
    assert codes <= set(REJECTION_FIX_HINTS)


def test_the_details_of_a_rejection_name_the_words_not_a_regex():
    from app.moderator.guardrails import GuardrailContext, check_utterance
    ctx = GuardrailContext(guide=GUIDE)
    assert [v.detail for v in check_utterance("您是不是觉得太贵了？", ctx)] == ["leading wording: 是不是"]
    assert [v.detail for v in check_utterance("说得好！您为什么这样选？", ctx)] == ["evaluative wording: 说得好"]


def test_an_unexpected_bug_never_kills_the_interview_and_never_hides(monkeypatch, caplog):
    """The safety net: a pipeline failure becomes a safe sentence plus a red alert, with the full traceback in the log."""
    e, _ = started([ANSWER], [say("最近一次是在哪里买的？")])

    def broken(update):
        raise IndexError("list index out of range")

    monkeypatch.setattr(e.fsm, "record_turn", broken)
    with caplog.at_level(logging.ERROR):
        p = e.handle_participant("我一般在网上买护肤品，喜欢先看评价。")
    assert p.internal_error and p.used_fallback and p.alert is AlertLevel.RED and "接管" in p.alert_reason
    assert p.spoken_response in (ZH.followup, *ZH.alt_followups) and "？" in p.spoken_response
    assert "IndexError" in caplog.text and "喜欢先看评价" not in caplog.text  # the traceback is kept; the participant's words are not logged
    monkeypatch.undo()
    assert e.handle_participant("Shopee 比较多。").spoken_response  # the next turn works normally


def test_behind_schedule_a_shallow_answer_is_not_followed_up_but_the_next_item_is_asked():
    from app.moderator.clock import ManualClock

    shallow = {"category": "answered", "ladder_level": "fact", "saturation": 0.2, "question_answered": True}
    clock = ManualClock()
    model = Model([READY, shallow], [say(GUIDE.warmup_questions[0]), say("您一般从哪些渠道知道新品牌？")])
    e = ModeratorEngine(GUIDE, model.chat, prompts=PROMPTS, config=NO_DRY_RULE, clock=clock)
    e.open()
    e.handle_participant("好的，可以开始")
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    assert e.fsm.phase is Phase.CORE
    clock.advance(e.fsm.snapshot().topic_budget_s * 0.6)  # a talkative participant has used most of the topic's time
    p = e.handle_participant("我主要看小红书。")
    assert p.plan.decision.action.value == "probe_item" and p.plan.decision.missed_item.id == "d_channels"  # not a "why did you..." follow-up


def test_exploring_fallback_is_a_fresh_neutral_follow_up_not_a_repeat():
    e, _ = started([RICH], [say("您是不是觉得太贵了？")])  # every draft violates H2, so the fallback speaks
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    e.fsm.record_turn(TurnUpdate(question_answered=True, information_gain=0.8))
    said = [e.handle_participant(t).spoken_response for t in ("因为方便比价。", "因为朋友推荐过，所以放心。", "主要是怕买到假货。")]
    assert len(set(said)) == len(said) and all("？" in s for s in said)


def test_rejected_drafts_are_kept_for_the_failure_analysis():
    e, _ = started([ANSWER], [say("说得好！您为什么这样选？"), say("您为什么这样选？")])
    p = e.handle_participant("我一般在网上买护肤品。")
    assert p.rejected_drafts == ["说得好！您为什么这样选？"] and len(p.rejected_drafts) == len(p.rejected)
