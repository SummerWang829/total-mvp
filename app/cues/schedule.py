"""Which of the candidate cards is actually shown, and when: the part that keeps the cards from turning into noise.

Pure functions again: the cards already made, the clock and the candidates go in; at most one card comes out.
"""
from dataclasses import dataclass
from datetime import datetime

from . import constants as K
from .rules import Candidate


@dataclass(frozen=True)
class Existing:
    """A card that was already made for this interview (any status)."""

    kind: str
    seat: int | None
    dedupe_key: str
    status: str
    created_at: datetime


def _seconds_since(then: datetime, now: datetime) -> float:
    return (now - then).total_seconds()


def noisy(kind: K.Kind, cards: list[Existing]) -> bool:
    """The moderator has mostly dismissed this kind of card in this interview: it should wait longer next time."""
    handled = [c for c in cards if c.kind == kind.value and c.status in {K.Status.USED.value, K.Status.DISMISSED.value}]
    if len(handled) < K.NOISY_MIN_HANDLED:
        return False
    return sum(1 for c in handled if c.status == K.Status.DISMISSED.value) / len(handled) >= K.NOISY_DISMISS_SHARE


def choose(candidates: list[Candidate], cards: list[Existing], now: datetime, moderator_quiet_for_s: float | None) -> Candidate | None:
    """The one card to show now, or None.

    Args:
        candidates: what the rules found, most urgent first.
        cards: every card already made for this interview.
        now: the current time.
        moderator_quiet_for_s: seconds since the moderator last spoke (None: has not spoken yet).
    """
    pending = [c for c in cards if c.status == K.Status.PENDING.value]
    if len(pending) >= K.MAX_PENDING:
        return None
    since_last = min((_seconds_since(c.created_at, now) for c in cards), default=None)
    known = {c.dedupe_key for c in cards}
    for cand in candidates:
        if cand.dedupe_key in known:
            continue
        personal = cand.kind is K.Kind.COMPLIANCE
        gap = K.MIN_GAP_COMPLIANCE_S if personal else K.MIN_GAP_S
        if since_last is not None and since_last < gap:
            continue
        if not personal and moderator_quiet_for_s is not None and moderator_quiet_for_s < K.QUIET_AFTER_MODERATOR_S:
            continue
        cooldown = K.COOLDOWN_S[cand.kind] * (2 if noisy(cand.kind, cards) else 1)
        same_kind = [c for c in cards if c.kind == cand.kind.value]
        if cooldown and same_kind and min(_seconds_since(c.created_at, now) for c in same_kind) < cooldown:
            continue
        if cand.seat is not None and not personal:
            named = [c for c in cards if c.seat == cand.seat]
            if named and min(_seconds_since(c.created_at, now) for c in named) < K.SEAT_COOLDOWN_S:
                continue
        return cand
    return None


def expired(cards: list[Existing], now: datetime) -> list[str]:
    """Dedupe keys of waiting cards that have waited longer than K.EXPIRE_S (they go away on their own)."""
    return [c.dedupe_key for c in cards if c.status == K.Status.PENDING.value and _seconds_since(c.created_at, now) >= K.EXPIRE_S]
