"""A refused model service (no balance, bad key, ...) must never be worked around, and must stop an evaluation within seconds.

Why: on 2026-10-12 the DeepSeek balance ran out a few minutes into the final evaluation (HTTP 402). The engine kept answering with
fallback sentences, ~40 000 refused calls later 4 hours of garbage looked like results (1.3e4 "repeated questions", red lines "0")."""
import json
import logging

import httpx
import pytest

from app.config import settings
from app.moderator import providers
from app.moderator.clock import ManualClock
from app.moderator.constants import ALERT_SERVICE_UNAVAILABLE, FATAL_HTTP_REASONS, AlertLevel
from app.moderator.engine import ModeratorEngine
from app.moderator.knowledge import InterviewGuide
from app.moderator.providers import ChatError, ChatFatalError, MockModeratorChat, OpenAICompatModeratorChat
from eval import runner
from eval.constants import Intensity, PersonaType
from eval.models import load_guide, load_personas
from eval.session import ServiceUnavailableError
from eval.simulator import ModelParticipant

GUIDE: InterviewGuide = load_guide("sg_skincare_zh")


def _server(monkeypatch, statuses: list[int]) -> list[int]:
    """httpx.post answers the given statuses in turn (the last repeats); 200 carries a valid JSON reply. Returns the call log."""
    calls: list[int] = []

    def post(url, **kw):
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(status)
        body = {"choices": [{"message": {"content": '{"ok": true}'}}]} if status == 200 else {"error": "x"}
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(providers.httpx, "post", post)
    return calls


def _chat(monkeypatch) -> OpenAICompatModeratorChat:
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    return OpenAICompatModeratorChat(settings)


@pytest.mark.parametrize("status, word", [(401, "密钥"), (402, "余额"), (403, "权限"), (404, "模型名")])
def test_a_refusal_is_fatal_named_in_plain_chinese_and_never_retried(monkeypatch, status, word):
    calls = _server(monkeypatch, [status])
    with pytest.raises(ChatFatalError) as e:
        _chat(monkeypatch).complete([])
    assert calls == [status] and e.value.status_code == status and word in e.value.reason and e.value.reason == FATAL_HTTP_REASONS[status]


def test_rate_limits_are_waited_out_not_fatal(monkeypatch):
    calls = _server(monkeypatch, [429, 429, 200])
    assert _chat(monkeypatch).complete([]) == {"ok": True} and calls == [429, 429, 200]


def test_other_client_errors_stay_ordinary_errors(monkeypatch):
    _server(monkeypatch, [400])
    with pytest.raises(ChatError) as e:
        _chat(monkeypatch).complete([])
    assert not isinstance(e.value, ChatFatalError)


def test_the_fatal_error_is_not_swallowed_into_a_neutral_reading_or_a_fallback_draft():
    from app.moderator.model_calls import ModelCaller
    from app.moderator.prompts import load_prompts

    caller = ModelCaller(MockModeratorChat([ChatFatalError(402)]), load_prompts())
    with pytest.raises(ChatFatalError):
        caller.analyze(_ctx(), {}, "我一般在网上买。")
    with pytest.raises(ChatFatalError):
        caller.draft(_ctx(), _plan(), False, [])
    ordinary = ModelCaller(MockModeratorChat([ChatError("x")]), load_prompts())
    assert ordinary.analyze(_ctx(), {}, "我一般在网上买。") is None  # an ordinary failure still degrades gracefully


def _engine(chat):
    e = ModeratorEngine(GUIDE, chat, clock=ManualClock())
    e.open()
    return e


def _ctx():
    e = _engine(MockModeratorChat())
    return e.memory.build_context(GUIDE, e.fsm.snapshot(), e._peek_decision())


def _plan():
    from app.moderator.constants import Operator
    from app.moderator.schemas import DialogueActionPlan

    e = _engine(MockModeratorChat())
    return DialogueActionPlan(operator=Operator.SPECIFY, decision=e._peek_decision(), deep_dive_score=0.0, deep_dive=False)


def test_in_a_live_interview_a_refusal_becomes_a_safe_sentence_and_a_specific_red_alert(caplog):
    e = _engine(MockModeratorChat([ChatFatalError(402)]))
    with caplog.at_level(logging.ERROR):
        p = e.handle_participant("好的，可以开始")
    assert p.internal_error and p.fatal_service_error and p.alert is AlertLevel.RED
    assert p.alert_reason == ALERT_SERVICE_UNAVAILABLE.format(reason=FATAL_HTTP_REASONS[402]) and "余额" in p.alert_reason
    assert p.spoken_response and "？" in p.spoken_response  # the participant is not left in silence


def test_a_simulated_participant_does_not_turn_a_refusal_into_a_shy_answer():
    persona = load_personas()[PersonaType.SILENT]
    with pytest.raises(ChatFatalError):
        ModelParticipant(MockModeratorChat([ChatFatalError(402)]), persona, Intensity.LOW, GUIDE, 1).reply("您好？", [])


# ---------- the evaluation stops, fast, with no report ----------
def test_preflight_names_the_model_and_the_reason(monkeypatch):
    monkeypatch.setattr(runner, "OpenAICompatModeratorChat", lambda *a, **k: MockModeratorChat([ChatFatalError(402)]))
    with pytest.raises(ServiceUnavailableError) as e:
        runner.preflight("model", "rule", False)
    assert "被测的 AI 主持人" in str(e.value) and "402" in str(e.value) and "余额" in str(e.value)


def test_preflight_checks_every_real_model_and_nothing_for_the_offline_setup(monkeypatch):
    made = []

    def fake(*a, **k):
        made.append(1)
        return MockModeratorChat([{"ok": True}])

    monkeypatch.setattr(runner, "OpenAICompatModeratorChat", fake)
    runner.preflight("mock", "rule", False)
    assert made == []
    runner.preflight("model", "model", True)
    assert len(made) == 3  # the moderator, the simulated participant, the judge


def test_preflight_says_so_when_the_service_cannot_be_reached(monkeypatch):
    monkeypatch.setattr(runner, "OpenAICompatModeratorChat", lambda *a, **k: MockModeratorChat([ChatError("ConnectError")]))
    with pytest.raises(ServiceUnavailableError) as e:
        runner.preflight("model", "rule", False)
    assert "连不上" in str(e.value)


def test_a_refusal_in_the_middle_of_a_run_stops_all_sessions_and_writes_no_report(monkeypatch, tmp_path, capsys):
    from eval import report as report_mod

    monkeypatch.setattr(report_mod, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(runner, "preflight", lambda *a: None)  # the balance runs out only after the preflight
    started = []

    def run_one(scenario, brain, participant, judge):
        started.append(scenario.id)
        if len(started) >= 3:
            raise ServiceUnavailableError("AI 模型服务拒绝了请求（HTTP 402：账号余额不足）")
        return _quick(scenario)  # the first two sessions finish normally

    monkeypatch.setattr(runner, "run_one", run_one)
    code = runner.main(["--brain", "mock", "--participant", "rule", "--split", "train", "--workers", "2"])
    out = capsys.readouterr().out
    assert code == runner.EXIT_SERVICE_UNAVAILABLE == 2
    assert "评估中止" in out and "402" in out and "没有生成报告" in out
    assert not list(tmp_path.iterdir())  # no report, no transcripts
    assert len(started) < 20  # the other ~230 scenarios never started


def _quick(scenario):
    from eval.checkers import SessionCheck
    from eval.session import SessionResult

    result = SessionResult(scenario_id=scenario.id, guide_id=scenario.guide_id, language="zh", intro="x", turns=[], end_reason=None, elapsed_s=0,
                           planned_s=1, covered=[], asked=[], must_cover=[], topic_ids=[], transitions=[])
    return result, SessionCheck(scenario_id=scenario.id, coverage=None, repeats=0, followups=0, followup_hits=0, core_topics_reached=0, deep_topics=0,
                                time_deviation=None, latencies=[], fallbacks=0, completed=False), None


def test_a_bug_in_the_evaluation_itself_is_not_mistaken_for_a_refused_service(monkeypatch):
    monkeypatch.setattr(runner, "preflight", lambda *a: None)

    def run_one(scenario, brain, participant, judge):
        raise KeyError("a bug")

    monkeypatch.setattr(runner, "run_one", run_one)
    with pytest.raises(KeyError):
        runner.run(None, [0], "mock", "rule", False, 4, 2, None)
