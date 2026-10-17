"""The two model calls of a turn: read the answer (analyze) and draft what to say (speak). Output is validated
against app/moderator/schemas.py; unusable output is asked for once more, then the caller falls back (task doc 4.4)."""
import json
import logging
from typing import TypeVar

from pydantic import ValidationError

from .constants import MAX_PARSE_RETRIES, REJECTION_FIX_HINTS
from .memory import ModeratorContext
from .prompts import PromptSet
from .providers import ChatError, ChatFatalError, ChatMessage, ModeratorChatProvider
from .schemas import DialogueActionPlan, MemoryUpdate, RespondentInputAnalysis, SpokenDraft, Violation

log = logging.getLogger(__name__)
Reply = TypeVar("Reply", RespondentInputAnalysis, SpokenDraft, MemoryUpdate)  # works on Python 3.10+ (the doctor's minimum)


def _context_json(ctx: ModeratorContext) -> dict:
    """The context in the shape the prompts name (UPPER_CASE keys), as plain JSON."""
    return {
        "LANGUAGE": ctx.language, "RESEARCH_GOAL": ctx.research_goal, "PACING": ctx.pacing.model_dump(mode="json"),
        "WHISPERS": ctx.whispers, "SUMMARY": ctx.summary,
        "RECENT_TURNS": [{"speaker": t.speaker.value, "text": t.text} for t in ctx.recent_turns],
        "HYPOTHESES": ctx.hypotheses_internal, "FORBIDDEN_TOPICS": ctx.forbidden_topics, "PRODUCT_FACTS": ctx.product_facts,
        "BRAND_DISCLOSURE_ALLOWED": ctx.brand_disclosure_allowed, "ALREADY_ASKED": ctx.asked_questions,
        "KEY_FACTS": ctx.key_facts, "RECALLED_EARLIER": ctx.recalled,
    }


class ModelCaller:
    """Wraps the chat provider with the moderator's prompts and output validation.

    Args:
        chat: the injected chat model.
        prompts: the prompt version in use.
        seed: fixed seed for repeatable eval runs (None live).
    """

    def __init__(self, chat: ModeratorChatProvider, prompts: PromptSet, seed: int | None = None) -> None:
        self.chat = chat
        self.prompts = prompts
        self.seed = seed

    def analyze(self, ctx: ModeratorContext, uncovered: dict[str, str], answer: str) -> RespondentInputAnalysis | None:
        """Read the participant's answer.

        Args:
            ctx: this turn's context.
            uncovered: must-cover items not yet covered, id -> text (the only ids the model may report).
            answer: the participant's latest answer.

        Returns:
            The validated analysis, or None when the model gave nothing usable twice (the caller then uses a neutral reading).

        Raises:
            ChatFatalError: the service refused (bad key, no balance, ...): never turned into a neutral reading.
        """
        user = {**_context_json(ctx), "UNCOVERED_ITEMS": uncovered, "ANSWER": answer}
        messages = [{"role": "system", "content": self.prompts.analyze}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
        return self._ask(messages, RespondentInputAnalysis, "analysis")

    def draft(self, ctx: ModeratorContext, plan: DialogueActionPlan, asks_if_ai: bool,
              rejected: list[list[Violation]], rejected_drafts: list[str] | None = None) -> SpokenDraft | None:
        """Draft the next sentence(s) to say.

        Args:
            ctx: this turn's context.
            plan: chosen operator and the state machine's decision.
            asks_if_ai: the participant asked whether they talk to an AI.
            rejected: violations of earlier drafts in this turn, so the model can fix them.
            rejected_drafts: the earlier drafts themselves, so the model sees exactly what not to write again.

        Returns:
            The validated draft, or None when the model gave nothing usable twice.
        """
        user = {**_context_json(ctx), "PLAN": {"operator": plan.operator.value, "decision": plan.decision.model_dump(mode="json")},
                "OPERATOR_GUIDANCE": self.prompts.operators.get(plan.operator, ""), "ASKS_IF_AI": asks_if_ai,
                "REJECTED_BECAUSE": [[f"{v.red_line.value}: {v.detail}. FIX: {REJECTION_FIX_HINTS.get(v.red_line.value, 'rewrite it')}" for v in vs]
                                     for vs in rejected],
                "REJECTED_DRAFTS": rejected_drafts or []}
        messages = [{"role": "system", "content": self.prompts.speak}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
        return self._ask(messages, SpokenDraft, "draft")

    def summarize(self, language: str, previous: str, lines: list[tuple[str, str]], facts: list[str]) -> MemoryUpdate | None:
        """Fold lines that have left the verbatim window into the running summary of the interview.

        Args:
            language: the interview language (the summary is written in it).
            previous: the summary so far ("" at first).
            lines: (speaker, text) of the lines to fold in, in order.
            facts: the key facts already remembered (the summary need not repeat them).

        Returns:
            The new summary, or None when this prompt version has no summary prompt or the model gave nothing usable
            (the caller keeps the old summary; the rule-based digest still covers the gap).

        Raises:
            ChatFatalError: the service refused (bad key, no balance, ...).
        """
        if not self.prompts.summarize:
            return None
        user = {"LANGUAGE": language, "PREVIOUS_SUMMARY": previous, "KEY_FACTS": facts, "NEW_LINES": [{"speaker": s, "text": t} for s, t in lines]}
        messages = [{"role": "system", "content": self.prompts.summarize}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
        return self._ask(messages, MemoryUpdate, "summary")

    def _ask(self, messages: list[ChatMessage], schema: type[Reply], what: str) -> Reply | None:
        """Call the model and validate; one more try on failure (MAX_PARSE_RETRIES); None after that (logged, not raised)."""
        for attempt in range(1 + MAX_PARSE_RETRIES):
            try:
                raw = self.chat.complete(messages, seed=self.seed)
            except ChatFatalError:
                raise  # no balance / bad key: not a bad reply to work around. It goes up to the safety net and stops an evaluation
            except ChatError:
                log.warning("moderator %s: model call failed (attempt %d)", what, attempt + 1, exc_info=True)
                continue
            try:
                return schema.model_validate(raw)
            except ValidationError as e:
                fields = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
                log.warning("moderator %s: reply does not match the schema (attempt %d), fields: %s", what, attempt + 1, ", ".join(fields) or "-")
                if schema is RespondentInputAnalysis and (salvaged := self._salvage_analysis(raw, fields)):
                    return salvaged
        log.error("moderator %s: no usable model output after %d attempts; falling back", what, 1 + MAX_PARSE_RETRIES)
        return None

    @staticmethod
    def _salvage_analysis(raw: dict, bad_fields: list[str]) -> RespondentInputAnalysis | None:
        """Keep an analysis whose only faults are a few invalid fields: those fall back to their neutral defaults.

        A small model often gets one field wrong (an unknown category, a score of 2) while the rest is useful; losing the
        whole reading would make the state machine miss real progress. Drafts are never salvaged: what is said must be whole.

        Returns:
            The analysis without the invalid fields, or None if it still does not validate.
        """
        cleaned = {k: v for k, v in raw.items() if k not in bad_fields}
        try:
            analysis = RespondentInputAnalysis.model_validate(cleaned)
        except ValidationError:
            return None
        log.info("moderator analysis: kept the reply without invalid fields %s", ", ".join(bad_fields))
        return analysis
