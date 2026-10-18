"""Cue cards on the database: look at the room, make at most one card, expire old ones, show them to the team, record what the moderator did."""
import json
import logging
import re
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import groups, screener
from ..group_metrics.compute import Sentence, compute
from ..models import CaptionLine, CueCard, Interview, InterviewPlan, RoomState, Stimulus, utcnow
from . import constants as K
from .rules import Line, Snapshot, candidates
from .schedule import Existing, choose, expired

log = logging.getLogger(__name__)
_LOCK = threading.Lock()  # one process serves the site: two team members polling at once must not make the same card twice


def _label(speaker: str) -> str:
    m = re.fullmatch(r"p(\d+)", speaker)
    return f"参与者 ·{int(m.group(1)):02d}" if m else "主持人"


def _elapsed_ms(itv: Interview) -> int:
    from ..routers.group_metrics import elapsed_ms

    return elapsed_ms(itv)


def _steps(db: Session, itv: Interview) -> list[dict] | None:
    plan = db.get(InterviewPlan, itv.id)
    if not plan:
        return None
    try:
        return json.loads(plan.script_json).get("steps") or None
    except ValueError:
        log.warning("interview %s has a script that is not valid JSON", itv.id)
        return None


def _position(db: Session, itv: Interview, steps: list[dict] | None) -> tuple[int, float] | None:
    """Which section the moderator is really in: the one whose material was shown last, and for how long it has been on screen."""
    state = db.get(RoomState, itv.id)
    if not steps or not state or not state.stimulus_id or not state.shown_at:
        return None
    stim = db.get(Stimulus, state.stimulus_id)
    if not stim:
        return None
    for index, step in enumerate(steps):
        if step.get("show") == stim.key:
            return index, max(0.0, (utcnow() - state.shown_at) / timedelta(milliseconds=1))
    return None


def _moderator_quiet_for_s(itv: Interview, lines: list[Line], now_ms: int) -> float | None:
    from ..routers.concept import PARTIAL_TTL_S, _PARTIAL
    import time

    partial = _PARTIAL.get((itv.id, "moderator"))
    if partial and time.time() - partial[1] < PARTIAL_TTL_S:
        return 0.0  # the moderator is speaking right now
    ends = [ln.sentence.end_ms for ln in lines if ln.sentence.speaker == "moderator"]
    return None if not ends else max(0.0, (now_ms - max(ends)) / 1000)


def _existing(db: Session, interview_id: str) -> list[CueCard]:
    return list(db.scalars(select(CueCard).where(CueCard.interview_id == interview_id).order_by(CueCard.created_at)))


def refresh(db: Session, itv: Interview) -> None:
    """Expire waiting cards that nobody answered, and (while the interview is live) make one new card if the rules and limits allow."""
    with _LOCK:
        now = utcnow()
        cards = _existing(db, itv.id)
        stale = set(expired([Existing(c.kind, c.seat, c.dedupe_key, c.status, c.created_at) for c in cards], now))
        for c in cards:
            if c.dedupe_key in stale:
                c.status = K.Status.EXPIRED.value
        if stale:
            db.commit()
        if itv.status != "live":
            return
        rows = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq)).all()
        lines = [Line(r.seq, Sentence(r.speaker, r.text, r.at_ms, r.dur_ms), frozenset(f for f in r.flags.split(",") if f)) for r in rows]
        now_ms = _elapsed_ms(itv)
        seats = groups.seats(db, itv.id)
        metrics = compute([ln.sentence for ln in lines], [s.seat for s in seats], now_ms)
        profiles = {s.seat: screener.profile_lines(db, s.participant_id) for s in seats}
        steps = _steps(db, itv)
        snap = Snapshot(now_ms, metrics, lines, steps, profiles, _position(db, itv, steps))
        existing = [Existing(c.kind, c.seat, c.dedupe_key, c.status, c.created_at) for c in cards]
        pick = choose(candidates(snap), existing, now, _moderator_quiet_for_s(itv, lines, now_ms))
        if pick is None:
            return
        db.add(CueCard(org_id=itv.org_id, interview_id=itv.id, kind=pick.kind.value, seat=pick.seat, text=pick.text, ask=pick.ask,
                       evidence=json.dumps(list(pick.evidence)), priority=pick.priority, dedupe_key=pick.dedupe_key))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # the same card was made a moment ago


def cards_out(db: Session, itv: Interview) -> list[dict]:
    """Waiting cards (most urgent first), then the last few answered ones. Evidence text is copied from the caption lines here."""
    cards = _existing(db, itv.id)
    waiting = sorted((c for c in cards if c.status == K.Status.PENDING.value), key=lambda c: (-c.priority, c.created_at))
    done = [c for c in reversed(cards) if c.status != K.Status.PENDING.value][:K.SHOW_HANDLED]
    seqs = {s for c in waiting + done for s in json.loads(c.evidence or "[]")}
    said = {r.seq: r for r in db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id, CaptionLine.seq.in_(seqs)))} if seqs else {}
    out = []
    for c in waiting + done:
        proof = [{"seq": s, "who": _label(said[s].speaker), "text": said[s].text[:K.EVIDENCE_CHARS]} for s in json.loads(c.evidence or "[]")[:K.EVIDENCE_PER_CARD] if s in said]
        out.append({"id": c.id, "kind": c.kind, "text": c.text, "ask": c.ask, "status": c.status, "seat": c.seat, "evidence": proof,
                    "age_s": int((utcnow() - c.created_at) / timedelta(seconds=1))})
    return out


def handle(db: Session, card: CueCard, status: K.Status, actor: str) -> None:
    """The moderator used or dismissed a waiting card (a card that is no longer waiting stays as it is)."""
    if card.status != K.Status.PENDING.value:
        return
    card.status, card.handled_by, card.handled_at = status.value, actor, utcnow()
