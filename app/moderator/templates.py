"""Fixed sentences the AI moderator says without asking the model (Quality Charter: texts live outside the logic).

These carry the red lines that must never depend on a model: the AI disclosure at the start (H3), the honest answer to
"are you a real person?" (H3), stopping politely (H5), the crisis safety statement (H5) and the safe fallback sentence.
Phase A covers en and zh only (task doc 2); ms / ta come after en / zh reach the gates.
Crisis numbers are Singapore's: 995 (emergency) and Samaritans of Singapore 1767 (24 hours).
"""
from pydantic import BaseModel, ConfigDict

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "zh"})


class LanguageTemplates(BaseModel):
    """All fixed sentences for one language. `{topic}`, `{item}` and `{question}` are filled in by the engine."""

    model_config = ConfigDict(frozen=True)

    intro: str
    ai_answer: str          # prefix when the participant asks whether they talk to a person
    stop: str
    crisis: str
    wrapup: str
    closing: str
    followup: str           # generic safe follow-up
    alt_followups: tuple[str, ...]  # other safe follow-ups, so the fallback never repeats itself
    missed_item: str
    default_topic: str
    ai_disclosure_markers: tuple[str, ...]   # all must appear in the first AI turn (H3)
    crisis_markers: tuple[str, ...]          # one must appear in a crisis statement (H5)


TEMPLATES: dict[str, LanguageTemplates] = {
    "zh": LanguageTemplates(
        intro=("您好，我是本次访谈的 AI 主持人，不是真人。今天想听听您在{topic}方面的真实经历和想法，没有对错之分。"
               "访谈会录音录像并转成文字，只用于研究。您可以随时要求停止。我们现在可以开始吗？"),
        ai_answer="我是 AI 主持人，不是真人，研究团队的同事能看到我们的对话。",
        stop="好的，我们就到这里。非常感谢您抽时间参与，祝您一切顺利。",
        crisis=("听到这些我很担心您，我们先暂停访谈。如果您现在有危险，请立即拨打 995。"
                "您也可以随时拨打新加坡援人协会 24 小时热线 1767。我已经通知研究团队的同事。"),
        wrapup="我们差不多要结束了。还有什么重要的事，是我们今天没聊到的吗？",
        closing="非常感谢您今天的分享，访谈到这里就结束了。",
        followup="关于这一点，您能再多说一些吗？",
        alt_followups=("您能再展开讲讲当时的情况吗？", "还有什么想补充的吗？", "这一点对您来说意味着什么？"),
        missed_item="我还想了解一下：{item}。您能说说吗？",
        default_topic="日常购物和使用",
        ai_disclosure_markers=("AI 主持人", "不是真人"),
        crisis_markers=("995", "1767"),
    ),
    "en": LanguageTemplates(
        intro=("Hello, I'm the AI interviewer for today's session, not a real person. I'd like to hear your own experiences "
               "and views on {topic}; there are no right or wrong answers. The session is recorded and transcribed for research only. "
               "You can ask to stop at any time. Shall we begin?"),
        ai_answer="I'm an AI interviewer, not a real person, and our research team can see this conversation.",
        stop="Of course, we'll stop here. Thank you very much for your time, and all the best.",
        crisis=("I'm concerned to hear this, so let's pause the interview. If you are in danger right now, please call 995. "
                "You can also call the Samaritans of Singapore any time on 1767. I have let our research team know."),
        wrapup="We're nearly at the end. Is there anything important we haven't talked about today?",
        closing="Thank you so much for sharing today; that's the end of our interview.",
        followup="Could you tell me a bit more about that?",
        alt_followups=("Could you say a little more about what happened?", "Is there anything you would like to add?", "What does that mean to you?"),
        missed_item="I'd also like to hear about this: {item}. Could you tell me about it?",
        default_topic="your everyday shopping",
        ai_disclosure_markers=("AI interviewer", "not a real person"),
        crisis_markers=("995", "1767"),
    ),
}


def templates_for(language: str) -> LanguageTemplates:
    """Fixed sentences for an interview language.

    Raises:
        ValueError: the language is not supported by the AI moderator yet (ms / ta: phase A is en / zh only).
    """
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"AI moderator does not support language {language!r} yet (en / zh only in phase A)")
    return TEMPLATES[language]
