"""Pacing settings and the time budget of each phase and topic (task doc 4.3: 60-minute example, scaled to the planned length)."""
from pydantic import BaseModel, ConfigDict, Field

from .constants import (DEFAULT_DRY_ANSWER_GAIN, DEFAULT_DRY_STREAK_LIMIT, DEFAULT_EXPLORE_MIN_INFO_GAIN, DEFAULT_EXPLORE_UNTIL_SHARE, DEFAULT_HURRY_AFTER_SHARE, DEFAULT_MAX_EXPLORE_PER_TOPIC,
                        DEFAULT_MAX_FOLLOWUPS_PER_QUESTION, DEFAULT_MAX_TURNS_PER_TOPIC, DEFAULT_OVERTIME_FORCE_RATIO,
                        DEFAULT_SATURATION_THRESHOLD, PHASE_MINUTES_AT_60, REFERENCE_DURATION_MIN, Phase)
from .knowledge import InterviewGuide

SECONDS_PER_MINUTE = 60.0


class PacingConfig(BaseModel):
    """Limits the state machine enforces. Initial values from the task doc; the benchmark calibrates them (never hard-coded)."""

    model_config = ConfigDict(frozen=True)

    saturation_threshold: float = Field(default=DEFAULT_SATURATION_THRESHOLD, gt=0, le=1)
    max_followups_per_question: int = Field(default=DEFAULT_MAX_FOLLOWUPS_PER_QUESTION, ge=0, le=10)
    overtime_force_ratio: float = Field(default=DEFAULT_OVERTIME_FORCE_RATIO, ge=1, le=2)
    max_turns_per_topic: int = Field(default=DEFAULT_MAX_TURNS_PER_TOPIC, ge=1, le=100)
    hurry_after_share: float = Field(default=DEFAULT_HURRY_AFTER_SHARE, gt=0, le=1)
    explore_until_share: float = Field(default=DEFAULT_EXPLORE_UNTIL_SHARE, gt=0, le=1)
    max_explore_per_topic: int = Field(default=DEFAULT_MAX_EXPLORE_PER_TOPIC, ge=0, le=20)  # 0 switches exploring off
    explore_min_info_gain: float = Field(default=DEFAULT_EXPLORE_MIN_INFO_GAIN, ge=0, le=1)
    dry_answer_gain: float = Field(default=DEFAULT_DRY_ANSWER_GAIN, ge=0, le=1)
    dry_streak_limit: int = Field(default=DEFAULT_DRY_STREAK_LIMIT, ge=1, le=10)  # empty answers in a row before the last try


class TimeBudget(BaseModel):
    """Seconds planned for each phase and each core topic.

    Built with `for_guide`. The fixed phases keep their 60-minute proportions; the core gets the rest, split by topic weight.
    """

    model_config = ConfigDict(frozen=True)

    total_s: float = Field(gt=0)
    phase_s: dict[Phase, float]
    topic_s: dict[str, float]

    @classmethod
    def for_guide(cls, guide: InterviewGuide) -> "TimeBudget":
        """Plan the time of one interview.

        Args:
            guide: the project's guide (planned duration, topics and their weights, whether there are stimuli).

        Returns:
            The budget in seconds.

        Raises:
            ValueError: the fixed phases leave no time for the core topics (cannot happen within the guide's allowed durations).
        """
        scale = guide.duration_min / REFERENCE_DURATION_MIN
        fixed = {phase: minutes * scale * SECONDS_PER_MINUTE for phase, minutes in PHASE_MINUTES_AT_60.items()}
        if not guide.stimuli:
            fixed[Phase.STIMULI] = 0.0  # no materials, no stimuli phase: its time goes to the core topics
        total = guide.duration_min * SECONDS_PER_MINUTE
        core = total - sum(fixed.values())
        if core <= 0:
            raise ValueError("planned duration leaves no time for the core topics")
        weight_sum = sum(t.weight for t in guide.topics)
        topics = {t.id: core * t.weight / weight_sum for t in guide.topics}
        return cls(total_s=total, phase_s={**fixed, Phase.CORE: core}, topic_s=topics)
