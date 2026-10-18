"""The five rules that make a cue card. Pure functions: a snapshot of the room goes in, candidate cards come out (no database, no model).

Whether a candidate is actually shown is decided by ``schedule.py`` (limits, cooldowns, quiet moments).
"""
import re
from dataclasses import dataclass, field

from ..group_metrics.compute import Sentence, is_informative
from ..group_metrics.constants import SILENT_YELLOW_S
from ..content_rules import CONDUCT, SEVERE
from ..content_rules import constants as _CR
from ..moderator.recall import recall
from . import constants as K


K_CONTACT = _CR.CONTACT


@dataclass(frozen=True)
class Line:
    """A caption sentence with its sequence number (the evidence of a card is a list of these numbers)."""

    seq: int
    sentence: Sentence
    flags: frozenset[str] = frozenset()   # what the rule library hid in this sentence (contact, abuse, hate, threat, sexual)


@dataclass(frozen=True)
class Snapshot:
    """Everything the rules look at, at one moment."""

    now_ms: int
    metrics: dict                               # app.group_metrics.compute.compute(...)
    lines: list[Line]                           # every caption sentence so far, oldest first
    steps: list[dict] | None = None             # the script's sections (title, minutes, questions, ...), if the interview has one
    profiles: dict[int, list[str]] = field(default_factory=dict)   # seat -> screening answers as "question → answer"
    position: tuple[int, float] | None = None   # where the moderator really is: (section index, ms spent in it), from the material on screen


@dataclass(frozen=True)
class Candidate:
    """A card that could be shown."""

    kind: K.Kind
    text: str
    dedupe_key: str
    seat: int | None = None
    ask: str = ""
    evidence: tuple[int, ...] = ()

    @property
    def priority(self) -> int:
        """How urgent it is (higher first)."""
        return K.PRIORITY[self.kind]


def _participant_lines(snap: Snapshot) -> list[Line]:
    return [ln for ln in snap.lines if ln.sentence.speaker != "moderator"]


def current_step(steps: list[dict] | None, now_ms: int) -> tuple[int, float] | None:
    """(index of the section the clock says we are in, milliseconds spent inside it), or None without a script."""
    if not steps:
        return None
    start = 0.0
    for i, step in enumerate(steps):
        length = float(step.get("minutes") or 0) * 60000
        if now_ms < start + length or i == len(steps) - 1:
            return i, now_ms - start
        start += length
    return None


def where(snap: Snapshot) -> tuple[int, float] | None:
    """The section the interview is in. The moderator's own position (the section whose material is on screen) wins over the clock:
    a clock cannot show that one section ran long, because it simply moves on to the next one."""
    return snap.position or current_step(snap.steps, snap.now_ms)


def _step_text(step: dict) -> str:
    return " ".join([step.get("title", ""), *step.get("questions", []), *step.get("must_cover", []), *step.get("probes", [])])


# ---------- compliance ----------
def compliance(snap: Snapshot) -> list[Candidate]:
    """A participant said something very personal (a diagnosis, debt, a phone number or e-mail) in the last minute."""
    out = []
    for ln in _participant_lines(snap):
        if ln.sentence.end_ms < snap.now_ms - 60_000:
            continue
        text = ln.sentence.text
        lower = text.lower()
        if K_CONTACT in ln.flags or re.search(K.PHONE_PATTERN, text) or re.search(K.EMAIL_PATTERN, text):
            what = "联系方式"
        elif ln.flags & SEVERE:
            what = "歧视或威胁性的话"
        elif ln.flags & CONDUCT:
            what = "不文明的用语"
        elif any(w in lower for w in K.HEALTH_WORDS):
            what = "健康方面的个人情况"
        elif any(w in lower for w in K.MONEY_WORDS):
            what = "个人财务情况"
        else:
            continue
        seat = int(ln.sentence.speaker[1:]) if re.fullmatch(r"p\d+", ln.sentence.speaker) else None
        who = f"参与者 ·{seat:02d}" if seat else "有人"
        advice = ("请当场温和地提醒大家文明发言；系统已把这些词遮住，记录里不会保留原词。" if ln.flags & CONDUCT else
                  "系统已把它从记录里遮住。请提醒大家不要在这里留联系方式，需要联系由研究团队安排。" if K_CONTACT in ln.flags else
                  "先别深挖；需要的话提醒大家不必说得太具体，也不要在会后外传。")
        out.append(Candidate(K.Kind.COMPLIANCE, f"{who} 说了{what}（原话见下方）。{advice}",
                             f"compliance:{ln.seq}", seat, evidence=(ln.seq,)))
    return out


# ---------- one person talks too much ----------
def dominant(snap: Snapshot) -> list[Candidate]:
    """Someone has far more than their share: name who spoke least, as the people to ask next."""
    rows = snap.metrics["participants"]
    high = sorted((r for r in rows if r["state"] == "high"), key=lambda r: -r["share"])
    if not high:
        return []
    top = high[0]
    others = sorted((r for r in rows if r["seat"] != top["seat"]), key=lambda r: (r["share"], r["seat"]))[:2]
    names = " 和 ".join(f"{r['label']}" for r in others)
    text = f"{top['label']} 已占 {round(top['share'] * 100)}% 的发言时间。" + (f"下一问可以先点名 {names}（说得最少的）。" if others else "")
    return [Candidate(K.Kind.DOMINANT, text, f"dominant:{top['seat']}", top["seat"], ask="这个问题我想先听听其他几位朋友的看法，谁先来？")]


# ---------- someone has not spoken ----------
def silent(snap: Snapshot) -> list[Candidate]:
    """Someone has been quiet for a long time; if their screening answers touch what is being discussed, say so."""
    rows = [r for r in snap.metrics["participants"] if r["state"] == "silent" or (r["state"] == "quiet" and (r["silent_s"] or 0) >= SILENT_YELLOW_S)]
    if not rows:
        return []
    r = sorted(rows, key=lambda r: -(10**6 if r["silent_s"] is None else r["silent_s"]))[0]
    how = "还没发过言" if r["silent_s"] is None else f"已 {r['silent_s'] // 60} 分钟没发言"
    text = f"{r['label']} {how}。"
    profile = snap.profiles.get(r["seat"]) or []
    if profile:
        context = " ".join(ln.sentence.text for ln in snap.lines[-3:])
        step = where(snap)
        if step:
            context += " " + _step_text(snap.steps[step[0]])
        hit = recall(context, list(enumerate(profile)))
        if hit:
            text += f"TA 在问卷里写过：“{hit[0][1]}”，可以顺着这个请 TA 说说。"
    return [Candidate(K.Kind.SILENT, text, f"silent:{r['seat']}", r["seat"], ask="我想听听还没发言的朋友，这个问题你会怎么选？")]


# ---------- time ----------
def time_check(snap: Snapshot) -> list[Candidate]:
    """The current section has used clearly more time than planned (only when the interview has a script)."""
    step = where(snap)
    if step is None:
        return []
    index, inside_ms = step
    planned_ms = float(snap.steps[index].get("minutes") or 0) * 60000
    if planned_ms <= 0:
        return []
    over_ms = inside_ms - planned_ms
    last = index == len(snap.steps) - 1
    if over_ms < max(planned_ms * K.OVERRUN_SHARE, K.OVERRUN_MIN_S * 1000):
        return []
    title = snap.steps[index].get("title", "")
    used = round(inside_ms / 60000, 1)
    if last:
        text = f"最后一段「{title}」计划 {planned_ms / 60000:g} 分钟，已用 {used:g} 分钟，建议收尾。"
    else:
        nxt = snap.steps[index + 1].get("title", "")
        text = f"第 {index + 1} 段「{title}」计划 {planned_ms / 60000:g} 分钟，已用 {used:g} 分钟。建议转到下一段「{nxt}」。"
    return [Candidate(K.Kind.TIME, text, f"time:{index}")]


# ---------- off topic ----------
def offtopic(snap: Snapshot) -> list[Candidate]:
    """The last few informative sentences did not touch the current section at all (only when the interview has a script)."""
    step = where(snap)
    if step is None:
        return []
    index, _ = step
    text = _step_text(snap.steps[index])
    informative = [ln for ln in _participant_lines(snap) if is_informative(ln.sentence.text)]
    recent = informative[-K.OFFTOPIC_SENTENCES:]
    if len(recent) < K.OFFTOPIC_SENTENCES or recent[0].sentence.end_ms < snap.now_ms - 180_000:
        return []
    if any(recall(ln.sentence.text, [(0, text)]) for ln in recent):
        return []
    question = (snap.steps[index].get("questions") or [""])[0]
    title = snap.steps[index].get("title", "")
    return [Candidate(K.Kind.OFFTOPIC, f"最近 {K.OFFTOPIC_SENTENCES} 句话都没有碰到当前这段「{title}」，可能聊偏了。需要的话把话题拉回来。",
                      f"offtopic:{index}:{recent[-1].seq}", ask=question, evidence=tuple(ln.seq for ln in recent[-K.EVIDENCE_PER_CARD:]))]


RULES = (compliance, dominant, silent, time_check, offtopic)


def candidates(snap: Snapshot) -> list[Candidate]:
    """Every card the rules would make right now, most urgent first."""
    found = [c for rule in RULES for c in rule(snap)]
    return sorted(found, key=lambda c: -c.priority)
