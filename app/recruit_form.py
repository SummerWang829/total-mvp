"""The recruiting questionnaire (甄别问卷) of a project: who gets screened in or out before an interview.

One saved form per project, two sources:
- `ai`: written here. The researcher builds it question by question (single choice, multiple choice, text, number) with logic on the answers: carry on, jump to a
  later question, END AND SCREEN OUT (不符合), or END AS QUALIFIED. The AI can draft it from the research idea (and the project's research library). It is published as
  a link / QR code; candidates fill it in on a public page, and the people who qualify land in the project's participant list.
- `html`: a questionnaire made in another system (an https link), only kept and opened in a new window for testing.

The logic runs on the server only (`next_step`): the candidate's page asks "what is next, given these answers" and never sees which answer screens out. The same function
serves the researcher's test on the right-hand side of the editor, so what they test is what candidates get.
"""
import logging
import math
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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
MSG_VENDOR_MISSING = "AI 用的是云端模型，但没有填写服务商名称（.env 里的 MODERATOR_VENDOR），无法告诉你内容发给了谁，所以先不能用"
CONTACT_WORDS = re.compile(r"(phone number|telephone|e-?mail address|home address|passport|NRIC|身份证|手机号|电话号码|电子邮箱|家庭住址|护照)", re.I)

SYSTEM = """You write the screening questionnaire that decides who may be invited to a market research study. Reply with ONE JSON object and nothing else:
{"questions": [{"type": "single" | "multi" | "text" | "number", "text": "<the question>", "options": [{"text": "<answer>", "end": false}]}]}
Rules: write everything in the language {language}. Use 6 to 10 questions. Put the eligibility questions first (age 18 or over, country or city, how often they use or buy the
product category), then the questions that tell the target groups apart. "single" = choose one, "multi" = choose several, both need 2 to 8 options; "text" and "number" have no options.
Mark "end": true on an option that makes the person NOT eligible for this study (for example under 18, or never uses the category); every other option has "end": false.
Questions are neutral, one at a time, never leading, never naming or praising a brand, and never ask for a name, phone number, e-mail, address or any ID."""


class FormError(ValueError):
    """The questionnaire cannot be used or could not be written; the message is plain Chinese for the researcher."""


class AnswerError(ValueError):
    """An answer does not fit its question (a wrong option, a number that is not a number...)."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default="", max_length=20)
    text: str = Field(min_length=1, max_length=120)
    goto: str = Field(default="", max_length=20)  # "" = next question | a later question's id | END_OUT | END_OK
    tag: str = Field(default="", max_length=30)  # a label the participant gets when they choose this (matches the quota tables)

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
    html_url: str = Field(default="", max_length=MAX_URL)
    questions: list[Question] = Field(default_factory=list, max_length=MAX_QUESTIONS)

    @field_validator("html_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return clean_url(v) if v.strip() else ""


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


def public_question(q: Question, index: int, total: int) -> dict:
    """What a candidate may see of a question: no jumps, no tags, nothing about which answer screens out."""
    return {"id": q.id, "type": q.type, "text": q.text, "required": q.required, "options": [{"id": o.id, "text": o.text} for o in q.options], "index": index, "total": total}


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
            raise FormError("AI 暂时无法生成问卷，请稍后再试") from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:  # pydantic's ValidationError is a ValueError
            problem = f"not a valid questionnaire ({type(e).__name__})"
            log.warning("screener draft attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise FormError("AI 写的问卷不合格（题型、选项或内容不符合要求），请换个说法再试一次")
