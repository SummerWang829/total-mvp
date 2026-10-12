"""Models used by the evaluation, besides the moderator under test. Read from .env; empty values fall back to the
moderator's model. The judge should be a different model from the one tested (task doc 6.3)."""
import os
from dataclasses import dataclass

from app.config import Settings, settings


@dataclass(frozen=True)
class EvalModel:
    """One OpenAI-compatible chat model used by the evaluation."""

    provider: str
    base_url: str
    api_key: str | None
    model: str
    timeout_s: int

    def as_settings(self) -> Settings:
        """A Settings copy whose MODERATOR_* point at this model, so the moderator's chat client can talk to it."""
        import copy

        s = copy.copy(settings)
        s.moderator_provider, s.moderator_base_url, s.moderator_api_key = self.provider, self.base_url, self.api_key
        s.moderator_model, s.moderator_timeout_s, s.moderator_vendor = self.model, self.timeout_s, "eval"
        return s


def _model(prefix: str) -> EvalModel:
    """EVAL_<prefix>_* from the environment, falling back to the moderator's settings."""
    get = lambda key, default: os.getenv(f"EVAL_{prefix}_{key}", "") or default  # noqa: E731
    return EvalModel(provider=get("PROVIDER", settings.moderator_provider), base_url=get("BASE_URL", settings.moderator_base_url),
                     api_key=get("API_KEY", settings.moderator_api_key or ""), model=get("MODEL", settings.moderator_model),
                     timeout_s=int(get("TIMEOUT_S", str(settings.moderator_timeout_s))))


def participant_model() -> EvalModel:
    """The model that plays the simulated participant (EVAL_PARTICIPANT_*)."""
    return _model("PARTICIPANT")


def judge_model() -> EvalModel:
    """The model that scores interviews (EVAL_JUDGE_*); should differ from the moderator's model."""
    return _model("JUDGE")
