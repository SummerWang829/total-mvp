"""Run one scenario: the engine interviews a simulated participant on a simulated clock, and every turn is recorded."""
import logging

from pydantic import BaseModel, ConfigDict, Field

from app.moderator.clock import ManualClock
from app.moderator.constants import AlertLevel, ControlMode, EndReason, Operator, Phase, QualityRule, RedLine
from app.moderator.engine import ModeratorEngine
from app.moderator.knowledge import InterviewGuide
from app.moderator.providers import ModeratorChatProvider
from app.moderator.schemas import ModeratorTurnPayload

from .constants import (AI_NOMINAL_LATENCY_S, CHARS_PER_SECOND_ZH, MAX_TURNS_PER_SESSION, THINK_PAUSE_S, WORDS_PER_SECOND_EN, EventKind)
from .models import Scenario
from .simulator import Participant

log = logging.getLogger(__name__)
HUMAN_LINE = {"zh": "我是研究员，我来接着问：您平时最常在哪里买？", "en": "I'm a researcher stepping in: where do you usually buy?"}


class TurnRecord(BaseModel):
    """One exchange: the participant's answer and what the AI said back (None while a human was in control)."""

    model_config = ConfigDict(frozen=True)

    index: int
    participant: str
    ai: str | None
    phase: Phase
    operator: Operator | None = None
    deep_dive: bool = False
    information_gain: float = 0.0
    ladder: str | None = None
    topic_id: str | None = None
    covered: list[str] = Field(default_factory=list)
    rejected: list[list[RedLine | QualityRule]] = Field(default_factory=list)
    rejected_drafts: list[str] = Field(default_factory=list)
    internal_error: bool = False
    used_fallback: bool = False
    alert: AlertLevel = AlertLevel.NONE
    latency_s: float = 0.0
    control: ControlMode = ControlMode.AI
    asked_ai: bool = False


class SessionResult(BaseModel):
    """Everything the checkers need about one simulated interview."""

    model_config = ConfigDict(frozen=True)

    scenario_id: str
    guide_id: str
    language: str
    intro: str
    turns: list[TurnRecord]
    end_reason: EndReason | None
    elapsed_s: float
    planned_s: float
    covered: list[str]
    asked: list[str]  # items the moderator asked about on purpose (probe / recovery)
    must_cover: list[str]
    topic_ids: list[str]
    transitions: list[dict]
    error: str | None = None


def speaking_seconds(text: str | None, language: str) -> float:
    """Voice-equivalent time to say a line (so text runs measure the same pacing a voice interview would have)."""
    if not text:
        return 0.0
    if language == "zh":
        return len(text) / CHARS_PER_SECOND_ZH
    return len(text.split()) / WORDS_PER_SECOND_EN


def run_session(scenario: Scenario, guide: InterviewGuide, chat: ModeratorChatProvider, participant: Participant) -> SessionResult:
    """Interview the simulated participant until the engine ends the interview (or the safety cap is hit).

    Args:
        scenario: what to run.
        guide: the scenario's guide (already shortened for non-long scenarios).
        chat: the moderator's model (real, or the mock brain).
        participant: the simulated participant.

    Returns:
        The full record; `error` is set if the run broke (reported as a failure, never hidden).
    """
    clock = ManualClock()
    engine = ModeratorEngine(guide, chat, clock=clock, seed=scenario.seed)
    intro = engine.open().spoken_response or ""
    history: list[tuple[str, str]] = [("interviewer", intro)]
    clock.advance(speaking_seconds(intro, guide.language) + THINK_PAUSE_S)
    turns: list[TurnRecord] = []
    error = None
    last_ai = intro
    try:
        for index in range(1, MAX_TURNS_PER_SESSION + 1):
            said = _apply_events(scenario, index, engine, guide.language)
            answer = said or participant.reply(last_ai, history)
            clock.advance(speaking_seconds(answer, guide.language) + THINK_PAUSE_S)
            control = engine.control
            payload = engine.handle_participant(answer)
            if payload.spoken_response:
                clock.advance(AI_NOMINAL_LATENCY_S)  # a fixed nominal reply time: the interview's length must not depend on how fast the model API is today
            history.append(("participant", answer))
            if control is ControlMode.HUMAN:
                engine.add_human_turn(HUMAN_LINE[guide.language])
                history.append(("interviewer", HUMAN_LINE[guide.language]))
            turns.append(_record(index, answer, payload, engine, control))
            if payload.spoken_response:
                history.append(("interviewer", payload.spoken_response))
                last_ai = payload.spoken_response
                clock.advance(speaking_seconds(payload.spoken_response, guide.language) + THINK_PAUSE_S)
            if engine.fsm.phase is Phase.ENDED:
                break
        else:
            error = f"did not end within {MAX_TURNS_PER_SESSION} turns"
    except Exception as e:  # noqa: BLE001 — one broken scenario must not stop the batch; it is reported as a failure
        log.exception("scenario %s broke", scenario.id)
        error = f"{type(e).__name__}: {e}"
    return SessionResult(
        scenario_id=scenario.id, guide_id=scenario.guide_id, language=guide.language, intro=intro, turns=turns,
        end_reason=engine.fsm.end_reason, elapsed_s=clock.now(), planned_s=engine.fsm.budget.total_s,
        covered=sorted(engine.fsm.covered_items), asked=sorted(engine.fsm.asked_items), must_cover=[i.id for t in guide.topics for i in t.must_cover],
        topic_ids=[t.id for t in guide.topics], transitions=[t.model_dump(mode="json") for t in engine.fsm.transitions], error=error)


def _apply_events(scenario: Scenario, index: int, engine: ModeratorEngine, language: str) -> str | None:
    """Run the scripted events due before answer number `index`; return a scripted answer if there is one."""
    said = None
    for event in (e for e in scenario.events if e.turn == index):
        if event.kind is EventKind.WHISPER:
            engine.add_whisper(event.text)
        elif event.kind is EventKind.TAKE_OVER:
            engine.take_over()
        elif event.kind is EventKind.HAND_BACK:
            engine.hand_back()
        elif event.kind is EventKind.SAY:
            said = event.text
    return said


def _record(index: int, answer: str, payload: ModeratorTurnPayload, engine: ModeratorEngine, control: ControlMode) -> TurnRecord:
    """Flatten one engine turn for the checkers."""
    analysis, plan = payload.analysis, payload.plan
    return TurnRecord(
        index=index, participant=answer, ai=payload.spoken_response, phase=engine.fsm.phase,
        operator=plan.operator if plan else None, deep_dive=plan.deep_dive if plan else False,
        information_gain=analysis.signals.information_gain if analysis else 0.0, ladder=analysis.ladder_level.value if analysis else None,
        topic_id=plan.decision.topic_id if plan else None, covered=list(analysis.covered_item_ids) if analysis else [],
        rejected=[[v.red_line for v in vs] for vs in payload.rejected], rejected_drafts=payload.rejected_drafts, internal_error=payload.internal_error, used_fallback=payload.used_fallback, alert=payload.alert,
        latency_s=payload.latency_s, control=control, asked_ai=bool(analysis and analysis.asks_if_ai))
