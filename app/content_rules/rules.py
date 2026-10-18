"""Finding and hiding what must not stay in the record: see ``constants.py`` for the policy and the word lists.

``check(text)`` returns the text with every hit replaced and the set of categories found. The text is returned unchanged (the same string)
when nothing was found, so ordinary answers are never touched. Nothing here logs the words that matched.
"""
import os
import re
from dataclasses import dataclass
from functools import lru_cache

from . import constants as K

_FULLWIDTH = {c: chr(c - 0xFEE0) for c in list(range(0xFF21, 0xFF3B)) + list(range(0xFF41, 0xFF5B)) + list(range(0xFF10, 0xFF1A))}  # Ａ-Ｚ ａ-ｚ ０-９
_ZERO = {ord(c): None for c in K.ZERO_WIDTH}


@dataclass(frozen=True)
class Checked:
    """The answer of ``check``: the text to keep, and what was found in it."""

    text: str
    flags: frozenset[str]

    @property
    def severe(self) -> bool:
        """Hate or a threat: the team is told at once."""
        return bool(self.flags & K.SEVERE)

    @property
    def conduct(self) -> bool:
        """Abuse, hate, a threat or sexual harassment (how someone speaks, as opposed to private data)."""
        return bool(self.flags & K.CONDUCT)

    @property
    def clean(self) -> bool:
        """Nothing was found."""
        return not self.flags


def _letters(word: str) -> str:
    """A regex for a Latin word that also catches "f u c k", "f*ck", "sh1t" (gaps and look-alike characters); a space inside the entry may be missing."""
    return K.LETTER_GAP.join(K.LEET.get(ch, re.escape(ch)) for ch in word if ch != " ")


def _han(word: str) -> str:
    """A regex for a Chinese term that also catches spaces or dots between its characters."""
    return K.HAN_GAP.join(re.escape(ch) for ch in word)


def _term(word: str, exact: bool) -> str:
    """The regex of one list entry."""
    latin = all(ord(c) < 128 for c in word)
    body = _letters(word) if latin else _han(word)
    if exact and latin:
        return r"(?<![A-Za-z0-9])" + body + r"(?:s)?(?![A-Za-z0-9])"
    if latin and len(word) <= 3:
        return r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"   # a very short stem inside a longer word is not the word
    return body


def _extra() -> dict[str, list[tuple[str, bool]]]:
    """Terms the operations team added in the file named by CONTENT_RULES_EXTRA ("category:term" per line; "category!term" = exact word)."""
    path = os.getenv(K.EXTRA_FILE_ENV, "")
    out: dict[str, list[tuple[str, bool]]] = {c: [] for c in K.CATEGORIES}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for sep, exact in ((":", False), ("!", True)):
                if sep in line:
                    cat, term = line.split(sep, 1)
                    if cat.strip() in out and term.strip():
                        out[cat.strip()].append((term.strip().lower(), exact))
                    break
    return out


@lru_cache(maxsize=1)
def _compiled() -> dict[str, re.Pattern]:
    """One compiled pattern per category (built once; call ``reload()`` after changing the extra file)."""
    extra = _extra()
    out: dict[str, re.Pattern] = {}
    for cat in (K.ABUSE, K.HATE, K.THREAT, K.SEXUAL):
        parts = [_term(w, False) for w in K.STEMS.get(cat, ())] + [_term(w, True) for w in K.EXACT.get(cat, ())]
        parts += [_term(w, exact) for w, exact in extra[cat]]
        if cat == K.THREAT:
            parts += list(K.THREAT_PATTERNS)
        out[cat] = re.compile("|".join(parts), re.IGNORECASE)
    contact = [K.EMAIL, K.URL, K.AT_HANDLE, *K.PHONE_PATTERNS, *K.SOCIAL_PATTERNS]
    out[K.CONTACT] = re.compile("|".join(f"(?:{p})" for p in contact), re.IGNORECASE)
    return out


def reload() -> None:
    """Pick up a changed extra-terms file."""
    _compiled.cache_clear()


def check(text: str, lang: str = "zh") -> Checked:
    """Hide contact details and abusive, hateful, threatening or sexual words, and say what was found.

    Args:
        text: what a participant typed or said.
        lang: "zh" or anything else (only chooses the wording of the contact placeholder).

    Returns:
        The text to keep (the very same string when nothing was found) and the categories found.
    """
    if not text:
        return Checked(text, frozenset())
    work = text.translate(_ZERO).translate(_FULLWIDTH)  # zero-width characters and full-width letters are only ever used to slip past a filter
    flags: set[str] = set()
    patterns = _compiled()
    mask_contact = K.MASK_CONTACT["zh" if lang.startswith("zh") else "en"]
    new, n = patterns[K.CONTACT].subn(mask_contact, work)
    if n:
        flags.add(K.CONTACT)
    for cat in (K.THREAT, K.HATE, K.SEXUAL, K.ABUSE):
        new, n = patterns[cat].subn(K.MASK_WORD, new)
        if n:
            flags.add(cat)
    return Checked(new if flags or new != text else text, frozenset(flags))
