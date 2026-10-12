"""Probe operators: which kind of question to ask this turn (task doc 4.3). Exactly one per turn, chosen by code.

The state machine decides *whether* to stay on the question; this policy decides *how* to ask. Deep-dive weights and
threshold come from constants (initial values; the benchmark calibrates them).
"""
from pydantic import BaseModel, ConfigDict

from .constants import (DEEP_DIVE_THRESHOLD, DEEP_DIVE_WEIGHTS, NUDGE_MAX_CHARS, LadderLevel, Operator, PacingAction,
                        ResponseCategory)
from .fsm import PacingDecision
from .schemas import RespondentInputAnalysis

# The next rung of the ladder fact -> behaviour -> reason -> emotion -> value; at the top, confirm by reflecting back.
NEXT_RUNG: dict[LadderLevel, Operator] = {
    LadderLevel.FACT: Operator.SPECIFY, LadderLevel.BEHAVIOUR: Operator.WHY, LadderLevel.REASON: Operator.EMOTION,
    LadderLevel.EMOTION: Operator.IMPACT, LadderLevel.VALUE: Operator.REFLECT,
}
# Extra deeper questions in a topic that has time left, in turn: impact, comparison, feeling, indirect, reason, concrete example.
EXPLORE_ROTATION: tuple[Operator, ...] = (Operator.IMPACT, Operator.CONTRAST, Operator.EMOTION, Operator.PROJECTIVE, Operator.WHY, Operator.SPECIFY)
# The answer itself says how to respond, whatever the scores.
BY_CATEGORY: dict[ResponseCategory, Operator] = {
    ResponseCategory.NOT_UNDERSTOOD: Operator.REPHRASE, ResponseCategory.EMOTIONAL: Operator.REASSURE,
    ResponseCategory.SENSITIVE: Operator.REASSURE, ResponseCategory.OFF_TOPIC: Operator.REDIRECT,
    ResponseCategory.QUESTION_BACK: Operator.REDIRECT, ResponseCategory.CONTRADICTION: Operator.CLARIFY,
}
# Answers that stay at these rungs are not deep enough yet: the ladder says ask one rung higher (task doc 4.3 追问阶梯).
SHALLOW_RUNGS = frozenset({LadderLevel.FACT, LadderLevel.BEHAVIOUR})
# Only a real answer is worth digging into; the other categories have their own operator (rephrase, reassure, redirect, ...).
DIGGABLE_CATEGORIES = frozenset({ResponseCategory.ANSWERED})

# A new prepared question, topic, item or phase is due: say it (with a short bridge), do not probe the old one.
NEW_QUESTION_ACTIONS = frozenset({PacingAction.NEXT_QUESTION, PacingAction.NEXT_TOPIC, PacingAction.ENTER_PHASE,
                                  PacingAction.PROBE_ITEM, PacingAction.ASK_MISSED_ITEM})
# Operators that do not count as a follow-up on the current question (the cap of 3 counts only real probes).
NOT_FOLLOWUPS = frozenset({Operator.TRANSITION, Operator.REPHRASE, Operator.REASSURE, Operator.WAIT})


class OperatorChoice(BaseModel):
    """The chosen operator and the deep-dive score behind it."""

    model_config = ConfigDict(frozen=True)

    operator: Operator
    deep_dive_score: float
    deep_dive: bool


def choose_operator(analysis: RespondentInputAnalysis, decision: PacingDecision, participant_text: str,
                    weights: dict[str, float] = DEEP_DIVE_WEIGHTS, threshold: float = DEEP_DIVE_THRESHOLD) -> OperatorChoice:
    """Pick one operator for this turn.

    Args:
        analysis: the model's reading of the latest answer.
        decision: the state machine's decision for this turn.
        participant_text: the answer itself (its length decides nudge vs. scale).
        weights: deep-dive signal weights.
        threshold: score at or above which the moderator digs deeper.

    Returns:
        The operator, the deep-dive score and whether it is a deep dive.
    """
    score = min(1.0, analysis.signals.deep_dive_score(weights))
    deep = score >= threshold
    if decision.action in NEW_QUESTION_ACTIONS:
        return OperatorChoice(operator=Operator.TRANSITION, deep_dive_score=score, deep_dive=False)
    if operator := BY_CATEGORY.get(analysis.category):
        return OperatorChoice(operator=operator, deep_dive_score=score, deep_dive=False)
    if analysis.category is ResponseCategory.TOO_SHORT:
        short = len(participant_text.strip()) < NUDGE_MAX_CHARS
        return OperatorChoice(operator=Operator.NUDGE if short else Operator.SCALE, deep_dive_score=score, deep_dive=False)
    if decision.action is PacingAction.EXPLORE:  # the topic has time left: a new deeper question, a different kind each time
        return OperatorChoice(operator=EXPLORE_ROTATION[(decision.explore_index or 0) % len(EXPLORE_ROTATION)], deep_dive_score=score, deep_dive=True)
    if not decision.followup_allowed:
        return OperatorChoice(operator=Operator.TRANSITION, deep_dive_score=score, deep_dive=False)
    operator = NEXT_RUNG[analysis.ladder_level] if (deep or analysis.ladder_level in SHALLOW_RUNGS) else Operator.SPECIFY
    return OperatorChoice(operator=operator, deep_dive_score=score, deep_dive=deep)


def wants_deeper(analysis: RespondentInputAnalysis, weights: dict[str, float] = DEEP_DIVE_WEIGHTS, threshold: float = DEEP_DIVE_THRESHOLD) -> bool:
    """Whether the answer should be followed up rather than moving on to the next prepared question.

    Yes when it is a real answer that is either still shallow (fact / behaviour: no reason given yet) or scores high on the
    deep-dive signals (information gain, emotion, pain point, contradiction, business value). The caller adds the limits:
    follow-ups left on this question and time left in the topic.

    Args:
        analysis: the model's reading of the latest answer.
        weights: deep-dive signal weights.
        threshold: score at or above which the moderator digs deeper.
    """
    if analysis.category not in DIGGABLE_CATEGORIES or analysis.ladder_level is LadderLevel.VALUE:
        return False  # not a real answer, or already at the top rung: there is nothing deeper to ask
    return analysis.ladder_level in SHALLOW_RUNGS or analysis.signals.deep_dive_score(weights) >= threshold


def counts_as_followup(operator: Operator) -> bool:
    """Whether a question asked with this operator uses up one of the current question's follow-ups."""
    return operator not in NOT_FOLLOWUPS
