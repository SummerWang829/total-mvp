"""The analysis report of the 洞察与片段 page: cards made of numbers that are counted from the interviews, and short readings (rule-written, or written by the model).

Every number is counted here from what the participants said (the themes beside each answer, or the confirmed insights when no answers were worked out yet) and from the
questionnaire answers used to split people into groups; a model never writes a number or a quote. A model only (a) picks which comparison the team's own words ask for, from
lists of real themes and questions, and (b) words a short reading of the counts it is given. A participant appears only as a code.

A block is a card: "summary", "themes", "appendix", or "cmp:<questionnaire question>:<theme>" (how many people in each group mentioned the theme).
"""
import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import interview_answers, participant_code, respondent_table
from .groups import is_group
from .models import Insight, Interview, Project
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider
from .report_models import ReportTemplate

log = logging.getLogger(__name__)

MIN_GROUP = 3            # a comparison with a group smaller than this is left off by default
MAX_THEME_BARS = 8
MAX_AUTO_COMPARE = 3
MAX_PICKS = 3
MAX_ITEMS = 4            # answers shown per person in the side panel
MAX_NOTE = 400
SEP = respondent_table.SEP
FIXED = ("summary", "themes", "appendix")
TITLES = {"summary": "总体结论", "themes": "主题分布", "appendix": "附录：研究背景、方法与样本"}
PICK_SYSTEM = """You help a market research team choose which comparisons their report shows. You get what the team wants to see, a list of questionnaire questions (columns) and a list of themes people mentioned. Pick at most 3 comparisons that answer the team's words: each is one column and one theme, both copied exactly from the lists. Reply with ONE JSON object: {"picks": [{"col": "...", "theme": "..."}]}. If nothing fits reply {"picks": []}."""
NOTE_SYSTEM = """你是市场研究助手。你会收到一份深度访谈报告里每张图的统计数字（几位受访者提到、各组样本多少）。用中文为每张图写 1-2 句解读，再写一段 2-3 句的“总体结论”。规则：只能使用给你的数字，不许编造数字或原话；样本很小要说明“仅作方向参考”；受访者不要出现姓名。只输出一个 JSON 对象：{"summary": "...", "notes": {"<卡片编号>": "..."}}。"""


@dataclass
class Person:
    code: str
    interview_id: str
    screen: dict[str, str]                       # questionnaire question -> answer
    items: list[dict] = field(default_factory=list)   # {"q", "a", "themes": [...], "at_ms"}
    themes: set[str] = field(default_factory=set)


def _key(theme: str) -> str:
    return re.sub(r"\s+", " ", theme.strip().lower())


def people(db: Session, proj: Project) -> tuple[list[Person], list[dict], dict[str, str]]:
    """(the people who finished an interview, the questionnaire columns that split them (choice questions), {theme key: shown theme})."""
    itvs = [i for i in db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.status == "ended", Interview.purged_at.is_(None))).all() if not is_group(i)]
    codes = participant_code.code_map(db, proj.id)
    cols, by_person = respondent_table.screening(db, proj.id, list({i.participant_id for i in itvs}))
    label = {c["id"]: c["label"] for c in cols}
    shown: dict[str, str] = {}
    out: list[Person] = []
    for itv in sorted(itvs, key=lambda i: codes.get(i.participant_id, "")):
        stored = interview_answers.load(itv)
        items = [{"q": q, "a": it["a"], "themes": [t for t in it.get("themes") or [] if t.strip()], "at_ms": it.get("at_ms")} for q, it in stored.items() if it.get("a")]
        themes = {t for it in items for t in it["themes"]}
        if not themes:   # answers were not worked out: fall back to the confirmed insights of this interview
            for ins in db.scalars(select(Insight).where(Insight.interview_id == itv.id, Insight.status == "approved")).all():
                items.append({"q": "洞察", "a": ins.statement, "themes": [ins.theme], "at_ms": ins.evidence[0].start_ms if ins.evidence else None})
        p = Person(codes.get(itv.participant_id, "?"), itv.id, {label[k]: v for k, v in by_person.get(itv.participant_id, {}).items() if k in label}, items)
        for it in items:
            for t in it["themes"]:
                shown.setdefault(_key(t), t.strip())
                p.themes.add(_key(t))
        out.append(p)
    return out, [c for c in cols if c["type"] == "choice"], shown


def _groups(person: Person, col: str) -> list[str]:
    v = person.screen.get(col, "")
    return [x.strip() for x in v.split(SEP) if x.strip()] if v else []


def _panel(person: Person, theme: str | None, group: str = "") -> dict:
    """One row of the side panel: the person's answers about the theme (or all their answers), copied word for word."""
    items = [it for it in person.items if theme is None or theme in {_key(t) for t in it["themes"]}][:MAX_ITEMS]
    return {"code": person.code, "interview_id": person.interview_id, "group": group,
            "items": [{"q": it["q"], "a": it["a"], "themes": it["themes"], "at_ms": it["at_ms"]} for it in items]}


def themes_card(ps: list[Person], shown: dict[str, str]) -> dict:
    n = len(ps)
    count = Counter(t for p in ps for t in p.themes)
    bars = [{"label": shown[t], "k": k, "n": n, "people": [_panel(p, t) for p in ps if t in p.themes]} for t, k in count.most_common(MAX_THEME_BARS)]
    return {"id": "themes", "kind": "themes", "title": TITLES["themes"], "desc": f"每个主题被提到的受访者人数。样本：共 {n} 位。", "bars": bars, "ready": bool(bars), "reason": "" if bars else "还没有可统计的主题：先在“受访者数据”里生成回答的主题，或确认洞察"}


def compare_card(ps: list[Person], shown: dict[str, str], cols: list[dict], col: str, theme_key: str) -> dict | None:
    """How many people in each group of a questionnaire question mentioned a theme; None when the question or theme does not exist."""
    spec = next((c for c in cols if c["label"] == col), None)
    if spec is None or theme_key not in shown:
        return None
    opts = [o for o in (spec["options"] or sorted({g for p in ps for g in _groups(p, col)})) if any(o in _groups(p, col) for p in ps)]
    bars = []
    for o in opts:
        members = [p for p in ps if o in _groups(p, col)]
        bars.append({"label": o, "k": sum(theme_key in p.themes for p in members), "n": len(members), "people": [_panel(p, theme_key, o) for p in members if theme_key in p.themes]})
    small = [b["label"] for b in bars if b["n"] < MIN_GROUP]
    reason = "" if len(bars) >= 2 and not small else ("只有一组" if len(bars) < 2 else f"“{'、'.join(small)}”不到 {MIN_GROUP} 位，对比不可靠")
    sizes = "，".join(f"{b['label']} = {b['n']} 位" for b in bars)
    return {"id": f"cmp:{col}:{theme_key}", "kind": "compare", "title": f"{shown[theme_key]}：按{col}", "desc": f"提到“{shown[theme_key]}”的受访者人数。样本：{sizes}。", "bars": bars,
            "ready": not reason, "reason": reason}


def _fp(card: dict) -> str:
    return hashlib.sha1(json.dumps([[b["label"], b["k"], b["n"]] for b in card["bars"]], ensure_ascii=False).encode()).hexdigest()[:10]


def rule_note(card: dict) -> str:
    """A plain sentence that restates the numbers (used until a model's reading exists, and when no model is configured)."""
    bars = card["bars"]
    if not bars:
        return ""
    if card["kind"] == "themes":
        top = bars[:3]
        return "最常被提到的是" + "、".join(f"“{b['label']}”（{b['k']}/{b['n']}）" for b in top) + "。样本小，仅作方向参考。"
    hi = max(bars, key=lambda b: b["k"] / b["n"] if b["n"] else 0)
    return f"提到该主题的比例最高的是“{hi['label']}”（{hi['k']}/{hi['n']}）。样本小，仅作方向参考。"


def pick(text: str, ps: list[Person], shown: dict[str, str], cols: list[dict], chat: ModeratorChatProvider | None) -> list[str]:
    """Block ids (cmp:...) that answer the team's words. With a model it chooses from the real lists (anything not on them is dropped); otherwise the words are matched."""
    text = text.strip()
    if not text or not cols or not shown:
        return []
    labels = [c["label"] for c in cols]
    got: list[tuple[str, str]] = []
    if chat is not None:
        try:
            reply = chat.complete([{"role": "system", "content": PICK_SYSTEM}, {"role": "user", "content": json.dumps({"want": text[:600], "columns": labels, "themes": list(shown.values())[:60]}, ensure_ascii=False)}], temperature=0.1)
            for it in (reply.get("picks") if isinstance(reply, dict) else None) or []:
                if isinstance(it, dict) and it.get("col") in labels and _key(str(it.get("theme", ""))) in shown:
                    got.append((it["col"], _key(str(it["theme"]))))
        except ChatFatalError:
            raise
        except ChatError:
            log.warning("report block picking: model gave no usable reply")
    if not got:
        cs = [c for c in labels if c in text or interview_answers._score(c, text) >= 0.6]
        ts = [k for k, v in shown.items() if v in text or k in text.lower()]
        top = Counter(t for p in ps for t in p.themes).most_common(1)
        for c in cs[:MAX_PICKS]:
            for t in (ts or [k for k, _ in top])[:1]:
                got.append((c, t))
    seen, out = set(), []
    for c, t in got:
        i = f"cmp:{c}:{t}"
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out[:MAX_PICKS]


def _template(db: Session, proj: Project) -> ReportTemplate:
    t = db.scalar(select(ReportTemplate).where(ReportTemplate.project_id == proj.id))
    if t is None:
        t = ReportTemplate(org_id=proj.org_id, project_id=proj.id, focus="", picked=[], order=[], on=[], off=[], notes={})
        db.add(t)
        db.flush()
    return t


def template_of(db: Session, proj: Project) -> ReportTemplate:
    return _template(db, proj)


def _card_for(bid: str, ps: list[Person], shown: dict[str, str], cols: list[dict]) -> dict | None:
    if bid == "themes":
        return themes_card(ps, shown)
    if bid.startswith("cmp:"):
        _, _, rest = bid.partition(":")
        col, _, theme = rest.rpartition(":")
        return compare_card(ps, shown, cols, col, theme)
    return None


def build(db: Session, proj: Project, client: bool = False) -> dict:
    """The report as the page needs it: the blocks in order (each with whether it is on and where it came from), the cards of the ones that are on, and the readings."""
    tpl = _template(db, proj)
    ps, cols, shown = people(db, proj)
    n = len(ps)
    auto: list[str] = []
    top = Counter(t for p in ps for t in p.themes).most_common(1)
    if top:
        for c in cols[:MAX_AUTO_COMPARE]:
            auto.append(f"cmp:{c['label']}:{top[0][0]}")
    base = ["summary", *[i for i in tpl.picked if i.startswith("cmp:")], *[i for i in auto if i not in tpl.picked], "themes", "appendix"]
    ids = [i for i in tpl.order if i in base] + [i for i in base if i not in tpl.order]
    blocks, cards = [], []
    for bid in ids:
        card = _card_for(bid, ps, shown, cols)
        if bid in FIXED:
            title, source, default_on = TITLES[bid], "每次生成都有" if bid != "themes" else f"来自 {n} 位受访者的回答主题", True
            if bid == "themes" and card and not card["ready"]:
                default_on = False
        elif card:
            title, default_on = card["title"], card["ready"]
            source = ("你写的重点" if bid in tpl.picked else f"问卷题“{card['title'].split('：按')[-1]}”") + (f" · {card['reason']}" if card["reason"] else "")
        else:
            continue   # a picked block whose question or theme is gone
        on = bid in tpl.on or (bid not in tpl.off and default_on)
        blocks.append({"id": bid, "title": title, "on": on, "source": source})
        if on and card:
            note = tpl.notes.get(bid)
            card["note"] = {"t": note["t"], "ai": True, "stale": note.get("fp") != _fp(card)} if note and note.get("fp") == _fp(card) else {"t": rule_note(card), "ai": False, "stale": bool(note)}
            if client:   # a client sees the counts, never the questionnaire groups or what individuals said
                if card["kind"] == "compare":
                    continue
                for b in card["bars"]:
                    b["people"] = []
            cards.append(card)
    summary = tpl.notes.get("summary")
    mentions = Counter(t for p in ps for t in p.themes).most_common(3)
    rule = f"共 {n} 位受访者完成访谈" + ("，最常被提到的是" + "、".join(f"“{shown[t]}”（{k}/{n}）" for t, k in mentions) if mentions else "，还没有可统计的主题") + "。样本小，仅作方向参考。"
    return {"n": n, "focus": tpl.focus if not client else "", "blocks": [] if client else blocks, "cards": cards,
            "summary": {"t": summary["t"], "ai": True} if summary else {"t": rule, "ai": False}}


def write_notes(db: Session, proj: Project, chat: ModeratorChatProvider) -> dict:
    """Ask the model for a reading of each card that is on and for the overall conclusion; store them with the fingerprint of the numbers.

    Raises:
        ChatFatalError: the model service refused (bad key, no balance...).
        ChatError: no usable reply.
    """
    tpl = _template(db, proj)
    data = build(db, proj)
    cards = [c for c in data["cards"] if c["bars"]]
    payload = {"focus": tpl.focus[:600], "brief": (proj.brief or "")[:600], "n": data["n"], "cards": [{"id": c["id"], "title": c["title"], "bars": [{"label": b["label"], "k": b["k"], "n": b["n"]} for b in c["bars"]]} for c in cards]}
    reply = chat.complete([{"role": "system", "content": NOTE_SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], temperature=0.3)
    if not isinstance(reply, dict) or not str(reply.get("summary", "")).strip():
        raise ChatError("report reading has no summary")
    notes = dict(tpl.notes)
    notes["summary"] = {"t": str(reply["summary"]).strip()[:MAX_NOTE], "fp": ""}
    for c in cards:
        t = (reply.get("notes") or {}).get(c["id"]) if isinstance(reply.get("notes"), dict) else None
        if isinstance(t, str) and t.strip():
            notes[c["id"]] = {"t": t.strip()[:MAX_NOTE], "fp": _fp(c)}
    tpl.notes = notes
    return {"written": len(notes)}
