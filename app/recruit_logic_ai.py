"""小星星 as a questionnaire programmer (修正方案第 4 轮, 用户 2026-11-20: 像 SurveyMonkey / Qualtrics 一样理解自然语言并编程).

The researcher says what they want in plain words ("第 8 题按第 7 题选的城市出区", "把性别年龄挪到最前", "品牌题选项随机，以上都没有固定在最后",
"没买过电动车的跳过第 5 题"); the model returns the changed questionnaire using the form's own logic (jumps, screen-out, display logic on questions and
options, shuffling, piping). Nothing it writes is trusted: the result is expanded (option groups), checked by recruit_form.normalize, and on a failure the
model is told why in plain words and gets one more try. A question that is not an edit (asking for advice, "你是什么模型") is answered, never "edited".
The words of the conversation are never logged.
"""
import json
import logging
import re

from . import assistant_identity, recruit_form
from .moderator import providers
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 8000
LANG_NAME = {"zh": "简体中文", "en": "English", "ms": "Bahasa Melayu", "ta": "தமிழ்"}

SYSTEM = """你是“小星星助手”，一位资深的问卷编程专家（相当于 SurveyMonkey / Qualtrics 的逻辑编程能力）。研究员用自然语言告诉你怎么改甄别问卷，你理解意图后直接改好问卷的结构和逻辑。

只输出一个 JSON 对象：
{"reply": "用简体中文、一两句话说明你改了什么（题号用 Q1、Q2…按改后的顺序）；如果用户只是提问或要建议，就直接回答", "questions": [...] 或 null}
- 只是提问、要建议、闲聊时，questions 为 null，不要改问卷。
- 要改时，questions 是改后的【整份】问卷，按改后的顺序排列：
  - 没改动的题只写 {"id": "原 id", "keep": true}；
  - 改动的题写完整对象（保留原 id）；新题不写 id 或写新的 id；删掉的题不要出现。
- 题目和选项一律用问卷语言（{lang}）书写。不要问姓名、电话、邮箱、证件号。

题目对象：
{"id": "q3", "type": "single" | "multi" | "text" | "number" | "scale", "text": "题干", "required": true,
 "options": [{"id": "o1", "text": "选项", "goto": "", "tag": "", "excl": [], "fixed": false, "show_if": null}],
 "rules": [{"op": "lt|lte|gt|gte|eq", "value": 18, "goto": "end_out"}],   // 只有数字题有
 "randomize": false,      // true = 每位候选人看到的选项顺序随机；fixed=true 的选项（如“以上都没有”“其他”）留在原位
 "show_if": null}         // 题目显示条件，不满足就跳过这题

逻辑：
- 选项 goto："" 下一题；"end_out" 选了就甄别掉；"end_ok" 选了直接合格；或者后面某题的 id（跳题，只能往后跳）。
- tag：选了就打上的标签（配额用），只给“通过”的选项。
- excl：多选题里不能同时选的选项 id（如“以上都没有”和其他所有选项互斥）。
- show_if（题目或选项都可以有）：{"mode": "all" | "any", "conds": [条件…]}，条件只能看【前面】的题：
  {"q": "q7", "op": "any", "opts": ["o2"]}   前面那题选了其中任一个
  {"q": "q7", "op": "none", "opts": ["o2"]}  一个都没选
  {"q": "q7", "op": "all", "opts": ["o1","o2"]} 都选了（多选）
  {"q": "q2", "op": "gte", "value": 18}       数字题比较（lt/lte/gt/gte/eq）
  {"q": "q5", "op": "answered"}               答了这题
- 引用前面的答案：题干里写 {{q7}}，候选人看到的是他自己在 q7 选的内容。
- 选项要跟着前面的答案变（比如按选的城市出该城市的区）时，用 option_groups 代替 options，系统会自动展开：
  "option_groups": {"q": "q7", "groups": {"q7 的选项 id": ["朝阳区", "海淀区", ...], "另一个选项 id": [...]}, "always": ["以上都不是"]}
  每个城市写出它【全部】的区（按真实行政区划，不要编造），always 里的选项所有人都能看到。
- 选项可以很多（最多 200 个），但普通题保持精简。

改题原则：理解研究员的真实意图，一次改到位；需要时可以同时改多道题、加题、删题、调顺序；不要动与要求无关的题。"""


class EditError(ValueError):
    """The edit could not be made; the message is plain Chinese for the page."""


def _expand(item: dict, before: dict[str, dict]) -> dict:
    """One question as the model wrote it, made into a real question (kept ones copied, option groups turned into options with display logic)."""
    if item.get("keep") and str(item.get("id", "")) in before:
        return before[str(item["id"])]
    q = {k: v for k, v in item.items() if k not in ("keep", "option_groups")}
    og = item.get("option_groups")
    if isinstance(og, dict) and og.get("q") and isinstance(og.get("groups"), dict):
        opts, n = [], 0
        for oid, names in og["groups"].items():
            for name in (names if isinstance(names, list) else []):
                n += 1
                opts.append({"id": f"g{n}", "text": str(name)[:120], "show_if": {"mode": "all", "conds": [{"q": str(og["q"]), "op": "any", "opts": [str(oid)]}]}})
        for name in og.get("always") or []:
            n += 1
            opts.append({"id": f"g{n}", "text": str(name)[:120], "fixed": True})
        q["options"] = opts
    return q


def _plain(err: Exception) -> str:
    """A checking error in words a researcher reads (no English program messages)."""
    text = str(err)
    if "at most" in text and "options" in text:
        return "选项太多了（一道题最多 200 个）"
    if "at least 2 options" in text:
        return "选择题至少要有 2 个选项"
    if "contact details" in text:
        return "甄别题不能问联系方式或证件"
    m = re.search(r"问卷不合格：([\w.]+)", text)
    if m:
        return "有一道题的格式不对（" + re.sub(r"questions\.(\d+)", lambda x: f"第 {int(x.group(1)) + 1} 题", m.group(1)) + "）"
    return text.replace("问卷不合格：", "")[:120]


def _lang_ok(questions: list[dict], changed: set[str], lang: str) -> bool:
    if lang != "zh":
        return True
    for q in questions:
        if q.get("id") in changed:
            words = str(q.get("text", "")) + "".join(str(o.get("text", "")) for o in q.get("options") or [])
            if words.strip() and not re.search(r"[一-鿿]", words):
                return False
    return True


def edit(chat: ModeratorChatProvider | None, questions: list[dict], instruction: str, focus_qid: str, language: str, goals: list[str], need: str) -> dict:
    """Change the questionnaire as the researcher asked.

    Returns:
        {"changed": bool, "questions": [...] (when changed), "reply": "中文说明"}.

    Raises:
        EditError: no model, or still not a valid questionnaire after ATTEMPTS tries (the reason in plain Chinese).
        ChatFatalError: the model service refused (no balance, bad key).
    """
    if assistant_identity.asks_identity(instruction):
        return {"changed": False, "reply": assistant_identity.REPLY}
    if chat is None:
        raise EditError("离线演示模式：小星星助手没有接入，改题不可用")
    before = {str(q.get("id", "")): q for q in questions}
    pos = {qid: i + 1 for i, qid in enumerate(before)}
    user = {"instruction": instruction, "current_question": f"Q{pos[focus_qid]}（id {focus_qid}）" if focus_qid in pos else "",
            "questionnaire": [{"Q": f"Q{pos[str(q.get('id', ''))]}", **q} for q in questions], "goals": goals, "need": need}
    messages = [{"role": "system", "content": SYSTEM.replace("{lang}", LANG_NAME.get(language, "简体中文"))},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)[:60000]}]
    last = ""
    for n in range(ATTEMPTS):
        token = providers._text_extras.set({**providers._text_extras.get(), "max_tokens": MAX_OUTPUT_TOKENS})
        try:
            raw = chat.complete(messages, temperature=0.2)
        except ChatFatalError:
            raise
        except ChatError as e:
            log.warning("questionnaire edit: model call failed (%s)", type(e).__name__)
            last = "小星星这次没有回复成功"
            continue
        finally:
            providers._text_extras.reset(token)
        reply = " ".join(str(raw.get("reply") or "").split())[:400]
        items = raw.get("questions")
        if items is None:
            return {"changed": False, "reply": reply or "好的。"}
        try:
            if not isinstance(items, list) or not items:
                raise recruit_form.FormError("没有给出题目")
            out = [_expand(it, before) for it in items if isinstance(it, dict)]
            form = recruit_form.normalize({"source": "ai", "questions": out, "goals": goals, "need": need})
            new = [q.model_dump() for q in form.questions]
            changed = {q["id"] for q in new if before.get(q["id"]) != q}
            if not _lang_ok(new, changed, language):
                raise recruit_form.FormError("题目没有用问卷的语言写")
        except (recruit_form.FormError, ValueError, TypeError) as e:
            last = _plain(e)
            log.info("questionnaire edit: attempt %d not valid", n + 1)
            messages += [{"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)[:20000]},
                         {"role": "user", "content": f"这样改不合格：{last}。请按规则修正后重新输出整份 JSON。"}]
            continue
        if [q["id"] for q in new] == list(before) and not changed:
            return {"changed": False, "reply": reply or "这次没有需要改的地方"}
        return {"changed": True, "questions": new, "reply": reply or "已改好"}
    raise EditError(f"没有改成：{last or '小星星没给出可用的问卷'}，请换个说法再试一次")
