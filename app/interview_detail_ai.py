"""Model-made timeline slices for one interview recording.

The recording is ONE continuous video that starts at second 0 and ends when the interview ends;
there are no pre-cut clips. The caption track (transcript_segments, with ASR start_ms/end_ms) is
what gets sliced: the model only decides which caption lines belong to which outline question.
Every timestamp is copied from the caption track - the model never invents a time.

This keeps one time base (relative playback milliseconds), so the progress bar, the captions and
the answer bubbles all point at the same moment.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import ai_prompts
from .moderator.providers import ChatError, ChatFatalError

log = logging.getLogger(__name__)

MAX_CAPTIONS = 220
MAX_QUESTIONS = 60
TIMEOUT_S = 25

SEGMENT_SYSTEM = """你是访谈切片助手。你会收到一组带编号的访谈大纲题目，以及一组带编号的逐句字幕（每句含说话人和原文）。
请为每一句字幕指定它属于哪一道题：输出一个数组，元素是题目序号（从 1 开始），0 表示寒暄、设备调试或不属于任何题。
主持人的提问句归入它所问的那道题，追问也归入同一道题，受访者的回答归入对应题。
只依据文字内容判断；不要改字幕文字、不要输出时间码、不要推测没说过的内容。
只输出一个 JSON 对象：{"assign":[0,1,1,2,...]}，数组长度必须和字幕条数完全一致。"""


def chat_or_none():
    """The model that groups captions. None = offline demo; a cloud vendor must be named."""
    from .config import settings
    from .moderator.compliance import moderator_runs_locally
    from .moderator.providers import get_moderator_chat
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise RuntimeError("云端模型还没有在 .env 里写明服务商，没有发送任何内容")
    return get_moderator_chat()


def assign(chat, captions: list[dict], questions: list[str]) -> list[int]:
    """One question number per caption line (0 = not part of a question)."""
    rows = captions[:MAX_CAPTIONS]
    qs = "\n".join(f"{i + 1}. {str(q)[:180]}" for i, q in enumerate(questions[:MAX_QUESTIONS]))
    lines = "\n".join(
        f'{n + 1}. [{"主持人" if s.get("speaker") == "moderator" else "受访者"}] {str(s.get("text") or "")[:180]}'
        for n, s in enumerate(rows))
    prompt = ai_prompts.get("interview.qa_group", SEGMENT_SYSTEM)
    reply = chat.complete([{"role": "system", "content": prompt},
                           {"role": "user", "content": f"题目：\n{qs}\n\n字幕：\n{lines}"}], temperature=0)
    got = reply.get("assign") if isinstance(reply, dict) else None
    if not isinstance(got, list) or len(got) != len(rows):
        raise ChatError("timeline assign length mismatch")
    out = []
    nq = min(len(questions), MAX_QUESTIONS)
    for x in got:
        try:
            k = int(x)
        except (TypeError, ValueError):
            k = 0
        out.append(k if 1 <= k <= nq else 0)
    return out + [0] * (len(captions) - len(out))


def build(captions: list[dict], assign_list: list[int], questions: list[str]) -> list[dict]:
    """Group captions into question slices. start/end are always copied from the caption track."""
    groups: dict[int, dict] = {}
    for s, q in zip(captions, assign_list):
        if not q:
            continue
        g = groups.get(q)
        if g is None:
            g = {"q": q, "question": questions[q - 1] if q <= len(questions) else "",
                 "ask": "", "answer": [], "text_zh": [], "segment_ids": [],
                 "start_ms": int(s.get("start_ms") or 0), "end_ms": int(s.get("end_ms") or 0)}
            groups[q] = g
        g["start_ms"] = min(g["start_ms"], int(s.get("start_ms") or 0))
        g["end_ms"] = max(g["end_ms"], int(s.get("end_ms") or 0))
        g["segment_ids"].append(s.get("id"))
        text = str(s.get("text") or "")
        if s.get("speaker") == "moderator" and not g["ask"]:
            g["ask"] = text
        else:
            g["answer"].append(text)
        if s.get("text_zh"):
            g["text_zh"].append(str(s["text_zh"]))
    out = []
    for q in sorted(groups):
        g = groups[q]
        out.append({"q": q, "question": g["question"], "ask": g["ask"],
                    "answer": " ".join(g["answer"]).strip(), "text_zh": " ".join(g["text_zh"]).strip(),
                    "segment_ids": g["segment_ids"], "at_ms": g["start_ms"], "end_ms": g["end_ms"]})
    return out


def generate(chat, captions: list[dict], questions: list[str]) -> list[dict]:
    """Model slices with a timeout; any failure is logged and the caller falls back to rule slices."""
    if chat is None or not captions or not questions:
        return []
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=1)
        marks = pool.submit(assign, chat, captions, questions).result(timeout=TIMEOUT_S)
        return build(captions, marks, questions)
    except ChatFatalError as e:
        log.error("timeline slices: model refused: %s", e)
        raise
    except Exception as e:  # noqa: BLE001 - a bad model answer must not break the page
        log.warning("timeline slices failed: %s: %s", type(e).__name__, e)
        return []
    finally:
        if pool is not None:
            pool.shutdown(wait=False)


def rule_slices(captions: list[dict]) -> list[dict]:
    """Offline fallback: each moderator turn starts a new slice (no model involved)."""
    out: list[dict] = []
    cur = None
    for s in captions:
        text = str(s.get("text") or "")
        if s.get("speaker") == "moderator":
            if cur and cur["answer"]:
                out.append(_done(cur))
            cur = {"q": len(out) + 1, "question": "", "ask": text, "answer": [], "text_zh": [],
                   "segment_ids": [], "start_ms": int(s.get("start_ms") or 0), "end_ms": int(s.get("end_ms") or 0)}
        else:
            if cur is None:
                cur = {"q": 1, "question": "", "ask": "（开场 / 自由发言）", "answer": [], "text_zh": [],
                       "segment_ids": [], "start_ms": int(s.get("start_ms") or 0), "end_ms": int(s.get("end_ms") or 0)}
            cur["answer"].append(text)
            if s.get("text_zh"):
                cur["text_zh"].append(str(s["text_zh"]))
        if cur:
            cur["start_ms"] = min(cur["start_ms"], int(s.get("start_ms") or 0))
            cur["end_ms"] = max(cur["end_ms"], int(s.get("end_ms") or 0))
            cur["segment_ids"].append(s.get("id"))
    if cur and cur["answer"]:
        out.append(_done(cur))
    return out


def _done(g: dict) -> dict:
    return {"q": g["q"], "question": g["question"], "ask": g["ask"],
            "answer": " ".join(g["answer"]).strip(), "text_zh": " ".join(g["text_zh"]).strip(),
            "segment_ids": g["segment_ids"], "at_ms": g["start_ms"], "end_ms": g["end_ms"]}


# ---------- the other model features on the respondent detail page ----------
TRANSLATE_SYSTEM = """你是市场研究转写翻译员。把输入的中文字幕逐条译成 {language}，只输出一个 JSON 对象：
{"items":[{"id":1,"text":"译文"}, ...]}，条数与顺序必须和输入一致。
保留口语、停顿词和说话人语气，不润色、不总结、不补充原文没有的信息；
数字、金额、品牌名、地名按原文照写；不要输出任何解释。"""
SUMMARY_SYSTEM = """你是定性研究分析师。下面是同一位受访者按访谈大纲逐题回答的记录。
请只依据这些回答，归纳 3 到 6 条要点，覆盖：背景与画像、行为与场景、动机与原因、态度与评价、未来意向。
每条一句话，不引用原话、不推测、不写没有出现过的条件。
只输出一个 JSON 对象：{"bullets":["...","..."]}。"""
TAGS_SYSTEM = """你是市场研究编码员。依据这位受访者整场访谈的所有回答，给出 3 到 6 个画像标签，
每个不超过 8 个汉字，用「特征 + 人群」式短语（如「高频访港」「品质消费倾向」）。
同一含义在不同受访者身上必须用同一个标签；不要输出姓名、编号或任何个人标识。
只输出一个 JSON 对象：{"tags":["...","..."]}。"""
QUALITY_SYSTEM = """你是深访质量监控员。对每条「主持人提问 → 受访者回答」按六维打 1-5 分：
relevance 相关性、density 信息密度与独特性、depth 深度、clarity 清晰度、consistency 一致性、completeness 完整性。
1=答非所问/泛泛/空答，3=基本满足，5=有一手细节可直接用于洞察。
主持人的引导性追问不计入受访者分数；空答、拒答、寒暄把该条标为 invalid:true 且不进均值。
只输出一个 JSON 对象：{"scores":[{"q":1,"relevance":5,"density":4,"depth":4,"clarity":5,"consistency":5,"completeness":4,"invalid":false}]}。"""
FIX_SYSTEM = """你是语音转写校对员。只修正明显的识别错误：专名、品牌、数字金额、标点、断句。
严禁改写原意、增删内容、补充说话人没说的信息；不确定的地方保持原文。
只输出一个 JSON 对象：{"text":"修正后的句子","changed":true,"reason":"改了什么"}。"""
EXPORT_SYSTEM = """你要把单场访谈导出为研究文档。用 2 到 3 句话写一段客观摘要，只依据给定回答；
同时列出检测到的个人信息类型（姓名/电话/邮箱/证件/编号），不要输出这些值本身。
只输出一个 JSON 对象：{"abstract":"...","pii":["..."]}。"""

DIMS = (("relevance", "相关性"), ("density", "信息密度与独特性"), ("depth", "深度"),
        ("clarity", "清晰度"), ("consistency", "一致性"), ("completeness", "完整性"))


def _call(chat, key: str, system: str, user_text: str, parser, temperature: float = 0.2):
    """One model call with the console-editable prompt, a timeout and a soft failure (None)."""
    if chat is None or not user_text.strip():
        return None
    prompt = ai_prompts.get(key, system)
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=1)
        reply = pool.submit(chat.complete,
                            [{"role": "system", "content": prompt}, {"role": "user", "content": user_text[:30000]}],
                            temperature=temperature).result(timeout=TIMEOUT_S)
        return parser(reply)
    except ChatFatalError as e:
        log.error("detail %s: model refused: %s", key, e)
        raise
    except Exception as e:  # noqa: BLE001 - one failed feature must not break the page
        log.warning("detail %s failed: %s: %s", key, type(e).__name__, e)
        return None
    finally:
        if pool is not None:
            pool.shutdown(wait=False)


def translate(chat, captions: list[dict], language: str = "English") -> dict:
    """{caption id: translation}. Times and ids are never touched."""
    rows = [c for c in captions[:MAX_CAPTIONS] if (c.get("text") or "").strip()]
    if not rows:
        return {}
    numbered = "\n".join(f'{i + 1}. {str(c["text"])[:400]}' for i, c in enumerate(rows))
    prompt = ai_prompts.get("interview.translate", TRANSLATE_SYSTEM).replace("{language}", language)

    def parse(reply):
        items = reply.get("items") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            return None
        out = {}
        for x in items:
            if isinstance(x, dict) and str(x.get("text") or "").strip():
                try:
                    out[rows[int(x.get("id")) - 1]["id"]] = str(x["text"]).strip()
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
        return out or None

    # the language slot must survive the console prompt, so resolve it before the call
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=1)
        reply = pool.submit(chat.complete,
                            [{"role": "system", "content": prompt}, {"role": "user", "content": numbered}],
                            temperature=0.1).result(timeout=TIMEOUT_S)
        return parse(reply) or {}
    except ChatFatalError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("detail translate failed: %s: %s", type(e).__name__, e)
        return {}
    finally:
        if pool is not None:
            pool.shutdown(wait=False)


def summarize(chat, answers: list[str]) -> list[str]:
    def parse(r):
        x = r.get("bullets") if isinstance(r, dict) else None
        return [str(t).strip()[:300] for t in x if str(t).strip()][:6] if isinstance(x, list) else None
    return _call(chat, "interview.summary", SUMMARY_SYSTEM, "\n".join(f"{i+1}. {a[:600]}" for i, a in enumerate(answers)), parse) or []


def profile_tags(chat, answers: list[str]) -> list[str]:
    def parse(r):
        x = r.get("tags") if isinstance(r, dict) else None
        return [str(t).strip()[:16] for t in x if str(t).strip()][:6] if isinstance(x, list) else None
    return _call(chat, "interview.profile_tags", TAGS_SYSTEM, "\n".join(answers[:80]), parse) or []


def quality(chat, pairs: list[dict]) -> dict | None:
    """Six-dimension mean. Fewer than 3 scoreable answers = no score (avoids 'answered less, scored higher')."""
    usable = [p for p in pairs if (p.get("answer") or "").strip()]
    if len(usable) < 3:
        return None

    def parse(r):
        items = r.get("scores") if isinstance(r, dict) else None
        if not isinstance(items, list):
            return None
        vals = []
        for x in items:
            if not isinstance(x, dict) or x.get("invalid"):
                continue
            try:
                vals.append({k: max(1.0, min(5.0, float(x.get(k, 3)))) for k, _ in DIMS})
            except (TypeError, ValueError):
                continue
        if not vals:
            return None
        dims = {label: round(sum(v[k] for v in vals) / len(vals), 1) for k, label in DIMS}
        return {"mean": round(sum(dims.values()) / len(dims), 2), "dims": dims, "scored": len(vals)}

    text = "\n".join(f'{i+1}. 问：{p.get("ask","")[:200]}\n答：{p.get("answer","")[:600]}' for i, p in enumerate(usable[:40]))
    return _call(chat, "interview.quality", QUALITY_SYSTEM, text, parse, temperature=0)


def fix_text(chat, text: str) -> dict | None:
    def parse(r):
        if not isinstance(r, dict) or not str(r.get("text") or "").strip():
            return None
        return {"text": str(r["text"]).strip(), "changed": bool(r.get("changed")), "reason": str(r.get("reason") or "")[:120]}
    return _call(chat, "interview.transcript_fix", FIX_SYSTEM, text[:1500], parse, temperature=0)


def export_brief(chat, answers: list[str]) -> dict:
    def parse(r):
        if not isinstance(r, dict):
            return None
        return {"abstract": str(r.get("abstract") or "")[:600], "pii": [str(x)[:40] for x in (r.get("pii") or [])][:8]}
    return _call(chat, "interview.export", EXPORT_SYSTEM, "\n".join(answers[:80]), parse) or {"abstract": "", "pii": []}
