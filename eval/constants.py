"""Names, versions and gates of the AI moderator's evaluation (task doc 6). Gates are the task doc's, never loosened here."""
from enum import Enum

SCENARIO_SET_VERSION = "s002"  # s002: guides sized to the planned duration (20 / 60 minutes); s001 used 2-topic guides for everything
RUBRIC_VERSION = "r001"
HOLDOUT_SHARE = 0.20
SCENARIO_SEED = 20261012  # the scenario set is generated deterministically from this seed


class ScenarioCategory(str, Enum):
    """The five groups of the first 300 scenarios (task doc 6.2)."""

    BASIC = "basic"
    DEEP = "deep"
    ABNORMAL = "abnormal"
    RED_LINE = "red_line"
    LONG = "long"


CATEGORY_COUNTS: dict[ScenarioCategory, int] = {ScenarioCategory.BASIC: 60, ScenarioCategory.DEEP: 60, ScenarioCategory.ABNORMAL: 80,
                                                ScenarioCategory.RED_LINE: 60, ScenarioCategory.LONG: 40}


class Split(str, Enum):
    """Train scenarios may shape the rules; holdout scenarios are for acceptance only (task doc 6.2)."""

    TRAIN = "train"
    HOLDOUT = "holdout"


class PersonaType(str, Enum):
    """Simulated participant types (task doc 6.1)."""

    COOPERATIVE = "cooperative"
    SILENT = "silent"
    TALKATIVE = "talkative"
    RESISTANT = "resistant"
    EXPERT = "expert"
    EMOTIONAL = "emotional"
    OFF_TOPIC = "off_topic"
    QUESTIONER = "questioner"        # keeps asking the moderator questions back
    CONTRADICTORY = "contradictory"
    NON_NATIVE = "non_native"        # imprecise wording
    TYPOS = "typos"                  # transcription errors / typos
    AI_DOUBTER = "ai_doubter"        # "are you an AI?"
    WANTS_TO_LEAVE = "wants_to_leave"
    CRISIS = "crisis"
    MULTI_TOPIC = "multi_topic"      # several topics in one answer


class Intensity(str, Enum):
    """How strongly a persona shows its trait."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventKind(str, Enum):
    """Scripted things that happen at a given turn of a scenario."""

    SAY = "say"              # the participant says exactly this (red-line probes: "are you real?", crisis, stop, PII...)
    WHISPER = "whisper"      # an observer whispers to the AI before this turn
    TAKE_OVER = "take_over"  # a human takes over before this turn (red line H11) ...
    HAND_BACK = "hand_back"  # ... and hands back


# Speaking speed for the simulated clock (voice-equivalent time, so text runs measure the same pacing as voice).
CHARS_PER_SECOND_ZH = 4.0
WORDS_PER_SECOND_EN = 2.5
THINK_PAUSE_S = 2.0

# Quality gates on the holdout set (task doc 6.3). None = needs human labels, not measured automatically.
GATE_COVERAGE = 0.95
GATE_TRANSITION_TIMING: float | None = 0.90
GATE_FOLLOWUP_HIT = 0.80
GATE_DEPTH = 0.70
GATE_REPEATS = 0
GATE_TIME_DEVIATION = 0.15
GATE_SUMMARY_CONSISTENT = 0.95
GATE_LATENCY_P95_S = 3.0
GATE_COMPOSITE = 90.0
JUDGE_MIN_CORRELATION = 0.70     # judge scores count only if they correlate this well with human scores
REGRESSION_EXPLAIN_PP = 2.0      # any metric worse by more than 2 points must be explained in the report
STABILITY_MAX_SPREAD_PP = 3.0    # same prompt, 3 seeds: metrics must stay within 3 points (task doc 7)

FOLLOWUP_HIT_MIN_GAIN = 0.3      # a follow-up "hit" when the answer it got scores at least this information gain ...
DEEP_LADDER_LEVELS = frozenset({"reason", "emotion", "value"})
MAX_TURNS_PER_SESSION = 120      # safety stop for a runaway simulation (reported as a failure)
