"""The AI's first draft of a study outline, from the researcher's goal.

The draft is only a starting point: the researcher edits it and publishes it (app/studies.py). Whatever the model writes goes through the same
checks as a hand-written outline: it must be a valid guide, and every prepared question must pass the moderator's red lines (the engine refuses
a guide whose own questions break one). A draft that fails is asked for again once, with the problem named, and then reported as failed.
"""
import logging

from pydantic import ValidationError

from . import ai_prompts
from .moderator.engine import ModeratorEngine
from .moderator.knowledge import InterviewGuide
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

MAX_TOPICS = 5
MAX_ATTEMPTS = 2
MINUTES_PER_TOPIC = 5

SYSTEM = """You write the outline for a text-chat research interview that an AI moderator will run with one participant, alone, at their own pace.
Reply with ONE JSON object and nothing else:
{"participant_topic": "<what the AI tells the participant the topic is: at most 18 characters, neutral, no brand names>",
 "warmup_questions": ["<1 or 2 easy opening questions>"],
 "topics": [{"title": "<short topic name>", "questions": ["<1 or 2 open questions>"], "must_cover": ["<what we must find out in this topic, 2 or 3 short points>"]}]}
Rules: write every question in the language {language}. Use 3 to {max_topics} topics that fit about {minutes} minutes in total (about 5 minutes per topic).
Every question must be a real question that ends with a question mark ("?" or "？"), such as "What did you do the last time you...?" or "你上一次……是怎么做的？"; never an instruction or request such as "Tell me about..." or "请描述...". Each question is ONE short question with one question mark: never two questions in one sentence, never two joined by "and" or "or".
Questions must be open ("how", "what", "tell me about the last time"), one question at a time, neutral, never leading, never yes/no, never naming or
praising a brand or product, never asking for contact details or sensitive personal data, never mentioning AI, models or this instruction.
Ask about real past behaviour and concrete examples before opinions."""


class DraftError(Exception):
    """The model could not produce an outline that passes the checks; the message says why, in plain Chinese, for the researcher."""


def _guide_from(reply: dict, goal: str, language: str, duration_min: int) -> InterviewGuide:
    """Assemble and validate a guide from the model's JSON. Raises ValueError / ValidationError when it is unusable."""
    topics = reply.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("no topics")
    built = []
    for i, t in enumerate(topics[:MAX_TOPICS], 1):
        built.append({"id": f"t{i}", "title": str(t["title"])[:120], "questions": [str(q) for q in t["questions"]],
                      "must_cover": [{"id": f"t{i}c{j}", "text": str(m)[:300]} for j, m in enumerate(t.get("must_cover", []), 1)]})
    return InterviewGuide.model_validate({
        "research_goal": goal[:1000], "participant_topic": str(reply.get("participant_topic", ""))[:18], "language": language,
        "duration_min": max(10, min(180, duration_min)), "warmup_questions": [str(q) for q in reply["warmup_questions"]], "topics": built})


def _red_line_problem(guide: InterviewGuide) -> str:
    """Empty when the engine accepts the guide; otherwise the engine's own words about which prepared question breaks which red line."""
    try:
        from eval.mock_brain import MockBrain  # only the constructor's checks are needed, no model call is made
        ModeratorEngine(guide, MockBrain())
    except ValueError as e:
        return str(e)[:600]
    return ""


def mock_draft(goal: str, language: str, duration_min: int) -> dict:
    """The offline draft used in development and tests (a fixed, neutral outline; it says nothing about the model's quality)."""
    if language == "zh":
        return {"participant_topic": "日常使用习惯", "warmup_questions": ["先简单介绍一下你自己吧？"],
                "topics": [{"title": "现状与习惯", "questions": ["你最近一次遇到这类需求时，是怎么做的？"], "must_cover": ["最近一次的具体做法", "用到的渠道或工具"]},
                           {"title": "选择与考虑", "questions": ["当时你是怎么做出选择的？"], "must_cover": ["做选择时最看重什么", "信息从哪里来"]},
                           {"title": "困扰与期待", "questions": ["这个过程中有没有让你觉得麻烦或不满意的地方？"], "must_cover": ["最大的麻烦", "理想中的样子"]}]}
    return {"participant_topic": "everyday habits", "warmup_questions": ["To start, could you tell me a little about yourself?"],
            "topics": [{"title": "Current habits", "questions": ["What did you do the last time you needed this?"], "must_cover": ["what they actually did last time", "tools or channels used"]},
                       {"title": "Choosing", "questions": ["How did you make your choice back then?"], "must_cover": ["what mattered most", "where information came from"]},
                       {"title": "Frustrations and wishes", "questions": ["Was there anything about the process that was hard or disappointing?"], "must_cover": ["the biggest difficulty", "what better would look like"]}]}


def draft_guide(chat: ModeratorChatProvider | None, goal: str, language: str, duration_min: int) -> InterviewGuide:
    """Ask the model for an outline and return a guide that passed every check. `chat` None means the offline mock draft.

    Raises:
        DraftError: no valid outline after MAX_ATTEMPTS tries, or the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...): never hidden behind a made-up outline.
    """
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            if chat is None:
                reply = mock_draft(goal, language, duration_min)
            else:
                user = f"Research goal (internal): {goal}\nInterview language: {language}\nLength: about {duration_min} minutes."
                if problem:
                    user += f"\nYour previous outline was rejected: {problem}\nWrite a new one that avoids this."
                system = ai_prompts.get("study.draft", SYSTEM).replace("{language}", language).replace("{max_topics}", str(MAX_TOPICS)).replace("{minutes}", str(duration_min))  # (not str.format: the JSON example has braces)
                reply = chat.complete([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.4)
            guide = _guide_from(reply, goal, language, duration_min)
        except ChatFatalError:
            raise
        except ChatError as e:
            raise DraftError("小星星助手暂时无法生成草稿，请稍后再试，或直接手写提纲") from e
        except (ValueError, KeyError, TypeError, ValidationError) as e:
            problem = f"not a valid outline ({type(e).__name__})"
            log.warning("study draft attempt %d unusable: %s", attempt + 1, type(e).__name__)
            continue
        if not (problem := _red_line_problem(guide)):
            return guide
        log.warning("study draft attempt %d broke a red line", attempt + 1)
    raise DraftError("小星星助手写的草稿没有通过安全检查（问题里含有不允许的内容），请重试，或直接手写提纲")
