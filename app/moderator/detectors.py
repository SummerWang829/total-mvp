"""Rule-based reading of the participant's words for the cases that must never depend on a model (red lines H3, H5).

A miss here is still caught by the model's own analysis (category CRISIS / WANTS_TO_STOP, asks_if_ai); either one
triggers the safe path. Patterns are deliberately broad: stopping an interview by mistake costs little, missing a crisis
costs a lot. Every miss found in testing or pilots becomes a permanent test case (task doc 4.5).
"""
import re

# Each list: English patterns (word boundaries, case-insensitive) and Chinese substrings/patterns.
_CRISIS = [
    r"\bkill (?:myself|me)\b", r"\bsuicid", r"\bend(?:ing)? (?:my|it) (?:own )?(?:life|all)\b", r"\btake my own life\b", r"\bthinking (?:of|about) (?:dying|suicide)\b", r"\bkeep thinking about dying\b", r"\bhurt(?:ing)? myself\b", r"\bself[- ]harm",
    r"\bdon'?t want to (?:live|be alive)\b", r"\bno reason to live\b", r"\bwant to die\b", r"\bbetter off dead\b",
    r"自杀", r"不想活", r"轻生", r"伤害自己", r"结束(?:自己的)?生命", r"想死", r"活着没(?:有)?意思", r"自残", r"割腕", r"跳楼",
]
_STOP = [
    r"\b(?:i )?(?:want|would like|need) to stop\b", r"\bstop (?:the|this) (?:interview|session|chat)\b", r"\bend (?:the|this) (?:interview|session)\b",
    r"\bi'?m done(?! with\b)", r"\bi'?m done with (?:this|the|that) (?:interview|session|chat|conversation|survey)\b", r"\bdon'?t want to (?:continue|go on|do this)\b", r"\bcan we stop\b", r"\bplease stop\b", r"\bi (?:have to|need to|must) (?:go|leave)\b",
    r"(?<!apps )(?<!app )(?<!one )(?<!ones )(?<!that )(?<!which )\bi quit\b(?!\s+[a-z])", r"\bno more questions\b",
    r"不想(?:再)?(?:继续|聊|做)(?:了|下去)?$", r"我想停", r"(?:请|可以)?停(?:止|下)(?:访谈|吧|一下)?(?:吧|了)?$", r"结束(?:访谈)?吧", r"不聊了", r"我要走了",
    r"不想(?:再)?继续", r"到此为止", r"我不(?:想)?参加了", r"我退出",
]
_ASKS_AI = [
    r"\bare you (?:a )?(?:real|human|person|bot|robot|ai|machine)\b", r"\bis this (?:a )?(?:bot|robot|ai|real person|human)\b",
    r"\bam i (?:talking|speaking|chatting) (?:to|with) (?:a )?(?:real|human|person|bot|robot|ai|machine)", r"\bare you an? ai\b",
    r"\bwho am i talking to\b",
    # \s* everywhere: people type "你是不是 AI" with a space before Latin letters (found by eval run s001, 2026-10-12)
    r"[你您]\s*是\s*(?:不是)?\s*(?:真人|机器人|AI|人工智能|人|bot)", r"[你您]\s*是\s*真(?:的)?\s*人\s*(?:吗|嘛)", r"是不是\s*(?:真人|机器人|AI|bot)",
    r"我(?:是)?\s*在\s*(?:跟|和)\s*(?:谁|什么|机器人|AI|bot|真人)", r"(?:真人|机器人|AI)\s*(?:吗|嘛)",
]


# Questions about WHAT the AI is made of or who made it, and attempts to read its instructions. The answer is a fixed sentence, never the model's.
_ASKS_MODEL = [
    r"\b(?:what|which)\s+(?:model|llm|ai|language model|technology|tech|engine|software)\s+(?:are|is|do|does|did)\b", r"\bwhat\s+are\s+you\s+(?:built|based|running|powered|trained)\b",
    r"\b(?:which|what)\s+(?:company|firm|vendor|lab)\b.{0,30}\b(?:you|your|made|built|behind|owns?)\b", r"\bwho\s+(?:made|built|created|trained|developed|owns|programmed)\s+you\b",
    r"\bare\s+you\s+(?:chat\s*gpt|gpt[\s-]?\d*|openai|claude|anthropic|gemini|bard|deepseek|llama|mistral|qwen|copilot|alexa|siri|kimi|ernie|doubao|hunyuan)\b",
    r"\b(?:powered|built|based|running)\s+(?:on|by|with)\b.{0,25}\b(?:gpt|llm|model|openai|claude|gemini|deepseek|llama)\b",
    r"\b(?:your|the system)\s+(?:system\s+)?(?:prompt|instructions|rules|guidelines)\b", r"\bignore\s+(?:all\s+|any\s+)?(?:your\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)\b",
    r"\b(?:reveal|show|print|repeat|tell me)\s+(?:me\s+)?(?:your|the system)\s+(?:system\s+)?(?:prompt|instructions)\b", r"\bjailbreak\b", r"\bpretend\s+(?:to be|you are)\b",
    r"[你您]\s*(?:是|用的是|基于|背后是|底层是)\s*(?:什么|哪个|哪家|啥|哪一个)\s*(?:大?语言)?(?:模型|大模型|AI|ai|公司|技术|引擎|产品)",
    r"[你您]\s*(?:是)?\s*基于\s*(?:什么|哪个|哪家|哪一个)", r"[你您]\s*(?:用|使用|采用|跑)\s*(?:的)?\s*(?:是)?\s*(?:什么|哪个|哪家|哪一个)\s*(?:模型|大模型|技术|AI|ai|引擎)",
    r"(?:哪家|哪个|什么)\s*公司\s*(?:的|做的|开发的|出品|研发的|训练的)", r"谁\s*(?:开发|做|造|创造|训练|研发|设计)\s*了?\s*[你您]", r"[你您]\s*(?:是|背后是)\s*谁\s*(?:开发|做|造|训练|研发)的",
    r"[你您]\s*(?:是|是不是|用的是)\s*(?:chat\s*gpt|gpt|openai|claude|gemini|deepseek|豆包|kimi|通义|千问|文心|混元|智谱|讯飞|星火|元宝|copilot)", r"(?:大)?语言模型", r"底层\s*(?:模型|技术)",
    r"(?:你的|你们的|你)\s*(?:系统)?\s*(?:提示词|指令|设定|规则|prompt)", r"忽略\s*(?:你)?\s*(?:之前|以上|上面|先前|前面)\s*(?:的)?\s*(?:所有)?\s*(?:指令|提示|规则|设定)",
    r"(?:把|将)\s*(?:你的)?\s*(?:系统)?\s*(?:提示词|指令|设定)\s*(?:发|告诉|给|输出|说)", r"假装\s*(?:你)?\s*是", r"越狱",
]


def detect_asks_model(text: str) -> bool:
    """The participant asks what model, company or technology the AI is, who made it, or tries to read or override its instructions.

    Answered with a fixed sentence (templates.model_answer): the AI is honest that it is an AI (red line H3) and says nothing else about itself.
    """
    return _matches(_ASKS_MODEL, text)


_MENTIONS_AI = [r"\bAI\b", r"\bbot\b", r"robot", r"machine", r"artificial", r"\breal (?:person|human)\b", r"\bhuman\b", r"\bperson\b",
                r"人工智能", r"机器", r"真人", r"AI", r"真的人", r"活人"]


def detect_mentions_ai(text: str) -> bool:
    """Whether the participant's words touch on AI, bots or "a real person" at all.

    The model's own "the participant asks if I am an AI" flag tends to stay true after the first time; it only counts when the
    participant's current words are about it (found by eval run 20261012-080304: "I am an AI" was repeated in every turn)."""
    return any(re.search(p, text, flags=re.IGNORECASE) for p in _MENTIONS_AI)


_READY = [
    r"^(?:ok|okay|yes|yeah|yep|sure|ready|go ahead|let'?s (?:start|begin|go)|sounds good|fine|alright|of course)\b",
    r"^(?:好|好的|好啊|可以|可以的|行|没问题|开始吧|开始|准备好了|嗯|嗯嗯|OK|ok)(?:[，,。.!！ ]|$)", r"可以开始", r"我们开始", r"准备好了",
]


def detect_ready(text: str) -> bool:
    """During the intro: the participant agrees to begin. Backs up the model's ready_to_start, which small models often miss."""
    return _matches(_READY, text) and not detect_stop(text)


def _matches(patterns: list[str], text: str) -> bool:
    """Whether any pattern is found in the text (case-insensitive, trailing punctuation ignored for end-anchored ones)."""
    cleaned = text.strip().rstrip("。.!！?？~～ ")
    return any(re.search(p, cleaned, flags=re.IGNORECASE) for p in patterns)


def detect_crisis(text: str) -> bool:
    """Signs of danger to the participant (red line H5: stop, safety statement, red alert)."""
    return _matches(_CRISIS, text)


_CONDITIONAL = re.compile(r"\b(?:if|when|whenever|once|unless)\b", re.IGNORECASE)


def detect_stop(text: str) -> bool:
    """The participant asks to stop or end the interview (red line H5: stop at once, politely).

    A pattern found after "if / when / once ..." in the same sentence is an idiom about something else ("if the food is late, I'm
    done" means done with the app, found in evaluation runs), not a request to end this interview. The model's own reading of the
    answer still catches a real request that is phrased that way.
    """
    for sentence in re.split(r"(?<=[.!?。！？])\s*", text):
        cleaned = sentence.strip().rstrip("。.!！?？~～ ")  # as _matches does, for the patterns anchored to the end
        for pattern in _STOP:
            found = re.search(pattern, cleaned, re.IGNORECASE)
            if found and not _CONDITIONAL.search(cleaned[:found.start()]):
                return True
    return False


def detect_asks_if_ai(text: str) -> bool:
    """The participant asks whether they are talking to a person or an AI (red line H3: answer honestly)."""
    return _matches(_ASKS_AI, text)
