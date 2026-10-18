"""Per-turn pipeline of the AI moderator (task doc 4.2): listen -> judge -> choose -> speak -> check.

Safety comes first and never depends on the model: a stop request or crisis signal (rules in detectors.py, or the
model's own reading) ends the interview with a fixed statement. Every draft passes the red-line gate before it is said;
a failing draft is regenerated at most MAX_REGENERATIONS times, then a safe fixed sentence is said and staff are alerted.
When a human takes over, the AI never calls the model and never speaks (red line H11).
"""
import logging
import threading
import time

from .clock import Clock, SystemClock
from .constants import (ALERT_SERVICE_UNAVAILABLE, DRY_ANSWER_MAX_CHARS, DRY_ANSWER_MAX_WORDS, MAX_REGENERATIONS, NEW_FACTS_PER_TURN, RED_ALERT_AFTER_FAILED_TURNS, REMEMBER_MIN_CHARS, SUMMARY_RETRY_AFTER_TURNS, AlertLevel, ControlMode, EndReason, PacingAction, Phase,
                        QualityRule, ResponseCategory, Speaker, TransitionReason)
from .detectors import detect_asks_if_ai, detect_asks_model, detect_crisis, detect_mentions_ai, detect_ready, detect_stop
from .fsm import FOLLOWUP_PHASES, InterviewStateMachine, PacingDecision, TurnUpdate
from .guardrails import GuardrailContext, check_utterance
from .knowledge import InterviewGuide
from .memory import ConversationMemory, ModeratorContext
from .model_calls import ModelCaller
from .operators import choose_operator, counts_as_followup, wants_deeper
from .pacing import PacingConfig
from .repeats import repeats_earlier
from .prompts import PromptSet, load_prompts
from .providers import ChatFatalError, ModeratorChatProvider
from .schemas import DialogueActionPlan, ModeratorTurnPayload, RespondentInputAnalysis, Violation
from .templates import LanguageTemplates, templates_for

log = logging.getLogger(__name__)

# a human can take over at any time and hand back at any time; nothing else changes who speaks
CONTROL_TRANSITIONS: dict[ControlMode, frozenset[ControlMode]] = {ControlMode.AI: frozenset({ControlMode.HUMAN}),
                                                                  ControlMode.HUMAN: frozenset({ControlMode.AI})}


class EngineStateError(Exception):
    """An engine call that makes no sense now (e.g. opening twice, a participant turn after the end)."""


class ModeratorEngine:
    """Runs one AI-moderated interview, turn by turn.

    Args:
        guide: the project's guide (language must be en or zh in phase A).
        chat: the injected chat model (MockModeratorChat in tests).
        prompts: prompt version; the current one by default.
        config: pacing limits.
        clock: time source.
        seed: fixed model seed for repeatable eval runs.
        profile: the participant's screening answers ("question → answer"), given to the model as background every turn.
        background_memory: read facts out of each answer and refresh the summary in a background thread, so the reply never waits for them
            (the real rooms and evaluations; off in unit tests, whose scripted models count calls in order).

    Raises:
        ValueError: unsupported language, or the fixed intro breaks a red line (e.g. participant_topic too long).
    """

    def __init__(self, guide: InterviewGuide, chat: ModeratorChatProvider, prompts: PromptSet | None = None,
                 config: PacingConfig | None = None, clock: Clock | None = None, seed: int | None = None,
                 profile: list[str] | None = None, background_memory: bool = False) -> None:
        self.guide = guide
        self.texts: LanguageTemplates = templates_for(guide.language)
        self.clock = clock or SystemClock()
        self.fsm = InterviewStateMachine(guide, config, self.clock)
        self.memory = ConversationMemory(profile=profile)
        self.calls = ModelCaller(chat, prompts or load_prompts(), seed)
        self.control = ControlMode.AI
        self.turn_log: list[ModeratorTurnPayload] = []
        self._last_was_followup = False
        self._failed_turns_in_row = 0
        self._summary_retry_at = 0
        self.background_memory = background_memory
        self._memory_thread: threading.Thread | None = None
        self._intro = self.texts.intro.format(topic=guide.participant_topic or self.texts.default_topic)
        if found := check_utterance(self._intro, self._gate(is_first_turn=True)):
            raise ValueError(f"the fixed intro breaks a red line: {found[0].red_line.value} {found[0].detail}")
        self._check_fixed_sentences()
        self._check_prepared_questions()

    def _check_fixed_sentences(self) -> None:
        """The fixed answers are said word for word, so they must pass every red line themselves (H13 above all).

        Raises:
            ValueError: a fixed sentence breaks a red line (fix app/moderator/templates.py).
        """
        sep = "" if self.guide.language == "zh" else " "
        for sentence, asked_ai in ((self.texts.ai_answer + sep + self.texts.model_answer, True), (self.texts.model_answer, False),
                                   (self.texts.contact_reminder, False), (self.texts.conduct_reminder, False), (self.texts.conduct_end, False)):
            if found := check_utterance(sentence, self._gate(asked_ai=asked_ai, is_statement=True)):
                raise ValueError(f"a fixed sentence breaks a red line: {found[0].red_line.value} {found[0].detail}")

    def _check_prepared_questions(self) -> None:
        """Prepared questions are said word for word as the safe fallback, so each must pass every red line itself.

        Raises:
            ValueError: lists every prepared question that breaks a red line (fix the guide before the interview).
        """
        prepared = [*self.guide.warmup_questions, *[q for t in self.guide.topics for q in t.questions],
                    *[q for s in self.guide.stimuli for q in s.questions]]
        gate = self._gate()
        broken = [f"「{q}」{v.red_line.value} {v.detail}" for q in prepared for v in check_utterance(q, gate)]
        if broken:
            raise ValueError("prepared questions break red lines: " + "; ".join(broken))

    # ---------- public API ----------
    def open(self) -> ModeratorTurnPayload:
        """Start the interview: the fixed intro that discloses the AI (red line H3).

        Raises:
            EngineStateError: already opened.
        """
        if self.fsm.started:
            raise EngineStateError("interview already opened")
        self.fsm.start()
        return self._say(self._intro, analysis=None, plan=None)

    def handle_participant(self, text: str, signals: list[str] | None = None) -> ModeratorTurnPayload:
        """Process the participant's answer and produce the AI's next turn (or silence while a human is in control).

        Args:
            text: what the participant said (typed text, or a transcript in the voice version).
            signals: short hints about how it was said (app/signals), for the model only; the AI must never mention them (red line H14).

        Raises:
            EngineStateError: not opened yet, or the interview has ended.
            ValueError: blank text.
        """
        if not self.fsm.started or self.fsm.phase is Phase.ENDED:
            raise EngineStateError("interview is not running")
        started = time.monotonic()
        self.memory.add_turn(Speaker.PARTICIPANT, text, self._now())
        self.memory.signals = list(signals or [])[:3]
        self._schedule_memory(text, self._now())
        if self.control is ControlMode.HUMAN:
            return self._record(ModeratorTurnPayload(analysis=None, plan=None, spoken_response=None))  # H11: silent
        try:
            payload = self._respond(text)
        except Exception as error:  # noqa: BLE001 — a live interview must not die on an unexpected bug: say a safe sentence and call a human
            log.exception("moderator pipeline failed; saying a safe sentence and alerting staff")
            payload = self._internal_error_turn(error)
        return self._record(payload.model_copy(update={"latency_s": time.monotonic() - started}))

    def _internal_error_turn(self, error: Exception) -> ModeratorTurnPayload:
        """What is said when the pipeline itself broke: a fixed, checked neutral follow-up (never the same twice) and a red alert.

        The participant gets an ordinary sentence instead of silence, and every staff member online is told to take over.
        The evaluation counts these turns as errors, so the safety net can never hide a bug. When the cause is the model service
        refusing us (no balance, bad key), staff are told exactly that, and the evaluation stops instead of carrying on."""
        candidates = [self.texts.followup, *self.texts.alt_followups]
        sentence = next((c for c in candidates if not self._repeat_violations(c)), candidates[0])
        fatal = isinstance(error, ChatFatalError)
        reason = ALERT_SERVICE_UNAVAILABLE.format(reason=error.reason) if fatal else "AI 内部出错，已改说安全句，请立即接管并通知技术人员"
        return self._say(sentence, None, None, used_fallback=True, internal_error=True, fatal_service_error=fatal, alert=AlertLevel.RED,
                         alert_reason=reason)

    def add_whisper(self, text: str) -> None:
        """An observer's private note for the next turn (never shown to the participant)."""
        self.memory.add_whisper(text, self._now())

    def take_over(self) -> None:
        """A staff member takes over: from now on the AI stays silent (red line H11).

        Raises:
            EngineStateError: a human is already in control.
        """
        self._switch(ControlMode.HUMAN)

    def hand_back(self) -> None:
        """The staff member hands back; the AI continues, reading what was said meanwhile from memory.

        Raises:
            EngineStateError: the AI is already in control.
        """
        self._switch(ControlMode.AI)

    def add_human_turn(self, text: str) -> None:
        """What the human moderator said while in control (kept in memory so the AI can continue from it)."""
        self.memory.add_turn(Speaker.HUMAN, text, self._now())

    # ---------- the pipeline ----------
    def _respond(self, text: str) -> ModeratorTurnPayload:
        """Safety first, then judge -> choose -> speak -> check."""
        asks_ai = detect_asks_if_ai(text)  # by rule first: an honest answer (H3) is owed on every path, also when the interview ends
        if detect_crisis(text):
            return self._end_with(EndReason.CRISIS, None, asks_ai)
        if detect_stop(text):
            return self._end_with(EndReason.PARTICIPANT_STOPPED, None, asks_ai)
        if detect_asks_model(text):
            return self._answer_about_the_ai(text)  # what the AI is made of is never the model's to say: a fixed sentence, then the interview goes on
        if not self.background_memory:
            self._refresh_summary()
        analysis = self._analyze(text)
        self.memory.add_facts(analysis.new_facts[:NEW_FACTS_PER_TURN], self._now())
        asks_ai = asks_ai or (analysis.asks_if_ai and detect_mentions_ai(text))  # the model's flag only counts when these words are about AI
        if analysis.category is ResponseCategory.CRISIS:
            return self._end_with(EndReason.CRISIS, analysis, asks_ai)
        if analysis.category is ResponseCategory.WANTS_TO_STOP:
            return self._end_with(EndReason.PARTICIPANT_STOPPED, analysis, asks_ai)
        decision = self._decide(analysis, text)
        if decision.action is PacingAction.END:
            return self._say(self._with_ai_answer(self.texts.closing, asks_ai), analysis=analysis, plan=None, is_statement=True)
        choice = choose_operator(analysis, decision, text)
        plan = DialogueActionPlan(operator=choice.operator, decision=decision, deep_dive_score=choice.deep_dive_score, deep_dive=choice.deep_dive)
        return self._speak(analysis, plan, asks_ai)

    def _with_ai_answer(self, statement: str, asks_ai: bool) -> str:
        """Put the honest "I am an AI" answer in front of a fixed statement when the participant asked (red line H3)."""
        if not asks_ai:
            return statement
        return self.texts.ai_answer + ("" if self.guide.language == "zh" else " ") + statement

    def _analyze(self, text: str) -> RespondentInputAnalysis:
        """The model's reading of the answer, or a neutral reading if the model failed (logged in model_calls)."""
        snapshot = self.fsm.snapshot()
        ctx = self.memory.build_context(self.guide, snapshot, self._peek_decision(), consume_whispers=False, recall_query=text)  # whispers are for the speaking call
        uncovered = {i.id: i.text for i in self.fsm.missed_items()}
        analysis = self.calls.analyze(ctx, uncovered, text)
        return analysis or RespondentInputAnalysis(saturation=snapshot.saturation)

    def _decide(self, analysis: RespondentInputAnalysis, text: str) -> PacingDecision:
        """Hand the reading to the state machine (the intro waits for the participant's go-ahead, by model or by rule)."""
        if self.fsm.phase is Phase.INTRO:
            ready = analysis.ready_to_start or detect_ready(text)
            return self.fsm.participant_ready() if ready else self.fsm.record_turn(TurnUpdate())
        # Only a real answer finishes a question: a model that says "too short" or "off topic" and "answered" in one breath is overruled.
        answered = analysis.question_answered and analysis.category is ResponseCategory.ANSWERED and not self._dig_deeper(analysis)
        update = TurnUpdate(was_followup=self._last_was_followup, saturation=analysis.saturation,
                            covered_item_ids=analysis.covered_item_ids, question_answered=answered,
                            information_gain=analysis.signals.information_gain, dry=self._is_dry(analysis, text))
        return self.fsm.record_turn(update)

    def _is_dry(self, analysis: RespondentInputAnalysis, text: str) -> bool:
        """Whether the answer is short, adds almost nothing and covers no item: the participant has nothing more to say here.

        Length is part of the test on purpose: a long answer that adds nothing (dodging, counter-questions) is a different
        problem with its own operator (redirect), and the model's information score alone is too noisy to end a topic on."""
        zh = self.guide.language == "zh"
        short = len(text.strip()) < DRY_ANSWER_MAX_CHARS if zh else len(text.split()) < DRY_ANSWER_MAX_WORDS
        return short and analysis.signals.information_gain < self.fsm.config.dry_answer_gain and not analysis.covered_item_ids

    def _dig_deeper(self, analysis: RespondentInputAnalysis) -> bool:
        """Stay on this question for another follow-up instead of moving on (the ladder, task doc 4.3).

        The answer must be worth digging into (operators.wants_deeper), a follow-up must still be allowed on this question
        (this answer may itself have been one), and the topic must still have time (the state machine moves on when it does not).
        """
        snapshot = self.fsm.snapshot()
        used = snapshot.followups_used + int(self._last_was_followup)
        has_room = used < self.fsm.config.max_followups_per_question
        topic_has_time = snapshot.phase is not Phase.CORE or snapshot.topic_elapsed_s < snapshot.topic_budget_s
        return wants_deeper(analysis) and self.fsm.phase in FOLLOWUP_PHASES and has_room and topic_has_time and not self.fsm.hurrying()

    def _speak(self, analysis: RespondentInputAnalysis, plan: DialogueActionPlan, asks_ai: bool) -> ModeratorTurnPayload:
        """Draft, check, regenerate up to MAX_REGENERATIONS times; otherwise the safe fallback sentence and an alert."""
        ctx = self._context(plan.decision)
        gate = self._gate(asked_ai=asks_ai)
        rejected: list[list[Violation]] = []
        rejected_drafts: list[str] = []
        for _ in range(1 + MAX_REGENERATIONS):
            draft = self.calls.draft(ctx, plan, asks_ai, rejected, rejected_drafts)
            if draft is None:
                break  # the model gave nothing usable: regenerating cannot help
            found = check_utterance(draft.spoken_response, gate) + self._repeat_violations(draft.spoken_response)
            if not found:
                self._failed_turns_in_row = 0
                self._last_was_followup = counts_as_followup(plan.operator)
                return self._say(draft.spoken_response, analysis, plan, note=draft.note, attempts=len(rejected) + 1, rejected=rejected,
                                 rejected_drafts=rejected_drafts)
            log.warning("moderator draft rejected: %s", ", ".join(v.red_line.value for v in found))
            rejected.append(found)
            rejected_drafts.append(draft.spoken_response)
        return self._fallback(analysis, plan, asks_ai, rejected, rejected_drafts)

    def _fallback(self, analysis: RespondentInputAnalysis, plan: DialogueActionPlan, asks_ai: bool,
                  rejected: list[list[Violation]], rejected_drafts: list[str]) -> ModeratorTurnPayload:
        """Say a fixed, checked sentence instead (never a violating one) and alert staff; red after repeated failures."""
        self._failed_turns_in_row += 1
        self._last_was_followup = False
        gate = self._gate(asked_ai=asks_ai)
        prefix = self.texts.ai_answer + ("" if self.guide.language == "zh" else " ") if asks_ai else ""
        sentence = self._safe_sentence(plan.decision, prefix, gate)
        red = self._failed_turns_in_row >= RED_ALERT_AFTER_FAILED_TURNS
        reason = (f"AI 连续 {self._failed_turns_in_row} 轮没通过红线检查，建议接管" if red else "AI 这一轮没通过红线检查，已改用安全兜底句")
        return self._say(sentence, analysis, plan, attempts=len(rejected), rejected=rejected, rejected_drafts=rejected_drafts, used_fallback=True,
                         alert=AlertLevel.RED if red else AlertLevel.YELLOW, alert_reason=reason)

    def _safe_sentence(self, decision: PacingDecision, prefix: str, gate: GuardrailContext) -> str:
        """A fixed, checked sentence (the prefix, then the question the state machine wants or a neutral follow-up), a fresh one if there is one.

        Raises:
            RuntimeError: every fixed sentence breaks a red line (fix app/moderator/templates.py).
        """
        candidates = [prefix + q for q in (self._fallback_question(decision), self.texts.followup, *self.texts.alt_followups)]
        clean = [c for c in candidates if not check_utterance(c, gate)]
        if not clean:
            raise RuntimeError("the fixed fallback sentence breaks a red line: fix app/moderator/templates.py")
        return next((c for c in clean if not self._repeat_violations(c)), clean[0])

    def _answer_about_the_ai(self, text: str) -> ModeratorTurnPayload:
        """The participant asked what model, company or technology the AI is, or tried to read its instructions (red line H13).

        The answer is fixed: it says honestly that this is an AI (H3), that nothing more will be shared, and goes back to the interview.
        No model is called, so nothing about the model can leak, and the turn counts as an ordinary neutral turn for the pacing.
        """
        analysis = RespondentInputAnalysis(saturation=self.fsm.snapshot().saturation)
        decision = self._decide(analysis, text)
        if decision.action is PacingAction.END:
            return self._say(self._with_ai_answer(self.texts.closing, True), analysis=analysis, plan=None, is_statement=True)
        sep = "" if self.guide.language == "zh" else " "
        prefix = self.texts.ai_answer + sep + self.texts.model_answer + sep
        self._last_was_followup = False
        return self._say(self._safe_sentence(decision, prefix, self._gate(asked_ai=True)), analysis=analysis, plan=None)

    def moderation_reply(self, text: str, kind: str, ending: bool = False) -> ModeratorTurnPayload:
        """A message in which the rule library hid contact details or abusive / hateful words (``text`` is the already hidden version).

        The reply is fixed, never the model's: a short reminder and then the interview goes on; or, when ``ending``, a polite closing statement.
        The model is not called, so nothing in the message can steer it, and the turn counts as a neutral turn for the pacing.

        Args:
            text: what the participant typed, with the hidden parts already replaced.
            kind: "contact" or "conduct".
            ending: the interview ends here (repeated abuse or hate).

        Raises:
            EngineStateError: not opened yet, or already ended.
            ValueError: blank text.
        """
        if not self.fsm.started or self.fsm.phase is Phase.ENDED:
            raise EngineStateError("interview is not running")
        started = time.monotonic()
        self.memory.add_turn(Speaker.PARTICIPANT, text, self._now())
        if self.control is ControlMode.HUMAN:
            return self._record(ModeratorTurnPayload(analysis=None, plan=None, spoken_response=None))  # H11: silent while a human is in control
        if ending:
            self.fsm.stop(EndReason.CONDUCT)
            payload = self._say(self.texts.conduct_end, None, None, is_statement=True)
        else:
            analysis = RespondentInputAnalysis(saturation=self.fsm.snapshot().saturation)
            decision = self._decide(analysis, text)
            sep = "" if self.guide.language == "zh" else " "
            prefix = (self.texts.contact_reminder if kind == "contact" else self.texts.conduct_reminder) + sep
            self._last_was_followup = False
            payload = self._say(self._safe_sentence(decision, prefix, self._gate()), analysis=analysis, plan=None)
        return self._record(payload.model_copy(update={"latency_s": time.monotonic() - started}))

    def _fallback_question(self, decision: PacingDecision) -> str:
        """The prepared question, missed item or wrap-up question the state machine asked for; else a neutral follow-up."""
        if decision.question:
            return decision.question
        if decision.missed_item:
            return self.texts.missed_item.format(item=decision.missed_item.text)
        if decision.phase is Phase.WRAPUP:
            return self.texts.wrapup
        return self.texts.followup

    def _end_with(self, reason: EndReason, analysis: RespondentInputAnalysis | None, asks_ai: bool = False) -> ModeratorTurnPayload:
        """Stop at once with the fixed statement (H5), answering honestly first if the participant asked whether this is an AI (H3).
        A crisis raises a red alert to every staff member online."""
        self.fsm.stop(reason)
        crisis = reason is EndReason.CRISIS
        text = self._with_ai_answer(self.texts.crisis if crisis else self.texts.stop, asks_ai)
        return self._say(text, analysis, None, is_statement=True, alert=AlertLevel.RED if crisis else AlertLevel.NONE,
                         alert_reason="受访者出现危机信号，访谈已停止，请立即跟进" if crisis else "")

    # ---------- helpers ----------
    def _say(self, text: str, analysis: RespondentInputAnalysis | None, plan: DialogueActionPlan | None, **fields: object) -> ModeratorTurnPayload:
        """Record what the AI says in memory and build the turn payload."""
        self.memory.add_turn(Speaker.AI, text, self._now())
        return ModeratorTurnPayload(analysis=analysis, plan=plan, spoken_response=text, **fields)

    def _record(self, payload: ModeratorTurnPayload) -> ModeratorTurnPayload:
        """Keep the turn for the log and the eval."""
        self.turn_log.append(payload)
        return payload

    def _context(self, decision: PacingDecision) -> ModeratorContext:
        """This turn's context for the model (consumes queued whispers); older lines about what is being discussed come back word for word."""
        latest = next((t.text for t in reversed(self.memory.turns) if t.speaker is Speaker.PARTICIPANT), "")
        about = " ".join(x for x in (latest, decision.question or "", decision.missed_item.text if decision.missed_item else "") if x)
        return self.memory.build_context(self.guide, self.fsm.snapshot(), decision, recall_query=about)

    def _schedule_memory(self, text: str, at_s: float) -> None:
        """Start the background memory work for this answer, after any earlier work has finished (one at a time, in order)."""
        if not self.background_memory:
            return
        previous = self._memory_thread
        thread = threading.Thread(target=self._memory_job, args=(previous, text, at_s), daemon=True, name="moderator-memory")
        self._memory_thread = thread
        thread.start()

    def flush_memory(self) -> None:
        """Wait until the background memory work has caught up (tests, and the end of an evaluation session)."""
        thread = self._memory_thread
        if thread is not None:
            thread.join()

    def _memory_job(self, previous: threading.Thread | None, text: str, at_s: float) -> None:
        """Facts from this answer, then the summary if it is due. Never raises: the reply does not depend on it, and a model failure
        here shows up on the next reply's own call (ChatFatalError) anyway."""
        if previous is not None:
            previous.join()
        try:
            if self.calls.prompts.remember and len(text.strip()) >= REMEMBER_MIN_CHARS:
                note = self.calls.remember(self.guide.language, [f for _, f in self.memory.facts], self.memory.profile, text)
                if note:
                    self.memory.add_facts(note.new_facts, at_s)
            self._refresh_summary()
        except Exception:  # noqa: BLE001 - a background helper must not die loudly into the interview
            log.exception("moderator memory update failed (the interview carries on without it)")

    def _refresh_summary(self) -> None:
        """Every SUMMARY_EVERY_TURNS lines that leave the verbatim window, fold them into the running summary (one extra model call).

        A failed call changes nothing: the rule-based digest in memory.history_digest still covers those lines, and the next turn tries again."""
        if not self.memory.summary_due() or len(self.memory.turns) < self._summary_retry_at:
            return
        old = self.memory.turns_to_summarize()
        update = self.calls.summarize(self.guide.language, self.memory.summary, [(t.speaker.value, t.text) for t in old],
                                      [f for _, f in self.memory.facts])
        if update:
            self.memory.set_summary(update.summary, old[-1].index + 1)
        else:
            self._summary_retry_at = len(self.memory.turns) + SUMMARY_RETRY_AFTER_TURNS  # do not spend two failing calls on every turn

    def _peek_decision(self) -> PacingDecision:
        """A read-only "where are we" decision, used for the analysis call before the state machine moves."""
        reason = self.fsm.transitions[-1].reason if self.fsm.transitions else TransitionReason.STARTED
        return PacingDecision(action=PacingAction.CONTINUE, phase=self.fsm.phase, reason=reason, followup_allowed=self.fsm.followup_allowed())

    def _repeat_violations(self, text: str) -> list[Violation]:
        """A question the AI already asked in this interview is not asked again (eval gate: 0 repeats)."""
        if repeats_earlier(text, self.memory.asked_questions()):
            return [Violation(red_line=QualityRule.Q1_REPEAT, detail="repeats an earlier question")]
        return []

    def _gate(self, is_first_turn: bool = False, asked_ai: bool = False, is_statement: bool = False) -> GuardrailContext:
        """Facts for the red-line gate."""
        return GuardrailContext(guide=self.guide, is_first_turn=is_first_turn, is_statement=is_statement, participant_asked_ai=asked_ai, control=self.control)

    def _switch(self, to: ControlMode) -> None:
        """Change who speaks for the team, through the single control gate.

        Raises:
            EngineStateError: the switch is not allowed (already in that mode).
        """
        if to not in CONTROL_TRANSITIONS[self.control]:
            raise EngineStateError(f"control is already {self.control.value}")
        log.info("moderator control %s -> %s", self.control.value, to.value)
        self.control = to
        self._last_was_followup = False

    def _now(self) -> float:
        """Seconds since the interview started (0 before)."""
        return self.fsm.snapshot().elapsed_s
