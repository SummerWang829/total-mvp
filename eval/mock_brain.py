"""A scripted stand-in for the moderator's model, so the whole evaluation runs offline and in CI.

It reads the same JSON the real model gets and answers with simple rules. Its scores say nothing about the moderator's
quality; reports made with it are labelled "mock-brain" and only prove that the pipeline works end to end.
"""
import json
import re

from app.moderator.detectors import detect_asks_if_ai, detect_crisis, detect_stop
from app.moderator.prompts import load_prompts
from app.moderator.providers import ChatMessage
from app.moderator.templates import templates_for

NEUTRAL_PROBE = {
    "zh": {"specify": "能举一个最近的具体例子吗？", "why": "当时是什么原因让您这样决定？", "emotion": "那时候您是什么感受？", "impact": "这件事后来带来了什么影响？",
           "reflect": "您的意思是这一点对您很重要，我理解得对吗？", "nudge": "能再多说一点吗？", "scale": "如果用 0 到 10 分，您会打几分？",
           "redirect": "我们回到刚才的问题，您平时是怎么做的？", "rephrase": "换个问法：您一般怎么选？", "reassure": "没关系，慢慢来。您愿意多说一点吗？",
           "clarify": "您刚才说的这一点，具体是指什么？"},
    "en": {"specify": "Could you give me a recent, concrete example?", "why": "What made you decide that?", "emotion": "How did you feel at that moment?",
           "impact": "What did that lead to afterwards?", "reflect": "So this matters a lot to you; did I understand that correctly?",
           "nudge": "Could you say a little more?", "scale": "On a scale from 0 to 10, how would you rate it?",
           "redirect": "Coming back to my question, how do you usually do it?", "rephrase": "Let me put it another way: how do you usually choose?",
           "reassure": "That's fine, take your time. Would you like to say a bit more?", "clarify": "What exactly did you mean by that?"},
}
REASON_WORDS = re.compile(r"因为|所以|原因|because|reason|so that", re.IGNORECASE)
QUESTION_BACK = re.compile(r"你觉得呢|你怎么看|你会推荐|一般人|what do you think|what's your view|would you recommend|how do other", re.IGNORECASE)
SHORT_ANSWER_CHARS = 8
ANSWERED_CHARS = 30


class MockBrain:
    """Implements ModeratorChatProvider with rules (see module docstring)."""

    name = "mock-brain"

    def __init__(self) -> None:
        prompts = load_prompts()
        self._analyze_prompt = prompts.analyze
        self._summarize_prompt = prompts.summarize
        self._remember_prompt = prompts.remember

    def complete(self, messages: list[ChatMessage], temperature: float = 0.3, seed: int | None = None) -> dict:
        """Answer an analysis, a summary or a speaking request."""
        data = json.loads(messages[-1]["content"])
        if self._remember_prompt and messages[0]["content"] == self._remember_prompt:
            return {"new_facts": []}
        if self._summarize_prompt and messages[0]["content"] == self._summarize_prompt:
            return {"summary": (data["PREVIOUS_SUMMARY"] + " " + " ".join(x["text"][:20] for x in data["NEW_LINES"] if x["speaker"] == "participant"))[-600:]}
        return self._analyze(data) if messages[0]["content"] == self._analyze_prompt else self._speak(data)

    def _analyze(self, data: dict) -> dict:
        """Coverage = an uncovered item's text appears in the answer; other fields from length and keywords."""
        answer: str = data["ANSWER"]
        covered = [i for i, text in data["UNCOVERED_ITEMS"].items() if text in answer]
        category = ("crisis" if detect_crisis(answer) else "wants_to_stop" if detect_stop(answer) else
                    "question_back" if QUESTION_BACK.search(answer) else "too_short" if len(answer) < SHORT_ANSWER_CHARS else "answered")
        reason = bool(REASON_WORDS.search(answer))
        saturation = min(1.0, data["PACING"]["saturation"] + 0.12 + 0.25 * len(covered))
        return {"category": category, "ladder_level": "reason" if reason else "behaviour", "saturation": saturation, "covered_item_ids": covered,
                "question_answered": len(answer) >= ANSWERED_CHARS, "ready_to_start": True, "asks_if_ai": detect_asks_if_ai(answer),
                "signals": {"information_gain": min(1.0, len(answer) / 120), "emotion": 0.2, "pain_point": 0.4 if reason else 0.1,
                            "contradiction": 0.0, "business_value": 0.4}}

    def _speak(self, data: dict) -> dict:
        """Say what the state machine asked for, or a neutral probe for the chosen operator."""
        lang = data["LANGUAGE"]
        texts = templates_for(lang)
        decision = data["PLAN"]["decision"]
        if decision.get("question"):
            text = decision["question"]
        elif decision.get("missed_item"):
            text = texts.missed_item.format(item=decision["missed_item"]["text"])
        elif decision["phase"] == "wrapup":
            text = texts.wrapup
        else:
            text = NEUTRAL_PROBE[lang].get(data["PLAN"]["operator"], texts.followup)
        if data["ASKS_IF_AI"]:
            text = texts.ai_answer + ("" if lang == "zh" else " ") + text
        return {"spoken_response": text, "note": {"research_purpose": "mock", "expected_information": "mock", "probe_logic": data["PLAN"]["operator"]}}
