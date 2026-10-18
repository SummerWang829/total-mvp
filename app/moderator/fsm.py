"""Interview state machine: phases, time budget, saturation, follow-up cap, forced wrap-up, missed must-cover items.

Code decides the flow (task doc 4.3); the model only supplies scores (saturation 0-1, which items an answer covered,
whether the prepared question is answered). Every phase change goes through `can_transition` (Quality Charter: FSM first).

Typical use per live turn:
    fsm.start()                       -> ENTER_PHASE intro
    fsm.participant_ready()           -> ENTER_PHASE warmup + first warm-up question
    fsm.record_turn(TurnUpdate(...))  -> what to do next (continue / next question / next topic / ask missed item / end)
    fsm.stop(EndReason.CRISIS)        -> ended at once
"""
import logging

from pydantic import BaseModel, ConfigDict, Field

from .clock import Clock, SystemClock
from .constants import EndReason, PacingAction, Phase, TransitionReason
from .knowledge import GuideTopic, InterviewGuide, MustCoverItem
from .pacing import PacingConfig, TimeBudget

log = logging.getLogger(__name__)

# Every legal phase change. WRAPUP is reachable from any running phase (forced wrap-up on overtime);
# ENDED from any running phase (participant asks to stop, or a crisis signal).
ALLOWED_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.INTRO: frozenset({Phase.WARMUP, Phase.WRAPUP, Phase.ENDED}),
    Phase.WARMUP: frozenset({Phase.CORE, Phase.WRAPUP, Phase.ENDED}),
    Phase.CORE: frozenset({Phase.CORE, Phase.STIMULI, Phase.RECOVERY, Phase.WRAPUP, Phase.ENDED}),  # CORE -> CORE: next topic
    Phase.STIMULI: frozenset({Phase.RECOVERY, Phase.WRAPUP, Phase.ENDED}),
    Phase.RECOVERY: frozenset({Phase.WRAPUP, Phase.ENDED}),
    Phase.WRAPUP: frozenset({Phase.ENDED}),
    Phase.ENDED: frozenset(),
}
WRAPUP_RESERVE_EXCHANGES = 2  # the wrap-up is a closing question and its answer
QUESTION_LIST_PHASES = frozenset({Phase.WARMUP, Phase.STIMULI})
FOLLOWUP_PHASES = frozenset({Phase.WARMUP, Phase.CORE, Phase.STIMULI})


class IllegalTransitionError(Exception):
    """A phase change that ALLOWED_TRANSITIONS does not permit (a bug in the caller, never silently ignored)."""


class InterviewStateError(Exception):
    """An action that makes no sense in the current state, e.g. recording a turn before start() or after the end."""


class TurnUpdate(BaseModel):
    """What the engine learned from the participant's latest answer (task doc 4.2 "judge"); model output, validated here.

    Attributes:
        was_followup: the moderator's question that this answers was a follow-up (counts against the cap of 3).
        saturation: the model's 0-1 score for how fully the current topic is covered.
        covered_item_ids: must-cover items this answer covered (any topic; unknown ids are ignored and logged).
        question_answered: the engine judged the current prepared question answered; no deep-dive needed.
    """

    model_config = ConfigDict(frozen=True)

    was_followup: bool = False
    saturation: float = Field(default=0.0, ge=0, le=1)
    covered_item_ids: list[str] = Field(default_factory=list)
    question_answered: bool = False
    information_gain: float = Field(default=0.0, ge=0, le=1)  # how much new information the answer added (decides whether exploring goes on)
    dry: bool = False  # short, nothing new, nothing covered: the participant has nothing more to say here (set by the engine)


class PacingDecision(BaseModel):
    """The state machine's instruction for the next moderator turn."""

    model_config = ConfigDict(frozen=True)

    action: PacingAction
    phase: Phase
    reason: TransitionReason
    topic_id: str | None = None
    question: str | None = None            # the prepared question now due (NEXT_QUESTION / NEXT_TOPIC / ENTER_PHASE)
    missed_item: MustCoverItem | None = None
    followup_allowed: bool = False
    end_reason: EndReason | None = None
    explore_index: int | None = None       # EXPLORE only: which extra question of the topic this is (0, 1, ...); picks the operator


class PacingSnapshot(BaseModel):
    """The clock and position the model sees every turn (task doc 4.3: "AI 每轮能看到")."""

    model_config = ConfigDict(frozen=True)

    phase: Phase
    elapsed_s: float
    remaining_s: float
    total_s: float
    phase_elapsed_s: float
    phase_budget_s: float
    topic_id: str | None
    topic_title: str | None
    topic_elapsed_s: float
    topic_budget_s: float
    saturation: float
    followups_used: int
    followups_left: int
    uncovered_items: list[str]
    overtime: bool


class TransitionRecord(BaseModel):
    """One phase change, for the turn log and the eval's "过渡时机正确率"."""

    model_config = ConfigDict(frozen=True)

    at_s: float
    from_phase: Phase
    to_phase: Phase
    reason: TransitionReason
    topic_id: str | None = None


class TopicProgress(BaseModel):
    """Mutable progress of one core topic.

    Attributes:
        probing_item_id: the must-cover item being explored after the prepared questions ran out (None while on them).
    """

    topic_id: str
    question_index: int = 0
    turns: int = 0
    saturation: float = 0.0
    started_at_s: float | None = None
    probing_item_id: str | None = None
    budget_s: float = 0.0           # this topic's time, set when it starts: time the earlier topics did not use is shared out
    explore_count: int = 0          # extra deeper questions asked in this topic
    last_info_gain: float = 1.0     # information the latest answer added (1.0 before any answer: nothing argues against exploring)


class InterviewStateMachine:
    """Drives one interview through the phases.

    Args:
        guide: the project's validated discussion guide.
        config: pacing limits; defaults from the task doc.
        clock: time source; SystemClock live, ManualClock in tests and simulations.
    """

    def __init__(self, guide: InterviewGuide, config: PacingConfig | None = None, clock: Clock | None = None) -> None:
        self.guide = guide
        self.config = config or PacingConfig()
        self.clock = clock or SystemClock()
        self.budget = TimeBudget.for_guide(guide)
        self.transitions: list[TransitionRecord] = []
        self.end_reason: EndReason | None = None
        self._phase = Phase.INTRO
        self._started_at: float | None = None
        self._phase_started_at = 0.0
        self._question_index = 0
        self._followups = 0
        self._topic_index = 0
        self._progress = {t.id: TopicProgress(topic_id=t.id) for t in guide.topics}
        self._covered: set[str] = set()
        self._asked_items: set[str] = set()  # items already asked about (probe or recovery): never asked twice
        self._known_items = {i.id for t in guide.topics for i in t.must_cover}
        self._stimulus_questions = [q for s in guide.stimuli for q in s.questions]
        self._recovery_queue: list[MustCoverItem] = []
        self._core_used_s = 0.0  # time the finished core topics actually used (the rest is shared out, see _topic_budget_now)
        self._answers = 0            # answers taken so far (the average length of an exchange sets how much time the wrap-up needs)
        self._dry_streak = 0         # empty answers in a row since the phase / topic began
        self._last_try_used = False  # the one fresh-angle question of this phase / topic has been asked

    # ---------- the transition gate ----------
    @staticmethod
    def can_transition(from_phase: Phase, to_phase: Phase) -> bool:
        """Whether moving from one phase to another is legal (ALLOWED_TRANSITIONS)."""
        return to_phase in ALLOWED_TRANSITIONS[from_phase]

    def _transition(self, to_phase: Phase, reason: TransitionReason) -> None:
        """The only place the phase changes.

        Raises:
            IllegalTransitionError: the move is not in ALLOWED_TRANSITIONS.
        """
        if not self.can_transition(self._phase, to_phase):
            raise IllegalTransitionError(f"{self._phase.value} -> {to_phase.value} ({reason.value})")
        now = self._elapsed()
        self.transitions.append(TransitionRecord(at_s=now, from_phase=self._phase, to_phase=to_phase, reason=reason,
                                                 topic_id=self.current_topic.id if to_phase is Phase.CORE else None))
        log.info("moderator phase %s -> %s (%s) at %.0fs", self._phase.value, to_phase.value, reason.value, now)
        self._phase = to_phase
        self._phase_started_at = self.clock.now()
        self._question_index = 0
        self._followups = 0
        self._dry_streak = 0
        self._last_try_used = False

    # ---------- public state ----------
    @property
    def phase(self) -> Phase:
        """The current phase."""
        return self._phase

    @property
    def current_stimulus_id(self) -> str | None:
        """The id of the material the current question is about; None outside the STIMULI phase."""
        if self._phase is not Phase.STIMULI:
            return None
        left = self._question_index
        for s in self.guide.stimuli:
            if left < len(s.questions):
                return s.id
            left -= len(s.questions)
        return None

    @property
    def started(self) -> bool:
        """Whether start() has been called."""
        return self._started_at is not None

    @property
    def current_topic(self) -> GuideTopic:
        """The core topic in progress (or next due, before the core phase)."""
        return self.guide.topics[self._topic_index]

    def followup_allowed(self) -> bool:
        """Whether one more follow-up on the current question is allowed (hard cap per question, task doc 4.3)."""
        return self._phase in FOLLOWUP_PHASES and self._followups < self.config.max_followups_per_question

    @property
    def covered_items(self) -> frozenset[str]:
        """Must-cover item ids covered so far (only ids that exist in the guide)."""
        return frozenset(self._covered)

    @property
    def asked_items(self) -> frozenset[str]:
        """Must-cover item ids the moderator has asked about on purpose (probe or recovery), whether or not the answer covered them."""
        return frozenset(self._asked_items)

    def hurrying(self) -> bool:
        """Whether the current core topic is running short of time while must-cover items are still unasked.

        True once the topic has used `hurry_after_share` of its time and some of its must-cover items are neither covered nor
        asked about yet (a talkative participant can use up a topic's time on the first questions). The moderator then stops
        following up and goes to the items, so they are asked before the topic's time runs out."""
        if self._phase is not Phase.CORE:
            return False
        progress = self._progress[self.current_topic.id]
        unasked = [i for i in self.current_topic.must_cover if i.id not in self._covered and i.id not in self._asked_items]
        return bool(unasked) and self._topic_elapsed() >= progress.budget_s * self.config.hurry_after_share

    def missed_items(self) -> list[MustCoverItem]:
        """Must-cover items not covered yet, in guide order."""
        return [i for t in self.guide.topics for i in t.must_cover if i.id not in self._covered]

    def snapshot(self) -> PacingSnapshot:
        """The clock and position, for the model's context."""
        elapsed = self._elapsed()
        in_core = self._phase is Phase.CORE
        topic = self.current_topic if in_core else None
        progress = self._progress[topic.id] if topic else None
        return PacingSnapshot(
            phase=self._phase, elapsed_s=elapsed, remaining_s=max(0.0, self.budget.total_s - elapsed), total_s=self.budget.total_s,
            phase_elapsed_s=self._phase_elapsed(), phase_budget_s=self.budget.phase_s.get(self._phase, 0.0),
            topic_id=topic.id if topic else None, topic_title=topic.title if topic else None,
            topic_elapsed_s=self._topic_elapsed() if topic else 0.0, topic_budget_s=progress.budget_s if progress else 0.0,
            saturation=progress.saturation if progress else 0.0, followups_used=self._followups,
            followups_left=max(0, self.config.max_followups_per_question - self._followups) if self._phase in FOLLOWUP_PHASES else 0,
            uncovered_items=[i.text for i in topic.must_cover if i.id not in self._covered] if topic else [],
            overtime=self._overtime())

    # ---------- events ----------
    def start(self) -> PacingDecision:
        """Begin the interview in INTRO (the engine discloses the AI, purpose, recording and privacy).

        Raises:
            InterviewStateError: already started.
        """
        if self.started:
            raise InterviewStateError("interview already started")
        self._started_at = self._phase_started_at = self.clock.now()
        log.info("moderator interview started, budget %.0fs", self.budget.total_s)
        return self._decision(PacingAction.ENTER_PHASE, TransitionReason.STARTED)

    def participant_ready(self) -> PacingDecision:
        """The participant confirmed they are ready: leave INTRO for the first warm-up question.

        Raises:
            InterviewStateError: not in INTRO, or not started.
        """
        self._require_running()
        if self._phase is not Phase.INTRO:
            raise InterviewStateError(f"participant_ready only in intro, not {self._phase.value}")
        self._transition(Phase.WARMUP, TransitionReason.PARTICIPANT_READY)
        return self._decision(PacingAction.ENTER_PHASE, TransitionReason.PARTICIPANT_READY, question=self.guide.warmup_questions[0])

    def record_turn(self, update: TurnUpdate) -> PacingDecision:
        """Take in the engine's reading of the latest answer and decide the next step.

        Args:
            update: scores for the answer just given.

        Returns:
            What the moderator should do next.

        Raises:
            InterviewStateError: not started, or already ended.
        """
        self._require_running()
        self._apply(update)
        if self._overtime() and self._phase is not Phase.WRAPUP:
            return self._enter_wrapup(TransitionReason.OVERTIME)  # missed items are not chased when time is gone
        handler = {Phase.INTRO: self._on_intro, Phase.WARMUP: self._on_question_list, Phase.CORE: self._on_core,
                   Phase.STIMULI: self._on_question_list, Phase.RECOVERY: self._on_recovery, Phase.WRAPUP: self._on_wrapup}[self._phase]
        return handler(update)

    def stop(self, reason: EndReason) -> PacingDecision:
        """End at once (red line H5: the participant wants to stop, or a crisis signal).

        Raises:
            InterviewStateError: not started, or already ended.
        """
        self._require_running()
        why = {EndReason.CRISIS: TransitionReason.CRISIS, EndReason.PARTICIPANT_STOPPED: TransitionReason.PARTICIPANT_STOPPED,
               EndReason.CONDUCT: TransitionReason.PARTICIPANT_STOPPED}.get(
            reason, TransitionReason.WRAPUP_ANSWERED)
        return self._end(reason, why)

    # ---------- per-phase rules ----------
    def _on_intro(self, update: TurnUpdate) -> PacingDecision:
        """INTRO ends only through participant_ready(): the participant must say they are ready."""
        return self._decision(PacingAction.CONTINUE, TransitionReason.IN_PROGRESS)

    def _on_question_list(self, update: TurnUpdate) -> PacingDecision:
        """WARMUP and STIMULI: prepared questions in order, each with up to N follow-ups, within the phase budget."""
        questions = self._phase_questions()
        if self._phase_elapsed() >= self.budget.phase_s[self._phase]:
            return self._leave_question_list(TransitionReason.PHASE_TIME_UP)
        if self._dry_out():
            return self._leave_question_list(TransitionReason.NO_NEW_INFO)
        if self._last_try_due():
            return self._last_try()
        done = self._question_done(update)
        if done is None:
            return self._decision(PacingAction.CONTINUE, TransitionReason.IN_PROGRESS)
        if self._question_index + 1 >= len(questions):
            return self._leave_question_list(TransitionReason.QUESTIONS_DONE)
        self._next_question()
        return self._decision(PacingAction.NEXT_QUESTION, done, question=questions[self._question_index])

    def _on_core(self, update: TurnUpdate) -> PacingDecision:
        """CORE: leave the topic at its turn limit or out of its time; when saturated or out of prepared questions, dig deeper
        while the topic still has time and the answers still add information, then leave; otherwise walk its prepared
        questions, then probe its still-uncovered must-cover items one by one."""
        progress = self._progress[self.current_topic.id]
        if progress.turns >= self.config.max_turns_per_topic:
            return self._next_topic(TransitionReason.TOPIC_TURN_LIMIT)
        if self._topic_elapsed() >= progress.budget_s:
            return self._next_topic(TransitionReason.TOPIC_TIME_UP)
        if progress.saturation >= self.config.saturation_threshold:
            return self._explore_or_leave(progress, TransitionReason.SATURATED)
        if self._dry_out():
            return self._next_topic(TransitionReason.NO_NEW_INFO)
        if self._last_try_due():
            progress.probing_item_id = None
            return self._last_try()
        done = self._question_done(update)
        if progress.probing_item_id in self._covered:
            done = TransitionReason.ITEM_COVERED
        if self.hurrying():  # running short of time with must-cover items unasked: they come before more follow-ups and prepared questions
            return self._probe_next_item(progress, TransitionReason.BEHIND_SCHEDULE)
        if done is None:
            return self._decision(PacingAction.CONTINUE, TransitionReason.IN_PROGRESS)
        if progress.probing_item_id is None and progress.question_index + 1 < len(self.current_topic.questions):
            self._next_question()  # resets the follow-up count; the topic keeps its own place in its prepared questions
            progress.question_index += 1  # (not self._question_index: probes and explore questions also advance that one, which ran past the list)
            return self._decision(PacingAction.NEXT_QUESTION, done, question=self.current_topic.questions[progress.question_index])
        return self._probe_next_item(progress, done)

    def _last_try_due(self) -> bool:
        """Whether the last several answers were all empty and the one fresh-angle question has not been asked yet."""
        return self._dry_streak >= self.config.dry_streak_limit and not self._last_try_used

    def _dry_out(self) -> bool:
        """Whether even the fresh-angle question brought nothing: time to move on (the caller leaves the topic or list)."""
        return self._dry_streak >= self.config.dry_streak_limit and self._last_try_used

    def _last_try(self) -> PacingDecision:
        """Ask one more question from a fresh angle (a new question: its follow-up count starts again), once per topic / list phase."""
        self._last_try_used = True
        self._next_question()
        return self._decision(PacingAction.LAST_TRY, TransitionReason.LAST_TRY)

    def _probe_next_item(self, progress: TopicProgress, reason: TransitionReason) -> PacingDecision:
        """Explore the topic's next uncovered, never-asked item; when none is left the topic is done.

        Args:
            progress: the current topic's progress.
            reason: why the previous question or item is finished (reported with the new probe).
        """
        item = next((i for i in self.current_topic.must_cover if i.id not in self._covered and i.id not in self._asked_items), None)
        if item is None:
            return self._explore_or_leave(progress, TransitionReason.QUESTIONS_DONE)
        self._next_question()
        progress.probing_item_id = item.id
        self._asked_items.add(item.id)
        return self._decision(PacingAction.PROBE_ITEM, reason, missed_item=item)

    def _can_explore(self, progress: TopicProgress) -> bool:
        """Whether one more deeper question is worth asking in this topic.

        Yes while: exploring is switched on and not used up for this topic; the topic has used less than `explore_until_share`
        of its time; the turn limit is not reached; and the latest answer still added information (diminishing returns:
        the moderator does not pad the time with questions that no longer get anything new)."""
        cfg = self.config
        return (cfg.max_explore_per_topic > 0 and progress.explore_count < cfg.max_explore_per_topic
                and self._topic_elapsed() < progress.budget_s * cfg.explore_until_share
                and progress.turns < cfg.max_turns_per_topic
                and progress.last_info_gain >= cfg.explore_min_info_gain)

    def _explore_or_leave(self, progress: TopicProgress, reason: TransitionReason) -> PacingDecision:
        """The topic has what it needs: dig deeper if that is still worthwhile, otherwise move on."""
        if not self._can_explore(progress):
            return self._next_topic(reason)
        self._next_question()  # an extra question is a new question: its follow-up count starts again
        progress.probing_item_id = None
        progress.explore_count += 1
        return self._decision(PacingAction.EXPLORE, TransitionReason.EXPLORING, explore_index=progress.explore_count - 1)

    def _on_recovery(self, update: TurnUpdate) -> PacingDecision:
        """RECOVERY: each never-asked missed item is asked once (no follow-ups), while time before the planned end
        still leaves room for the wrap-up; then wrap up. A short answer does not stop it: the must-cover items are what the
        research needs, so even a participant with little to say is asked each of them once (user decision 2026-10-13)."""
        self._recovery_queue = [i for i in self._recovery_queue if i.id not in self._covered]
        if not self._recovery_queue:
            return self._enter_wrapup(TransitionReason.MISSED_ITEMS)
        if self._elapsed() + self._wrapup_reserve() >= self.budget.total_s:
            return self._enter_wrapup(TransitionReason.PHASE_TIME_UP)
        item = self._recovery_queue.pop(0)
        self._asked_items.add(item.id)
        return self._decision(PacingAction.ASK_MISSED_ITEM, TransitionReason.MISSED_ITEMS, missed_item=item)

    def _on_wrapup(self, update: TurnUpdate) -> PacingDecision:
        """WRAPUP: the participant's answer to "anything else?" ends the interview."""
        return self._end(EndReason.COMPLETED, TransitionReason.WRAPUP_ANSWERED)

    # ---------- moves ----------
    def _leave_question_list(self, reason: TransitionReason) -> PacingDecision:
        """End of WARMUP goes to the first core topic; end of STIMULI goes towards wrap-up."""
        if self._phase is Phase.WARMUP:
            return self._enter_topic(0, reason)
        return self._leave_content(reason)

    def _next_topic(self, reason: TransitionReason) -> PacingDecision:
        """Go to the next core topic, or past the core when the last one is done."""
        self._core_used_s += self._topic_elapsed()
        if self._topic_index + 1 < len(self.guide.topics):
            return self._enter_topic(self._topic_index + 1, reason)
        if self._stimulus_questions:
            self._transition(Phase.STIMULI, reason)
            return self._decision(PacingAction.ENTER_PHASE, reason, question=self._stimulus_questions[0])
        return self._leave_content(reason)

    def _enter_topic(self, index: int, reason: TransitionReason) -> PacingDecision:
        """Start core topic number `index` (CORE -> CORE between topics)."""
        self._topic_index = index
        topic = self.current_topic
        self._transition(Phase.CORE, reason)
        progress = self._progress[topic.id]
        progress.started_at_s = self.clock.now()
        progress.budget_s = self._topic_budget_now(index)
        action = PacingAction.NEXT_TOPIC if index else PacingAction.ENTER_PHASE
        return self._decision(action, reason, question=topic.questions[0])

    def _leave_content(self, reason: TransitionReason) -> PacingDecision:
        """After the last content phase: ask never-asked missed items once if there are any, else wrap up.
        Items already explored in their topic are not asked again (the "重复提问 = 0" gate)."""
        unasked = [i for i in self.missed_items() if i.id not in self._asked_items]
        if not unasked:
            return self._enter_wrapup(reason)
        self._transition(Phase.RECOVERY, TransitionReason.MISSED_ITEMS)
        self._recovery_queue = unasked
        return self._on_recovery(TurnUpdate())

    def _enter_wrapup(self, reason: TransitionReason) -> PacingDecision:
        """Summarise and ask the closing question."""
        self._transition(Phase.WRAPUP, reason)
        return self._decision(PacingAction.ENTER_PHASE, reason)

    def _end(self, end_reason: EndReason, reason: TransitionReason) -> PacingDecision:
        """Finish; nothing more is said after this except the closing / safety statement."""
        self._transition(Phase.ENDED, reason)
        self.end_reason = end_reason
        return self._decision(PacingAction.END, reason, end_reason=end_reason)

    # ---------- helpers ----------
    def _wrapup_reserve(self) -> float:
        """Seconds to keep for the wrap-up: its planned time, or two exchanges at the pace of this interview if that is longer.

        A participant who gives two-minute answers needs more than the planned wrap-up minutes for the closing question and
        its answer; reserving only the plan let those interviews run over the time gate."""
        average_exchange_s = self._elapsed() / max(1, self._answers)
        return max(self.budget.phase_s[Phase.WRAPUP], WRAPUP_RESERVE_EXCHANGES * average_exchange_s)

    def _topic_budget_now(self, index: int) -> float:
        """Time for topic number `index` when it starts: what is really left, shared by weight among this and the later topics.

        "Left" = the planned total minus the time already used, minus what the phases after the core still need (stimuli,
        recovery, wrap-up). Time the earlier phases or topics did not use goes to the topics after them (they can dig deeper);
        a topic or phase that ran over leaves them less. The first topic of a 60-minute interview that opened quickly therefore
        gets more than its planned share instead of the interview ending early with the time unspent."""
        topics = self.guide.topics[index:]
        reserved = self.budget.phase_s.get(Phase.STIMULI, 0.0) + self.budget.phase_s.get(Phase.RECOVERY, 0.0) + self._wrapup_reserve()
        remaining_core = max(0.0, self.budget.total_s - self._elapsed() - reserved)
        return remaining_core * topics[0].weight / sum(t.weight for t in topics)

    def _apply(self, update: TurnUpdate) -> None:
        """Count the follow-up, store the topic's saturation and the covered items (unknown ids are logged and dropped)."""
        if update.was_followup:
            self._followups += 1
        unknown = [i for i in update.covered_item_ids if i not in self._known_items]
        if unknown:
            log.warning("moderator model reported %d unknown must-cover id(s); ignored", len(unknown))
        self._covered |= {i for i in update.covered_item_ids if i in self._known_items}
        self._answers += 1
        self._dry_streak = self._dry_streak + 1 if update.dry else 0
        if self._phase is Phase.CORE:
            progress = self._progress[self.current_topic.id]
            progress.turns += 1
            progress.saturation = update.saturation
            progress.last_info_gain = update.information_gain

    def _question_done(self, update: TurnUpdate) -> TransitionReason | None:
        """Why the current prepared question is finished, or None if the moderator should stay on it."""
        if update.question_answered:
            return TransitionReason.QUESTION_DONE
        if self._followups >= self.config.max_followups_per_question:
            return TransitionReason.FOLLOWUP_LIMIT
        return None

    def _next_question(self) -> None:
        """Move to the next prepared question; its follow-up count starts again."""
        self._question_index += 1
        self._followups = 0

    def _phase_questions(self) -> list[str]:
        """Prepared questions of the current list phase."""
        return self.guide.warmup_questions if self._phase is Phase.WARMUP else self._stimulus_questions

    def _decision(self, action: PacingAction, reason: TransitionReason, **extra: object) -> PacingDecision:
        """Build a decision stamped with the current phase, topic and follow-up allowance."""
        topic_id = self.current_topic.id if self._phase is Phase.CORE else None
        return PacingDecision(action=action, phase=self._phase, reason=reason, topic_id=topic_id,
                              followup_allowed=self.followup_allowed(), **extra)

    def _require_running(self) -> None:
        """Raises InterviewStateError unless the interview has started and not ended."""
        if not self.started:
            raise InterviewStateError("interview not started")
        if self._phase is Phase.ENDED:
            raise InterviewStateError("interview already ended")

    def _elapsed(self) -> float:
        """Seconds since start (0 before start)."""
        return self.clock.now() - self._started_at if self._started_at is not None else 0.0

    def _phase_elapsed(self) -> float:
        """Seconds in the current phase."""
        return self.clock.now() - self._phase_started_at if self.started else 0.0

    def _topic_elapsed(self) -> float:
        """Seconds on the current core topic."""
        started = self._progress[self.current_topic.id].started_at_s
        return self.clock.now() - started if started is not None else 0.0

    def _overtime(self) -> bool:
        """Whether the interview is past its budget by more than the allowed ratio (forced wrap-up)."""
        return self._elapsed() >= self.budget.total_s * self.config.overtime_force_ratio
