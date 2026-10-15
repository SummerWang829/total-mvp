"""Material marking: a participant taps ♥ (like) or ✕ (dislike) at spots on a concept card / image, and the research team
reads them back as a heatmap.

Only image materials can be marked: a video is an embedded cross-origin player, so we cannot place a tap on it or read
its timeline. Coordinates are fractions of the image (0..1), so they line up on any screen. Marks are personal data and
are purged with the interview (``app/retention.py``). They share the shape the quant concept-test circling will push in
(roadmap §5), so both feed one heatmap.
"""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .concept import at_ms
from .models import Interview, Stimulus, StimulusMark

KINDS = ("like", "dislike")
MAX_MARKS_PER_STIMULUS = 60  # per participant per material; a safety cap, well above any real concept test


def _clamp(v: float) -> float:
    """Keep a coordinate inside the image (0..1); a browser should already, but never trust it."""
    return 0.0 if v < 0 else 1.0 if v > 1 else float(v)


def mark_out(m: StimulusMark) -> dict:
    """One mark as the browser draws it (no personal identity beyond the participant id)."""
    return {"id": m.id, "stimulus_id": m.stimulus_id, "participant_id": m.participant_id,
            "kind": m.kind, "x": m.x, "y": m.y, "at_ms": m.at_ms}


def _image(db: Session, interview_id: str, stimulus_id: str) -> Stimulus:
    """The image material to mark, checked to belong to this interview.

    Raises:
        ValueError: the material is unknown, belongs to another interview, or is a video (not markable).
    """
    s = db.get(Stimulus, stimulus_id)
    if not s or s.interview_id != interview_id:
        raise ValueError("unknown material")
    if s.kind != "image":
        raise ValueError("only an image can be marked")
    return s


def add_mark(db: Session, org_id: str, itv: Interview, stimulus_id: str, participant_id: str,
             kind: str, x: float, y: float) -> StimulusMark:
    """Add one mark for a participant on an image material.

    Args:
        kind: ``like`` or ``dislike``.
        x, y: position as a fraction of the image (0..1); clamped into range.

    Returns:
        The stored mark.

    Raises:
        ValueError: the kind is not one of :data:`KINDS`, the material is not a markable image, or the participant has
            already placed :data:`MAX_MARKS_PER_STIMULUS` marks on it.
    """
    if kind not in KINDS:
        raise ValueError("unknown mark kind")
    _image(db, itv.id, stimulus_id)
    n = len(list(db.scalars(select(StimulusMark.id).where(
        StimulusMark.stimulus_id == stimulus_id, StimulusMark.participant_id == participant_id))))
    if n >= MAX_MARKS_PER_STIMULUS:
        raise ValueError("too many marks on this material")
    m = StimulusMark(org_id=org_id, interview_id=itv.id, stimulus_id=stimulus_id, participant_id=participant_id,
                     kind=kind, x=_clamp(x), y=_clamp(y), at_ms=at_ms(itv))
    db.add(m)
    return m


def remove_mark(db: Session, interview_id: str, participant_id: str, mark_id: str) -> bool:
    """Delete one of the participant's own marks (tap a pin to take it off). Returns True when a mark was removed.

    Only the participant's own mark on their own interview can be removed, so one person cannot erase another's marks.
    """
    m = db.get(StimulusMark, mark_id)
    if not m or m.interview_id != interview_id or m.participant_id != participant_id:
        return False
    db.delete(m)
    return True


def participant_marks(db: Session, interview_id: str, stimulus_id: str, participant_id: str) -> list[dict]:
    """A participant's own marks on one material (so their browser can redraw its pins)."""
    rows = db.scalars(select(StimulusMark).where(
        StimulusMark.interview_id == interview_id, StimulusMark.stimulus_id == stimulus_id,
        StimulusMark.participant_id == participant_id).order_by(StimulusMark.created_at))
    return [mark_out(m) for m in rows]


def stimulus_marks(db: Session, interview_id: str, stimulus_id: str) -> list[dict]:
    """Every mark on one material, for the moderator's live overlay (observers never call this)."""
    rows = db.scalars(select(StimulusMark).where(
        StimulusMark.interview_id == interview_id, StimulusMark.stimulus_id == stimulus_id).order_by(StimulusMark.created_at))
    return [mark_out(m) for m in rows]


def summary(db: Session, interview_id: str) -> list[dict]:
    """The heatmap input for the research team: each image material with its marks and like / dislike totals.

    Materials with no marks are included (empty), so the team sees which cards were shown but drew no reaction.
    """
    images = db.scalars(select(Stimulus).where(
        Stimulus.interview_id == interview_id, Stimulus.kind == "image").order_by(Stimulus.position)).all()
    marks = list(db.scalars(select(StimulusMark).where(StimulusMark.interview_id == interview_id)))
    by_stim: dict[str, list[StimulusMark]] = {}
    for m in marks:
        by_stim.setdefault(m.stimulus_id, []).append(m)
    out = []
    for s in images:
        ms = by_stim.get(s.id, [])
        out.append({
            "stimulus_id": s.id, "key": s.key, "title": s.title, "url": s.url,
            "like": sum(1 for m in ms if m.kind == "like"),
            "dislike": sum(1 for m in ms if m.kind == "dislike"),
            "marks": [mark_out(m) for m in ms],
        })
    return out


def purge(db: Session, interview_id: str) -> None:
    """Delete every mark for an interview (called when its personal data is purged)."""
    db.execute(delete(StimulusMark).where(StimulusMark.interview_id == interview_id))
