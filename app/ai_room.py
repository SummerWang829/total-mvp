"""AI moderation in the interview room (text chat version): one engine per interview, kept in memory, with its chat log in the database.

The engine (app/moderator) decides what the AI says; this module connects it to the room: it starts a session, takes the
participant's typed answers, lets staff take over / hand back / whisper, shows staff the alerts, and at the end turns the
chat into transcript segments (the AI's lines are labelled `ai_moderator`, shown as "AI 主持人").

The engine lives in this process's memory. If the server restarts mid-interview the engine is gone: the session is then
handed to the human moderator with a red alert, instead of silently starting the AI over with an empty memory.
"""
import json
import logging
import threading
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .audit import audit
from . import content_rules, screener, signals
from .models import AiSession, ChatMessage, Interview, Reaction, RoomState, Stimulus, TranscriptSegment, utcnow
from .moderator.compliance import ai_moderation_blocker
from .moderator.constants import AlertLevel, ModerationMode, Phase
from .moderator.engine import EngineStateError, ModeratorEngine
from .moderator.clock import ActiveClock
from .moderator.knowledge import InterviewGuide
from .moderator.providers import ModeratorChatProvider, get_moderator_chat
from .moderator.schemas import ModeratorTurnPayload
from .moderator.snapshot import SnapshotError
from .moderator import snapshot

log = logging.getLogger(__name__)

DEMO_GUIDES: dict[str, str] = {"zh": "sg_skincare_zh", "en": "sg_food_delivery_en"}  # used when staff did not supply a guide (clearly a demo)
GUIDE_DIR = Path(__file__).resolve().parent.parent / "eval" / "guides"
MAX_PARTICIPANT_CHARS = 2000
MAX_HUMAN_CHARS = 2000
LAST_LINE_MS = 1500  # how long the final line of the chat lasts in the transcript
STAFF_SENDERS = ("ai", "participant", "moderator", "whisper")
PARTICIPANT_SENDERS = ("ai", "participant", "moderator")
SPEAKER_OF = {"ai": "ai_moderator", "moderator": "moderator", "participant": "participant"}
ALERT_ORDER = {AlertLevel.NONE.value: 0, AlertLevel.YELLOW.value: 1, AlertLevel.RED.value: 2}
ASYNC_KIND = "async"  # Interview.kind of a study done alone, any time (app/studies.py)
MSG_ENGINE_LOST = "AI 主持服务已重启，上下文丢失，已自动交给主持人。请接管访谈（AI 不会从头重来）"

_ENGINES: dict[str, ModeratorEngine] = {}
_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


class AiRoomError(Exception):
    """Something staff or the participant asked for cannot be done now; `status` is the HTTP status, `message` plain Chinese."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _lock(interview_id: str) -> threading.Lock:
    """The lock that serialises turns of one interview (the model call of a turn takes seconds)."""
    with _REGISTRY_LOCK:
        return _LOCKS.setdefault(interview_id, threading.Lock())


def _chat_model() -> ModeratorChatProvider:
    """The model for AI interviews: the configured one; in mock mode (development, tests) the offline scripted brain, so the room works end to end."""
    from .config import settings
    if settings.moderator_provider == "mock":
        from eval.mock_brain import MockBrain  # the same scripted brain the evaluation uses offline
        return MockBrain()
    return get_moderator_chat()


def demo_guide(language: str) -> InterviewGuide:
    """The demo guide for a language.

    Raises:
        AiRoomError: no demo guide for this language: staff must supply a guide.
    """
    name = DEMO_GUIDES.get(language)
    if not name:
        raise AiRoomError(409, "这个语言还没有演示提纲，请上传项目提纲后再启动 AI 主持")
    return InterviewGuide.model_validate(json.loads((GUIDE_DIR / f"{name}.json").read_text(encoding="utf-8")))


def _session(db: Session, interview_id: str) -> AiSession | None:
    return db.get(AiSession, interview_id)


def _need_session(db: Session, itv: Interview) -> AiSession:
    sess = _session(db, itv.id)
    if not sess:
        raise AiRoomError(409, "AI 主持还没有启动")
    return sess


def _next_seq(db: Session, interview_id: str) -> int:
    return (db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.interview_id == interview_id)) or 0) + 1


def _add_message(db: Session, itv: Interview, sender: str, text: str, payload: ModeratorTurnPayload | None = None) -> ChatMessage:
    """Store one chat line (the AI's lines also keep its internal note, operator and phase for the research team)."""
    plan = payload.plan if payload else None
    row = ChatMessage(org_id=itv.org_id, interview_id=itv.id, seq=_next_seq(db, itv.id), sender=sender, text=text,
                      note=payload.note.model_dump_json() if payload else "", operator=plan.operator.value if plan else "",
                      phase=plan.decision.phase.value if plan else "")
    db.add(row)
    db.flush()
    return row


def _apply_payload(db: Session, itv: Interview, sess: AiSession, engine: ModeratorEngine, payload: ModeratorTurnPayload) -> None:
    """Store what the AI said, raise the alert if the engine asked for one, and note when the AI has finished."""
    if payload.spoken_response:
        _add_message(db, itv, "ai", payload.spoken_response, payload)
    if payload.alert is not AlertLevel.NONE and ALERT_ORDER[payload.alert.value] >= ALERT_ORDER[sess.alert]:
        sess.alert, sess.alert_reason = payload.alert.value, payload.alert_reason[:300]
        audit(db, itv.org_id, "ai_moderator", "ai.alert", itv.id, level=payload.alert.value)
    if engine.fsm.phase is Phase.ENDED:
        sess.finished = True
    _sync_stimulus(db, itv, engine)
    if itv.kind == ASYNC_KIND:
        _keep_or_finish_async(db, itv, sess, engine)


def _keep_or_finish_async(db: Session, itv: Interview, sess: AiSession, engine: ModeratorEngine) -> None:
    """A study done at the participant's own pace: save the AI after every turn so they can leave and come back (or the server can restart);
    when the AI has said its closing words, close the session and turn the chat into a transcript."""
    if sess.finished:
        sess.snapshot = None
        itv.status, itv.ended_at = "ended", utcnow()
        db.flush()
        finalize(db, itv)
    else:
        sess.snapshot = snapshot.dump(engine)


def _sync_stimulus(db: Session, itv: Interview, engine: ModeratorEngine) -> None:
    """Put the material the AI is asking about on the participant's screen (only materials staff installed for this interview; none otherwise).

    Leaving the stimuli phase takes the material off the screen again.
    """
    want = engine.fsm.current_stimulus_id
    row = db.scalar(select(Stimulus).where(Stimulus.interview_id == itv.id, Stimulus.key == want)) if want else None
    state = db.get(RoomState, itv.id)
    have = state.stimulus_id if state else None
    if row is None and have is None:
        return
    new_id = row.id if row else None
    if new_id == have or (row is None and not _shown_by_ai(state)):
        return  # nothing changes, or staff put up something by hand: do not take it away
    state = state or RoomState(interview_id=itv.id, org_id=itv.org_id)
    state.stimulus_id, state.shown_at, state.shown_by = new_id, utcnow(), "ai_moderator"
    db.add(state)
    audit(db, itv.org_id, "ai_moderator", "stimulus.show" if new_id else "stimulus.hide", new_id or itv.id)


def _shown_by_ai(state: RoomState | None) -> bool:
    return bool(state and state.shown_by == "ai_moderator")


def start(db: Session, itv: Interview, guide: dict | None, actor: str) -> AiSession:
    """Start AI moderation: build the engine and let it say its opening (which discloses that it is an AI).

    Args:
        db: database session (committed by the caller).
        itv: an AI-moderated, live interview.
        guide: the project's discussion guide as JSON, or None for the demo guide of the interview's language.
        actor: who started it (for the audit log).

    Raises:
        AiRoomError: not an AI interview, not live, already started, the model cannot be used, or the guide is invalid.
    """
    if itv.moderation != ModerationMode.AI.value:
        raise AiRoomError(409, "这场访谈不是 AI 主持")
    if itv.status != "live":
        raise AiRoomError(409, "请先点“开始访谈”，再启动 AI 主持")  # (a study done alone is set live by app/studies.begin before this)
    if _session(db, itv.id):
        raise AiRoomError(409, "AI 主持已经启动")
    if why := ai_moderation_blocker():
        raise AiRoomError(409, why)
    try:
        the_guide = InterviewGuide.model_validate(guide) if guide else demo_guide(itv.language)
    except ValueError as e:
        raise AiRoomError(422, f"提纲不合格：{str(e)[:200]}") from e
    engine = ModeratorEngine(the_guide, _chat_model(), profile=screener.profile_lines(db, itv.participant_id),
                             background_memory=True, clock=ActiveClock() if itv.kind == ASYNC_KIND else None,
                             written=itv.kind == ASYNC_KIND)  # facts and the summary are read while the participant waits for nothing
    sess = AiSession(interview_id=itv.id, org_id=itv.org_id, guide_json=the_guide.model_dump_json())
    db.add(sess)
    with _lock(itv.id):
        _ENGINES[itv.id] = engine
        _apply_payload(db, itv, sess, engine, engine.open())
    audit(db, itv.org_id, actor, "ai.start", itv.id, demo_guide=guide is None)
    return sess


def _engine(db: Session, itv: Interview, sess: AiSession) -> ModeratorEngine | None:
    """The live engine, or None after a server restart (then the human takes over, with a red alert)."""
    engine = _ENGINES.get(itv.id)
    if engine is None and sess.snapshot and not sess.finished:  # a study done alone: carry on from the saved state
        try:
            engine = _ENGINES[itv.id] = snapshot.load(sess.snapshot, _chat_model())
            log.info("AI moderator for interview %s restored from its saved state", itv.id)
            return engine
        except SnapshotError:
            log.error("saved AI moderator for interview %s cannot be loaded", itv.id)
    if engine is None and sess.control == "ai" and not sess.finished:
        sess.control, sess.alert, sess.alert_reason = "human", AlertLevel.RED.value, MSG_ENGINE_LOST
        log.error("AI moderator engine for interview %s is gone (server restart): handed to the human", itv.id)
    return engine


def participant_says(db: Session, itv: Interview, text: str) -> None:
    """The participant typed an answer: store it and, while the AI is in control, let the AI reply.

    Raises:
        AiRoomError: AI moderation not started, the interview is not live, the chat is finished, or the text is blank / too long.
    """
    text = text.strip()
    if not text or len(text) > MAX_PARTICIPANT_CHARS:
        raise AiRoomError(422, f"请输入 1 到 {MAX_PARTICIPANT_CHARS} 个字")
    sess = _need_session(db, itv)
    if itv.status != "live":
        raise AiRoomError(409, "访谈不在进行中")
    if sess.finished:
        raise AiRoomError(409, "访谈已经结束，不能再发送")
    checked = content_rules.check(text, itv.language)  # contact details and abusive words are hidden before anything is stored or sent to the model
    text = checked.text
    with _lock(itv.id):
        engine = _engine(db, itv, sess)
        _add_message(db, itv, "participant", text)
        if checked.conduct:
            sess.strikes += 1
        _tell_the_team(db, itv, sess, checked)
        if engine is None:
            return
        try:
            if checked.conduct or content_rules.constants.CONTACT in checked.flags:
                kind = "conduct" if checked.conduct else "contact"
                ending = checked.conduct and sess.strikes >= content_rules.STRIKES_BEFORE_ENDING
                payload = engine.moderation_reply(text, kind, ending)  # a fixed reminder, never the model's words
            else:
                payload = engine.handle_participant(text, _chat_hints(db, itv, text))
        except EngineStateError:
            sess.finished = True
            return
        _apply_payload(db, itv, sess, engine, payload)


def _chat_hints(db: Session, itv: Interview, text: str) -> list[str]:
    """Hints for the model about how this typed answer was given (tone of the words, unusually short, slow to arrive, reactions sent), against this
    participant's own earlier answers. The model uses them only to ask more gently and must never mention them (red line H14)."""
    rows = db.scalars(select(ChatMessage).where(ChatMessage.interview_id == itv.id, ChatMessage.sender.in_(("ai", "participant")))
                      .order_by(ChatMessage.seq)).all()
    lengths: list[int] = []
    delays: list[float] = []
    last_ai = None
    for r in rows:
        if r.sender == "ai":
            last_ai = r
        elif last_ai is not None:
            lengths.append(len(r.text))
            delays.append((r.created_at - last_ai.created_at).total_seconds())
    delay = (utcnow() - last_ai.created_at).total_seconds() if last_ai else None
    reacted = [x.emoji for x in db.scalars(select(Reaction).where(Reaction.interview_id == itv.id, Reaction.created_at >= last_ai.created_at)
                                         .order_by(Reaction.seq))] if last_ai else []
    return signals.chat.hints(text, lengths, delay, delays, reacted)


def _tell_the_team(db: Session, itv: Interview, sess: AiSession, checked: content_rules.Checked) -> None:
    """Raise an alert for the team when the rule library hid something (red for hate and threats, yellow otherwise), without the words themselves."""
    if checked.clean:
        return
    if checked.severe:
        level, reason = AlertLevel.RED, "受访者说了歧视或威胁性的话（已被遮住，记录里没有原词），建议立即查看，必要时接管或结束"
    elif checked.conduct:
        level, reason = AlertLevel.YELLOW, f"受访者说了不文明的用语（已被遮住），这是第 {sess.strikes} 次；到 {content_rules.STRIKES_BEFORE_ENDING} 次 AI 会礼貌地结束访谈"
    else:
        level, reason = AlertLevel.YELLOW, "受访者想留下联系方式（已被遮住，没有发给 AI 模型）"
    if ALERT_ORDER[level.value] >= ALERT_ORDER[sess.alert]:
        sess.alert, sess.alert_reason = level.value, reason
    audit(db, itv.org_id, "content_rules", "content.hidden", itv.id, flags=",".join(sorted(checked.flags)))


def takeover(db: Session, itv: Interview, actor: str) -> None:
    """A staff member takes over: the AI stays silent until the control is handed back (red line H11).

    Raises:
        AiRoomError: AI moderation not started, or a human already has control.
    """
    sess = _need_session(db, itv)
    if sess.control == "human":
        raise AiRoomError(409, "已经由主持人接管")
    with _lock(itv.id):
        if (engine := _ENGINES.get(itv.id)) is not None:
            engine.take_over()
        sess.control = "human"
    audit(db, itv.org_id, actor, "ai.takeover", itv.id)


def handback(db: Session, itv: Interview, actor: str) -> None:
    """The staff member hands the interview back to the AI, which continues from what was said meanwhile.

    Raises:
        AiRoomError: AI moderation not started, the AI is already in control, it has finished, or it lost its memory (restart).
    """
    sess = _need_session(db, itv)
    if sess.control == "ai":
        raise AiRoomError(409, "AI 已经在主持")
    if sess.finished:
        raise AiRoomError(409, "AI 已经说完结束语，不能再交还")
    with _lock(itv.id):
        engine = _ENGINES.get(itv.id)
        if engine is None:
            raise AiRoomError(409, "AI 的上下文已丢失（服务重启过），不能交还，请由主持人继续")
        engine.hand_back()
        sess.control = "ai"
    audit(db, itv.org_id, actor, "ai.handback", itv.id)


def human_says(db: Session, itv: Interview, text: str, actor: str) -> None:
    """What the human moderator types while in control (the AI reads it from memory when control is handed back).

    Raises:
        AiRoomError: AI moderation not started, the AI is in control (the human must take over first), or the text is blank / too long.
    """
    text = text.strip()
    if not text or len(text) > MAX_HUMAN_CHARS:
        raise AiRoomError(422, f"请输入 1 到 {MAX_HUMAN_CHARS} 个字")
    sess = _need_session(db, itv)
    if sess.control != "human":
        raise AiRoomError(409, "AI 正在主持：请先点“接管”")
    with _lock(itv.id):
        _add_message(db, itv, "moderator", text)
        if (engine := _ENGINES.get(itv.id)) is not None:
            engine.add_human_turn(text)
    audit(db, itv.org_id, actor, "ai.human_message", itv.id)


def whisper(db: Session, itv: Interview, text: str, actor: str) -> None:
    """A private note to the AI for its next turn (never shown to the participant; the AI never quotes it).

    Raises:
        AiRoomError: AI moderation not started, the AI lost its memory, or the note is blank or too long.
    """
    _need_session(db, itv)
    text = text.strip()
    with _lock(itv.id):
        engine = _ENGINES.get(itv.id)
        if engine is None:
            raise AiRoomError(409, "AI 的上下文已丢失，悄悄话无处可传")
        try:
            engine.add_whisper(text)
        except ValueError as e:
            raise AiRoomError(422, str(e)) from e
        _add_message(db, itv, "whisper", text)
    audit(db, itv.org_id, actor, "ai.whisper", itv.id)


def ack_alert(db: Session, itv: Interview, actor: str) -> None:
    """Staff acknowledge the alert (it goes back to none; a new one can come at the next turn).

    Raises:
        AiRoomError: AI moderation not started.
    """
    sess = _need_session(db, itv)
    sess.alert, sess.alert_reason = AlertLevel.NONE.value, ""
    audit(db, itv.org_id, actor, "ai.ack_alert", itv.id)


def _at_ms(itv: Interview, row: ChatMessage) -> int:
    started = itv.started_at or row.created_at
    return max(0, int((row.created_at - started).total_seconds() * 1000))


def state(db: Session, itv: Interview, after: int, view: str) -> dict:
    """What a viewer sees of the chat.

    Args:
        db: database session.
        itv: the interview.
        after: only lines with a bigger sequence number (the page polls with the last one it has).
        view: "staff" (everything: notes, whispers, alerts), "observer" (the conversation only) or "participant" (the same, from the
            participant's side: nothing internal).

    Returns:
        {"started": False} before the AI is started; otherwise control, finished, the new lines, and (staff only) the alert.
    """
    sess = _session(db, itv.id)
    if not sess:
        return {"started": False}
    senders = STAFF_SENDERS if view == "staff" else PARTICIPANT_SENDERS
    rows = db.scalars(select(ChatMessage).where(ChatMessage.interview_id == itv.id, ChatMessage.seq > after, ChatMessage.sender.in_(senders))
                      .order_by(ChatMessage.seq)).all()
    out_rows = []
    for r in rows:
        line = {"seq": r.seq, "sender": r.sender, "text": r.text, "at_ms": _at_ms(itv, r)}
        if view == "staff":
            line.update(note=json.loads(r.note) if r.note else None, operator=r.operator, phase=r.phase)
        out_rows.append(line)
    out = {"started": True, "control": sess.control, "finished": sess.finished, "messages": out_rows}
    if view == "staff":
        out.update(alert=sess.alert, alert_reason=sess.alert_reason, engine_alive=itv.id in _ENGINES)
    return out


def finalize(db: Session, itv: Interview) -> int:
    """The interview ended: turn the chat into transcript segments and release the engine.

    The AI's lines are stored with speaker `ai_moderator` (shown as "AI 主持人"), a human moderator's as `moderator`, the
    participant's as `participant`. Whispers are staff notes, not part of the conversation, and are left out.

    Returns:
        The number of segments written (0 when AI moderation was never started).
    """
    _ENGINES.pop(itv.id, None)
    if not _session(db, itv.id):
        return 0
    rows = db.scalars(select(ChatMessage).where(ChatMessage.interview_id == itv.id, ChatMessage.sender.in_(tuple(SPEAKER_OF)))
                      .order_by(ChatMessage.seq)).all()
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id))
    for i, r in enumerate(rows):
        start = _at_ms(itv, r)
        end = max(start, _at_ms(itv, rows[i + 1]) if i + 1 < len(rows) else start + LAST_LINE_MS)
        db.add(TranscriptSegment(org_id=itv.org_id, interview_id=itv.id, idx=i, speaker=SPEAKER_OF[r.sender], start_ms=start, end_ms=end,
                                 text_orig=r.text, lang=itv.language))
    return len(rows)


def purge(db: Session, interview_id: str) -> None:
    """Delete the chat and the session of an interview whose personal data is being deleted (retention)."""
    _ENGINES.pop(interview_id, None)
    db.execute(delete(ChatMessage).where(ChatMessage.interview_id == interview_id))
    db.execute(delete(AiSession).where(AiSession.interview_id == interview_id))


def forget_engines_for_tests() -> None:
    """Drop every in-memory engine (tests simulate a server restart with this)."""
    _ENGINES.clear()

