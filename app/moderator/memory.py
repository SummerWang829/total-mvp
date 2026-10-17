"""Conversation memory and the per-turn context the model sees (the "context manager" of Sprint 1).

- Recent turns go in word for word; older ones are replaced by a running summary, so long interviews fit the model.
- Observer whispers are high-priority notes for the next turn only; the participant never sees them (task doc 5).
- Long interviews (two hours): a list of key facts the participant stated is kept for the whole interview, a model-written summary is
  refreshed as lines leave the window, and older lines that match what is being discussed now come back word for word (recall.py).
- Hypotheses travel in the context to steer follow-ups but are marked internal; red lines (Sprint 2) stop them being said.
"""
from pydantic import BaseModel, ConfigDict, Field

from .constants import (CONTEXT_ASKED_QUESTIONS, DEFAULT_RECENT_TURNS_VERBATIM, DIGEST_LINE_CHARS, DIGEST_MAX_CHARS, FACT_CARD_CHARS, FACT_CARDS_MAX,
                        FACTS_CONTEXT_CHARS, MAX_WHISPER_CHARS, SUMMARY_EVERY_TURNS, SUMMARY_MAX_CHARS, Speaker)
from .fsm import PacingDecision, PacingSnapshot
from .knowledge import InterviewGuide
from .recall import recall


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
    key_facts: list[str] = Field(default_factory=list)   # what the participant stated earlier that must not be contradicted or asked again
    recalled: list[str] = Field(default_factory=list)    # older lines word for word that match what is being discussed now
    profile: list[str] = Field(default_factory=list)     # this participant's screening answers ("question → answer"), fixed for the whole interview


class ConversationMemory:
    """Holds the dialogue, whispers and the running summary of one interview.

    Args:
        recent_turns_verbatim: how many latest turns the model sees word for word.
        profile: the participant's screening answers, shown to the model every turn (never summarised away).
    """

    def __init__(self, recent_turns_verbatim: int = DEFAULT_RECENT_TURNS_VERBATIM, profile: list[str] | None = None) -> None:
        if recent_turns_verbatim < 1:
            raise ValueError("the model must see at least the latest turn")
        self.recent_turns_verbatim = recent_turns_verbatim
        self.profile = list(profile or [])  # background about this participant that was known before the interview began
        self.turns: list[DialogueTurn] = []
        self.summary = ""
        self.summarized_upto = 0  # turns[:summarized_upto] are covered by `summary`
        self.facts: list[tuple[float, str]] = []  # (seconds into the interview, fact) in the order stated
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

    def summary_due(self) -> bool:
        """Whether enough lines have left the verbatim window since the last summary to ask the model for a new one."""
        return len(self.turns_to_summarize()) >= SUMMARY_EVERY_TURNS

    def add_facts(self, facts: list[str], at_s: float) -> int:
        """Remember what the participant just stated (an allergy, a budget, a brand, a strong like or dislike).

        Blank and repeated facts are ignored; each is cut to FACT_CARD_CHARS; past FACT_CARDS_MAX the oldest are dropped.

        Returns:
            How many new facts were stored.
        """
        known = {f for _, f in self.facts}
        added = 0
        for raw in facts:
            fact = " ".join(raw.split())[:FACT_CARD_CHARS]
            if fact and fact not in known:
                self.facts.append((at_s, fact))
                known.add(fact)
                added += 1
        del self.facts[:-FACT_CARDS_MAX]
        return added

    def facts_digest(self) -> list[str]:
        """The remembered facts, oldest first, each with its minute; when over FACTS_CONTEXT_CHARS the oldest are left out."""
        lines = [f"{at / 60:.0f}分: {fact}" for at, fact in self.facts]
        while len(lines) > 1 and sum(len(x) for x in lines) > FACTS_CONTEXT_CHARS:
            lines.pop(0)
        return lines

    def recalled_lines(self, query: str, window_start: int) -> list[str]:
        """Participant lines from before the verbatim window that match the query, word for word with their minute."""
        old = [(t.index, t.text) for t in self.turns[:window_start] if t.speaker is Speaker.PARTICIPANT]
        return [f"{self.turns[i].at_s / 60:.0f}分: {text}" for i, text in recall(query, old)]

    def set_summary(self, summary: str, upto_index: int) -> None:
        """Store a new running summary that covers turns[:upto_index].

        Raises:
            ValueError: upto_index goes backwards or past the end.
        """
        if not self.summarized_upto <= upto_index <= len(self.turns):
            raise ValueError("summary must cover a growing prefix of the turns")
        self.summary = summary.strip()[:SUMMARY_MAX_CHARS]
        self.summarized_upto = upto_index

    def history_digest(self, window_start: int) -> str:
        """The condensed history of everything before the verbatim window, so the context stays a fixed size however long the interview.

        A model-written summary (set_summary) covers the first `summarized_upto` turns; the turns after it, up to the window, are
        condensed by rule: one short line per participant answer (the questions are already in ALREADY_ASKED). Without this the
        context grew with every turn (found in eval run 20261012-065429: 80-90 turn interviews had the slowest turns).

        Args:
            window_start: index of the first verbatim turn.

        Returns:
            At most DIGEST_MAX_CHARS characters; when over, the oldest lines are dropped.
        """
        covered = min(self.summarized_upto, window_start)
        lines = [f"{t.at_s / 60:.0f}分: {t.text[:DIGEST_LINE_CHARS]}" for t in self.turns[covered:window_start] if t.speaker is Speaker.PARTICIPANT]
        digest = "\n".join(lines)
        if len(digest) > DIGEST_MAX_CHARS:
            digest = digest[-DIGEST_MAX_CHARS:].split("\n", 1)[-1]  # keep the newest lines, whole lines only
        return (self.summary + "\n" + digest).strip() if self.summary and covered else digest

    def asked_questions(self) -> list[str]:
        """Everything the AI or a human moderator has said, in order (the model avoids repeating it)."""
        return [t.text for t in self.turns if t.speaker is not Speaker.PARTICIPANT]

    def build_context(self, guide: InterviewGuide, snapshot: PacingSnapshot, decision: PacingDecision,
                      consume_whispers: bool = True, recall_query: str = "") -> ModeratorContext:
        """Assemble this turn's context.

        Args:
            guide: the project's guide.
            snapshot: the state machine's clock and position.
            decision: what the state machine wants done this turn.
            consume_whispers: hand the queued whispers over (used once); False leaves them queued and out of this context
                (the analysis call: whispers steer what is said, not how the answer is read).
            recall_query: what is being discussed now; older lines matching it are added word for word (empty: none).

        Returns:
            The validated context.
        """
        window_start = max(0, len(self.turns) - self.recent_turns_verbatim)
        recent = self.turns[window_start:]
        return ModeratorContext(
            language=guide.language, research_goal=guide.research_goal, pacing=snapshot, decision=decision,
            whispers=[w.text for w in self.take_whispers()] if consume_whispers else [], summary=self.history_digest(window_start), recent_turns=recent,
            hypotheses_internal=list(guide.hypotheses), forbidden_topics=list(guide.forbidden_topics),
            product_facts=list(guide.product_facts), brand_disclosure_allowed=guide.brand_disclosure_allowed,
            asked_questions=self.asked_questions()[-CONTEXT_ASKED_QUESTIONS:], key_facts=self.facts_digest(), profile=self.profile,
            recalled=self.recalled_lines(recall_query, window_start) if recall_query else [])
