"""Simulated participants. Not real people: scores from them only show direction (task doc 0.3); pilots decide.

- RuleBasedParticipant: deterministic, offline, cheap; exercises the whole pipeline in CI. Its answers mention
  must-cover items by their exact text so a scripted model can "notice" coverage.
- ModelParticipant: a chat model plays the persona; used for the real evaluation.
"""
import json
import logging
import random
from typing import Protocol

from app.moderator.knowledge import InterviewGuide
from app.moderator.providers import ChatError, ModeratorChatProvider

from .constants import Intensity, PersonaType
from .models import Persona

log = logging.getLogger(__name__)

INTENSITY_FACTOR: dict[Intensity, float] = {Intensity.LOW: 0.6, Intensity.MEDIUM: 1.0, Intensity.HIGH: 1.3}
READY_REPLY = {"zh": "好的，可以开始。", "en": "Sure, let's begin."}
REASON = {"zh": "因为这样比较放心。", "en": "Because that makes me feel safer."}
FALLBACK_REPLY = {"zh": "嗯，我想想。", "en": "Hmm, let me think."}


class Participant(Protocol):
    """Anything that answers the moderator like a participant."""

    def reply(self, ai_text: str, history: list[tuple[str, str]]) -> str:
        """The participant's answer to `ai_text`; history is [(speaker, text), ...] so far."""
        ...


class RuleBasedParticipant:
    """Answers from the persona's answer bank, deterministic for a seed.

    Args:
        persona: the persona.
        intensity: how strongly it shows its trait.
        guide: the interview guide (its must-cover items are what the persona "knows").
        seed: random seed.
    """

    def __init__(self, persona: Persona, intensity: Intensity, guide: InterviewGuide, seed: int) -> None:
        self.persona, self.guide, self.rng = persona, guide, random.Random(seed)
        self.lang = guide.language
        self.factor = INTENSITY_FACTOR[intensity]
        self.items = [i.text for t in guide.topics for i in t.must_cover]
        self.mentioned: set[str] = set()
        self.started = False

    def reply(self, ai_text: str, history: list[tuple[str, str]]) -> str:
        """An answer showing the persona's trait; covers a must-cover item when asked about it or by chance."""
        if not self.started:
            self.started = True
            return READY_REPLY[self.lang]
        asked = next((i for i in self.items if i in ai_text), None)
        cooperates = self.persona.type not in (PersonaType.SILENT, PersonaType.RESISTANT) or self.rng.random() < 0.3 / self.factor
        item = asked if asked and cooperates else self._maybe_item()
        bank = self.persona.answers[self.lang]
        text = self.rng.choice([a for a in bank if ("{item}" in a) == bool(item)] or bank)
        text = text.replace("{item}", item or "")
        if item:
            self.mentioned.add(item)
        if self.rng.random() < self.persona.reasons:
            text += ("" if self.lang == "zh" else " ") + REASON[self.lang]
        return text

    def _maybe_item(self) -> str | None:
        """A not-yet-mentioned item, with the persona's chance of covering one."""
        if self.rng.random() >= min(1.0, self.persona.covers_items * self.factor):
            return None
        fresh = [i for i in self.items if i not in self.mentioned]
        return self.rng.choice(fresh) if fresh else None


class ModelParticipant:
    """A chat model plays the persona (EVAL_PARTICIPANT_* in .env).

    Args:
        chat: the chat model.
        persona: the persona to play.
        intensity: how strongly to show the trait.
        guide: the interview guide (only its participant-facing topic and language are shown to the player).
        seed: model seed for repeatable runs.
    """

    def __init__(self, chat: ModeratorChatProvider, persona: Persona, intensity: Intensity, guide: InterviewGuide, seed: int) -> None:
        self.chat, self.persona, self.guide, self.seed = chat, persona, guide, seed
        language = "simplified Chinese" if guide.language == "zh" else "English"
        self.system = (f"You are role-playing a participant in a market-research interview about {guide.participant_topic or 'shopping'} "
                       f"in Singapore. Persona: {persona.roleplay} Show this trait with {intensity.value} intensity. "
                       f"Always answer in {language}, as the participant only, never as the interviewer. "
                       'Return ONE JSON object: {"reply": str}.')

    def reply(self, ai_text: str, history: list[tuple[str, str]]) -> str:
        """The model's in-character answer; a neutral "let me think" if the model fails (logged)."""
        transcript = "\n".join(f"{who}: {text}" for who, text in history[-16:])
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": json.dumps({"conversation_so_far": transcript, "interviewer_just_said": ai_text}, ensure_ascii=False)}]
        try:
            reply = str(self.chat.complete(messages, temperature=0.8, seed=self.seed).get("reply", "")).strip()
        except ChatError:
            log.warning("simulated participant: model call failed", exc_info=True)
            reply = ""
        return reply or FALLBACK_REPLY[self.guide.language]
