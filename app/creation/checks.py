"""The checks on a study in the making (检查提示): plain rules, no model. Each finding says what is wrong, where, and what to tell the assistant to fix it.

Run on every look at the dialog and sent to the assistant with every turn, so it can raise them itself. Findings are hints: nothing here blocks anyone.
"""
import re
from dataclasses import asdict, dataclass

from .. import guide_text
from ..models import Project, RecruitForm
from .stages import State

LEADING = re.compile(r"是不是|对不对|对吧|难道|不是吗|应该.{0,6}吧|是否同意|你也觉得|大家都|很多人都")
MAX_Q_CHARS = 70
MAX_SCREEN_QUESTIONS = 10
DEPTH_EXTRA = {"none": 0.0, "shallow": 0.7, "deep": 1.6, "free": 1.3}
MINUTES_PER_QUESTION = 1.2
TOO_LONG, TOO_SHORT = 1.15, 0.6


@dataclass
class Finding:
    id: str
    stage: str
    level: str   # "warn" | "note"
    text: str
    fix: str     # what to say to the assistant to have it fixed

    def view(self) -> dict:
        return asdict(self)


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[^一-鿿A-Za-z0-9]", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def estimate_minutes(guide: dict) -> int:
    """About how long the outline takes: a little over a minute per question plus time for its follow-ups."""
    metas = guide.get("meta") or {}
    total = 0.0
    for qs, ms in [(guide["warmup_questions"], metas.get("warmup") or [])] + [(t["questions"], (metas.get("topics") or [[]] * 99)[i]) for i, t in enumerate(guide["topics"])]:
        for j in range(len(qs)):
            depth = (ms[j] if j < len(ms) else {}).get("depth", guide_text.DEFAULT_DEPTH)
            total += MINUTES_PER_QUESTION + DEPTH_EXTRA.get(depth, 0.7)
    return round(total)


def _outline(project: Project, state: State, goals: list[str]) -> list[Finding]:
    text = project.outline_text or ""
    if not text.strip():
        return []
    try:
        g = guide_text.parse_guide_text(text)
    except guide_text.GuideTextError as e:
        return [Finding("outline_unreadable", "outline", "warn", f"大纲文字有一处读不懂：{e}", "请帮我修好大纲文字里读不懂的地方")]
    out: list[Finding] = []
    metas = g.get("meta") or {}
    rows = [("暖场", i, q, (metas.get("warmup") or [])[i] if i < len(metas.get("warmup") or []) else {}) for i, q in enumerate(g["warmup_questions"])]
    for ti, t in enumerate(g["topics"]):
        ms = (metas.get("topics") or [])[ti] if ti < len(metas.get("topics") or []) else []
        rows += [(t["title"], i, q, ms[i] if i < len(ms) else {}) for i, q in enumerate(t["questions"])]
        if not (t.get("section_guide") or "").strip():
            out.append(Finding(f"no_section_guide:{ti}", "outline", "note", f"环节「{t['title']}」还没有环节指引", f"请给环节「{t['title']}」写环节指引：要了解什么、怎么追问、不要问什么"))
    n = 0
    for sec, i, q, m in rows:
        n += 1
        if LEADING.search(q):
            out.append(Finding(f"leading:{n}", "outline", "warn", f"第 {n} 题带有引导：{q[:30]}", f"请把第 {n} 题改成中立的问法，不要暗示答案"))
        if len(re.findall(r"[?？]", q)) >= 2:
            out.append(Finding(f"double:{n}", "outline", "warn", f"第 {n} 题一句话里问了两件事", f"请把第 {n} 题拆成一次只问一件事"))
        if len(q) > MAX_Q_CHARS:
            out.append(Finding(f"long:{n}", "outline", "note", f"第 {n} 题太长（{len(q)} 字）", f"请把第 {n} 题改短，一句话说清"))
        if m.get("depth") in ("deep", "free") and not (m.get("guide") or "").strip():
            out.append(Finding(f"no_guide:{n}", "outline", "note", f"第 {n} 题要深追，但没有写访谈指引", f"请给第 {n} 题写一句访谈指引"))
    target = int(state.decisions.get("minutes") or g.get("duration_min") or 0)
    est = estimate_minutes(g)
    if target and est > target * TOO_LONG:
        out.append(Finding("too_long", "outline", "warn", f"大纲大约要 {est} 分钟，比计划的 {target} 分钟长", f"请把大纲缩到 {target} 分钟左右，先去掉最不重要的问题"))
    elif target and est < target * TOO_SHORT:
        out.append(Finding("too_short", "outline", "note", f"大纲大约只有 {est} 分钟，比计划的 {target} 分钟短很多", "请补充能覆盖研究目标的问题，让时长接近计划"))
    pool = _bigrams(" ".join(q for _, _, q, _ in rows) + " " + " ".join(m["text"] for t in g["topics"] for m in t.get("must_cover", [])) + " " + " ".join(t.get("section_guide", "") for t in g["topics"]))
    for k, goal in enumerate(goals):
        gb = _bigrams(goal)
        if gb and len(gb & pool) < 2:
            out.append(Finding(f"uncovered:{k}", "outline", "warn", f"研究目标「{goal[:24]}」好像没有对应的题", f"研究目标「{goal}」没有问题覆盖，请补充相关的问题"))
    return out


def _screening(f: RecruitForm | None, state: State) -> list[Finding]:
    qs = list(f.questions or []) if f else []
    if not qs:
        return []
    out: list[Finding] = []
    if len(qs) > MAX_SCREEN_QUESTIONS:
        out.append(Finding("screen_many", "screen", "warn", f"甄别问卷有 {len(qs)} 题，超过 {MAX_SCREEN_QUESTIONS} 题，容易有人中途放弃", "请把甄别问卷精简到 10 题以内"))
    text = " ".join(str(q.get("text", "")) for q in qs)
    calibers = " ".join(c.get("term", "") for c in (state.decisions.get("audience") or {}).get("caliber", []))
    if re.search(r"收入|月入|年薪|年收入", text) and not re.search(r"税前|税后", text) and "收入" not in calibers:
        out.append(Finding("calibre_income", "screen", "warn", "收入题没有写明是税前还是税后、个人还是家庭", "请问我收入的口径（税前/税后、个人/家庭），并写进收入题"))
    if "大湾区" in text and "大湾区" not in calibers:
        out.append(Finding("calibre_region", "screen", "note", "题里写了“大湾区”，但还没约定包含哪些城市", "请问我“大湾区”指哪些城市，并写进问卷"))
    if not any(o.get("goto") == "end_out" for q in qs for o in q.get("options", [])) and not any(q.get("rules") for q in qs):
        out.append(Finding("screen_no_exit", "screen", "warn", "甄别问卷里没有任何选项会筛掉人", "请给不符合条件的选项设置“结束 · 不符合”"))
    aud = state.decisions.get("audience")
    if aud and aud.get("groups") and sum(g["target"] for g in aud["groups"]) != aud["total"]:
        out.append(Finding("audience_sum", "audience", "warn", "各组人数加起来和总人数对不上", "请核对各组人数，让它们加起来等于总人数"))
    return out


def run(project: Project, f: RecruitForm | None, state: State) -> list[dict]:
    """Every finding for the study as it is now (warnings first)."""
    goals = list(f.goals or []) if f else []
    found = [*(_screening(f, state)), *(_outline(project, state, goals))]
    if not goals:
        found.insert(0, Finding("no_goals", "goal", "warn", "还没有研究目标", "请根据研究背景帮我整理研究目标"))
    return [x.view() for x in sorted(found, key=lambda x: x.level != "warn")]
