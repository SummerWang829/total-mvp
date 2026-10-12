"""Conversation memory and the per-turn context the model sees (the "context manager" of Sprint 1).

- Recent turns go in word for word; older ones are replaced by a running summary, so long interviews fit the model.
- Observer whispers are high-priority notes for the next turn only; the participant never sees them (task doc 5).
- Hypotheses travel in the context to steer follow-ups but are marked internal; red lines (Sprint 2) stop them being said.
"""
from pydantic import BaseModel, ConfigDict, Field

from .constants import DEFAULT_RECENT_TURNS_VERBATIM, MAX_WHISPER_CHARS, Speaker
from .fsm import PacingDecision, PacingSnapshot
from .knowledge import InterviewGuide


class DialogueTurn(BaseModel):
    """One line of the conversation."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    speaker: Speaker
    text: str = Field(min_length=1)
    at_s: float = Field(ge=0)


class Whisper(BaseModel):
    """An observer's private note to the AI."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=MAX_WHISPER_CHARS)
    at_s: float = Field(ge=0)


class ModeratorContext(BaseModel):
    """Everything the model is given for one turn. Built fresh every turn by ConversationMemory.build_context."""

    model_config = ConfigDict(frozen=True)

    language: str
    research_goal: str
    pacing: PacingSnapshot
    decision: PacingDecision
    whispers: list[str]                 # high priority: consider first this turn
    summary: str                        # older turns, condensed
    recent_turns: list[DialogueTurn]    # word for word
    hypotheses_internal: list[str]      # steer follow-ups only; never said or hinted
    forbidden_topics: list[str]
    product_facts: list[str]
    brand_disclosure_allowed: bool
    asked_questions: list[str]          # everything the moderator already asked (no repeats)


class ConversationMemory:
    """Holds the dialogue, whispers and the running summary of one interview.

    Args:
        recent_turns_verbatim: how many latest turns the model sees word for word.
    """

    def __init__(self, recent_turns_verbatim: int = DEFAULT_RECENT_TURNS_VERBATIM) -> None:
        if recent_turns_verbatim < 1:
            raise ValueError("the model must see at least the latest turn")
        self.recent_turns_verbatim = recent_turns_verbatim
        self.turns: list[DialogueTurn] = []
        self.summary = ""
        self.summarized_upto = 0  # turns[:summarized_upto] are covered by `summary`
        self._pending_whispers: list[Whisper] = []
        self.whisper_log: list[Whisper] = []

    def add_turn(self, speaker: Speaker, text: str, at_s: float) -> DialogueTurn:
        """Append a line.

        Raises:
            ValueError: blank text (pydantic validation), or a time earlier than the previous line.
        """
        if self.turns and at_s < self.turns[-1].at_s:
            raise ValueError("turns must be added in time order")
        turn = DialogueTurn(index=len(self.turns), speaker=speaker, text=text.strip(), at_s=at_s)
        self.turns.append(turn)
        return turn

    def add_whisper(self, text: str, at_s: float) -> Whisper:
        """Queue an observer's note for the next turn.

        Raises:
            pydantic.ValidationError: empty, or longer than MAX_WHISPER_CHARS.
        """
        whisper = Whisper(text=text.strip(), at_s=at_s)
        self._pending_whispers.append(whisper)
        self.whisper_log.append(whisper)
        return whisper

    def take_whispers(self) -> list[Whisper]:
        """Hand the queued whispers to this turn; each is used once."""
        taken, self._pending_whispers = self._pending_whispers, []
        return taken

    def turns_to_summarize(self) -> list[DialogueTurn]:
        """Turns that have dropped out of the verbatim window and are not in the summary yet."""
        window_start = max(0, len(self.turns) - self.recent_turns_verbatim)
        return self.turns[self.summarized_upto:window_start]

    def set_summary(self, summary: str, upto_index: int) -> None:
        """Store a new running summary that covers turns[:upto_index].

        Raises:
            ValueError: upto_index goes backwards or past the end.
        """
        if not self.summarized_upto <= upto_index <= len(self.turns):
            raise ValueError("summary must cover a growing prefix of the turns")
        self.summary = summary.strip()
        self.summarized_upto = upto_index

    def asked_questions(self) -> list[str]:
        """Everything the AI or a human moderator has said, in order (the model avoids repeating it)."""
        return [t.text for t in self.turns if t.speaker is not Speaker.PARTICIPANT]

    def build_context(self, guide: InterviewGuide, snapshot: PacingSnapshot, decision: PacingDecision,
                      consume_whispers: bool = True) -> ModeratorContext:
        """Assemble this turn's context.

        Args:
            guide: the project's guide.
            snapshot: the state machine's clock and position.
            decision: what the state machine wants done this turn.
            consume_whispers: hand the queued whispers over (used once); False leaves them queued and out of this context
                (the analysis call: whispers steer what is said, not how the answer is read).

        Returns:
            The validated context.
        """
        # Never lose a turn: if the summary lags behind, the not-yet-summarised turns stay verbatim too.
        window_start = max(0, len(self.turns) - self.recent_turns_verbatim)
        recent = self.turns[min(self.summarized_upto, window_start):]
        return ModeratorContext(
            language=guide.language, research_goal=guide.research_goal, pacing=snapshot, decision=decision,
            whispers=[w.text for w in self.take_whispers()] if consume_whispers else [], summary=self.summary, recent_turns=recent,
            hypotheses_internal=list(guide.hypotheses), forbidden_topics=list(guide.forbidden_topics),
            product_facts=list(guide.product_facts), brand_disclosure_allowed=guide.brand_disclosure_allowed,
            asked_questions=self.asked_questions())
