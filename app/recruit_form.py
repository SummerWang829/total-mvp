"""The recruiting questionnaire (甄别问卷) of a project: who gets screened in or out before an interview.

Two sources, one saved form per project:
- `ai`: the researcher writes the research idea (or takes the research background from the plan) and the AI writes the questions: stem, options and which
  option ends the questionnaire (the person is screened out). Editing the idea writes the questions again.
- `html`: a questionnaire made in another system (an https link). We only keep the link and open it in a new window for testing; it is never embedded here.

What the model writes is checked like anything else that comes from outside: a fixed shape, limits on sizes, no question that asks for contact details.
"""
import logging
import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MAX_QUESTIONS = 15
MAX_OPTIONS = 8
MAX_ATTEMPTS = 2
MAX_URL = 500
MIN_IDEA_CHARS = 8
MSG_VENDOR_MISSING = "AI 用的是云端模型，但没有填写服务商名称（.env 里的 MODERATOR_VENDOR），无法告诉你内容发给了谁，所以先不能用"
CONTACT_WORDS = re.compile(r"(phone number|telephone|e-?mail address|home address|passport|NRIC|身份证|手机号|电话号码|电子邮箱|家庭住址|护照)", re.I)

SYSTEM = """You write the screening questionnaire that decides who may be invited to a market research study. Reply with ONE JSON object and nothing else:
{"questions": [{"type": "single" | "multi" | "open" | "number", "text": "<the question>", "options": [{"text": "<answer>", "end": false}]}]}
Rules: write everything in the language {language}. Use 6 to 10 questions. Put the eligibility questions first (age 18 or over, country or city, how often they use or buy the
product category), then the questions that tell the target groups apart. "single" = choose one, "multi" = choose several, both need 2 to 8 options; "open" and "number" have no options.
Mark "end": true on an option that makes the person NOT eligible for this study (for example under 18, or never uses the category); every other option has "end": false.
Questions are neutral, one at a time, never leading, never naming or praising a brand, and never ask for a name, phone number, e-mail, address or any ID."""


class FormError(ValueError):
    """The questionnaire cannot be used or could not be written; the message is plain Chinese for the researcher."""


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=120)
    end: bool = False  # choosing this ends the questionnaire: the person is screened out


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default="", max_length=20)
    type: Literal["single", "multi", "open", "number"]
    text: str = Field(min_length=1, max_length=300)
    options: list[Option] = Field(default_factory=list, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def _shape(self):
        if self.type in ("single", "multi") and len(self.options) < 2:
            raise ValueError("a choice question needs at least 2 options")
        if self.type in ("open", "number"):
            self.options = []
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


def normalize(raw: dict) -> FormBody:
    """Parse and number a form (question ids q1, q2...). Raises FormError with a plain message."""
    try:
        body = FormBody.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        raise FormError(f"问卷不合格：{'.'.join(str(x) for x in first['loc'])}（{first['msg']}）") from e
    for i, q in enumerate(body.questions, 1):
        q.id = f"q{i}"
    if body.source == "html" and not body.html_url:
        raise FormError("请填写问卷链接")
    return body


def mock_questions(idea: str, language: str) -> list[dict]:
    """The offline questionnaire used in development and tests (fixed; it says nothing about the model's quality)."""
    if language == "zh":
        return [{"type": "single", "text": "您的年龄是？", "options": [{"text": "18 岁以下", "end": True}, {"text": "18-34 岁", "end": False}, {"text": "35 岁及以上", "end": False}]},
                {"type": "single", "text": "您现在居住在哪里？", "options": [{"text": "新加坡", "end": False}, {"text": "其他地区", "end": True}]},
                {"type": "single", "text": "您多久使用一次这类产品？", "options": [{"text": "每天", "end": False}, {"text": "每周", "end": False}, {"text": "几乎不用", "end": True}]},
                {"type": "multi", "text": "您通常在哪里购买？", "options": [{"text": "线上商城", "end": False}, {"text": "实体店", "end": False}, {"text": "朋友推荐", "end": False}]},
                {"type": "open", "text": "最近一次购买时，您最看重什么？"}]
    return [{"type": "single", "text": "What is your age?", "options": [{"text": "Under 18", "end": True}, {"text": "18-34", "end": False}, {"text": "35 or over", "end": False}]},
            {"type": "single", "text": "Where do you live now?", "options": [{"text": "Singapore", "end": False}, {"text": "Somewhere else", "end": True}]},
            {"type": "single", "text": "How often do you use this kind of product?", "options": [{"text": "Every day", "end": False}, {"text": "Every week", "end": False}, {"text": "Hardly ever", "end": True}]},
            {"type": "multi", "text": "Where do you usually buy it?", "options": [{"text": "Online shops", "end": False}, {"text": "Physical stores", "end": False}, {"text": "Friends' recommendations", "end": False}]},
            {"type": "open", "text": "What mattered most the last time you bought it?"}]


def generate(chat: ModeratorChatProvider | None, idea: str, language: str) -> list[Question]:
    """The AI writes the questions from the research idea. `chat` None means the offline mock questionnaire.

    Raises:
        FormError: the idea is too short, or no usable questionnaire after MAX_ATTEMPTS tries / the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...): never hidden behind a made-up questionnaire.
    """
    if len(idea.strip()) < MIN_IDEA_CHARS:
        raise FormError("先写一下研究想法（至少一两句话），再生成问卷")
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            if chat is None:
                reply = {"questions": mock_questions(idea, language)}
            else:
                user = f"Research idea (internal): {idea.strip()}\nQuestionnaire language: {language}"
                if problem:
                    user += f"\nYour previous questionnaire was rejected: {problem}\nWrite a new one that avoids this."
                reply = chat.complete([{"role": "system", "content": SYSTEM.replace("{language}", language)}, {"role": "user", "content": user}], temperature=0.3)
            raw = reply.get("questions")
            if not isinstance(raw, list) or not raw:
                raise ValueError("no questions")
            return [Question.model_validate(q) for q in raw[:MAX_QUESTIONS]]
        except ChatFatalError:
            raise
        except ChatError as e:
            raise FormError("AI 暂时无法生成问卷，请稍后再试") from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:  # pydantic's ValidationError is a ValueError
            problem = f"not a valid questionnaire ({type(e).__name__})"
            log.warning("screener draft attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise FormError("AI 写的问卷不合格（题型、选项或内容不符合要求），请换个说法再试一次")
