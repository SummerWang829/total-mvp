"""Suggestions written from a project's research background (the plan's step 1): interview topics, group-discussion topics, a quota hint and listening keywords.

They are suggestions for the team to read and take over by hand; nothing is applied by itself. The background goes to the AI service (the wizard says so next to the
box), so it must not hold participants' personal details.
"""
import logging

from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider
from .recruit_form import FormError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
MIN_BRIEF_CHARS = 8
MAX_TOPICS = 6
MAX_QUESTIONS = 3
MAX_KEYWORDS = 12
MAX_TEXT = 300

SYSTEM = """You help a market research team set up a study from its research background. Reply with ONE JSON object and nothing else:
{"depth_topics": [{"title": "<topic>", "questions": ["<open question>"]}],
 "group_topics": [{"title": "<topic>", "questions": ["<open question>"]}],
 "quota_hint": "<one or two sentences: which kinds of people to recruit and a sensible split>",
 "listening_keywords": ["<search keyword for public opinion>"]}
Rules: write everything in the language {language}. 3 to 5 topics for depth interviews and 3 to 5 for group discussions, each with 1 to 3 short open questions (one question mark each,
neutral, never leading, never naming a brand). 5 to 10 listening keywords (short phrases people really write online). Never ask for contact details or sensitive personal data."""


def mock_suggest(language: str) -> dict:
    """The offline suggestion used in development and tests (fixed; it says nothing about the model's quality)."""
    if language == "zh":
        return {"depth_topics": [{"title": "使用现状", "questions": ["您最近一次使用这类产品是什么时候？"]}, {"title": "选择原因", "questions": ["当时您是怎么选的？"]}, {"title": "困扰与期待", "questions": ["过程中有哪些不方便的地方？"]}],
                "group_topics": [{"title": "大家的看法", "questions": ["说起这类产品，你们最先想到什么？"]}, {"title": "购买渠道", "questions": ["你们通常在哪里买？为什么？"]}, {"title": "改进想法", "questions": ["如果可以改一件事，你们想改什么？"]}],
                "quota_hint": "按年龄段和使用频率各招一半，保证男女比例接近。", "listening_keywords": ["使用体验", "值不值得买", "推荐", "吐槽"]}
    return {"depth_topics": [{"title": "Current use", "questions": ["When did you last use this kind of product?"]}, {"title": "Why they chose it", "questions": ["How did you choose it?"]}, {"title": "Frustrations", "questions": ["What was hard about it?"]}],
            "group_topics": [{"title": "First thoughts", "questions": ["What comes to mind first about this kind of product?"]}, {"title": "Where they buy", "questions": ["Where do you usually buy it, and why?"]}, {"title": "Ideas", "questions": ["If you could change one thing, what would it be?"]}],
            "quota_hint": "Recruit half in each age band and usage frequency, with a balanced gender split.", "listening_keywords": ["review", "worth it", "recommend", "complaint"]}


def _topics(raw) -> list[dict]:
    out = []
    for t in (raw if isinstance(raw, list) else [])[:MAX_TOPICS]:
        title = str(t["title"]).strip()[:MAX_TEXT]
        qs = [str(q).strip()[:MAX_TEXT] for q in t["questions"][:MAX_QUESTIONS] if str(q).strip()]
        if title and qs:
            out.append({"title": title, "questions": qs})
    if not out:
        raise ValueError("no topics")
    return out


def suggest(chat: ModeratorChatProvider | None, brief: str, language: str, library: str = "") -> dict:
    """Suggestions from the research background. `chat` None means the offline mock.

    Raises:
        FormError: the background is too short, or no usable answer after MAX_ATTEMPTS tries / the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if len(brief.strip()) < MIN_BRIEF_CHARS:
        raise FormError("先在“研究背景”里写几句话，再生成建议")
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            if chat is None:
                reply = mock_suggest(language)
            else:
                user = f"Research background (internal): {brief.strip()}\nLanguage: {language}"
                if library:
                    user += f"\n\nReference material from this project's research library (internal):\n{library}"
                if problem:
                    user += f"\nYour previous answer was rejected: {problem}\nWrite a new one that avoids this."
                reply = chat.complete([{"role": "system", "content": SYSTEM.replace("{language}", language)}, {"role": "user", "content": user}], temperature=0.4)
            return {"depth_topics": _topics(reply.get("depth_topics")), "group_topics": _topics(reply.get("group_topics")), "quota_hint": str(reply.get("quota_hint", "")).strip()[:600],
                    "listening_keywords": [str(k).strip()[:60] for k in (reply.get("listening_keywords") or [])[:MAX_KEYWORDS] if str(k).strip()]}
        except ChatFatalError:
            raise
        except ChatError as e:
            raise FormError("AI 暂时无法生成建议，请稍后再试") from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            problem = f"not a valid answer ({type(e).__name__})"
            log.warning("plan suggestion attempt %d unusable: %s", attempt + 1, type(e).__name__)
    raise FormError("AI 写的建议不合格，请再试一次")
