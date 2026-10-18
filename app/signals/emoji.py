"""Which emoji are real, what they are called, and roughly what tone they carry.

A participant may send any emoji from the picker (like iMessage, WhatsApp or WeChat). The server accepts only strings that are an emoji in the
official list shipped with the picker (``app/static/vendor/emoji/data``), so a "reaction" can never carry free text, and refuses a few emoji that
are used to insult or to harass. The tone is a rough hint (a smiling face is positive, an angry face negative); most emoji carry no tone.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from . import constants as K

DATA = Path(__file__).resolve().parent.parent / "static" / "vendor" / "emoji" / "data"
MAX_LEN = 32                      # the longest sequences (a family with skin tones, a flag) are well below this
_VS16 = "️"                  # the "show as emoji" marker: a sender may or may not include it
_SKIN = re.compile("[\U0001F3FB-\U0001F3FF]")
# Used to insult or harass: refused whatever the skin tone (a sign of the same spirit as the word rules in app/content_rules).
BLOCKED = frozenset({"🖕", "🍆", "🍑", "💦", "🔞"})

_NEGATIVE = (("broken heart", -0.7), ("enraged", -0.9), ("pouting", -0.9), ("symbols on mouth", -0.9), ("cursing", -0.9), ("angry", -0.8), ("vomit", -0.9), ("nauseated", -0.8),
             ("loudly crying", -0.7), ("sob", -0.7), ("crying", -0.6), ("scream", -0.6), ("fearful", -0.6), ("weary", -0.6), ("confounded", -0.6), ("disappointed", -0.6),
             ("thumbs down", -0.7), ("worried", -0.5), ("anxious", -0.5), ("unamused", -0.5), ("rolling eyes", -0.5), ("persevering", -0.5), ("tired", -0.5), ("frowning", -0.5),
             ("pensive", -0.4), ("sad", -0.5), ("confused", -0.3), ("grimacing", -0.3), ("downcast", -0.3), ("sweat", -0.2), ("expressionless", -0.2), ("neutral", -0.1),
             ("skull", -0.3), ("pile of poo", -0.2), ("unwell", -0.5), ("sleepy", -0.1))
_POSITIVE = (("heart eyes", 0.9), ("hearts", 0.9), ("kiss", 0.7), ("star-struck", 0.8), ("party", 0.7), ("clapping", 0.6), ("thumbs up", 0.6), ("beaming", 0.6), ("tears of joy", 0.5),
             ("rolling on the floor", 0.5), ("grinning", 0.5), ("smiling", 0.5), ("blowing", 0.6), ("relieved", 0.4), ("sunglasses", 0.4), ("halo", 0.5), ("savoring", 0.5),
             ("winking", 0.4), ("ok hand", 0.4), ("raising hands", 0.6), ("folded hands", 0.3), ("sparkles", 0.4), ("red heart", 0.8), ("heart", 0.7), ("laugh", 0.5), ("smile", 0.5))


def _norm(emoji: str) -> str:
    return emoji.replace(_VS16, "")


def _base(emoji: str) -> str:
    return _SKIN.sub("", _norm(emoji))


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    """Every emoji of the official list (and every skin-tone variant), with its English and Chinese name and its group."""
    en = json.loads((DATA / "en.json").read_text(encoding="utf-8"))
    zh = {_norm(e["emoji"]): e.get("annotation", "") for e in json.loads((DATA / "zh.json").read_text(encoding="utf-8"))}
    out: dict[str, dict] = {}
    for e in en:
        base = _norm(e["emoji"])
        info = {"en": e.get("annotation", ""), "zh": zh.get(base, ""), "group": e.get("group")}
        out[base] = info
        for skin in e.get("skins", []):
            out[_norm(skin["emoji"])] = info
    return out


def is_blocked(emoji: str) -> bool:
    """One of the few emoji used to insult or harass (whatever the skin tone)."""
    return _base(emoji) in {_base(b) for b in BLOCKED}


def is_valid(emoji: str) -> bool:
    """A single real emoji from the official list, and not a blocked one."""
    return bool(emoji) and len(emoji) <= MAX_LEN and _norm(emoji) in _index() and not is_blocked(emoji)


def name_en(emoji: str) -> str:
    """The English name (what the model is told), or "" when unknown."""
    return (_index().get(_norm(emoji)) or _index().get(_base(emoji)) or {}).get("en", "")


def label_zh(emoji: str) -> str:
    """The Chinese name shown to the team, falling back to the English one."""
    info = _index().get(_norm(emoji)) or _index().get(_base(emoji)) or {}
    return info.get("zh") or info.get("en") or ""


@lru_cache(maxsize=1)
def _manual() -> dict[str, float]:
    return {_base(k): v for k, v in K.EMOJI_TONE.items()}


def tone(emoji: str) -> float:
    """-1 .. 1, rough; 0 for the many emoji that carry no tone (objects, places, flags)."""
    if _base(emoji) in _manual():
        return _manual()[_base(emoji)]
    info = _index().get(_norm(emoji)) or _index().get(_base(emoji))
    if not info or info["group"] not in (0, 1):   # smileys, emotion, hands and gestures only
        return 0.0
    name = info["en"].lower()
    for words, table in (("negative", _NEGATIVE), ("positive", _POSITIVE)):
        for key, value in table:
            if key in name:
                return value
    return 0.0
