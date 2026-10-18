"""Disagreement map of a focus group (innovation plan, direction D): who took which position on which topic, where people disagree, who changed their mind and
what was said just before, and where the whole table agrees.

How it stays honest: the model reads the numbered transcript and returns *claims* made of a speaker, a topic, a stance and line NUMBERS (never quotes). The
server checks every claim: the cited lines must exist and must have been spoken by the claimed speaker, otherwise the claim is dropped; the quotes shown are
copied by the server from those lines. Claims, and the "changed their mind" events derived from them, are only suggestions until a researcher accepts them.
"""
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .groups import speaker_label
from .models import Claim, Interview, PersuasionEvent, TranscriptSegment
from .providers import LLM
from .repo.tokens import tokens

STANCES = ("pro", "con", "unsure")
STANCE_ZH = {"pro": "赞成", "con": "反对", "unsure": "犹豫"}
MAX_LINES = 600
MAX_TOPICS = 12
TRIGGER_WINDOW = 15          # how many lines back to look for what may have changed someone's mind
SAME_TOPIC_OVERLAP = 0.6
_SEAT = re.compile(r"^p\d{1,2}$")


class AnalysisError(Exception):
    """The analysis cannot run (not a focus group, no transcript); `message` is plain Chinese for the researcher."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Line:
    n: int
    id: str
    speaker: str
    text: str
    at_ms: int


def lines_of(db: Session, itv: Interview) -> list[Line]:
    rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx).limit(MAX_LINES)).all()
    return [Line(i + 1, r.id, r.speaker, r.text_orig, r.start_ms) for i, r in enumerate(rows)]


def _topic_key(topic: str) -> set[str]:
    return set(tokens(topic))


def _same(a: set[str], b: set[str]) -> bool:
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= SAME_TOPIC_OVERLAP)


def clean_claims(raw: list, lines: list[Line]) -> list[dict]:
    """The model's claims that pass the server's checks, with similar topic names merged under the first name used.

    A claim is kept when: its speaker is a seat (p1, p2...), its stance is known, and every cited line exists and was spoken by that seat."""
    by_n = {ln.n: ln for ln in lines}
    out: list[dict] = []
    names: list[tuple[str, set[str]]] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        speaker, topic, stance = str(r.get("speaker", "")).strip().lower(), str(r.get("topic", "")).strip()[:60], str(r.get("stance", "")).strip().lower()
        cited = []
        for x in r.get("lines") or []:
            try:
                cited.append(int(str(x).strip("[] ")))
            except ValueError:
                continue
        cited = list(dict.fromkeys(cited))
        if not _SEAT.match(speaker) or not topic or stance not in STANCES or not cited or any(c not in by_n or by_n[c].speaker != speaker for c in cited):
            continue
        key = _topic_key(topic)
        label = next((name for name, k in names if _same(k, key)), None)
        if label is None:
            if len(names) >= MAX_TOPICS:
                continue
            names.append((topic, key))
            label = topic
        out.append({"speaker": speaker, "topic": label, "stance": stance, "reason": str(r.get("reason", "")).strip()[:200], "lines": cited})
    return out


def find_shifts(claims: list[Claim], lines_by_id: dict[str, Line]) -> list[dict]:
    """For each person and topic, where the stance changed between two claims: the earlier and later claim, and a possible trigger (another person's claim in the
    last lines before the change that took the stance the person moved to). A trigger raises the confidence; without one the event is a weak guess."""
    out = []
    by: dict[tuple[str, str], list[Claim]] = defaultdict(list)
    for c in claims:
        by[(c.speaker, c.topic)].append(c)
    for (speaker, topic), cs in by.items():
        cs.sort(key=lambda c: min(lines_by_id[i].n for i in c.line_ids))
        for before, after in zip(cs, cs[1:]):
            if before.stance == after.stance:
                continue
            at = min(lines_by_id[i].n for i in after.line_ids)
            near = [o for o in claims if o.topic == topic and o.speaker != speaker and o.stance == after.stance
                    and at - TRIGGER_WINDOW <= max(lines_by_id[i].n for i in o.line_ids) < at]
            trigger = max(near, key=lambda o: max(lines_by_id[i].n for i in o.line_ids), default=None)
            out.append({"speaker": speaker, "topic": topic, "from": before.stance, "to": after.stance, "trigger": trigger.id if trigger else None, "confidence": 0.6 if trigger else 0.3})
    return out


def analyze(db: Session, itv: Interview, llm: LLM) -> dict:
    """Read the transcript, store the checked claims and the changes of mind as suggestions (earlier suggestions and rejections are replaced, accepted claims stay).

    Returns {"claims": n, "dropped": n, "shifts": n}.

    Raises:
        AnalysisError: not a focus group, or no transcript to read.
    """
    if itv.kind != "group":
        raise AnalysisError(409, "分歧地图只用于座谈会")
    lines = lines_of(db, itv)
    if not [ln for ln in lines if _SEAT.match(ln.speaker)]:
        raise AnalysisError(409, "还没有转写，或转写里没有受访者的发言。请先转写")
    raw = llm.extract_claims([{"n": ln.n, "speaker": ln.speaker, "text": ln.text[:400]} for ln in lines])
    good = clean_claims(raw, lines)
    db.execute(delete(PersuasionEvent).where(PersuasionEvent.interview_id == itv.id, PersuasionEvent.status != "accepted"))
    db.execute(delete(Claim).where(Claim.interview_id == itv.id, Claim.status != "accepted"))
    db.flush()
    by_n = {ln.n: ln for ln in lines}
    accepted = {(c.speaker, c.topic, tuple(c.line_ids)) for c in db.scalars(select(Claim).where(Claim.interview_id == itv.id, Claim.status == "accepted"))}
    kept = []
    for g in good:
        if (g["speaker"], g["topic"], tuple(by_n[n].id for n in g["lines"])) in accepted:
            continue      # already accepted by a researcher: it stays as it is, not a second copy
        row = Claim(org_id=itv.org_id, interview_id=itv.id, speaker=g["speaker"], topic=g["topic"], stance=g["stance"], reason=g["reason"], line_ids=[by_n[n].id for n in g["lines"]])
        db.add(row)
        kept.append(row)
    db.flush()
    by_id = {ln.id: ln for ln in lines}
    all_claims = db.scalars(select(Claim).where(Claim.interview_id == itv.id, Claim.status != "rejected")).all()
    shifts = find_shifts(all_claims, by_id)
    for sh in shifts:
        db.add(PersuasionEvent(org_id=itv.org_id, interview_id=itv.id, speaker=sh["speaker"], topic=sh["topic"], from_stance=sh["from"], to_stance=sh["to"],
                               trigger_claim_id=sh["trigger"], confidence=sh["confidence"]))
    db.flush()
    return {"claims": len(good), "dropped": len(raw) - len(good) if isinstance(raw, list) else 0, "shifts": len(shifts)}


def view(db: Session, itv: Interview) -> dict:
    """The disagreement map: topics x speakers with the stance and the quotes behind it, pairs in conflict, changes of mind (with the line before), consensus and dissent.

    Rejected claims are not shown. Quotes are copied from the transcript by line."""
    segs = {s.id: s for s in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id))}
    order = {s.id: s.idx for s in segs.values()}
    claims = [c for c in db.scalars(select(Claim).where(Claim.interview_id == itv.id, Claim.status != "rejected")) if all(i in segs for i in c.line_ids)]
    claims.sort(key=lambda c: min(order[i] for i in c.line_ids))

    def claim_out(c: Claim) -> dict:
        return {"id": c.id, "speaker": speaker_label(c.speaker), "key": c.speaker, "topic": c.topic, "stance": c.stance, "stance_zh": STANCE_ZH[c.stance], "reason": c.reason, "status": c.status,
                "quotes": [{"text": segs[i].text_orig, "at_ms": segs[i].start_ms} for i in sorted(c.line_ids, key=lambda i: order[i])]}
    outs = {c.id: claim_out(c) for c in claims}
    topics = list(dict.fromkeys(c.topic for c in claims))
    speakers = sorted({c.speaker for c in claims}, key=lambda s: int(s[1:]))
    matrix = [{"topic": t, "cells": {s: [outs[c.id] for c in claims if c.topic == t and c.speaker == s] for s in speakers if any(c.topic == t and c.speaker == s for c in claims)}} for t in topics]
    final: dict[tuple[str, str], Claim] = {(c.topic, c.speaker): c for c in claims}      # the latest stance of each person on each topic
    conflicts, consensus, dissent = [], [], []
    for t in topics:
        now = {s: final[(t, s)] for s in speakers if (t, s) in final}
        pros, cons = [s for s, c in now.items() if c.stance == "pro"], [s for s, c in now.items() if c.stance == "con"]
        for a in pros:
            for b in cons:
                conflicts.append({"topic": t, "a": outs[now[a].id], "b": outs[now[b].id]})
        stated = {c.stance for c in now.values() if c.stance != "unsure"}
        if len(now) >= 2 and len(stated) == 1 and not any(c.stance == "unsure" for c in now.values()):
            consensus.append({"topic": t, "stance_zh": STANCE_ZH[next(iter(stated))], "speakers": [speaker_label(s) for s in now]})
        elif len(now) >= 3 and (len(pros) == 1 or len(cons) == 1) and len(pros) != len(cons):
            lone, side = (pros[0], "赞成") if len(pros) == 1 and len(cons) >= 2 else (cons[0], "反对") if len(cons) == 1 and len(pros) >= 2 else (None, "")
            if lone:
                dissent.append({"topic": t, "who": speaker_label(lone), "stance_zh": side, "claim": outs[now[lone].id]})
    events = []
    for e in db.scalars(select(PersuasionEvent).where(PersuasionEvent.interview_id == itv.id, PersuasionEvent.status != "rejected")):
        trig = outs.get(e.trigger_claim_id) if e.trigger_claim_id else None
        events.append({"id": e.id, "speaker": speaker_label(e.speaker), "topic": e.topic, "from": STANCE_ZH[e.from_stance], "to": STANCE_ZH[e.to_stance], "confidence": e.confidence,
                       "needs_review": e.confidence < 0.5, "status": e.status, "trigger": trig})
    return {"speakers": [{"key": s, "label": speaker_label(s)} for s in speakers], "matrix": matrix, "conflicts": conflicts, "shifts": events, "consensus": consensus, "dissent": dissent,
            "claims": len(claims), "analysed": bool(claims)}
