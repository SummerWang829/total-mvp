"""Model-based scoring of whole interviews (task doc 6.3), and the calibration that decides whether it may be trusted.

Until a judge correlates at least JUDGE_MIN_CORRELATION with human scores on the calibration set, its scores are
reported for information only and never used to pass a gate (task doc 6.3 ③).
"""
import json
import logging
import math
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.moderator.knowledge import InterviewGuide
from app.moderator.providers import ChatError, ModeratorChatProvider

from .constants import JUDGE_MIN_CORRELATION, RUBRIC_VERSION
from .session import SessionResult

log = logging.getLogger(__name__)
EVAL_DIR = Path(__file__).parent
CALIBRATION_FILE = EVAL_DIR / "calibration" / "human_scores.json"
MIN_CALIBRATION_SESSIONS = 30


class JudgeScores(BaseModel):
    """One judge's grading of one interview."""

    model_config = ConfigDict(frozen=True)

    research_alignment: int = Field(ge=0, le=30)
    question_quality: int = Field(ge=0, le=25)
    probe_quality: int = Field(ge=0, le=30)
    flow_time: int = Field(ge=0, le=15)
    summary_consistent: bool = True
    subtle_leading: list[str] = Field(default_factory=list)
    invented_facts: list[str] = Field(default_factory=list)
    comment: str = ""

    @property
    def composite(self) -> int:
        """Research 30 + questions 25 + probes 30 + flow 15 = 100 (task doc 6.3)."""
        return self.research_alignment + self.question_quality + self.probe_quality + self.flow_time


class Judge(Protocol):
    """Scores a finished interview."""

    name: str

    def score(self, result: SessionResult, guide: InterviewGuide) -> JudgeScores | None:
        """The scores, or None when the judge could not score this interview."""
        ...


class ModelJudge:
    """A chat model grades the transcript with the rubric of RUBRIC_VERSION.

    Args:
        chat: the judge model (should differ from the moderator's model).
    """

    def __init__(self, chat: ModeratorChatProvider) -> None:
        self.chat = chat
        self.name = getattr(chat, "name", "judge")
        self.rubric = json.loads((EVAL_DIR / f"rubric_{RUBRIC_VERSION}.json").read_text(encoding="utf-8"))["instructions"]

    def score(self, result: SessionResult, guide: InterviewGuide) -> JudgeScores | None:
        """Grade one interview; None (logged) if the judge fails twice or answers out of range."""
        lines = [f"INTERVIEWER: {result.intro}"]
        for t in result.turns:
            lines.append(f"PARTICIPANT: {t.participant}")
            if t.ai:
                lines.append(f"INTERVIEWER: {t.ai}")
        payload = {"RESEARCH_GOAL": guide.research_goal, "GUIDE_TOPICS": [t.title for t in guide.topics],
                   "PRODUCT_FACTS": guide.product_facts, "TRANSCRIPT": "\n".join(lines)}
        messages = [{"role": "system", "content": self.rubric}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(2):
            try:
                return JudgeScores.model_validate(self.chat.complete(messages, temperature=0.0, seed=1))
            except (ChatError, ValidationError):
                log.warning("judge could not score %s (attempt %d)", result.scenario_id, attempt + 1, exc_info=True)
        return None


class Calibration(BaseModel):
    """Whether the judge may be used as evidence."""

    model_config = ConfigDict(frozen=True)

    trusted: bool
    correlation: float | None
    sessions: int
    reason: str


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation, or None when it is undefined (fewer than 2 pairs, or no variation)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def calibrate(judge_composites: dict[str, int], human_file: Path = CALIBRATION_FILE) -> Calibration:
    """Compare judge composites with human composites on the calibration sessions.

    Args:
        judge_composites: scenario id -> judge composite, for the sessions of this run.
        human_file: [{"scenario_id": str, "composite": number}, ...] scored by our researchers.

    Returns:
        trusted=True only with ≥ MIN_CALIBRATION_SESSIONS shared sessions and correlation ≥ JUDGE_MIN_CORRELATION.
    """
    if not human_file.is_file():
        return Calibration(trusted=False, correlation=None, sessions=0, reason="还没有人工打分的校准集（需要 30 段由我们研究员打分的访谈）")
    human = {r["scenario_id"]: float(r["composite"]) for r in json.loads(human_file.read_text(encoding="utf-8"))}
    shared = sorted(set(human) & set(judge_composites))
    r = pearson([human[s] for s in shared], [float(judge_composites[s]) for s in shared])
    if len(shared) < MIN_CALIBRATION_SESSIONS or r is None:
        return Calibration(trusted=False, correlation=r, sessions=len(shared), reason=f"校准样本不足（{len(shared)}/{MIN_CALIBRATION_SESSIONS}）")
    if r < JUDGE_MIN_CORRELATION:
        return Calibration(trusted=False, correlation=r, sessions=len(shared), reason=f"与人工分相关性 {r:.2f} < {JUDGE_MIN_CORRELATION}")
    return Calibration(trusted=True, correlation=r, sessions=len(shared), reason="已校准")
