"""Checks on the questions of an outline. Built from the 2026-11-18 red-line trial (docs/大纲工作台_红线试验.md): some of the AI host's red lines fit a question a researcher writes
(they block), some only warn, the rest are about what the AI says live and do not apply. Pure rules, no model.

A result is {"blocked": bool, "issues": [{"id", "level": "block"|"warn", "text"}]}. A revision whose result is blocked cannot be accepted.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from ..moderator.constants import RedLine
from ..moderator.guardrails import CHECKS, GuardrailContext
from ..moderator.knowledge import InterviewGuide

BLOCK_LINES = (RedLine.H2_LEADING, RedLine.H4_PERSONAL_DATA, RedLine.H9_ADVICE, RedLine.H12_TOO_LONG, RedLine.H6_LANGUAGE)
WARN_LINES = (RedLine.H1_ONE_QUESTION, RedLine.H13_MODEL_IDENTITY)
LINE_TEXT = {RedLine.H1_ONE_QUESTION: "一句里问了不止一件事，可以拆开", RedLine.H2_LEADING: "带有诱导", RedLine.H4_PERSONAL_DATA: "要了个人信息", RedLine.H6_LANGUAGE: "语言和访谈语言不一致",
             RedLine.H9_ADVICE: "含建议或评价", RedLine.H12_TOO_LONG: "太长", RedLine.H13_MODEL_IDENTITY: "提到了模型或公司，请确认是研究需要"}
MAX_HAN = 30
LIST_WORDS = re.compile(r"(?:和|以及|还有|并且|同时)")
PRESUME = re.compile(r"最大的|最重要的|最喜欢的|最满意的")
VOICE_ONLY = re.compile(r"出声|说出来|口述|大声")
_GUIDE_DIR = Path(__file__).resolve().parent.parent.parent / "eval" / "guides"


@lru_cache(maxsize=4)
def _ctx(lang: str) -> GuardrailContext:
    """The red-line gate needs a guide only to know the interview language; the sample guides are borrowed for that."""
    name = "sg_skincare_zh.json" if lang == "zh" else "sg_food_delivery_en.json"
    return GuardrailContext(guide=InterviewGuide.model_validate(json.loads((_GUIDE_DIR / name).read_text(encoding="utf-8"))))


def check_question(text: str, lang: str = "zh", fmt: str = "") -> dict:
    """Check one question. `fmt` is the interview format code (a text interview must not ask people to speak)."""
    issues: list[dict] = []
    ctx = _ctx("zh" if lang == "zh" else "en")
    for line, fn in CHECKS:
        if line in BLOCK_LINES or line in WARN_LINES:
            if fn(text, ctx):
                issues.append({"id": line.value, "level": "block" if line in BLOCK_LINES else "warn", "text": LINE_TEXT[line]})
    han = len(re.findall(r"[一-鿿]", text))
    if lang == "zh" and han > MAX_HAN and not any(i["id"] == "H12" for i in issues):
        issues.append({"id": "long", "level": "warn", "text": f"一句超过 {MAX_HAN} 个字"})
    if lang == "zh" and len(LIST_WORDS.findall(text)) >= 2:
        issues.append({"id": "list", "level": "warn", "text": "一句里并列了多件事，可以拆开"})
    if PRESUME.search(text):
        issues.append({"id": "presume", "level": "warn", "text": "默认受访者有这样的答案，可以改成先问有没有"})
    if fmt == "text" and VOICE_ONLY.search(text):
        issues.append({"id": "format", "level": "warn", "text": "文字访谈里不适合让人出声说"})
    return {"blocked": any(i["level"] == "block" for i in issues), "issues": issues}


def check_questions(texts: list[str], lang: str = "zh", fmt: str = "") -> dict:
    """Check a list; blocked if any is blocked. Issues carry the index of the question they belong to."""
    issues, blocked = [], False
    for n, t in enumerate(texts):
        r = check_question(t, lang, fmt)
        blocked = blocked or r["blocked"]
        issues += [{**i, "index": n} for i in r["issues"]]
    return {"blocked": blocked, "issues": issues}
