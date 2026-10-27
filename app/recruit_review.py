"""The questionnaire assistant's second job: look the questionnaire over and say what could be better, and answer questions about it with proof.

Most suggestions need no model at all: they are read from the questionnaire, the quotas and the anonymous counts of how many people were stopped at each question
(`RecruitForm.out_by`). The model only adds what needs reading (leading wording, goals the questions do not cover). A suggestion never changes the questionnaire by itself:
the researcher adopts it, or leaves it where it is. It goes away when the question it is about is edited, and "不再提醒" keeps it from coming back.

Where an answer or a suggestion comes from is named with keys from a fixed set (`brief`, `library`, `stats`, `quota:<name>`, `question:<id>`); the server turns them into the
labels the page shows and drops any key it does not know, so the model cannot invent a source.
"""
import hashlib
import json
import logging
import math

from . import recruit_form
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

STRICT_SHARE = 0.4  # a question that stops this share of the people who started is flagged
MIN_STARTED = 5  # ... once at least this many people have started (fewer says nothing)
COMFORT_QUESTIONS = 10
SECONDS_PER_QUESTION = 25
MAX_AI_ITEMS = 5
MAX_TEXT = 300
KIND_LABEL = {"quota": "配额对不上", "strict": "条件偏严", "exclusive": "选项互斥", "dead_end": "没有人能继续", "length": "题量", "wording": "措辞", "coverage": "没覆盖研究目标"}
AI_KINDS = ("wording", "coverage")

REVIEW_SYSTEM = """You review a recruiting (screening) questionnaire for a market research team. You get the research background, goals, the recruiting need, the questionnaire and anonymous counts.
Reply with ONE JSON object and nothing else:
{"items": [{"kind": "wording" | "coverage", "question_id": "<id of the question it is about, or \\"\\" when it is about the whole questionnaire>",
 "text": "<what is wrong and why, 1 or 2 short sentences, Chinese, plain words>",
 "instruction": "<what to tell the editor assistant to fix it, one short sentence, Chinese, e.g. 把第 7 题改成“您怎么看保险？”>",
 "sources": ["brief" | "library" | "stats" | "question:<id>"]}]}
"wording": a question that leads the person, names a brand, asks two things at once, or is unclear. "coverage": a research goal or a recruiting need that no question helps to decide.
At most 5 items. Only say what really is a problem; an empty list is a fine answer. Never invent numbers."""


def qhash(q: recruit_form.Question) -> str:
    """A short fingerprint of a question: a suggestion about it expires when this changes."""
    return hashlib.sha1(json.dumps(q.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def sig_of(kind: str, qid: str, key: str) -> str:
    return hashlib.sha1(f"{kind}|{qid}|{key}".encode()).hexdigest()[:12]


def _no(questions: list[recruit_form.Question], qid: str) -> int:
    return next((i + 1 for i, q in enumerate(questions) if q.id == qid), 0)


def _item(kind: str, qid: str, key: str, text: str, instruction: str, sources: list[str], fix: dict | None = None, button: str = "采纳") -> dict:
    return {"sig": sig_of(kind, qid, key), "kind": kind, "label": KIND_LABEL[kind], "qid": qid, "text": text, "instruction": instruction, "fix": fix, "sources": sources, "button": button}


def rule_suggestions(questions: list[recruit_form.Question], quotas: list[dict], stats: dict) -> list[dict]:
    """The suggestions that are read straight from the data. `stats` = {"started": n, "out_by": {question id: how many were stopped there}}."""
    out: list[dict] = []
    provided = {o.tag for q in questions for o in q.options if o.tag}
    for qt in quotas:
        if qt.get("field") == "tag" and qt.get("value") and qt["value"] not in provided:
            out.append(_item("quota", "", f"{qt.get('name', '')}|{qt['value']}", f"配额“{qt.get('name') or qt['value']}”按标签“{qt['value']}”统计，但问卷里没有任何选项会打这个标签，问卷来的人不会计入这个配额。",
                             f"请在问卷里能判断这一类人的那道题上，给对应的选项加标签“{qt['value']}”", [f"quota:{qt.get('name') or qt['value']}"], button="采纳：让 AI 加标签"))
    started = stats.get("started", 0)
    for q in questions:
        no = _no(questions, q.id)
        stopped = (stats.get("out_by") or {}).get(q.id, 0)
        if started >= MIN_STARTED and stopped / started >= STRICT_SHARE:
            pct = round(stopped * 100 / started)
            out.append(_item("strict", q.id, "strict", f"第 {no} 题筛掉了 {pct}% 的人（{started} 人开始作答，{stopped} 人在这里被筛掉），是最严的一关，看看条件是不是设得太窄。",
                             f"第 {no} 题筛掉了 {pct}% 的人，帮我看看条件是不是设得太窄，如果是请放宽", ["stats", f"question:{q.id}"], button="采纳：让 AI 看看怎么放宽"))
        tagged = [o for o in q.options if o.tag]
        if q.type == "multi" and len({o.tag for o in tagged}) >= 2 and any(b.id not in (a.excl or []) for i, a in enumerate(tagged) for b in tagged[i + 1:] if a.tag != b.tag):
            out.append(_item("exclusive", q.id, "excl", f"第 {no} 题是多选，带标签的选项没有设“互斥”，同一个人可能同时拿到这些标签（{'、'.join(sorted({o.tag for o in tagged}))}）。",
                             f"把第 {no} 题带标签的选项设为互斥", [f"question:{q.id}"], {"op": "exclusive", "qid": q.id}, "采纳：设为互斥"))
        if q.options and all(o.goto == recruit_form.END_OUT for o in q.options):
            out.append(_item("dead_end", q.id, "dead", f"第 {no} 题的每个选项都会把人筛掉，没有人能继续答下去。", f"第 {no} 题的选项都会筛掉人，请改成至少有一个选项能继续", [f"question:{q.id}"], button="采纳：让 AI 修"))
    if len(questions) > COMFORT_QUESTIONS:
        mins = max(1, math.ceil(len(questions) * SECONDS_PER_QUESTION / 60))
        out.append(_item("length", "", "len", f"共 {len(questions)} 题，大约要答 {mins} 分钟，题越多越容易有人中途放弃。", f"把问卷精简到 {COMFORT_QUESTIONS} 题以内，保留筛选必需的", [], button="采纳：让 AI 精简"))
    return out


def apply_fix(questions: list[recruit_form.Question], fix: dict) -> list[recruit_form.Question]:
    """Do the one deterministic fix (set the tagged options of a multi-choice question as mutually exclusive). Returns a new list."""
    new = [q.model_copy(deep=True) for q in questions]
    q = next((x for x in new if x.id == fix.get("qid")), None)
    if q is None or fix.get("op") != "exclusive":
        raise recruit_form.FormError("这条建议已经不适用了")
    tagged = [o for o in q.options if o.tag]
    for a in tagged:
        for b in tagged:
            if a is not b and a.tag != b.tag and b.id not in (a.excl or []):
                a.excl = [*(a.excl or []), b.id]
    return new


def source_labels(keys: list, questions: list[recruit_form.Question], quotas: list[dict], has_brief: bool, has_library: bool) -> list[str]:
    """The names shown for the sources an answer rests on. Keys that are not in the fixed set are dropped."""
    out: list[str] = []
    names = {qt.get("name") or qt.get("value"): qt for qt in quotas}
    for k in keys if isinstance(keys, list) else []:
        k = str(k)
        if k == "brief" and has_brief:
            out.append("研究背景")
        elif k == "library" and has_library:
            out.append("研究库资料")
        elif k == "stats":
            out.append("作答统计（匿名）")
        elif k.startswith("quota:") and k[6:] in names:
            out.append(f"配额：{k[6:]}（目标 {names[k[6:]].get('target', '?')} 人）")
        elif k.startswith("question:") and _no(questions, k[9:]):
            out.append(f"问卷第 {_no(questions, k[9:])} 题")
    return list(dict.fromkeys(out))


def stats_context(questions: list[recruit_form.Question], quotas: list[dict], stats: dict, tally: dict) -> str:
    """The anonymous numbers the model may use, as text: counts only, never a name or an answer of one person."""
    lines = [f"People who started: {stats.get('started', 0)}; qualified: {stats.get('qualified', 0)}; screened out: {stats.get('out', 0)}."]
    for q in questions:
        stopped = (stats.get("out_by") or {}).get(q.id, 0)
        if stopped:
            lines.append(f"Question {_no(questions, q.id)} (id {q.id}) stopped {stopped} people.")
        per = (tally.get("q") or {}).get(q.id) or {}
        n = per.get("_n", 0)
        if n and q.options:
            lines.append(f"Question {_no(questions, q.id)}, share of qualified people choosing: " + ", ".join(f"{o.text[:30]} {round(per.get(o.id, 0) * 100 / n)}%" for o in q.options))
    for qt in quotas:
        lines.append(f"Quota {qt.get('name') or qt.get('value')}: target {qt.get('target')}, have {qt.get('have', '?')}.")
    return "\n".join(lines)


def ai_review(chat: ModeratorChatProvider | None, questions: list[recruit_form.Question], goals: list[str], need: str, brief: str, library: str, stats_text: str, language: str) -> list[dict]:
    """The model's own suggestions (wording, goals not covered); the offline mock has none. Each comes back with the fingerprint of the question it is about.

    Raises:
        recruit_form.FormError: the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if chat is None:
        return []
    context = (f"Research background:\n{brief.strip() or '(none)'}\n\nReference material:\n{library or '(none)'}\n\nGoals: {json.dumps(goals, ensure_ascii=False)}\nRecruiting need: {need or '(none)'}\n\n"
               f"Questionnaire (JSON):\n{json.dumps([q.model_dump() for q in questions], ensure_ascii=False)}\n\nAnonymous counts:\n{stats_text}\nWrite in {language}.")
    try:
        reply = chat.complete([{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": context}], temperature=0.2)
    except ChatFatalError:
        raise
    except ChatError as e:
        raise recruit_form.FormError("AI 暂时无法检查，请稍后再试") from e
    by_id = {q.id: q for q in questions}
    out: list[dict] = []
    for it in (reply.get("items") if isinstance(reply, dict) and isinstance(reply.get("items"), list) else [])[:MAX_AI_ITEMS]:
        if not isinstance(it, dict) or it.get("kind") not in AI_KINDS:
            continue
        qid = str(it.get("question_id") or "")
        text, instruction = str(it.get("text", "")).strip()[:MAX_TEXT], str(it.get("instruction", "")).strip()[:MAX_TEXT]
        if not text or not instruction or (qid and qid not in by_id):
            continue
        item = _item(it["kind"], qid, text[:40], text, instruction, [s for s in it.get("sources", []) if isinstance(s, str)], button="采纳：让 AI 改")
        item["qhash"] = qhash(by_id[qid]) if qid else ""
        out.append(item)
    return out


def open_suggestions(questions: list[recruit_form.Question], quotas: list[dict], stats: dict, ai_items: list[dict], dismissed: list[str], has_brief: bool, has_library: bool) -> list[dict]:
    """What the page shows: the rule suggestions plus the model's, minus the dismissed ones and the ones whose question was edited since."""
    by_id = {q.id: q for q in questions}
    items = rule_suggestions(questions, quotas, stats) + [i for i in ai_items if not i.get("qid") or (i["qid"] in by_id and i.get("qhash") == qhash(by_id[i["qid"]]))]
    seen = set(dismissed or [])
    out = []
    for i in items:
        if i["sig"] in seen:
            continue
        out.append({**{k: v for k, v in i.items() if k not in ("qhash", "sources")}, "qno": _no(questions, i["qid"]) if i["qid"] else 0, "sources": source_labels(i.get("sources", []), questions, quotas, has_brief, has_library)})
    return out
