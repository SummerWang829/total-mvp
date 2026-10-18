"""The tone of the WORDS in a sentence (positive / negative, and how strongly), from emoji and a small word list. No model, no network.

It is a rough hint: negation ("不太满意", "not great") and intensifiers ("很", "really") are understood, sarcasm and context are not.
"""
import re

from . import constants as K
from . import emoji as _emoji

_HAN = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-z']+")
_ENTRIES = sorted({**K.POSITIVE, **K.NEGATIVE}.items(), key=lambda kv: -len(kv[0]))   # longest first: "不喜欢" before "喜欢"
_EMOJI = sorted(K.EMOJI_TONE, key=len, reverse=True)
_ZH_NEG = tuple(n for n in K.NEGATIONS if _HAN.search(n))
_EN_NEG = frozenset(n for n in K.NEGATIONS if not _HAN.search(n))
_ZH_INT = tuple(n for n in K.INTENSIFIERS if _HAN.search(n))
_EN_INT = frozenset(n for n in K.INTENSIFIERS if not _HAN.search(n))


def _before(text: str, pos: int) -> tuple[bool, bool]:
    """Whether a negation and whether an intensifier stand just before a word that starts at ``pos``."""
    head = text[max(0, pos - 6):pos]
    if _HAN.search(text[pos:pos + 1]):
        near = head[-K.NEGATION_WINDOW - 1:]
        return any(n in near for n in _ZH_NEG), any(i in head[-3:] for i in _ZH_INT)
    words = _WORD.findall(text[max(0, pos - 40):pos])[-K.NEGATION_WINDOW:]
    return any(w in _EN_NEG for w in words), any(w in _EN_INT for w in words[-2:])


def tone(text: str) -> tuple[float, float]:
    """(score from -1 to 1, strength from 0 to 1) of the words and emoji in ``text``; (0, 0) when nothing in the lists is found."""
    lower = text.lower()
    taken = [False] * len(lower)
    hits: list[float] = []
    for emoji in _EMOJI:
        for m in re.finditer(re.escape(emoji), lower):
            if not any(taken[m.start():m.end()]):
                taken[m.start():m.end()] = [True] * (m.end() - m.start())
                hits.append(K.EMOJI_TONE[emoji])
    for i, ch in enumerate(lower):  # any other emoji: its tone from the official list (a face, a hand gesture, a heart)
        if ord(ch) > 0x2000 and not taken[i] and (value := _emoji.tone(ch)):
            taken[i] = True
            hits.append(value)
    for word, value in _ENTRIES:
        for m in re.finditer(re.escape(word), lower):
            if any(taken[m.start():m.end()]):
                continue
            taken[m.start():m.end()] = [True] * (m.end() - m.start())
            negated, boosted = _before(lower, m.start())
            score = -value * 0.8 if negated else value
            hits.append(max(-1.0, min(1.0, score * (1.3 if boosted else 1.0))))
    if not hits:
        return 0.0, 0.0
    score = sum(hits) / len(hits)
    bangs = min(3, text.count("!") + text.count("！"))
    score += (K.EXCLAMATION_BOOST * bangs) * (1 if score > 0 else -1 if score < 0 else 0)
    score = max(-1.0, min(1.0, score))
    return round(score, 2), round(min(1.0, max(abs(h) for h in hits + [score])), 2)
