"""How a sentence was said: pace, volume and the pause before it, each compared only with the same speaker's own earlier sentences.

Everything here is a hint for the team. A pace or a volume has no meaning on its own (microphones, rooms, accents and topics differ), so a
sentence is called "slow" only when it is clearly slower than THIS person's usual, and nothing is said until there are enough earlier sentences.
"""
import re
from dataclasses import dataclass
from statistics import median

from . import constants as K
from .tone import tone

_HAN = re.compile(r"[一-鿿]")
_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Past:
    """What was measured for one earlier sentence of the same speaker (None = not measured)."""

    rate: float | None = None      # characters (Chinese) or words per second
    vol_db: float | None = None
    gap_ms: int | None = None


@dataclass(frozen=True)
class Signals:
    """The hints for one sentence."""

    rate: float | None
    senti: float                   # -1 .. 1
    tags: tuple[str, ...]


def rate_of(text: str, dur_ms: int) -> float | None:
    """Characters (Chinese) or words per second, or None when the sentence is too short or too brief to give a pace."""
    if dur_ms < K.MIN_SENTENCE_MS_FOR_RATE:
        return None
    units = len(_NOISE.sub("", text)) if _HAN.search(text) else len(text.split())
    return round(units / (dur_ms / 1000), 2) if units >= K.MIN_SENTENCE_UNITS else None


def _usual(values: list[float | int | None]) -> float | None:
    """The speaker's usual value (median of the last K.BASELINE_WINDOW measured ones), or None while there are too few."""
    got = [v for v in values[-K.BASELINE_WINDOW:] if v is not None]
    return median(got) if len(got) >= K.BASELINE_MIN_SENTENCES else None


def analyze(text: str, dur_ms: int, vol_db: float | None, gap_ms: int | None, history: list[Past]) -> Signals:
    """The pace, tone and tags of a new sentence, against ``history`` (the same speaker's earlier sentences, oldest first)."""
    rate = rate_of(text, dur_ms)
    score, strength = tone(text)
    tags: list[str] = []
    usual_rate = _usual([p.rate for p in history])
    if rate is not None and usual_rate:
        if rate <= usual_rate * K.SLOW_RATIO:
            tags.append("slow")
        elif rate >= usual_rate * K.FAST_RATIO:
            tags.append("fast")
    usual_vol = _usual([p.vol_db for p in history])
    if vol_db is not None and usual_vol is not None:
        if vol_db <= usual_vol - K.QUIET_DB:
            tags.append("quiet")
        elif vol_db >= usual_vol + K.LOUD_DB:
            tags.append("loud")
    if gap_ms is not None and K.HESITATION_MIN_MS <= gap_ms <= K.HESITATION_MAX_MS:
        usual_gap = _usual([p.gap_ms for p in history])
        if usual_gap is None or gap_ms >= usual_gap * K.HESITATION_TIMES:  # with no habit known yet, any long pause is worth noting
            tags.append("hesitation")
    if score <= K.STRONG_NEGATIVE:
        tags.append("negative")
    elif score >= K.STRONG_POSITIVE:
        tags.append("positive")
    return Signals(rate, score, tuple(tags))


def is_notable(tags: tuple[str, ...] | list[str], senti: float = 0.0) -> bool:
    """Worth the team's attention: a long pause, a strongly negative tone, or several of slow / quiet / negative at once."""
    if "hesitation" in tags or senti <= -0.7:
        return True
    return sum(1 for t in tags if t in {"slow", "quiet", "negative"}) >= K.NOTABLE_MIN_TAGS


def labels(tags: tuple[str, ...] | list[str]) -> list[str]:
    """The tags in plain Chinese, in a fixed order."""
    return [K.TAG_LABELS[t] for t in ("hesitation", "slow", "fast", "quiet", "loud", "negative", "positive") if t in tags]
