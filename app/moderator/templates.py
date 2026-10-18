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
    intro_written: str      # the opening of a written study done alone: says the conversation is saved as text (no audio or video), and that the participant may leave and return
    ai_answer: str          # prefix when the participant asks whether they talk to a person
    model_answer: str       # prefix when they ask what model / company the AI is: nothing is revealed, nothing is denied
    contact_reminder: str   # prefix when a message held a phone number, e-mail or account name (it has been hidden)
    conduct_reminder: str   # prefix when a message held abusive or hateful words (they have been hidden)
    conduct_end: str        # the closing statement after repeated abuse: no question
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
        intro_written=("您好，我是本次访谈的 AI 主持人，不是真人。今天想听听您在{topic}方面的真实经历和想法，没有对错之分。"
                       "我们会把这段文字对话记录下来，只用于研究。您可以随时要求停止，也可以先离开、之后再回来继续。我们现在可以开始吗？"),
        ai_answer="我是 AI 主持人，不是真人，研究团队的同事能看到我们的对话。",
        model_answer="至于我背后用的是什么技术，我这边不方便透露。",
        contact_reminder="为了保护您的隐私，请不要在这里留下联系方式，我已经帮您隐藏了。需要联系的话，研究团队会另外安排。",
        conduct_reminder="我们还是用互相尊重的方式交流吧，刚才那些用词我已经隐藏了。",
        conduct_end="因为对话里多次出现不合适的用语，我们先在这里结束本次访谈。谢谢您的时间。",
        stop="好的，我们就到这里。非常感谢您抽时间参与，祝您一切顺利。",
        crisis=("听到这些我很担心您，我们先暂停访谈。如果您现在有危险，请立即拨打 995。"
                "您也可以随时拨打新加坡援人协会 24 小时热线 1767。我已经通知研究团队的同事。"),
        wrapup="我们差不多要结束了。还有什么重要的事，是我们今天没聊到的吗？",
        closing="非常感谢您今天的分享，访谈到这里就结束了。",
        followup="关于这一点，您能再多说一些吗？",
        alt_followups=("您能再展开讲讲当时的情况吗？", "还有什么想补充的吗？", "这一点对您来说意味着什么？", "能举一个具体的例子吗？",
                       "当时的情况具体是怎样的？", "这件事后来是怎么发展的？", "您当时是怎么考虑的？", "还有别的细节可以说说吗？",
                       "对您来说，这里面最重要的是什么？", "您能说得再具体一些吗？", "这和您平时的做法一样吗？", "当时您身边的人是什么反应？"),
        missed_item="我还想了解一下：{item}。您能说说吗？",
        default_topic="日常购物和使用",
        ai_disclosure_markers=("AI 主持人", "不是真人"),
        crisis_markers=("995", "1767"),
    ),
    "en": LanguageTemplates(
        intro=("Hello, I'm the AI interviewer for today's session, not a real person. I'd like to hear your own experiences "
               "and views on {topic}; there are no right or wrong answers. The session is recorded and transcribed for research only. "
               "You can ask to stop at any time. Shall we begin?"),
        intro_written=("Hello, I'm the AI interviewer for this study, not a real person. I'd like to hear your own experiences "
                       "and views on {topic}; there are no right or wrong answers. This written conversation is saved for research only. "
                       "You can ask to stop at any time, or leave and come back later to carry on. Shall we begin?"),
        ai_answer="I'm an AI interviewer, not a real person, and our research team can see this conversation.",
        model_answer="I'm not able to share what technology I run on.",
        contact_reminder="To protect your privacy, please do not leave contact details here; I have hidden them. If we need to reach you, our research team will arrange it separately.",
        conduct_reminder="Let's keep our conversation respectful; I have hidden the words from your last message.",
        conduct_end="Because inappropriate language came up several times, we will end the interview here. Thank you for your time.",
        stop="Of course, we'll stop here. Thank you very much for your time, and all the best.",
        crisis=("I'm concerned to hear this, so let's pause the interview. If you are in danger right now, please call 995. "
                "You can also call the Samaritans of Singapore any time on 1767. I have let our research team know."),
        wrapup="We're nearly at the end. Is there anything important we haven't talked about today?",
        closing="Thank you so much for sharing today; that's the end of our interview.",
        followup="Could you tell me a bit more about that?",
        alt_followups=("Could you say a little more about what happened?", "Is there anything you would like to add?", "What does that mean to you?",
                       "Could you give me a specific example?", "What was that like at the time?", "How did that turn out in the end?",
                       "What were you thinking at that point?", "Is there another detail you can share?", "What matters most to you in that?",
                       "Could you be a little more specific?", "Is that how you usually do it?", "How did the people around you react?"),
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
