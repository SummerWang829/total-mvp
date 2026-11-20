"""The rule side of "生成大纲": what is already known, what still has to be asked, how the outline is split into modules and minutes, what is wrong with it and what to suggest.

Everything here is plain code (no model): it is repeatable and tested. The model only helps with wording and with writing the questions (app/outline_wb/writer.py).
A suggestion always names what it rests on (a line of the background, a research goal, a trait of the people); a suggestion that rests on nothing is not made.
"""
import math
import re
from dataclasses import dataclass

ASK_MIN_SCORE = 0.15        # below this a question is not worth asking
MAX_PER_ROUND, MAX_TOTAL = 3, 10
ROUND_NAMES = {1: "目标", 2: "人群与执行", 3: "执行细节"}
MIN_PER_QUESTION = 2.2      # minutes one question with its follow-ups takes
MIN_QUESTIONS = 2
WARM_SHARE, CLOSE_SHARE = 0.15, 0.07   # warm-up (with the opening) and closing share of the total time
DEPTHS = ("none", "shallow", "deep")   # 不追问 / 追问 1 次 / 追问 1 次以上
DEPTH_LABELS = {"none": "不追问", "shallow": "1 次", "deep": "1 次以上"}
RANK = {"warm": 0, "behaviour": 1, "attitude": 2, "concept": 3, "price": 4, "brand": 5, "compare": 2, "close": 9}   # order of modules: what people did before what they think, unprompted before prompted, concept before price
RESEARCH_TYPES = {"usage": "使用与态度", "journey": "购买旅程", "brand": "品牌认知", "concept": "概念测试", "ad": "广告测试", "pricing": "定价", "usability": "可用性", "localize": "出海本地化"}
TYPE_WORDS = {"concept": ("概念", "新品", "原型", "试用", "包装"), "pricing": ("价格", "定价", "多少钱", "愿意付"), "ad": ("广告", "短片", "投放"), "usability": ("网站", "App", "界面", "操作", "易用"),
              "brand": ("品牌认知", "品牌形象", "品牌联想"), "journey": ("购买", "购买旅程", "决策", "怎么买", "怎么选"), "localize": ("进入", "本地化", "出海", "新市场"), "usage": ("使用", "习惯", "态度")}


@dataclass
class Slot:
    name: str
    label: str
    importance: float
    impact: float
    round: int


SLOT_DEFS = [Slot("research_type", "研究类型", 1.0, 1.0, 1), Slot("key_questions", "要回答的问题", 0.9, 1.0, 1), Slot("audience", "访谈对象", 0.8, 0.9, 2), Slot("minutes", "时长", 0.6, 0.7, 3),
             Slot("materials", "要给受访者看的物料", 0.7, 0.8, 3), Slot("price", "价格怎么谈", 0.6, 0.6, 3)]
OPTIONS = {
    "research_type": [(v, k) for k, v in RESEARCH_TYPES.items()],
    "minutes": [("30 分钟", 30), ("45 分钟", 45), ("60 分钟", 60)],
    "materials": [("概念卡", "concept"), ("实物或包装图", "image"), ("视频片段", "video"), ("网站任务", "website"), ("不看素材", "none")],
    "price": [("放在看完概念之后", "after_concept"), ("不谈价格", "skip"), ("先谈价格", "first")],
}
QUESTION_TEXT = {"research_type": "这次研究主要想弄清什么？", "key_questions": "这次研究要回答哪几个问题？（最多 4 个，每行一个）", "audience": "访谈的是什么样的人？",
                 "minutes": "每场访谈多长？", "materials": "要不要给受访者看素材？", "price": "价格要不要谈？"}


SLOT_KIND = {"research_type": "choice", "materials": "multi", "price": "choice", "minutes": "number", "key_questions": "lines", "audience": "text"}   # how the "改" editor looks (修正方案第 1 轮第 6 条)
OTHER = "其他"   # a code missing from the dictionary is shown as this, never as the code itself


def slot_text(name: str, value) -> str:
    """The Chinese a person reads for a slot's value: never an internal code such as usage / none / skip / after_concept."""
    if name == "research_type":
        return RESEARCH_TYPES.get(str(value), OTHER) if value else "—"
    if name in ("materials", "price"):
        names = {v: t for t, v in OPTIONS[name]}
        vals = [x for x in (value if isinstance(value, list) else [value]) if x and x != "none"]
        if name == "materials" and not vals:
            return "不看素材"
        return "、".join(names.get(str(x), OTHER) for x in vals) or "—"
    if name == "minutes":
        return f"{value} 分钟" if value else "—"
    if name == "key_questions":
        return "　".join(f"{'①②③④'[i]} {x}" for i, x in enumerate((value or [])[:4]))
    return str(value or "—")


def slot_options(name: str) -> list[dict]:
    """The choices of a choice slot for its editor: [{value, text}] (empty for a text slot)."""
    return [{"value": v, "text": t} for t, v in OPTIONS.get(name, []) if name != "minutes"]


def fingerprint(slots: dict[str, dict], background: str) -> str:
    """What the overview and the questions were made from. When it changes, what was made earlier is shown as "需要重新生成" (never overwritten by itself)."""
    import hashlib
    import json
    raw = json.dumps({"bg": " ".join((background or "").split()), **{k: slots[k]["value"] for k in sorted(slots)}}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def guess_type(background: str, goals: list[str]) -> tuple[str, float]:
    """The research type the background points to, and how sure that is (0 when nothing in the text points anywhere)."""
    text = (background or "") + " ".join(goals or [])
    hits = {t: sum(text.count(w) for w in ws) for t, ws in TYPE_WORDS.items()}
    best = max(hits, key=lambda k: hits[k])
    if hits[best] == 0:
        return "usage", 0.0
    return best, min(0.9, 0.5 + 0.1 * hits[best])


def known(context: dict, answers: dict) -> dict[str, dict]:
    """Every slot with its value, where it came from (project / answer / inferred / default / none) and how sure that is (0..1). `context` is read from the project, `answers` are the researcher's."""
    out: dict[str, dict] = {}
    for s in SLOT_DEFS:
        if s.name in answers and not (s.name == "minutes" and context.get("sync")):   # a bound 立项 sub-project owns the length (修正方案第 1 轮第 5 条)
            out[s.name] = {"value": answers[s.name], "source": "answer", "confidence": 1.0}
        elif s.name == "key_questions" and context.get("goals"):
            out[s.name] = {"value": list(context["goals"])[:4], "source": "project", "confidence": 1.0}
        elif s.name == "audience" and context.get("audience"):
            out[s.name] = {"value": context["audience"], "source": "project", "confidence": 1.0}
        elif s.name == "research_type":
            t, c = guess_type(context.get("background", ""), context.get("goals", []))
            out[s.name] = {"value": t, "source": "inferred" if c else "default", "confidence": c or 0.2}
        elif s.name == "minutes":
            out[s.name] = {"value": context.get("minutes") or 45, "source": "project" if context.get("minutes") else "default", "confidence": 1.0 if context.get("minutes") else 0.5}
        elif s.name == "price":
            t = out.get("research_type", {}).get("value", "usage")
            need = t in ("concept", "pricing")
            out[s.name] = {"value": "after_concept" if need else "skip", "source": "inferred" if need else "default", "confidence": 0.6 if need else 0.9}
        elif s.name == "materials":
            t = out.get("research_type", {}).get("value", "usage")
            out[s.name] = {"value": ["concept"] if t == "concept" else ["video"] if t == "ad" else ["website"] if t == "usability" else [], "source": "inferred" if t in ("concept", "ad", "usability") else "default",
                           "confidence": 0.6 if t in ("concept", "ad", "usability") else 0.5}
        else:
            out[s.name] = {"value": "", "source": "none", "confidence": 0.0}
    return out


def ask_score(slot: Slot, confidence: float) -> float:
    """How much asking about a slot is worth: how important it is × how unsure we are × how much of the outline depends on it."""
    return slot.importance * (1.0 - confidence) * slot.impact


def next_questions(slots: dict[str, dict], round_no: int, already_asked: int = 0) -> list[dict]:
    """What to ask in this round: the highest scores first, at most three in a round and ten in all, none below ASK_MIN_SCORE."""
    left = max(0, MAX_TOTAL - already_asked)
    scored = sorted(((ask_score(s, slots[s.name]["confidence"]), s) for s in SLOT_DEFS if s.round == round_no), key=lambda x: -x[0])
    out = []
    for score, s in scored:
        if score < ASK_MIN_SCORE or len(out) >= min(MAX_PER_ROUND, left):
            continue
        cur = slots[s.name]["value"]
        opts = [{"text": t, "value": v, "recommended": v == cur or (isinstance(cur, list) and v in cur)} for t, v in OPTIONS.get(s.name, [])]
        out.append({"slot": s.name, "question": QUESTION_TEXT[s.name], "options": opts, "score": round(score, 2), "free_text": s.name in ("key_questions", "audience")})
    return out


# ---- structure and time --------------------------------------------------------------------------------------------------------------

def _module(mid: str, name: str, purpose: str, content: str, kind: str, serves: list[int], depth: str, materials: list[str] | None = None) -> dict:
    return {"id": mid, "name": name, "purpose": purpose, "content": content, "kind": kind, "serves": serves, "depth": depth, "materials": materials or [], "minutes": 0, "questions": 0, "status": "todo"}


def build_modules(slots: dict[str, dict], context: dict) -> list[dict]:
    """The modules in the order the rules want: warm-up, one module per key question (what people did before what they think), concept, price, closing."""
    kqs = [k for k in (slots["key_questions"]["value"] or []) if str(k).strip()][:4] or ["了解目标人群的真实经历和看法"]
    mats = slots["materials"]["value"] or []
    mods = [_module("warm", "暖场", "让受访者放松，了解日常习惯", "平时怎么买、怎么用", "warm", [], "shallow")]
    made: set[str] = set()
    for i, k in enumerate(kqs, 1):
        kind = _kind_of(k)
        if kind in ("price", "concept") and kind in made:
            kind = "attitude"
        if kind == "price":
            mods.append(_module("price", "价格", f"回答：{k}", "合理价位、超过多少放弃", "price", [i], "deep"))
        elif kind == "concept":
            mods.append(_module("concept", "概念评估", f"回答：{k}", "第一印象、理由、想试用的程度（打分）", "concept", [i], "deep", [m for m in mats if m in ("concept", "image")]))
        else:
            mods.append(_module(f"k{i}", _short(k), f"回答：{k}", "具体经历、原因、比较、影响因素" if kind == "behaviour" else "看法、理由、期待", kind, [i], "deep"))
        made.add(kind)
    if "concept" not in made and any(m in mats for m in ("concept", "image")):
        mods.append(_module("concept", "概念评估", "看素材后的第一反应与意愿", "第一印象、理由、想试用的程度（打分）", "concept", [], "deep", [m for m in mats if m in ("concept", "image")]))
    if "price" not in made and slots["price"]["value"] in ("after_concept", "first"):
        mods.append(_module("price", "价格", "价格接受范围", "合理价位、超过多少放弃", "price", [], "deep"))
    if any(m in mats for m in ("video", "website")):
        mods.append(_module("brand", "品牌与任务", "看短片或完成网站任务后的反应", "看法、卡在哪里", "brand", [], "deep", [m for m in mats if m in ("video", "website")]))
    mods.append(_module("close", "收尾", "补充遗漏、致谢", "还有什么想说的", "close", [], "none"))
    mods.sort(key=lambda m: (RANK[m["kind"]], m["id"]))
    return allocate(mods, int(slots["minutes"]["value"] or 45))


def _kind_of(question: str) -> str:
    """Which kind of module a key question becomes: one about price or about reacting to the concept gets its own kind (so the order rules apply), otherwise what people did or what they think."""
    if re.search(r"价格|价位|定价|多少钱|愿意付", question):
        return "price"
    if re.search(r"概念|第一反应|第一印象|试用|购买意愿", question):
        return "concept"
    return "behaviour" if re.search(r"怎么|如何|什么时候|经历|过程|渠道|习惯", question) else "attitude"


def _short(text: str) -> str:
    t = re.sub(r"[？?。！!，,\s]+$", "", str(text).strip())
    return t if len(t) <= 8 else t[:8]


def allocate(mods: list[dict], total: int) -> list[dict]:
    """Minutes and question counts: warm-up and closing get a fixed share, every other module is first guaranteed room for two questions, the rest goes by weight (a module serving the first key question ×1.3).
    When the guarantee alone is more than the time there is, the minutes still add up to the total and `tight` is set so the check can say so."""
    warm = round(total * WARM_SHARE) if any(m["kind"] == "warm" for m in mods) else 0
    close = round(total * CLOSE_SHARE) if any(m["kind"] == "close" for m in mods) else 0
    core = [m for m in mods if m["kind"] not in ("warm", "close")]
    left = max(0, total - warm - close)
    floor = MIN_QUESTIONS * MIN_PER_QUESTION
    weights = [1.3 if 1 in m["serves"] else 1.0 for m in core]
    extra = max(0.0, left - floor * len(core))
    for m, w in zip(core, weights):
        if left < floor * len(core):   # not even room for the guarantee: share what there is; the modules are flagged "tight" below
            m["minutes"] = left / len(core)
        else:
            m["minutes"] = floor + extra * w / sum(weights)
    out = []
    for m in mods:
        if m["kind"] == "warm":
            m["minutes"] = warm
        elif m["kind"] == "close":
            m["minutes"] = close
        out.append(m)
    _round_to_total(out, total)
    for m in out:
        m["questions"] = max(1 if m["kind"] in ("warm", "close") else MIN_QUESTIONS, int(m["minutes"] / MIN_PER_QUESTION)) if m["minutes"] else 0
        m["tight"] = m["kind"] not in ("warm", "close") and m["minutes"] < floor
    return out


def _round_to_total(mods: list[dict], total: int) -> None:
    """Whole minutes that add up to `total` (the biggest module absorbs the rounding)."""
    for m in mods:
        m["minutes"] = max(1, int(round(m["minutes"]))) if m["minutes"] else 0
    diff = total - sum(m["minutes"] for m in mods)
    if mods and diff:
        big = max(mods, key=lambda m: m["minutes"])
        big["minutes"] = max(1, big["minutes"] + diff)


# ---- findings and suggestions ---------------------------------------------------------------------------------------------------------

def findings(modules: list[dict], context: dict, slots: dict[str, dict]) -> list[dict]:
    """What is wrong or worth a look in this structure. Each finding: {id, level ok|warn, text}. Pure rules."""
    out: list[dict] = []
    total = int(slots["minutes"]["value"] or 45)
    kqs = slots["key_questions"]["value"] or []
    for i, k in enumerate(kqs, 1):
        n = sum(m["questions"] for m in modules if i in m["serves"])
        if n < 2:
            out.append({"id": f"cover{i}", "level": "warn", "text": f"问题{i}“{_short(k)}”只有 {n} 道题，建议至少 2 道"})
    if sum(m["minutes"] for m in modules) != total:
        out.append({"id": "total", "level": "warn", "text": f"模块合计 {sum(m['minutes'] for m in modules)} 分，与设定的 {total} 分不一致"})
    ranks = [RANK[m["kind"]] for m in modules]
    if ranks != sorted(ranks):
        out.append({"id": "order", "level": "warn", "text": "模块顺序不符合“先行为后态度、先无提示后有提示、先概念后价格”"})
    for m in modules:
        if m.get("tight"):
            out.append({"id": f"tight_{m['id']}", "level": "warn", "text": f"“{m['name']}”只有 {m['minutes']} 分，放不下 {MIN_QUESTIONS} 道题，需要加时间或去掉别的"})
        elif len(m["materials"]) >= 2 and m["minutes"] <= 13:
            out.append({"id": f"crowded_{m['id']}", "level": "warn", "text": f"“{m['name']}”放了 {len(m['materials'])} 个物料，{m['minutes']} 分可能偏紧"})
    mats = {x for m in modules for x in m["materials"]}
    if len(mats) >= 2 and not context.get("rotate"):
        out.append({"id": "rotate", "level": "warn", "text": "有多个物料，建议轮换展示顺序（A/B 两组）"})
    if not any(f["level"] == "warn" for f in out):
        out.append({"id": "pass", "level": "ok", "text": "每个问题都有对应的模块，时长一致，顺序合规"})
    return out


def _quote(text: str, needle_words: tuple[str, ...]) -> str:
    """The sentence of `text` that contains one of the words (what a suggestion rests on), or ''."""
    for s in re.split(r"[。！？!?\n；;]", text or ""):
        if any(w in s for w in needle_words) and s.strip():
            return s.strip()[:80]
    return ""


def suggest(modules: list[dict], context: dict, slots: dict[str, dict], dismissed: set[str]) -> list[dict]:
    """At most three suggestions, each built from a finding and naming what it rests on: {id, title, reason, basis, op}. A suggestion with no basis is dropped."""
    bg = " ".join([context.get("background", "")] + list(context.get("goals", [])))
    audience = str(slots["audience"]["value"] or "")
    cands: list[dict] = []
    price = next((m for m in modules if m["kind"] == "price"), None)
    if price and price["minutes"] >= 10 and not any(m["kind"] == "price" and m["id"] != "price" for m in modules):
        cands.append({"id": "split_price", "score": 0.9, "title": "把“价格”拆成两个模块", "reason": "先问自发的价格认知，再看价格后的反应，符合“先无提示、后有提示”",
                      "basis": _quote(bg, ("价格", "定价", "多少钱")) or f"研究类型：{RESEARCH_TYPES.get(slots['research_type']['value'], '')}", "op": {"type": "split", "module": "price"}})
    q = _quote(bg, ("比较", "对比", "竞品", "品牌", "其他"))
    if q and not any("比较" in m["name"] or "竞品" in m["name"] for m in modules):
        cands.append({"id": "add_compare", "score": 0.85, "title": "增加“竞品对比”模块（5 分）", "reason": "背景里提到比较，但现在没有一个模块专门问她们拿什么来比较", "basis": q,
                      "op": {"type": "add", "module": {"id": "compare", "name": "竞品对比", "purpose": "了解她们会拿什么来比较", "content": "现在用的品牌、为什么换或不换", "kind": "compare", "serves": [1], "depth": "deep", "minutes": 5, "materials": []}}})
    if len({x for m in modules for x in m["materials"]}) >= 2 and not context.get("rotate"):
        cands.append({"id": "rotate", "score": 0.6, "title": "物料做 A/B 轮换", "reason": f"受访者看到的第一件东西会影响后面的回答，{'“' + audience[:24] + '”' if audience else '这类人群'}尤其如此",
                      "basis": audience or "访谈对象", "op": {"type": "rotate"}})
    for f in findings(modules, context, slots):
        if f["id"].startswith("tight_"):
            mid = f["id"][6:]
            cands.append({"id": f"widen_{mid}", "score": 0.8, "title": f"给“{next(m['name'] for m in modules if m['id'] == mid)}”加 3 分钟", "reason": "这个模块时间放不下最少的题数，从时间最宽的模块匀 3 分钟",
                          "basis": "总时长不变", "op": {"type": "shift", "to": mid, "minutes": 3}})
    cands = [c for c in cands if c["basis"] and c["id"] not in dismissed]
    cands.sort(key=lambda c: -c["score"])
    return [{k: v for k, v in c.items() if k != "score"} for c in cands[:3]]


def apply_suggestions(modules: list[dict], context: dict, ops: list[dict], total: int) -> tuple[list[dict], dict]:
    """The modules after the chosen suggestions are applied, and the new context flags. The total stays the same: new time is taken from the other modules."""
    mods = [dict(m) for m in modules]
    ctx = dict(context)
    for op in ops:
        t = op["type"]
        if t == "split":
            i = next(n for n, m in enumerate(mods) if m["id"] == op["module"])
            m = mods[i]
            a = {**m, "id": m["id"] + "_a", "name": m["name"] + "认知", "purpose": "先看没有提示时的认知", "content": "心里的合理预期", "depth": "shallow", "materials": [], "minutes": m["minutes"] // 2, "questions": max(2, m["questions"] // 2)}
            b = {**m, "id": m["id"] + "_b", "name": m["name"] + "反应", "purpose": "看到具体内容后的反应", "content": "超过多少放弃、看法", "minutes": m["minutes"] - a["minutes"], "questions": max(2, m["questions"] - a["questions"])}
            mods[i:i + 1] = [a, b]
        elif t == "add":
            new = dict(op["module"], questions=2, status="todo")
            pos = next((n for n, m in enumerate(mods) if RANK[m["kind"]] > RANK[new["kind"]]), len(mods))
            mods.insert(pos, new)
            _take(mods, new["id"], new["minutes"])
        elif t == "rotate":
            ctx["rotate"] = True
        elif t == "shift":
            _take(mods, op["to"], op["minutes"], add=True)
    for m in mods:
        m["questions"] = max(1 if m["kind"] in ("warm", "close") else MIN_QUESTIONS, int(m["minutes"] / MIN_PER_QUESTION))
        m["tight"] = m["kind"] not in ("warm", "close") and m["minutes"] < MIN_QUESTIONS * MIN_PER_QUESTION
    _round_to_total(mods, total)
    return mods, ctx


def _take(mods: list[dict], target: str, minutes: int, add: bool = False) -> None:
    """Give `target` `minutes` more (or, for a freshly added module, keep what it has) by taking one minute at a time from the module with the most to spare."""
    need = minutes if add else minutes
    if add:
        next(m for m in mods if m["id"] == target)["minutes"] += minutes
    while need > 0:
        donors = [m for m in mods if m["id"] != target and m["kind"] not in ("warm", "close") and m["minutes"] - 1 >= MIN_QUESTIONS * MIN_PER_QUESTION] or [m for m in mods if m["id"] != target and m["minutes"] > 1]
        if not donors:
            break
        max(donors, key=lambda m: m["minutes"])["minutes"] -= 1
        need -= 1


def totals(modules: list[dict]) -> dict:
    return {"minutes": sum(m["minutes"] for m in modules), "questions": sum(m["questions"] for m in modules)}


def count_for_minutes(minutes: float) -> int:
    return max(MIN_QUESTIONS, math.floor(minutes / MIN_PER_QUESTION))
