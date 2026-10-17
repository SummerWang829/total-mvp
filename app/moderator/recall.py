"""Finding what the participant said earlier about the thing being discussed now (the interview's long-term memory).

A two-hour interview has far more lines than any model call can carry. The newest lines go in word for word and the rest as a
summary and a list of key facts; this module adds the third piece: when the current answer or question touches something said
long ago ("the fragrance made my skin itch", minute 12), the matching old lines come back word for word.

Matching is by shared words (English) and overlapping character pairs (Chinese, which has no spaces), with no model call.
"""
import re

from .constants import RECALL_LINE_CHARS, RECALL_MIN_SCORE, RECALL_TOP_K

_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)
_LATIN_WORD = re.compile(r"[a-z0-9]{3,}")
_LATIN = re.compile(r"[a-z0-9]+")
WORD_WEIGHT = 1.5  # a whole word found in both lines is as telling as about two matching character pairs


def _units(text: str) -> dict[str, float]:
    """What a line is matched on: its words of three or more letters (English), and overlapping character pairs of the rest (Chinese)."""
    lower = text.lower()
    units = {w: WORD_WEIGHT for w in _LATIN_WORD.findall(lower)}
    flat = _NOISE.sub("", _LATIN.sub(" ", lower))
    units.update({flat[i:i + 2]: 1.0 for i in range(len(flat) - 1)})
    return units


def recall(query: str, candidates: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The earlier lines that best match the query.

    Args:
        query: what is being discussed now (the participant's latest answer plus the question about to be asked).
        candidates: (turn index, text) of lines that are no longer in the verbatim window.

    Returns:
        Up to RECALL_TOP_K (index, text cut to RECALL_LINE_CHARS) in time order, each sharing rare character pairs with the query (score at least RECALL_MIN_SCORE); empty when nothing matches (so an unrelated topic brings nothing back).
    """
    wanted = _units(query)
    if not wanted:
        return []
    shared = [(index, text, {u: w for u, w in _units(text).items() if u in wanted}) for index, text in candidates]
    seen_in: dict[str, int] = {}
    for _, _, units in shared:
        for unit in units:
            seen_in[unit] = seen_in.get(unit, 0) + 1
    scored = []
    for index, text, units in shared:
        score = sum(w / seen_in[u] for u, w in units.items())  # a unit found in many lines ("不会") says little about this topic
        if score >= RECALL_MIN_SCORE:
            scored.append((score, index, text))
    best = sorted(scored, key=lambda x: (-x[0], -x[1]))[:RECALL_TOP_K]
    return [(index, text[:RECALL_LINE_CHARS]) for _, index, text in sorted(best, key=lambda x: x[1])]
