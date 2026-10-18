"""Emotion signals (hints about how something was said): emoji reactions, pace / pauses / volume against the speaker's own habits, tone of the words."""
from . import chat
from .speech import Past, Signals, analyze, is_notable, labels, rate_of
from .tone import tone

__all__ = ["chat", "Past", "Signals", "analyze", "is_notable", "labels", "rate_of", "tone"]
