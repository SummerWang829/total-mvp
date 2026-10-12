"""Detecting repeated questions: used by the engine before speaking and by the evaluation when counting (same rule both places)."""
import difflib
import re
from collections.abc import Iterable

from .constants import REPEAT_SIMILARITY

QUESTION_SPLIT = re.compile(r"(?<=[。！？!?.])\s*")
QUESTION_MARKS = ("?", "？")


def question_sentences(text: str) -> list[str]:
    """The question sentences of a line, lower-cased with spaces removed (so wording changes in spacing do not hide a repeat)."""
    return [re.sub(r"\s+", "", s.lower()) for s in QUESTION_SPLIT.split(text) if s.strip().endswith(QUESTION_MARKS)]


def repeats_earlier(text: str, earlier_lines: Iterable[str]) -> bool:
    """Whether a question in `text` repeats (nearly word for word) a question in any earlier line.

    Args:
        text: what is about to be said.
        earlier_lines: everything said before in this interview.
    """
    earlier = [q for line in earlier_lines for q in question_sentences(line)]
    return any(difflib.SequenceMatcher(None, q, e).ratio() >= REPEAT_SIMILARITY for q in question_sentences(text) for e in earlier)


def count_repeats(lines: Iterable[str]) -> int:
    """How many questions in a whole interview repeat an earlier one (eval gate: 0)."""
    seen: list[str] = []
    repeats = 0
    for line in lines:
        for q in question_sentences(line):
            repeats += any(difflib.SequenceMatcher(None, q, e).ratio() >= REPEAT_SIMILARITY for e in seen)
            seen.append(q)
    return repeats
