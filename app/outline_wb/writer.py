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


def write_module(chat: ModeratorChatProvider | None, module: dict, context: dict, lang: str = "zh", fmt: str = "") -> list[dict]:
    """The questions of one module: [{text, followups, must_cover, minutes, backup}]. Questions the quality gate blocks are dropped (and the gap is filled from the template)."""
    from . import quality
    if chat is None:
        qs = _mock_questions(module, context)
    else:
        raw = _ask(chat, MODULE_PROMPT, {"MODULE": {k: module[k] for k in ("name", "purpose", "content", "minutes", "questions", "depth")}, "CONTEXT": {"background": context.get("background", ""), "goals": context.get("goals", []), "audience": context.get("audience", "")}, "LANGUAGE": lang, "FORMAT": fmt})
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


def chat_ops(chat: ModeratorChatProvider | None, outline: list[dict], selected: str, text: str) -> tuple[str, list[dict]]:
    """The side dialog: (a short reply, up to three proposed operations). The caller checks them (items must exist, types must fit) and stores them as pending revisions."""
    sel = next((i for i in outline if i["uid"] == selected), None)
    if chat is None:
        if sel and re.search(r"口语|改|换|中立", text):
            return "我给了一个改法，点“接受”才会写进大纲。", [{"op": "rewrite", "item_uid": sel["uid"], "after": {"lang": "zh", "text": re.sub(r"[？?]$", "", sel["text"]) + "，可以说说吗？"}, "reason": "（演示模式）示例改写"}]
        return "（演示模式）我没有改动。请选中一道题，再告诉我怎么改。", []
    raw = _ask(chat, CHAT_PROMPT, {"OUTLINE": outline, "SELECTED": selected, "USER": text})
    ops = [o for o in raw.get("ops", []) if isinstance(o, dict)][:3] if isinstance(raw.get("ops"), list) else []
    return " ".join(str(raw.get("reply", "")).split())[:300] or "好的。", ops


MODEL_FATAL = ChatFatalError
