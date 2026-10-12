"""Validated shapes of the evaluation's data: personas, scenarios, scripted events and what a scenario expects."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.moderator.constants import EndReason
from app.moderator.knowledge import InterviewGuide

from .constants import EventKind, Intensity, PersonaType, ScenarioCategory, Split

EVAL_DIR = Path(__file__).parent
GUIDE_DIR = EVAL_DIR / "guides"


class Persona(BaseModel):
    """A simulated participant type. `roleplay` instructs a model playing it; the answer banks drive the rule-based player."""

    model_config = ConfigDict(frozen=True)

    type: PersonaType
    roleplay: str = Field(min_length=20)
    answers: dict[str, list[str]]       # language -> typical answers showing the trait ({item} = a must-cover item)
    covers_items: float = Field(ge=0, le=1)  # chance an answer covers one more must-cover item (rule-based player)
    reasons: float = Field(ge=0, le=1)       # chance an answer gives a reason (reaches the "reason" rung)


class ScriptedEvent(BaseModel):
    """Something that happens before the participant's answer number `turn` (1 = the answer to the intro)."""

    model_config = ConfigDict(frozen=True)

    turn: int = Field(ge=1)
    kind: EventKind
    text: str = ""


class Expectation(BaseModel):
    """What a scenario must show, beyond zero red-line violations."""

    model_config = ConfigDict(frozen=True)

    ends_with: EndReason | None = None          # e.g. CRISIS: the interview must end that way, on that turn
    end_turn: int | None = None
    honest_ai_turn: int | None = None           # the AI's reply to this answer must say it is an AI
    silent_turns: tuple[int, int] | None = None  # answers in [start, end) get no AI reply (human in control)


class Scenario(BaseModel):
    """One simulated interview."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: ScenarioCategory
    split: Split
    guide_id: str
    persona: PersonaType
    intensity: Intensity
    seed: int
    duration_min: float | None = None  # shorter than the guide for non-long scenarios, to keep runs affordable
    events: list[ScriptedEvent] = Field(default_factory=list)
    expect: Expectation = Field(default_factory=Expectation)


def load_guide(guide_id: str) -> InterviewGuide:
    """A guide from eval/guides/<id>.json.

    Raises:
        FileNotFoundError: no such guide.
        pydantic.ValidationError: the guide is malformed.
    """
    return InterviewGuide.model_validate(json.loads((GUIDE_DIR / f"{guide_id}.json").read_text(encoding="utf-8")))


def load_personas() -> dict[PersonaType, Persona]:
    """All personas from eval/personas/personas.json.

    Raises:
        ValueError: a persona type is missing or defined twice.
    """
    raw = json.loads((EVAL_DIR / "personas" / "personas.json").read_text(encoding="utf-8"))
    personas = [Persona.model_validate(p) for p in raw]
    by_type = {p.type: p for p in personas}
    if len(by_type) != len(personas) or set(by_type) != set(PersonaType):
        raise ValueError("personas.json must define every persona type exactly once")
    return by_type
