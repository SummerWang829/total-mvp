"""The research team's assistants always call themselves 小星星助手 (user 2026-11-01).

A short message that asks which model, AI, tool or company is answering ("你是什么模型", "what model are you", "siapa anda", ...) gets one fixed
reply without calling any model, so no model can name itself; the prompts also carry PROMPT_LINE for questions this check does not catch.
Only the researcher-side chats use this. The AI interviewer that talks to participants keeps its own disclosure rules (it must say it is an AI).
"""
import re

NAME = "小星星助手"
REPLY = "我是小星星助手。"
PROMPT_LINE = ("If you are asked, in any language, which model, AI, tool, company or product you are or who made you, answer only that you are "
               "“小星星助手”, and never name any model, vendor or company. 如果被问到你是什么模型、什么工具、哪家公司做的，只回答“我是小星星助手”。")

MAX_LEN = 80   # identity questions are short; a long message that merely mentions a model is a real request
_PATTERNS = [
    r"(你|您)(到底)?(是|用的是|用了|用|基于|属于|叫)(什么|哪个|哪家|哪种|谁)",
    r"(你|您)是谁",
    r"(什么|哪个|哪家|哪种)(大)?(模型|ai|AI|人工智能)(在)?(回答|驱动|支持)",
    r"^(用的|这是|这个助手是|这个是)?(什么|哪个|哪家|哪种)(大)?(模型|AI|ai)[？?吗呢]?$",
    r"(谁|哪家公司|什么公司)(开发|做|训练|制作)(了)?(你|您)",
    r"(你|您)是(chatgpt|gpt|deepseek|claude|通义|文心|kimi|豆包|gemini|qwen)",
    r"\bwhat (model|llm|ai|tool) (are you|is this|is it|powers|runs|do you use|are you using)\b",
    r"\bwhat are you\b",
    r"\bwhich (model|llm|ai|company|tool) (are you|is this|is it|powers|runs|made|built|do you use)\b",
    r"\bwho (are|made|built|created|developed|trained) you\b",
    r"\bare you (chatgpt|gpt|deepseek|claude|gemini|an? (ai|bot|llm|model))\b",
    r"\byour (name|model)\b",
    r"\b(model|alat|ai) apa\b",
    r"\bsiapa (anda|kamu|awak)\b",
    r"\b(anda|kamu|awak) siapa\b",
    r"(நீ|நீங்கள்) யார்",
    r"எந்த (மாடல்|கருவி|ai)",
]
_RX = [re.compile(p, re.I) for p in _PATTERNS]


def asks_identity(message: str) -> bool:
    """True when a short message asks who or what the assistant is.

    Raises:
        Nothing.
    """
    text = (message or "").strip()
    if not text or len(text) > MAX_LEN:
        return False
    return any(rx.search(text) for rx in _RX)
