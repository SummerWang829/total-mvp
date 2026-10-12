"""Rule-based reading of the participant's words for the cases that must never depend on a model (red lines H3, H5).

A miss here is still caught by the model's own analysis (category CRISIS / WANTS_TO_STOP, asks_if_ai); either one
triggers the safe path. Patterns are deliberately broad: stopping an interview by mistake costs little, missing a crisis
costs a lot. Every miss found in testing or pilots becomes a permanent test case (task doc 4.5).
"""
import re

# Each list: English patterns (word boundaries, case-insensitive) and Chinese substrings/patterns.
_CRISIS = [
    r"\bkill (?:myself|me)\b", r"\bsuicid", r"\bend (?:my|it) (?:life|all)\b", r"\bhurt(?:ing)? myself\b", r"\bself[- ]harm",
    r"\bdon'?t want to (?:live|be alive)\b", r"\bno reason to live\b", r"\bwant to die\b", r"\bbetter off dead\b",
    r"自杀", r"不想活", r"轻生", r"伤害自己", r"结束(?:自己的)?生命", r"想死", r"活着没(?:有)?意思", r"自残", r"割腕", r"跳楼",
]
_STOP = [
    r"\b(?:i )?(?:want|would like|need) to stop\b", r"\bstop (?:the|this) (?:interview|session|chat)\b", r"\bend (?:the|this) (?:interview|session)\b",
    r"\bi'?m done\b", r"\bdon'?t want to (?:continue|go on|do this)\b", r"\bcan we stop\b", r"\bplease stop\b", r"\bi (?:have to|need to|must) (?:go|leave)\b",
    r"\bi quit\b", r"\bno more questions\b",
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


def detect_stop(text: str) -> bool:
    """The participant asks to stop or end the interview (red line H5: stop at once, politely)."""
    return _matches(_STOP, text)


def detect_asks_if_ai(text: str) -> bool:
    """The participant asks whether they are talking to a person or an AI (red line H3: answer honestly)."""
    return _matches(_ASKS_AI, text)
