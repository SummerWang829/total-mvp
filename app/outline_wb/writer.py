"""The model's part of "生成大纲": wording for suggestions, the questions of one module, and the edits the side dialog proposes. The model only proposes; every answer is
checked by code (a suggestion must rest on a real line of the project's text; questions go through app/outline_wb/quality.py; edits become pending revisions).
With no model configured (`chat` is None) every function answers from fixed templates, so the whole page can be walked through offline.
"""
import contextvars
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from ..config import settings
from ..moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider, text_extras
from . import flow

log = logging.getLogger(__name__)
TIMEOUT_S = 25
SYSTEM_RULES = ("You are 小星星助手, helping a market-research team design an interview outline. Reply with ONE JSON object and nothing else. Write Simplified Chinese unless told the interview language. "
                "Never put names, phone numbers or other personal data in a question. If asked which model or tool you are, say only that you are 小星星助手.")
WORD_PROMPT = SYSTEM_RULES + ' Task: improve the wording of each suggestion. Input: SUGGESTIONS (id, title, reason, basis) and CONTEXT (background, goals, audience). Output {"items":[{"id","title","reason","basis"}]}. "basis" MUST be copied word for word from CONTEXT; if you cannot, leave the suggestion out.'
MODULE_PROMPT = SYSTEM_RULES + (' Task: write the questions of ONE outline module. Input: MODULE (name, purpose, content, minutes, questions, depth), CONTEXT, LANGUAGE, FORMAT. Rules: open, neutral, one idea per question, complete sentences, '
                                '8-30 Chinese characters; ask about real recent behaviour before attitudes; never lead (no 是不是/难道/对吧); never ask for personal data; unprompted before prompted. '
                                'Output {"questions":[{"text","followups":["..."],"must_cover":["..."],"minutes":number,"backup":false}]} with about MODULE.questions questions (plus at most 1 backup).')
CHAT_PROMPT = SYSTEM_RULES + (' Task: the researcher asks to change the outline. Input: OUTLINE (items with uid, label, kind, text), SELECTED (uid or empty), USER. Output {"reply":"one short sentence","ops":[{"op":"rewrite|add_followup|insert|rename|minutes",'
                              '"item_uid":"..." ,"after":{"text":"..."|"label":"..."|"minutes":n},"reason":"..."}]} with at most 3 ops, only for items that exist. Answer questions with ops=[].')


def _ask(chat: ModeratorChatProvider, system: str, payload: dict) -> dict:
    """One model call with a hard timeout; the answer must be a JSON object.

    Raises:
        ChatFatalError: the service refused (key, balance ...).
        ChatError: no usable answer in time.
    """
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with text_extras():
            ctx = contextvars.copy_context()
            future = pool.submit(ctx.run, chat.complete, msgs, temperature=settings.ai_temperature)
        raw = future.result(timeout=TIMEOUT_S)
    except FutureTimeout as e:
        raise ChatError("MODEL_TIMEOUT") from e
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not isinstance(raw, dict):
        raise ChatError("NOT_AN_OBJECT")
    return raw


def word_suggestions(chat: ModeratorChatProvider | None, suggestions: list[dict], context: dict) -> list[dict]:
    """The model's wording for the suggestions, kept only where its "basis" really is a piece of the project's text; otherwise the rule's own wording stays."""
    if chat is None or not suggestions:
        return suggestions
    allowed = " ".join([context.get("background", ""), " ".join(context.get("goals", [])), str(context.get("audience", ""))])
    try:
        raw = _ask(chat, WORD_PROMPT, {"SUGGESTIONS": [{k: s[k] for k in ("id", "title", "reason", "basis")} for s in suggestions], "CONTEXT": {"background": context.get("background", ""), "goals": context.get("goals", []), "audience": context.get("audience", "")}})
    except (ChatError, ValueError) as e:
        log.warning("outline_wb: wording failed (%s); rule wording kept", type(e).__name__)
        return suggestions
    by_id = {i.get("id"): i for i in raw.get("items", []) if isinstance(i, dict)}
    out = []
    for s in suggestions:
        n = by_id.get(s["id"])
        if n and str(n.get("basis", "")).strip() and str(n["basis"]).strip() in allowed and str(n.get("title", "")).strip():
            s = {**s, "title": str(n["title"]).strip()[:60], "reason": str(n.get("reason", s["reason"])).strip()[:160], "basis": str(n["basis"]).strip()[:80]}
        out.append(s)
    return out


def _mock_questions(module: dict, context: dict) -> list[dict]:
    kind, name = module["kind"], module["name"]
    texts = {"warm": ["先聊聊你平时是怎么买日常用品的？", "最近一次逛街或网购，你买了什么？"],
             "concept": ["看完之后，你第一反应是什么？", "哪一点最先吸引你，或者让你有顾虑？", "如果满分是十分，你有多想试用？"],
             "price": ["你心里觉得它大概值多少钱？", "价格是多少时你会犹豫？", "看到这个价格，你会怎么选？"],
             "brand": ["看完之后，你对这个品牌有什么看法？", "有什么地方让你卡住或不确定吗？"],
             "close": ["还有什么想补充的吗？"],
             "compare": ["你现在常用哪些品牌？", "你为什么换过或没换过？"]}.get(kind) or [f"最近一次和“{name}”有关的经历是什么时候？", "当时是怎么决定的？", "有什么让你犹豫的地方吗？"]
    n = max(1, module["questions"])
    extras = ["能举一个最近的具体例子吗？", "当时身边有人影响你的选择吗？", "有没有哪次你本来想买却没买？", "你会怎么向朋友描述这件事？", "后来有什么变化吗？"]
    texts = (texts + [x for x in extras if x not in texts])[:n]
    fu = "" if module["depth"] == "none" else "为什么这样想？"
    return [{"text": t, "followups": [fu] if fu else [], "must_cover": [], "minutes": round(module["minutes"] / n, 1), "backup": False} for t in texts]


def write_module(chat: ModeratorChatProvider | None, module: dict, context: dict, lang: str = "zh", fmt: str = "", keep: list[str] | None = None) -> list[dict]:
    """The questions of one module: [{text, followups, must_cover, minutes, backup}]. Questions the quality gate blocks are dropped (and the gap is filled from the template)."""
    from . import quality
    if chat is None:
        qs = _mock_questions(module, context)
    else:
        raw = _ask(chat, MODULE_PROMPT, {"MODULE": {k: module[k] for k in ("name", "purpose", "content", "minutes", "questions", "depth")}, "CONTEXT": {"background": context.get("background", ""), "goals": context.get("goals", []), "audience": context.get("audience", "")}, "LANGUAGE": lang, "FORMAT": fmt,
                                           **({"ALREADY_ACCEPTED": [k for k in keep if k][:6], "NOTE": "These questions stay in the module as they are; write the others around them and do not repeat them."} if keep else {})})
        qs = []
        for q in raw.get("questions", []) if isinstance(raw.get("questions"), list) else []:
            if isinstance(q, dict) and str(q.get("text", "")).strip():
                qs.append({"text": " ".join(str(q["text"]).split())[:200], "followups": [str(f)[:120] for f in q.get("followups", []) if str(f).strip()][:3] if isinstance(q.get("followups"), list) else [],
                           "must_cover": [str(m)[:120] for m in q.get("must_cover", []) if str(m).strip()][:4] if isinstance(q.get("must_cover"), list) else [], "minutes": float(q.get("minutes") or 0), "backup": bool(q.get("backup"))})
    ok = [q for q in qs if not quality.check_question(q["text"], lang, fmt)["blocked"]]
    if len(ok) < len([q for q in qs if not q["backup"]]) or not ok:
        ok += [q for q in _mock_questions(module, context) if q["text"] not in {x["text"] for x in ok}][: max(0, module["questions"] - len(ok))]
    if module["depth"] == "none":
        for q in ok:
            q["followups"] = []
    return ok[: module["questions"] + 1]


def write_one(chat: ModeratorChatProvider | None, module: dict, context: dict, focus: str, lang: str = "zh", fmt: str = "") -> str:
    """One new question for a focus question (关注问题的“新增想法”), worded for `module`. Without a model, or when its wording is unusable or blocked, a plain neutral template is used."""
    from . import quality
    plain = f"你怎么看“{focus.strip().rstrip('？?。')[:24]}”这件事？"
    if chat is None:
        return plain
    try:
        raw = _ask(chat, MODULE_PROMPT, {"MODULE": {**{k: module[k] for k in ("name", "purpose", "content", "minutes", "depth")}, "questions": 1}, "FOCUS": focus[:200],
                                         "CONTEXT": {"background": context.get("background", ""), "goals": context.get("goals", []), "audience": context.get("audience", "")}, "LANGUAGE": lang, "FORMAT": fmt})
    except ChatError as e:
        if isinstance(e, ChatFatalError):
            raise
        log.warning("outline_wb: one-question draft failed (%s); template used", type(e).__name__)
        return plain
    for q in raw.get("questions", []) if isinstance(raw.get("questions"), list) else []:
        t = " ".join(str(q.get("text", "")).split())[:200] if isinstance(q, dict) else ""
        if t and not quality.check_question(t, lang, fmt)["blocked"]:
            return t
    return plain


def chat_ops(chat: ModeratorChatProvider | None, outline: list[dict], selected: str, text: str, material: str = "") -> tuple[str, list[dict]]:
    """The side dialog: (a short reply, up to three proposed operations). The caller checks them (items must exist, types must fit) and stores them as pending revisions."""
    sel = next((i for i in outline if i["uid"] == selected), None)
    if chat is None:
        if material:
            return "（演示模式）我读了你给的资料 [资料 1]，没有改动大纲。", []
        if sel and re.search(r"口语|改|换|中立", text):
            return "我给了一个改法，点“接受”才会写进大纲。", [{"op": "rewrite", "item_uid": sel["uid"], "after": {"lang": "zh", "text": re.sub(r"[？?]$", "", sel["text"]) + "，可以说说吗？"}, "reason": "（演示模式）示例改写"}]
        return "（演示模式）我没有改动。请选中一道题，再告诉我怎么改。", []
    raw = _ask(chat, CHAT_PROMPT, {"OUTLINE": outline, "SELECTED": selected, "USER": text, **({"MATERIALS": material} if material else {})})
    ops = [o for o in raw.get("ops", []) if isinstance(o, dict)][:3] if isinstance(raw.get("ops"), list) else []
    return " ".join(str(raw.get("reply", "")).split())[:300] or "好的。", ops


MODEL_FATAL = ChatFatalError


FIX_PROMPT = SYSTEM_RULES + (' Task: rewrite ONE interview question so that the problems in NOTES are fixed (plain everyday words, one idea, neutral, 8-30 Chinese characters, a complete sentence). '
                             'Input: QUESTION, NOTES, LANGUAGE. Output {"text":"the new question"}.')


def fix_question(chat: ModeratorChatProvider | None, text: str, notes: list[str], lang: str = "zh", fmt: str = "") -> str:
    """A rewrite of one question that answers the notes from a pilot. With no model it appends a plain invitation, only so the page can be walked through offline. A rewrite the quality gate blocks is
    replaced by the same offline wording."""
    from . import quality
    fallback = re.sub(r"[？?]$", "", text) + ("，可以说说吗？" if lang == "zh" else ", could you tell me more?")
    if chat is None:
        return fallback
    try:
        new = " ".join(str(_ask(chat, FIX_PROMPT, {"QUESTION": text, "NOTES": notes, "LANGUAGE": lang}).get("text", "")).split())[:200]
    except (ChatError, ValueError) as e:
        log.warning("outline_wb: fix_question failed (%s)", type(e).__name__)
        return fallback
    return new if new and not quality.check_question(new, lang, fmt)["blocked"] else fallback


CODE_PROMPT = SYSTEM_RULES + (' Task: code ONE interview answer for the focus question. Input: QUESTION, CODES (the allowed codes; empty = make a short label of at most 6 Chinese characters), ANSWER. '
                              'Output {"code":"...","confidence":0.0-1.0}. If CODES is not empty the code MUST be one of them, or "其他" when none fits. Confidence is how sure you are the answer says that.')
MAX_CODE_CHARS = 20


def code_answer(chat: ModeratorChatProvider | None, question: str, codes: list[str], answer: str) -> tuple[str, float]:
    """(code, confidence 0..1) for one answer. With no model the offline rules answer (a code whose words appear in the answer, else the first few characters), so the board can be walked through
    offline. A model answer outside the allowed codes becomes "其他" with low confidence.

    Raises:
        ChatFatalError: the service refused.
        ChatError: no usable answer in time.
    """
    if chat is None:
        hit = next((c for c in codes if c and c in answer), "")
        if codes:
            return (hit, 0.9) if hit else ("其他", 0.5)
        clause = re.split(r"[，。！？,.!?\s]+", answer.strip())[0] or answer.strip()
        return clause[:6], 0.7
    raw = _ask(chat, CODE_PROMPT, {"QUESTION": question, "CODES": codes, "ANSWER": answer[:600]})
    code = " ".join(str(raw.get("code", "")).split())[:MAX_CODE_CHARS] or "其他"
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    if codes and code not in codes:
        code, conf = "其他", min(conf, 0.5)
    return code, conf


NAME_PROMPT = SYSTEM_RULES + (' Task: give each interview-outline module a short whole name (4-8 Chinese characters, a noun phrase such as 售后痛点 or 品牌认知对比, never a cut-off sentence). '
                              'Input: MODULES [{id, purpose}]. Output {"names":{"<id>":"name"}}.')
VERB_RE = re.compile(r"^(?:对比|比较|识别|观察|了解|探索|评估|测试|弄清|找出|看看|分析)")


def _rule_name(text: str) -> str:
    """A module name without a model: the part after the last 的 (the thing the question is about), else the start; never a cut-off half sentence."""
    t = re.sub(r"^回答[:：]\s*", "", str(text or "")).strip().rstrip("。.？?")
    t = VERB_RE.sub("", t)
    tail = t.split("的")[-1] if "的" in t else t
    if len(tail) < 4 and "的" in t:
        tail = t.split("的")[-2][-4:] + tail
    tail = re.split(r"[，,、；;]", tail)[0]
    if len(tail) > 8:   # never cut a word in half: keep the first of "A 与 B"
        for sep in "与和及":
            if sep in tail and len(tail.split(sep)[0]) >= 2:
                tail = tail.split(sep)[0]
                break
    return tail[:8] or "访谈模块"


def name_modules(chat: ModeratorChatProvider | None, modules: list[dict], context: dict) -> None:
    """修正方案第 2 轮 C13: the modules made from the questions to answer get short whole names (in place). Warm-up, closing, concept and price keep theirs."""
    todo = [m for m in modules if m["kind"] in ("behaviour", "attitude")]
    if not todo:
        return
    names: dict = {}
    if chat is not None:
        try:
            raw = _ask(chat, NAME_PROMPT, {"MODULES": [{"id": m["id"], "purpose": m["purpose"]} for m in todo]})
            names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
        except ChatFatalError:
            raise
        except ChatError as e:
            log.warning("outline_wb: module names failed (%s); rule names used", type(e).__name__)
    for m in todo:
        n = " ".join(str(names.get(m["id"], "")).split())
        m["name"] = n[:10] if 2 <= len(n) <= 10 else _rule_name(m["purpose"])


SLOT_PROMPT = SYSTEM_RULES + (' Task: the researcher asks to change what is known about the study. Input: SLOTS {name: {value, text, options}}, USER. '
                              'Change only these slots: research_type, key_questions (a list of up to 4), audience (text), materials (a list of option values, ["none"] for no material), price, '
                              'minutes (the interview length, an integer 5-240), format (one of audio, text, text_voice, video, video_screen), languages (a list of zh, en, ms, ta). '
                              'Use only option values that exist. Output {"reply":"one short Chinese sentence saying what was changed","changes":{"<slot>":value}}; changes may be empty when nothing fits.')
MAT_WORDS = {"概念": "concept", "图片": "image", "包装": "image", "实物": "image", "视频": "video", "短片": "video", "网站": "website", "网页": "website"}


def slot_ops(chat: ModeratorChatProvider | None, slots: dict, text: str) -> tuple[str, dict]:
    """修正方案第 2 轮 C3: (reply, {slot: new value}) for the dialog on 目标 / 人群与执行. The caller checks every value like the “改” button does.
    修正方案第 4 轮: the length, format and languages too (“访谈时长改为30分钟”)."""
    m = re.search(r"(?:时长|多长|访谈).{0,8}?(\d{1,3})\s*分钟|(\d{1,3})\s*分钟", text or "")
    if m and re.search(r"时长|分钟", text) and not re.search(r"物料|素材|价格", text):   # a plain length change needs no model
        n = int(m.group(1) or m.group(2))
        return f"已把访谈时长改为 {n} 分钟。", {"minutes": n}
    if chat is None:
        if "物料" in text or "素材" in text:
            if re.search(r"不看|不要|不用|没有", text):
                return "已把“物料”改成“不看素材”。", {"materials": ["none"]}
            picked = [v for w, v in MAT_WORDS.items() if w in text] or ["concept"]
            return "已把“物料”改成看素材：" + "、".join(dict.fromkeys(picked)) + "。要换成别的素材吗？", {"materials": list(dict.fromkeys(picked))}
        return "（演示模式）这一页只改“已经知道的”，比如“物料改成概念卡”。", {}
    raw = _ask(chat, SLOT_PROMPT, {"SLOTS": slots, "USER": text})
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    allowed = {"research_type", "key_questions", "audience", "materials", "price", "minutes", "format", "languages"}
    return " ".join(str(raw.get("reply", "")).split())[:200] or "好的。", {k: v for k, v in changes.items() if k in allowed}
