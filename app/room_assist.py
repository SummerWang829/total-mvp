"""What the moderator's room shows next to the video: how far the interview has got through the outline, whether the time is on track, who has been talking, follow-up
questions worth asking, and a summary of each chapter that has been covered.

Everything is worked out by rules from what is already stored (the outline of the project and the live captions); nothing is sent to a model and nothing here is written by
one. Every quote is copied word for word from a caption line. The suggestions are hints: the moderator decides.
"""
import re

from .interview_answers import _score

MATCH_MIN = 0.16        # how much of an outline question's wording a moderator line must share to count as asking it
TALK_WARN_PCT = 40      # the moderator talking more than this share of the words is worth a nudge
TALK_FEW_LINES = 6      # fewer finished lines than this: too early to say anything about who talks more
BEHIND_BY = 0.15        # time used minus questions covered, as a share, beyond which the pace is called behind
MAX_PROBES = 3
QUOTE_CHARS = 120
SHARES = {"warmup": 1, "topic": 3, "stimulus": 3}   # how the minutes are shared between the parts (the same as the outline page)

VAGUE = {"zh": ["还行", "一般", "挺好", "不错", "谨慎", "方便", "随便", "看情况", "差不多", "还可以", "有点", "比较"],
         "en": ["fine", "okay", "ok", "so-so", "decent", "kind of", "sort of", "depends", "pretty good"]}
EXPERIENCE = {"zh": ["之前", "有一次", "上次", "以前", "那次", "曾经", "去年"], "en": ["last time", "once", "before", "back then", "used to", "last year"]}
CONTRAST = {"zh": ["但是", "不过", "可是", "然而"], "en": ["but", "however", "although"]}
NUMBER = re.compile(r"\d+\s*(元|块|人民币|rmb|sgd|\$|%|分钟|次|个)?|[一二三四五六七八九十百千两]+(元|块|次|个)")
QUESTIONS = {
    "zh": {"vague": "你说的“{w}”，具体指什么？能举个例子吗？", "experience": "你提到“{w}”，那次是怎么发生的？后来你是怎么处理的？",
           "contrast": "你说到“{w}”，后面的部分能再多讲一点吗？", "number": "你提到“{w}”，为什么是这个数？是怎么想到的？"},
    "en": {"vague": "You said “{w}”. What do you mean by that? Could you give an example?", "experience": "You mentioned “{w}”. How did that happen, and what did you do afterwards?",
           "contrast": "After “{w}” you added something. Could you say more about that part?", "number": "You mentioned “{w}”. How did you arrive at that?"},
}
REASON = {"zh": {"vague": "模糊词", "experience": "经历，没展开", "contrast": "转折，后半句没展开", "number": "数字，没说原因"},
          "en": {"vague": "vague word", "experience": "an experience, not expanded", "contrast": "a turn, second half not expanded", "number": "a number, no reason given"}}


def _lang(text: str) -> str:
    return "zh" if re.search(r"[一-鿿]", text) else "en"


def part_minutes(parts: list[dict], total: int) -> list[int]:
    """Minutes for each part of the outline, adding up to the planned length."""
    w = [SHARES.get(p.get("kind"), 1) for p in parts]
    s = sum(w) or 1
    return [max(1, round(x / s * total)) for x in w]


def coverage(outline: dict, lines: list[dict]) -> dict:
    """Which outline questions the moderator has already asked, judged by the wording of the moderator's caption lines.

    Returns:
        {"questions": [{"q", "part", "covered", "at_ms"}], "covered": n, "total": n, "current": index of the first question not yet asked (or None)}
    """
    qs = [(i, q["text"]) for i, p in enumerate(outline["parts"]) for q in p["questions"] if not q.get("cover")]
    mod = [ln for ln in lines if ln["speaker"] == "moderator"]
    taken: set[int] = set()
    out = []
    for part, text in qs:
        best, at = 0.0, -1
        for k, ln in enumerate(mod):
            if k in taken:
                continue
            sc = _score(text, ln["text"])
            if sc > best:
                best, at = sc, k
        covered = best >= MATCH_MIN
        if covered:
            taken.add(at)
        out.append({"q": text, "part": part, "covered": covered, "at_ms": mod[at]["at_ms"] if covered else None})
    nxt = next((i for i, x in enumerate(out) if not x["covered"]), None)
    return {"questions": out, "covered": sum(1 for x in out if x["covered"]), "total": len(out), "current": nxt}


def pacing(cov: dict, elapsed_ms: int | None, planned_min: int) -> dict:
    """Is the time on track? Compares the share of time used with the share of questions asked, and says which questions to squeeze when behind."""
    if elapsed_ms is None or not cov["total"]:
        return {"used_pct": None, "covered_pct": None, "behind": False, "advice": ""}
    used = min(150, round(elapsed_ms / 60000 / max(1, planned_min) * 100))
    done = round(cov["covered"] / cov["total"] * 100)
    behind = (used - done) / 100 > BEHIND_BY
    left = [i + 1 for i, x in enumerate(cov["questions"]) if not x["covered"]]
    squeeze = left[len(left) - max(1, len(left) // 3):] if left else []
    advice = f"用了 {used}% 的时间，只问了 {done}% 的问题，建议压缩第 {'、'.join(map(str, squeeze))} 题" if behind and squeeze else ""
    return {"used_pct": used, "covered_pct": done, "behind": behind, "advice": advice}


def talk_share(lines: list[dict]) -> dict:
    """Share of the words said by the moderator and by the participant; says when the moderator is taking more than a third."""
    mod = sum(len(ln["text"]) for ln in lines if ln["speaker"] == "moderator")
    par = sum(len(ln["text"]) for ln in lines if ln["speaker"] != "moderator")
    total = mod + par
    if len(lines) < TALK_FEW_LINES or not total:
        return {"moderator_pct": None, "participant_pct": None, "advice": ""}
    m = round(mod * 100 / total)
    return {"moderator_pct": m, "participant_pct": 100 - m, "advice": f"你说了 {m}% 的话，试着多让受访者说" if m > TALK_WARN_PCT else ""}


def probe_suggestions(lines: list[dict], asked: set[str]) -> list[dict]:
    """Up to three follow-up questions from what the participant said last: a vague word, an experience, a turn ("but ..."), a number. `trigger` is a word-for-word piece of the line.

    `asked` holds the keys of suggestions already used or dismissed ("<line id>:<rule>"), which are not offered again.
    """
    out = []
    for ln in reversed([x for x in lines if x["speaker"] != "moderator"][-4:]):
        text, lang = ln["text"], _lang(ln["text"])
        for rule, words in (("experience", EXPERIENCE[lang]), ("vague", VAGUE[lang]), ("contrast", CONTRAST[lang])):
            hit = next((w for w in words if w in text.lower()), None)
            key = f"{ln['id']}:{rule}"
            if hit and key not in asked:
                out.append({"key": key, "rule": REASON[lang][rule], "trigger": _around(text, hit), "question": QUESTIONS[lang][rule].format(w=hit), "at_ms": ln["at_ms"]})
        m = NUMBER.search(text)
        key = f"{ln['id']}:number"
        if m and key not in asked:
            out.append({"key": key, "rule": REASON[lang]["number"], "trigger": _around(text, m.group(0)), "question": QUESTIONS[lang]["number"].format(w=m.group(0).strip()), "at_ms": ln["at_ms"]})
    return out[:MAX_PROBES]


def _around(text: str, word: str) -> str:
    i = text.lower().find(word.lower())
    a, b = max(0, i - 12), min(len(text), i + len(word) + 12)
    return text[a:b].strip()[:QUOTE_CHARS]


def chapter_summaries(outline: dict, lines: list[dict], cov: dict) -> list[dict]:
    """For every chapter (a part of the outline) whose questions have all been asked: the participant's longest lines in it, word for word with their times.

    A chapter runs from its first question to the first question of the next chapter that was asked.
    """
    by_part: dict[int, list[dict]] = {}
    for x in cov["questions"]:
        by_part.setdefault(x["part"], []).append(x)
    done = [i for i, p in enumerate(outline["parts"]) if by_part.get(i) and all(x["covered"] for x in by_part[i])]
    starts = sorted((min(x["at_ms"] for x in qs if x["covered"]), i) for i, qs in by_part.items() if any(x["covered"] for x in qs))
    out = []
    for i in done:
        start = min(x["at_ms"] for x in by_part[i])
        nxt = next((s for s, j in starts if s > start), None)
        mine = [ln for ln in lines if ln["speaker"] != "moderator" and ln["at_ms"] >= start and (nxt is None or ln["at_ms"] < nxt)]
        top = sorted(mine, key=lambda ln: -len(ln["text"]))[:3]
        out.append({"part": i, "title": outline["parts"][i]["title"], "points": [{"text": ln["text"][:QUOTE_CHARS * 2], "at_ms": ln["at_ms"]} for ln in sorted(top, key=lambda ln: ln["at_ms"])],
                    "unexpanded": [p["trigger"] for p in probe_suggestions(mine, set())][:2]})
    return out
