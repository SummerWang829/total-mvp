"""Hints about a typed answer in the AI interviewer's chat, for the model (never for the participant).

A typed answer has no voice, so the hints are what a text chat offers: the tone of the words, emoji, an answer much shorter than this person's usual,
a reply that took much longer than usual to arrive, and reactions sent since the last question. Each is compared only with the same person's own
earlier messages. They are short English phrases because the prompts are in English; the model is told never to mention them to the participant.
"""
from statistics import median

from . import constants as K
from . import emoji as _emoji
from .tone import tone

MIN_PRIOR = K.BASELINE_MIN_SENTENCES
MAX_HINTS = 3


def hints(text: str, prior_lengths: list[int], delay_s: float | None = None, prior_delays: list[float] | None = None,
          reactions: list[str] | None = None) -> list[str]:
    """Up to three short hints about the newest typed answer.

    Args:
        text: the answer (already passed through the content rules).
        prior_lengths: lengths (characters) of this participant's earlier answers, oldest first.
        delay_s: seconds between the interviewer's last message and this answer (None: unknown).
        prior_delays: the same measure for earlier answers.
        reactions: keys of emoji reactions sent since the interviewer's last message.
    """
    out: list[str] = []
    score, strength = tone(text)
    if score <= K.STRONG_NEGATIVE:
        out.append("the words are clearly negative" + (" (strong)" if strength >= 0.8 else ""))
    elif score >= K.STRONG_POSITIVE:
        out.append("the words are clearly positive" + (" (strong)" if strength >= 0.8 else ""))
    lengths = prior_lengths[-K.BASELINE_WINDOW:]
    if len(lengths) >= MIN_PRIOR and len(text.strip()) <= median(lengths) * K.CHAT_SHORT_RATIO and len(text.strip()) < 40:
        out.append("much shorter than this person's usual answer")
    delays = (prior_delays or [])[-K.BASELINE_WINDOW:]
    if delay_s is not None and len(delays) >= MIN_PRIOR and delay_s >= max(10.0, median(delays) * K.CHAT_SLOW_RATIO):
        out.append("took much longer than usual to answer")
    for glyph in dict.fromkeys(reactions or []):
        if _emoji.is_valid(glyph):
            out.append(f"sent a reaction: {glyph} ({_emoji.name_en(glyph)})")
    return out[:MAX_HINTS]
