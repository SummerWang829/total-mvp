"""Evidence validation: an insight only exists if every quote is really in the transcript.

The model (or a human) proposes {segment_id, quote}. We look the segment up ourselves, check the
quote is a real substring (after normalisation), and copy the timestamps from the segment.
Anything that does not check out is dropped with a reason.
"""
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TranscriptSegment

MIN_QUOTE_CHARS = 4


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return "".join(ch for ch in s if unicodedata.category(ch)[0] not in "PSZC")


def quote_matches(quote: str, text: str) -> bool:
    """Quote may use '…'/'...' to skip words; the parts must appear in order."""
    parts = [norm(p) for p in re.split(r"…|\.{3}", quote)]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < MIN_QUOTE_CHARS:
        return False
    hay, pos = norm(text), 0
    for p in parts:
        i = hay.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


@dataclass
class Checked:
    segment: TranscriptSegment
    quote: str
    lang: str


def check_evidence(db: Session, interview_id: str, items: list[dict]) -> tuple[list[Checked], list[str]]:
    ok: list[Checked] = []
    errors: list[str] = []
    segs = {s.id: s for s in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == interview_id))}
    for it in items:
        seg = segs.get(it.get("segment_id", ""))
        quote = (it.get("quote") or "").strip()
        if not seg:
            errors.append(f"找不到片段 {it.get('segment_id')!r}")
            continue
        want = it.get("lang")
        order = [want] if want in ("orig", "zh") else ["orig", "zh"]
        for side in order:
            if quote_matches(quote, seg.text_orig if side == "orig" else seg.text_zh):
                ok.append(Checked(seg, quote, side))
                break
        else:
            errors.append(f"引文不在片段 #{seg.idx} 的原文里：{quote[:40]!r}")
    return ok, errors
