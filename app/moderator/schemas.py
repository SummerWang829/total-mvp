"""Structured model output (task doc 4.4). Every model reply is validated against these before anything else uses it."""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import DEEP_DIVE_WEIGHTS, NEW_FACTS_PER_TURN, SUMMARY_MAX_CHARS, AlertLevel, LadderLevel, Operator, QualityRule, RedLine, ResponseCategory
from .fsm import PacingDecision

NOTE_FIELD_MAX_CHARS = 400  # InternalNote fields are capped at this length


class Signals(BaseModel):
    """Five 0-1 signals that decide whether to dig deeper (task doc 4.2)."""

    model_config = ConfigDict(frozen=True)

    information_gain: float = Field(default=0.0, ge=0, le=1)
    emotion: float = Field(default=0.0, ge=0, le=1)
    pain_point: float = Field(default=0.0, ge=0, le=1)
    contradiction: float = Field(default=0.0, ge=0, le=1)
    business_value: float = Field(default=0.0, ge=0, le=1)

    def deep_dive_score(self, weights: dict[str, float] = DEEP_DIVE_WEIGHTS) -> float:
        """Weighted sum of the signals (weights from config, calibrated by the benchmark).

        Raises:
            KeyError: a weight names a signal that does not exist.
        """
        return sum(getattr(self, name) * weight for name, weight in weights.items())


class RespondentInputAnalysis(BaseModel):
    """The model's reading of the participant's latest answer."""

    model_config = ConfigDict(frozen=True)

    category: ResponseCategory = ResponseCategory.ANSWERED
    emotion_label: str = Field(default="neutral", max_length=40)
    off_topic: bool = False
    ladder_level: LadderLevel = LadderLevel.FACT
    signals: Signals = Field(default_factory=Signals)
    saturation: float = Field(default=0.0, ge=0, le=1)           # of the current topic
    covered_item_ids: list[str] = Field(default_factory=list)
    question_answered: bool = False
    ready_to_start: bool = False                                  # intro: the participant agreed to begin
    asks_if_ai: bool = False                                      # "are you a real person?" (red line H3)
    new_facts: list[str] = Field(default_factory=list, max_length=NEW_FACTS_PER_TURN * 2)  # facts the participant just stated, to remember all interview


class InternalNote(BaseModel):
    """Why the moderator says what it says. Logged for the research team; never shown to the participant."""

    model_config = ConfigDict(frozen=True)

    research_purpose: str = Field(default="", max_length=400)
    expected_information: str = Field(default="", max_length=400)
    probe_logic: str = Field(default="", max_length=400)


class SpokenDraft(BaseModel):
    """What the speaking call must return."""

    model_config = ConfigDict(frozen=True)

    spoken_response: str = Field(min_length=1, max_length=600)
    note: InternalNote = Field(default_factory=InternalNote)

    @field_validator("note", mode="before")
    @classmethod
    def _lenient_note(cls, value: object) -> object:
        """The note is only a memo for the research team: a model that writes it as a sentence, a list or with too-long
        fields must not cost us the whole reply (found in the first DeepSeek run, 2026-10-12). What cannot be used is dropped."""
        if isinstance(value, str):
            return {"research_purpose": value[:NOTE_FIELD_MAX_CHARS]}
        if not isinstance(value, dict):
            return {}
        usable = {k: v[:NOTE_FIELD_MAX_CHARS] for k, v in value.items() if k in InternalNote.model_fields and isinstance(v, str)}
        return usable


class DialogueActionPlan(BaseModel):
    """What was chosen for this turn: by the state machine (flow) and the operator policy (how to ask)."""

    model_config = ConfigDict(frozen=True)

    operator: Operator
    decision: PacingDecision
    deep_dive_score: float = Field(ge=0, le=1)
    deep_dive: bool


class Violation(BaseModel):
    """One failure found before speaking: a red line, or a quality rule such as "no repeated question"."""

    model_config = ConfigDict(frozen=True)

    red_line: RedLine | QualityRule
    detail: str = Field(max_length=200)


class ModeratorTurnPayload(BaseModel):
    """The full record of one AI turn: analysis, plan, what was said, internal note, and how the red-line gate went.

    `spoken_response` is None when the AI must stay silent (a human took over, red line H11).
    """

    model_config = ConfigDict(frozen=True)

    analysis: RespondentInputAnalysis | None
    plan: DialogueActionPlan | None
    spoken_response: str | None
    is_statement: bool = False                         # closing / safety statement: no question required
    note: InternalNote = Field(default_factory=InternalNote)
    attempts: int = Field(default=0, ge=0)             # speaking calls made (1 + regenerations)
    rejected: list[list[Violation]] = Field(default_factory=list)  # violations of each rejected draft
    rejected_drafts: list[str] = Field(default_factory=list)       # the rejected drafts themselves (AI text only), for the training loop's failure analysis
    used_fallback: bool = False
    fatal_service_error: bool = False                  # the model service refused us (no balance, bad key): staff must be told, an evaluation must stop
    internal_error: bool = False                       # the pipeline broke and the safety net spoke (a bug: the evaluation counts it as an error)
    alert: AlertLevel = AlertLevel.NONE
    alert_reason: str = ""
    latency_s: float = Field(default=0.0, ge=0)


class MemoryUpdate(BaseModel):
    """What the summarising call must return: the running summary of the interview so far."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS * 2)
