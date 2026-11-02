"""The recruiting questionnaire (甄别问卷) of a project: who gets screened in or out before an interview.

One saved form per project, two sources:
- `ai`: written here. The researcher builds it question by question (single choice, multiple choice, text, number) with logic on the answers: carry on, jump to a
  later question, END AND SCREEN OUT (不符合), or END AS QUALIFIED. The AI can draft it from the research idea (and the project's research library). It is published as
  a link / QR code; candidates fill it in on a public page, and the people who qualify land in the project's participant list.
- `html`: a questionnaire made in another system (an https link), only kept and opened in a new window for testing.

The logic runs on the server only (`next_step`): the candidate's page asks "what is next, given these answers" and never sees which answer screens out. The same function
serves the researcher's test on the right-hand side of the editor, so what they test is what candidates get.
"""
import json
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import assistant_identity
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MAX_QUESTIONS = 30
MAX_OPTIONS = 12
MAX_RULES = 4
MAX_ATTEMPTS = 2
MAX_URL = 500
MAX_TEXT_ANSWER = 500
MAX_TAGS = 10
MIN_IDEA_CHARS = 8
END_OUT = "end_out"  # screen out: the person does not qualify
END_OK = "end_ok"  # finish: the person qualifies
MSG_VENDOR_MISSING = "小星星助手还没有配置好（.env 里的 MODERATOR_VENDOR），先不能用"
CONTACT_WORDS = re.compile(r"(phone number|telephone|e-?mail address|home address|passport|NRIC|身份证|手机号|电话号码|电子邮箱|家庭住址|护照)", re.I)

SYSTEM = """You write the screening questionnaire that decides who may be invited to a market research study. Reply with ONE JSON object and nothing else:
{"questions": [{"type": "single" | "multi" | "text" | "number", "text": "<the question>", "options": [{"text": "<answer>", "end": false}]}]}
Rules: write everything in the language {language}. Use 6 to 10 questions. Put the eligibility questions first (age 18 or over, country or city, how often they use or buy the
product category), then the questions that tell the target groups apart. "single" = choose one, "multi" = choose several, both need 2 to 8 options; "text" and "number" have no options.
Mark "end": true on an option that makes the person NOT eligible for this study (for example under 18, or never uses the category); every other option has "end": false.
Questions are neutral, one at a time, never leading, never naming or praising a brand, and never ask for a name, phone number, e-mail, address or any ID."""


class FormError(ValueError):
    """The questionnaire cannot be used or could not be written; the message is plain Chinese for the researcher."""


class ChatUnavailable(FormError):
    """The model service gave no usable reply even after the automatic retries; `reason` says why in plain words (never any conversation text)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


FORMAT_ERRORS = ("not JSON", "JSON object", "response shape")  # the service answered, but not with something usable: worth asking once more
FATAL_NOTE_TTL_S = 600  # a refusal (no balance, bad key) is remembered this long, so the page can grey out the buttons that need the model
_fatal_note: dict = {"reason": "", "at": 0.0}


def chat_reason(e: ChatError) -> str:
    """The failure in plain words, from the error the model client raised (its messages hold no conversation text)."""
    s = str(e)
    if "429" in s:
        return "服务繁忙（请求过于频繁）"
    if "HTTP 5" in s:
        return "服务暂时不可用"
    if "Timeout" in s or "timed out" in s.lower():
        return "服务响应超时"
    if "cut off" in s:
        return "回复太长，被截断了（可以让它只改其中一部分）"
    if any(m in s for m in FORMAT_ERRORS):
        return "返回的内容格式不正确"
    return "服务暂时无法连接"


def note_fatal(reason: str) -> None:
    """Remember that the model service refused (bad key, no balance...)."""
    _fatal_note.update(reason=reason, at=time.monotonic())


def clear_fatal() -> None:
    """A reply came through: the refusal is over."""
    _fatal_note.update(reason="", at=0.0)


def recent_fatal() -> str:
    """The reason of a refusal in the last few minutes, or ''."""
    return _fatal_note["reason"] if _fatal_note["reason"] and time.monotonic() - _fatal_note["at"] < FATAL_NOTE_TTL_S else ""


class AnswerError(ValueError):
    """An answer does not fit its question (a wrong option, a number that is not a number...)."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default="", max_length=20)
    text: str = Field(min_length=1, max_length=120)
    goto: str = Field(default="", max_length=20)  # "" = next question | a later question's id | END_OUT | END_OK
    tag: str = Field(default="", max_length=30)  # a label the participant gets when they choose this (matches the quota tables)
    excl: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)  # ids of the options of the same multiple-choice question that cannot be chosen together with this one (always two-way)

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if isinstance(data, dict) and data.get("end") and not data.get("goto"):  # the first version only had "this ends it"
            data = {**{k: v for k, v in data.items() if k != "end"}, "goto": END_OUT}
        elif isinstance(data, dict) and "end" in data:
            data = {k: v for k, v in data.items() if k != "end"}
        return data


class Rule(BaseModel):
    """A rule on a number answer, e.g. "less than 18 -> screen out". The first rule that fits decides."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["lt", "lte", "gt", "gte", "eq"]
    value: float = Field(ge=-1e12, le=1e12)
    goto: str = Field(min_length=1, max_length=20)

    def fits(self, x: float) -> bool:
        return {"lt": x < self.value, "lte": x <= self.value, "gt": x > self.value, "gte": x >= self.value, "eq": x == self.value}[self.op]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default="", max_length=20)
    type: Literal["single", "multi", "text", "number"]
    text: str = Field(min_length=1, max_length=300)
    required: bool = True
    options: list[Option] = Field(default_factory=list, max_length=MAX_OPTIONS)
    rules: list[Rule] = Field(default_factory=list, max_length=MAX_RULES)

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if isinstance(data, dict) and data.get("type") == "open":
            data = {**data, "type": "text"}
        return data

    @model_validator(mode="after")
    def _shape(self):
        if self.type in ("single", "multi") and len(self.options) < 2:
            raise ValueError("a choice question needs at least 2 options")
        if self.type not in ("single", "multi"):
            self.options = []
        if self.type != "number":
            self.rules = []
        if CONTACT_WORDS.search(self.text):
            raise ValueError("a screening question must not ask for contact details or ID")
        return self


class FormBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["ai", "html"] = "ai"
    idea: str = Field(default="", max_length=5000)
    goals: list[str] = Field(default_factory=list, max_length=8)
    need: str = Field(default="", max_length=1500)
    html_url: str = Field(default="", max_length=MAX_URL)
    questions: list[Question] = Field(default_factory=list, max_length=MAX_QUESTIONS)

    @field_validator("html_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return clean_url(v) if v.strip() else ""

    @field_validator("goals")
    @classmethod
    def _goals(cls, v: list[str]) -> list[str]:
        return [g.strip()[:300] for g in v if g.strip()]


def clean_url(url: str) -> str:
    """An https link without credentials (it opens in a new window of the researcher's browser, so only a plain web address is accepted).

    Raises:
        ValueError: not an https address, or it carries a user name / password.
    """
    u = urlparse(url.strip())
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("问卷链接要是 https:// 开头的网址，且不能带用户名密码")
    return u.geturl()


def new_token() -> str:
    """The public link's token: long and random (the link is the only way in)."""
    return secrets.token_urlsafe(18)


def _link_exclusions(q: Question) -> None:
    """Make "cannot be chosen together" two-way and drop anything that makes no sense: only multiple choice has it, only between options of this question, never with itself."""
    ids = [o.id for o in q.options]
    if q.type != "multi":
        for o in q.options:
            o.excl = []
        return
    pairs = {(o.id, x) for o in q.options for x in o.excl if x in ids and x != o.id}
    pairs |= {(b, a) for a, b in pairs}
    for o in q.options:
        o.excl = [x for x in ids if (o.id, x) in pairs]


def _check_logic(questions: list[Question]) -> None:
    """Every jump goes forward to a question that exists (so no loop is possible); ids are unique."""
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise FormError("题目编号重复")
    pos = {q.id: i for i, q in enumerate(questions)}
    for i, q in enumerate(questions):
        for target in [o.goto for o in q.options] + [r.goto for r in q.rules]:
            if target in ("", END_OUT, END_OK):
                continue
            if target not in pos or pos[target] <= i:
                raise FormError(f"第 {i + 1} 题的跳转只能跳到后面的题，或选“结束”")


def normalize(raw: dict) -> FormBody:
    """Parse and check a form: question ids are kept (new ones get q1, q2...), options get ids, jumps are checked. Raises FormError with a plain message."""
    try:
        body = FormBody.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        raise FormError(f"问卷不合格：{'.'.join(str(x) for x in first['loc'])}（{first['msg']}）") from e
    used = {q.id for q in body.questions if q.id}
    n = 0
    for q in body.questions:
        if not q.id:
            while f"q{(n := n + 1)}" in used:
                pass
            q.id = f"q{n}"
            used.add(q.id)
        for j, o in enumerate(q.options, 1):
            o.id = o.id or f"o{j}"
        if len({o.id for o in q.options}) != len(q.options):
            raise FormError("选项编号重复")
        _link_exclusions(q)
    _check_logic(body.questions)
    if body.source == "html" and not body.html_url:
        raise FormError("请填写问卷链接")
    return body


def load(stored: list) -> list[Question]:
    """The questions as stored, in the current shape (forms saved by the first version get their option ids and jumps); [] when what is stored cannot be read."""
    try:
        return normalize({"source": "ai", "questions": stored or []}).questions
    except FormError:
        log.warning("a stored questionnaire could not be read")
        return []


# ---------- running the questionnaire (server side only) ----------
@dataclass
class Step:
    """Where a candidate is: either the next question to show, or the end (qualified / out)."""

    question: dict | None = None
    result: str = ""  # "" while running | "qualified" | "out"
    reason: str = ""  # for the team: which question ended it
    index: int = 0  # position (1-based) of the question to show
    total: int = 0
    tags: list[str] = field(default_factory=list)
    items: list[tuple[str, str]] = field(default_factory=list)  # (question, answer in words) of what was answered, for the participant's screening record
    ended_at: str = ""  # id of the question whose answer ended it
    chosen: dict = field(default_factory=dict)  # question id -> the option id(s) or number answered on the path (for the anonymous tallies)


def public_question(q: Question, index: int, total: int) -> dict:
    """What a candidate may see of a question: no jumps, no tags, nothing about which answer screens out."""
    return {"id": q.id, "type": q.type, "text": q.text, "required": q.required, "options": [{"id": o.id, "text": o.text, "excl": o.excl} for o in q.options], "index": index, "total": total}


def _check_answer(q: Question, raw) -> object:
    """The answer in its clean form (an option id, a list of ids, a string, a float) or None for a skipped optional question.

    Raises:
        AnswerError: wrong shape, an option that does not exist, nothing chosen for a required question.
    """
    empty = raw is None or raw == "" or raw == []
    if empty:
        if q.required:
            raise AnswerError("required")
        return None
    ids = {o.id for o in q.options}
    if q.type == "single":
        if not isinstance(raw, str) or raw not in ids:
            raise AnswerError("option")
        return raw
    if q.type == "multi":
        if not isinstance(raw, list) or not all(isinstance(x, str) and x in ids for x in raw) or len(set(raw)) != len(raw):
            raise AnswerError("option")
        if any(x in raw for o in q.options if o.id in raw for x in o.excl):
            raise AnswerError("exclusive")  # two options that cannot be chosen together
        return raw
    if q.type == "text":
        if not isinstance(raw, str):
            raise AnswerError("text")
        return raw.strip()[:MAX_TEXT_ANSWER]
    try:
        x = float(raw)
    except (TypeError, ValueError) as e:
        raise AnswerError("number") from e
    if not math.isfinite(x) or abs(x) > 1e12:
        raise AnswerError("number")
    return x


def _action(q: Question, ans, pos: dict[str, int]) -> str:
    """What this answer does: "" (next), a later question's id, END_OUT or END_OK. Screening out beats jumping, jumping beats finishing."""
    if ans is None:
        return ""
    if q.type == "number":
        return next((r.goto for r in q.rules if r.fits(ans)), "")
    if q.type == "text":
        return ""
    chosen = [o for o in q.options if o.id == ans] if q.type == "single" else [o for o in q.options if o.id in ans]
    gotos = [o.goto for o in chosen if o.goto]
    if END_OUT in gotos:
        return END_OUT
    jumps = [g for g in gotos if g in pos]
    if jumps:
        return min(jumps, key=lambda g: pos[g])
    return END_OK if END_OK in gotos else ""


def _words(q: Question, ans) -> str:
    if ans is None:
        return ""
    if q.type in ("single", "multi"):
        names = {o.id: o.text for o in q.options}
        return "、".join(names[a] for a in ([ans] if q.type == "single" else ans))
    return f"{ans:g}" if q.type == "number" else str(ans)


def next_step(questions: list[Question], answers: dict) -> Step:
    """Walk the questionnaire with the answers given so far and say what comes next. Answers to questions that are not on the path are ignored.

    Raises:
        AnswerError: an answer on the path does not fit its question.
    """
    pos = {q.id: i for i, q in enumerate(questions)}
    step = Step(total=len(questions))
    i = 0
    while i < len(questions):
        q = questions[i]
        if q.id not in answers:
            step.question, step.index = public_question(q, i + 1, len(questions)), i + 1
            return step
        ans = _check_answer(q, answers[q.id])
        if ans is not None:
            step.chosen[q.id] = ans
            step.items.append((q.text, _words(q, ans)))
            step.tags += [o.tag for o in q.options if o.tag and ((ans == o.id) if q.type == "single" else (q.type == "multi" and o.id in ans))]
        act = _action(q, ans, pos)
        if act in (END_OUT, END_OK):
            step.result, step.ended_at = ("out" if act == END_OUT else "qualified"), q.id
            step.reason = f"第 {i + 1} 题「{q.text[:40]}」" + ("的回答不符合要求" if act == END_OUT else "的回答直接通过")
            break
        i = pos[act] if act else i + 1
    else:
        step.result = "qualified"
    step.tags = list(dict.fromkeys(step.tags))[:MAX_TAGS]
    return step


# ---------- the AI's draft ----------
def mock_questions(idea: str, language: str) -> list[dict]:
    """The offline questionnaire used in development and tests (fixed; it says nothing about the model's quality)."""
    if language == "zh":
        return [{"type": "single", "text": "您的年龄是？", "options": [{"text": "18 岁以下", "end": True}, {"text": "18-34 岁", "end": False}, {"text": "35 岁及以上", "end": False}]},
                {"type": "single", "text": "您现在居住在哪里？", "options": [{"text": "新加坡", "end": False}, {"text": "其他地区", "end": True}]},
                {"type": "single", "text": "您多久使用一次这类产品？", "options": [{"text": "每天", "end": False}, {"text": "每周", "end": False}, {"text": "几乎不用", "end": True}]},
                {"type": "multi", "text": "您通常在哪里购买？", "options": [{"text": "线上商城", "end": False}, {"text": "实体店", "end": False}, {"text": "朋友推荐", "end": False}]},
                {"type": "text", "text": "最近一次购买时，您最看重什么？"}]
    return [{"type": "single", "text": "What is your age?", "options": [{"text": "Under 18", "end": True}, {"text": "18-34", "end": False}, {"text": "35 or over", "end": False}]},
            {"type": "single", "text": "Where do you live now?", "options": [{"text": "Singapore", "end": False}, {"text": "Somewhere else", "end": True}]},
            {"type": "single", "text": "How often do you use this kind of product?", "options": [{"text": "Every day", "end": False}, {"text": "Every week", "end": False}, {"text": "Hardly ever", "end": True}]},
            {"type": "multi", "text": "Where do you usually buy it?", "options": [{"text": "Online shops", "end": False}, {"text": "Physical stores", "end": False}, {"text": "Friends' recommendations", "end": False}]},
            {"type": "text", "text": "What mattered most the last time you bought it?"}]


def generate(chat: ModeratorChatProvider | None, idea: str, language: str, library: str = "", brief: str = "") -> list[Question]:
    """The AI writes the questions from what the team wrote (the idea box), the plan's research background and the project's research library, together.
    `chat` None means the offline mock questionnaire.

    Raises:
        FormError: the idea is too short, or no usable questionnaire after MAX_ATTEMPTS tries / the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...): never hidden behind a made-up questionnaire.
    """
    if len(idea.strip()) + len(brief.strip()) < MIN_IDEA_CHARS:
        raise FormError("先写一下研究想法，或在立项向导里写好研究背景（至少一两句话），再生成问卷")
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            if chat is None:
                reply = {"questions": mock_questions(idea, language)}
            else:
                user = f"Research idea (internal): {idea.strip() or '(none given)'}\nQuestionnaire language: {language}"
                if brief.strip():
                    user += f"\n\nResearch background from the project plan (internal):\n{brief.strip()}"
                if library:
                    user += f"\n\nReference material from this project's research library (internal):\n{library}"
                if problem:
                    user += f"\nYour previous questionnaire was rejected: {problem}\nWrite a new one that avoids this."
                reply = chat.complete([{"role": "system", "content": SYSTEM.replace("{language}", language)}, {"role": "user", "content": user}], temperature=0.3)
            raw = reply.get("questions")
            if not isinstance(raw, list) or not raw:
                raise ValueError("no questions")
            return normalize({"questions": raw[:MAX_QUESTIONS]}).questions
        except ChatFatalError:
            raise
        except ChatError as e:
            raise FormError("小星星助手暂时无法生成问卷，请稍后再试") from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:  # pydantic's ValidationError is a ValueError
            problem = f"not a valid questionnaire ({type(e).__name__})"
            log.warning("screener draft attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise FormError("小星星助手写的问卷不合格（题型、选项或内容不符合要求），请换个说法再试一次")


# ---------- the conversation that edits the questionnaire ----------
MAX_CHAT = 40
MAX_CHAT_TEXT = 2000
HISTORY_TURNS = 12
CHAT_SYSTEM = """You are the assistant inside a recruiting-questionnaire editor for a market research team. You and the researcher talk; you change the questionnaire for them.
Reply with ONE JSON object and nothing else:
{"reply": "<what you did, what you answer or what you ask, 1 to 4 short sentences, in Chinese, plain words>",
 "kind": "answer" | "suggest" | "change",
 "sources": ["brief" | "library" | "stats" | "quota:<quota name>" | "question:<question id>"],
 "questions": null | [<the COMPLETE questionnaire after your change>],
 "goals": null | ["<research goal>", ...],
 "need": null | "<the recruiting need: who, how many, and what screens out, 1 to 3 sentences, Chinese>"}
Use null for anything you did not change. When you change questions, send ALL of them again, keeping the id of every question and option you did not touch.
A question is {"id": "q1", "type": "single" | "multi" | "text" | "number", "text": "...", "required": true, "options": [{"id": "o1", "text": "...", "goto": "", "tag": "", "excl": []}], "rules": [{"op": "lt" | "lte" | "gt" | "gte" | "eq", "value": 18, "goto": "end_out"}]}.
"goto" on an option (or a number rule) says what happens after it: "" = next question, a LATER question's id = jump there, "end_out" = the person is screened out (not eligible), "end_ok" = finish as qualified.
"tag" is a label the person gets when they choose the option. "excl" (multi only) lists the ids of options that cannot be chosen together with this one; set it yourself for options like "none of these", "elsewhere", "prefer not to say" (exclusive with every other option) and for any options that plainly contradict each other. single/multi need 2 to 12 options; text and number have no options; only number questions have rules. At most 30 questions.
Write the questions in the language {language}. Questions are neutral, one at a time, never leading, never naming or praising a brand, and never ask for a name, phone number, e-mail, address or any ID (the page asks contact details itself after the person qualifies).
If asked to draft the questionnaire, use the research background and the reference material: 6 to 10 questions, eligibility first (age 18 or over, country or city, how often they use or buy), then what tells the target groups apart; also fill "goals" (3 to 4) and "need".
If the researcher asks a QUESTION about the questionnaire (why it is built this way, who gets screened out, how strict a question is, what a question is for), ANSWER it from the research background, the library, the quotas and the anonymous counts you are given: kind "answer", questions null, and list in "sources" what you relied on. Never invent numbers; if the counts do not say, say so. When you propose an improvement without making it, use kind "suggest" and change nothing. kind "change" only when you changed the questionnaire, goals or need.
If the request is unclear, ask one short question in "reply" and change nothing."""
CHAT_SYSTEM += " " + assistant_identity.PROMPT_LINE   # 小星星助手: never name the model or vendor (user 2026-11-01)


@dataclass
class ChatResult:
    """What one turn of the conversation produced: the reply, and whatever the AI changed (None = unchanged)."""

    reply: str
    questions: list[Question] | None = None
    goals: list[str] | None = None
    need: str | None = None
    kind: str = "answer"  # answer | suggest | change: derived from what really changed, so the label cannot say "nothing changed" when something did
    sources: list[str] = field(default_factory=list)  # keys from the fixed set (app/recruit_review.py); the page shows their names


def mock_chat(message: str, questions: list[Question], language: str) -> ChatResult:
    """The offline assistant used in development and tests: it can draft the fixed questionnaire and delete the last question; anything else it says it cannot do."""
    if "起草" in message or "draft" in message.lower() or not questions:
        qs = normalize({"questions": mock_questions(message, language)}).questions
        return ChatResult("好的，我根据研究背景起草了一份问卷（离线演示版）。你可以继续告诉我怎么改。", qs,
                          ["了解目标人群的选购习惯和信息来源", "找出购买决策中最看重的因素", "比较不同渠道人群的差异"], "新加坡 · 18 岁以上 · 经常使用这类产品的人；18 岁以下、几乎不用的筛掉。")
    if "删除最后" in message:
        return ChatResult("已删除最后一题。", questions[:-1], kind="change")
    if any(w in message for w in ("为什么", "多少", "吗", "？", "?")):
        return ChatResult("（离线演示版）我先看了作答统计：数字都在“配额与受访者名单”页的看板里。接上小星星助手后，这里会结合研究背景、配额和作答统计回答你的问题。", kind="answer", sources=["stats"])
    return ChatResult("（离线演示版只会“起草问卷”和“删除最后一题”；接上小星星助手后可以按你的话修改。）")


def _history_messages(history: list[dict]) -> list[dict]:
    return [{"role": "assistant" if h.get("role") == "ai" else "user", "content": str(h.get("text", ""))[:MAX_CHAT_TEXT]} for h in history[-HISTORY_TURNS:] if h.get("text")]


def chat_turn(chat: ModeratorChatProvider | None, message: str, history: list[dict], questions: list[Question], goals: list[str], need: str, language: str,
              brief: str = "", library: str = "", stats_text: str = "") -> ChatResult:
    """One turn: the AI reads the conversation so far, the current questionnaire, the research background and the library, and answers with a reply and the changes.
    `chat` None means the offline assistant. A change that is not a valid questionnaire is asked for again once with the problem named, then dropped (the reply says so).

    Raises:
        FormError: the message is empty.
        ChatUnavailable: the model service is unavailable (after the automatic retries); `reason` says why.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if not message.strip():
        raise FormError("先说点什么")
    if chat is None:
        return mock_chat(message, questions, language)
    context = (f"Research background from the project plan:\n{brief.strip() or '(none)'}\n\nReference material from the research library:\n{library or '(none)'}\n\n"
               f"Current questionnaire (JSON):\n{json.dumps([q.model_dump() for q in questions], ensure_ascii=False)}\nCurrent goals: {json.dumps(goals, ensure_ascii=False)}\nCurrent recruiting need: {need or '(none)'}\n\nAnonymous counts and quotas (numbers only, nobody's answers):\n{stats_text or '(none yet)'}")
    msgs = [{"role": "system", "content": CHAT_SYSTEM.replace("{language}", language)}, {"role": "user", "content": context}, *_history_messages(history), {"role": "user", "content": message.strip()[:MAX_CHAT_TEXT]}]
    problem = ""
    format_retried = False
    for attempt in range(MAX_ATTEMPTS):
        try:
            reply = chat.complete(msgs + ([{"role": "user", "content": f"Your previous change was rejected: {problem}. Answer again with a valid questionnaire."}] if problem else []), temperature=0.3)
            text = str(reply.get("reply", "")).strip()[:MAX_CHAT_TEXT] or "好的。"
            new_q = normalize({"questions": reply["questions"][:MAX_QUESTIONS]}).questions if reply.get("questions") else None
            new_goals = [str(g).strip()[:300] for g in reply["goals"][:8] if str(g).strip()] if reply.get("goals") else None
            new_need = str(reply["need"]).strip()[:1500] if reply.get("need") else None
            changed = new_q is not None or new_goals is not None or new_need is not None
            kind = "change" if changed else ("suggest" if reply.get("kind") == "suggest" else "answer")
            srcs = [str(s) for s in reply.get("sources", []) if isinstance(s, str)][:6] if isinstance(reply.get("sources"), list) else []
            clear_fatal()
            return ChatResult(text, new_q, new_goals, new_need, kind, srcs)
        except ChatFatalError as e:
            note_fatal(e.reason)
            raise
        except ChatError as e:
            if not format_retried and any(m in str(e) for m in FORMAT_ERRORS):
                format_retried = True
                log.warning("questionnaire chat: unusable reply (%s), asking once more", e)
                continue
            log.warning("questionnaire chat failed: %s", e)  # the error text names the cause only; nothing the researcher or the model said
            raise ChatUnavailable(chat_reason(e)) from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            problem = f"not a valid questionnaire ({type(e).__name__}: {str(e)[:120]})"
            log.warning("questionnaire chat attempt %d unusable: %s", attempt + 1, type(e).__name__)
    return ChatResult("我这次改出来的问卷不合格（题型、选项或跳转有问题），没有改动。能换个说法再说一次吗？")
