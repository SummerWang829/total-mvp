"""实时看板: while interviews run, every line gets its outline question, the lines that answer a focus question are coded (in the background, by the model when one is
configured), and the board adds them up. Rules decide what is worth coding; the model only picks a code and says how sure it is. Callers commit; the background job opens its own session.

A line is coded only when it (a) is the participant's, (b) is under the question a focus question points at ("asked"), or (c) clearly talks about the focus question while another
question was on ("volunteered"). Everything else costs nothing.
"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import CaptionLine, ChatMessage, Clip, Interview, Participant, Project, utcnow
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatError, ChatFatalError, get_moderator_chat
from . import service as svc, writer
from .models import CodedAnswer, FocusQuestion, OutlineSnapshot

log = logging.getLogger(__name__)
REVIEW_BELOW = 0.75        # a code the AI is less sure of goes to review
CLUSTER_MIN = 8            # answers to collect before they can be grouped
MIN_FOR_PERCENT = 20       # fewer people than this: counts only, no percentages
SATURATION_MIN = 8         # answers needed before "close to saturated" can be said
SATURATION_WINDOW = 5      # the last this-many answers all fell into codes already seen twice
RELEVANT = 0.3             # how much a line must overlap a focus question to be tried as "volunteered"
MIN_WORDS = 4              # shorter lines are never coded
MISS_AFTER = 0.7           # a live interview that is past this share of its length without reaching a focus question is "not asked" for now
WORKERS = 16
JOB_WAIT_S = 6.0           # a job waits this long for its line to be committed
LATEST: dict[str, float] = {}          # project id -> time of the last coded answer (the page's "has anything changed")
METRICS: list[dict] = []               # one entry per finished job: {"ms", "ok", "model"} (the load test reads it)
_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="board")
_ITEMS: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


@dataclass
class Task:
    interview_id: str
    org_id: str
    project_id: str
    kind: str          # caption | chat
    line_id: str
    text: str
    item_uid: str | None
    at_ms: int
    queued: float


def _items(db: Session, snap_id: str) -> list[dict]:
    if snap_id not in _ITEMS:
        snap = db.get(OutlineSnapshot, snap_id)
        _ITEMS[snap_id] = sorted(snap.content["items"], key=lambda i: i["position"]) if snap else []
    return _ITEMS[snap_id]


def _prev(db: Session, itv: Interview, model, order) -> tuple[str | None, str]:
    """The question and how it was found of the latest line of this interview that has one."""
    row = db.execute(select(model.item_uid, model.item_source).where(model.interview_id == itv.id, model.item_uid.is_not(None)).order_by(order.desc()).limit(1)).first()
    return (row[0], row[1] or "guess") if row else (None, "")


def _prev_item(db: Session, itv: Interview, model, order) -> str | None:
    return _prev(db, itv, model, order)[0]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def match_question(items: list[dict], question: str) -> str | None:
    """The outline item whose text is exactly this prepared question (the AI host asks the outline's own sentence when it moves to a question)."""
    q = _norm(question)
    return next((i["item_uid"] for i in items if q and any(_norm(t) == q for t in i["texts"].values())), None) if q else None


def assign(db: Session, itv: Interview, line, speaker: str, text: str, question: str | None = None, engine: bool = False) -> Task | None:
    """Put a new line under its outline question and return a coding task when the line is worth coding. Nothing happens for an interview without a snapshot. How the question was found is
    kept on the line: host (the human moderator pointed at it) > ai (the AI host's engine said which prepared question it was asking, or that this is a follow-up of the one before) >
    guess (matched by wording and the outline order; only when nobody said). A participant's line stays under the question before it. A pilot is numbered too but never coded."""
    if not itv.outline_snapshot_id:
        return None
    items = _items(db, itv.outline_snapshot_id)
    if not items:
        return None
    kind = "chat" if isinstance(line, ChatMessage) else "caption"
    model, order = (ChatMessage, ChatMessage.seq) if kind == "chat" else (CaptionLine, CaptionLine.seq)
    prev, prev_src = _prev(db, itv, model, order)
    uid, src = None, ""
    if itv.current_item_uid and any(i["item_uid"] == itv.current_item_uid for i in items):
        uid, src = itv.current_item_uid, "host"
    elif speaker in ("moderator", "ai"):
        if question:
            uid = match_question(items, question)
            src = "ai" if uid else ""
        if not uid and engine and prev:       # the engine spoke without a prepared question: a follow-up of the question before
            uid, src = prev, "ai"
        if not uid:
            g = svc.guess_item_uid(text, items, prev)
            uid, src = (g or prev), ("guess" if (g or prev) else "")
    else:
        uid, src = prev, prev_src
    if uid:
        line.item_uid, line.item_source = uid, src
    if itv.is_pilot or speaker != "participant" or len(re.sub(r"\W+", "", text)) < MIN_WORDS:
        return None
    return Task(itv.id, itv.org_id, itv.project_id, kind, line.id, text, uid, int(getattr(line, "at_ms", 0) or 0), time.monotonic())


def set_current(db: Session, itv: Interview, item_uid: str | None) -> None:
    """The human moderator says which question is being asked now (None = back to automatic).

    Raises:
        OutlineError: the interview has no outline snapshot, or the question is not in it.
    """
    if not itv.outline_snapshot_id:
        raise svc.OutlineError("这场访谈没有用新大纲")
    if item_uid and not any(i["item_uid"] == item_uid for i in _items(db, itv.outline_snapshot_id)):
        raise svc.OutlineError("这道题不在这场访谈用的大纲里", 404)
    itv.current_item_uid, itv.current_item_at = item_uid or None, utcnow() if item_uid else None


def reassign_line(db: Session, itv: Interview, line_id: str, item_uid: str) -> dict:
    """A researcher fixes the question number of a line afterwards. The line is marked "edited"; what was coded from it by the AI is thrown away and the line is coded again under its new question."""
    items = _items(db, itv.outline_snapshot_id) if itv.outline_snapshot_id else []
    if not any(i["item_uid"] == item_uid for i in items):
        raise svc.OutlineError("这道题不在这场访谈用的大纲里", 404)
    row = db.get(CaptionLine, line_id) or db.get(ChatMessage, line_id)
    if row is None or row.interview_id != itv.id or row.org_id != itv.org_id:
        raise svc.OutlineError("没有这句话", 404)
    row.item_uid, row.item_source = item_uid, "edited"
    for c in db.scalars(select(CodedAnswer).where(CodedAnswer.line_id == line_id, CodedAnswer.org_id == itv.org_id, CodedAnswer.coded_by == "ai", CodedAnswer.confirmed.is_(False))):
        db.delete(c)
    kind = "chat" if isinstance(row, ChatMessage) else "caption"
    speaker = "participant" if (getattr(row, "speaker", None) == "participant" or getattr(row, "sender", None) == "participant") else ""
    db.flush()
    if speaker and not itv.is_pilot and len(re.sub(r"\W+", "", row.text)) >= MIN_WORDS:
        submit(Task(itv.id, itv.org_id, itv.project_id, kind, row.id, row.text, item_uid, int(getattr(row, "at_ms", 0) or 0), time.monotonic()))
    return {"line_id": line_id, "item_uid": item_uid}


def relevance(text: str, focus: str, codes: list[str]) -> float:
    """How much a line talks about a focus question: the share of the focus question's (and codes') two-character pieces that also occur in the line."""
    flat = re.sub(r"\W+", "", focus + "".join(codes))
    bag = {flat[i:i + 2] for i in range(len(flat) - 1)}
    said = re.sub(r"\W+", "", text)
    if not bag or not said:
        return 0.0
    return sum(1 for b in bag if b in said) / len(bag)


def submit_recode(coded_id: str) -> None:
    """Code an answer that was added without a code (looking back through old transcripts): in the background, like a live one."""
    _POOL.submit(_recode, coded_id)


def _recode(coded_id: str) -> None:
    try:
        with SessionLocal() as db:
            c = db.get(CodedAnswer, coded_id)
            f = db.get(FocusQuestion, c.focus_id) if c else None
            text = _line_text(db, c) if c else ""
            if c is None or f is None or c.code or not text:
                return
            q, codes = f.text, list(f.codes or [])
        code, conf, _ = _code(q, codes, text)
        with SessionLocal() as db:
            c = db.get(CodedAnswer, coded_id)
            if c is not None and not c.code:
                c.code, c.ai_code, c.confidence = code, code, conf
                db.commit()
    except Exception:   # noqa: BLE001 - background job: logged, never raised
        log.exception("board: recode failed")


def submit(task: Task | None) -> None:
    if task is not None:
        _POOL.submit(_run, task)


def _chat():
    """The model for coding, or None (offline demo, or no vendor named: then coding is rule-based and says so in the log once)."""
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        log.warning("board: no vendor named, answers are coded by the offline rules")
        return None
    return get_moderator_chat()


def _text_of(db: Session, task: Task):
    """The committed line (the job may start before the request has committed it)."""
    model = ChatMessage if task.kind == "chat" else CaptionLine
    end = time.monotonic() + JOB_WAIT_S
    while time.monotonic() < end:
        row = db.get(model, task.line_id)
        if row is not None:
            return row
        db.rollback()
        time.sleep(0.25)
    return None


def _run(task: Task) -> None:
    """One coding job in three short steps so that no database transaction stays open while the model thinks (SQLite has one writer: a model call inside a transaction would block everyone):
    1 read what to code, 2 ask the model for each (no session open), 3 write all the answers and commit."""
    t0 = time.monotonic()
    ok, used_model = True, False
    try:
        todo: list[tuple[str, bool, str | None, str, list[str], str]] = []
        with SessionLocal() as db:
            row = _text_of(db, task)
            if row is None:
                raise LookupError("line never appeared")
            uid = row.item_uid or task.item_uid
            for f in db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == task.project_id, FocusQuestion.org_id == task.org_id)):
                asked = bool(uid) and uid in ([f.item_uid] + list(f.item_uids or []))
                if not asked and relevance(task.text, f.text, f.codes or []) < RELEVANT:
                    continue
                if db.scalar(select(CodedAnswer.id).where(CodedAnswer.focus_id == f.id, CodedAnswer.line_id == task.line_id)):
                    continue
                todo.append((f.id, asked, uid, f.text, list(f.codes or []), f.coding))
            db.rollback()
        results = []
        for fid, asked, uid, ftext, codes, how in todo:
            if how == "collect" and not codes:   # "collect the words first": nothing to choose from yet, so no model is asked; the answer waits to be grouped
                code, conf, used_model = "", 1.0, False
            else:
                code, conf, used_model = _code(ftext, codes, task.text)
            results.append((fid, asked, uid, code, conf))
        if results:
            with SessionLocal() as db:
                for fid, asked, uid, code, conf in results:
                    svc.add_coded(db, task.org_id, fid, task.interview_id, task.line_id, code, conf, origin="asked" if asked else "volunteered", mentioned_in_item_uid=None if asked else uid)
                db.commit()
            LATEST[task.project_id] = time.time()
    except (ChatFatalError, ChatError) as e:
        ok = False
        log.warning("board: coding failed (%s)", type(e).__name__)
    except Exception:   # noqa: BLE001 - a background job must never take the interview down; the failure is logged with its traceback and counted
        ok = False
        log.exception("board: job failed")
    with _LOCK:
        METRICS.append({"ms": int((time.monotonic() - task.queued) * 1000), "ok": ok, "model": used_model})
        del METRICS[:-5000]


def _code(question: str, codes: list[str], text: str) -> tuple[str, float, bool]:
    chat = _chat()
    code, conf = writer.code_answer(chat, question, codes, text)
    return code, conf, chat is not None


# ---- what the page reads ------------------------------------------------------------------------------------------------------------------

def _label(p: Participant | None) -> str:
    return (p.code if p and p.code else "P-" + (p.id[:4] if p else "????"))


def _real(db: Session, proj: Project):
    return list(db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.org_id == proj.org_id, Interview.is_pilot.is_(False), Interview.outline_snapshot_id.is_not(None), Interview.purged_at.is_(None))))


def _line_text(db: Session, ca: CodedAnswer) -> str:
    row = db.get(CaptionLine, ca.line_id) or db.get(ChatMessage, ca.line_id)
    return (row.text if row else "") or ""


def _sources(db: Session, line_ids: list[str]) -> dict[str, str]:
    """How each line got its question: host / ai / guess / edited (lines that have none are left out)."""
    out: dict[str, str] = {}
    if not line_ids:
        return out
    for model in (CaptionLine, ChatMessage):
        for lid, src in db.execute(select(model.id, model.item_source).where(model.id.in_(line_ids))):
            out[lid] = src or ""
    return out


def _saturated(codes: list[str]) -> bool:
    if len(codes) < SATURATION_MIN:
        return False
    seen: dict[str, int] = {}
    for c in codes[:-SATURATION_WINDOW]:
        seen[c] = seen.get(c, 0) + 1
    return all(seen.get(c, 0) >= 2 for c in codes[-SATURATION_WINDOW:])


def _focus_card(db: Session, proj: Project, f: FocusQuestion, itvs: list[Interview], since: float, labels: dict) -> dict:
    cas = list(db.scalars(select(CodedAnswer).where(CodedAnswer.focus_id == f.id, CodedAnswer.org_id == proj.org_id).order_by(CodedAnswer.created_at)))
    asked = {c.interview_id for c in cas if c.origin == "asked"}
    vol = {c.interview_id for c in cas if c.origin == "volunteered"}
    counts: dict[str, int] = {}
    for c in cas:
        if c.code:   # an answer still waiting to be grouped has no code and is not counted in the statistics
            counts[c.code] = counts.get(c.code, 0) + 1
    top = max(counts, key=lambda k: counts[k]) if counts else ""
    src = _sources(db, [c.line_id for c in cas])
    now = time.time()
    miss = sum(1 for i in itvs if i.id not in asked and i.id not in vol and (i.status == "ended" or (i.status == "live" and i.started_at and (now - i.started_at.timestamp()) / 60 > MISS_AFTER * (i.duration_min or 45))))
    return {"id": f.id, "text": f.text, "role": f.asked_role, "label": labels.get(f.item_uid or "", ""), "answered": len(asked | vol), "asked": len(asked), "volunteered": len(vol), "top": top, "top_n": counts.get(top, 0),
            "new": sum(1 for c in cas if c.created_at.timestamp() > since), "missing": miss, "review": sum(1 for c in cas if c.code and c.confidence < REVIEW_BELOW and not c.confirmed), "saturated": _saturated([c.code for c in cas if c.code]),
            "guessed": sum(1 for c in cas if src.get(c.line_id) == "guess"), "pending": sum(1 for c in cas if not c.code), "coding": f.coding, "during": f.timing == "during"}


def board(db: Session, proj: Project, since: float = 0.0) -> dict:
    """The overview: counts of interviews, one card per focus question, the latest answers and the alerts."""
    itvs = _real(db, proj)
    labels: dict[str, str] = {}
    for i in itvs:
        for it in _items(db, i.outline_snapshot_id):
            labels.setdefault(it["item_uid"], it["label"])
    foc = list(db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id, FocusQuestion.org_id == proj.org_id).order_by(FocusQuestion.created_at)))
    recent = list(db.scalars(select(CodedAnswer).where(CodedAnswer.org_id == proj.org_id, CodedAnswer.focus_id.in_([f.id for f in foc] or [""])).order_by(CodedAnswer.created_at.desc()).limit(8)))
    parts = {i.id: _label(i.participant) for i in itvs}
    ftext = {f.id: f.text for f in foc}
    return {"live": sum(1 for i in itvs if i.status == "live"), "done": sum(1 for i in itvs if i.status == "ended"), "token": LATEST.get(proj.id, 0.0), "now": time.time(),
            "cards": [_focus_card(db, proj, f, itvs, since, labels) for f in foc],
            "recent": [{"at": c.created_at.isoformat(timespec="seconds"), "who": parts.get(c.interview_id, "—"), "focus": ftext.get(c.focus_id, ""), "code": c.code, "origin": c.origin, "id": c.id} for c in recent],
            "alerts": alerts(db, proj, itvs, foc, parts)}


def alerts(db: Session, proj: Project, itvs: list[Interview], foc: list[FocusQuestion], parts: dict) -> list[dict]:
    """What needs a person: the AI host's own alerts (stop or crisis, red line), a focus question the interview is about to finish without, and answers that look perfunctory."""
    from ..models import AiSession
    out: list[dict] = []
    now = time.time()
    for i in itvs:
        if i.status != "live":
            continue
        ai = db.get(AiSession, i.id)
        if ai is not None and ai.alert in ("red", "yellow"):
            out.append({"level": "now" if ai.alert == "red" else "watch", "who": parts[i.id], "interview_id": i.id, "text": ai.alert_reason or "AI 主持人提醒", "kind": "ai"})
        left = (i.duration_min or 45) - (now - i.started_at.timestamp()) / 60 if i.started_at else None
        if left is not None and left < 8:
            got = {c.focus_id for c in db.scalars(select(CodedAnswer).where(CodedAnswer.interview_id == i.id))}
            for f in foc:
                if (f.item_uid or f.item_uids) and f.id not in got:
                    out.append({"level": "watch", "who": parts[i.id], "interview_id": i.id, "text": f"关注问题“{f.text[:16]}”还没问到，距离结束约 {max(0, int(left))} 分钟", "kind": "missed"})
        short = [m.text for m in db.scalars(select(ChatMessage).where(ChatMessage.interview_id == i.id, ChatMessage.sender == "participant").order_by(ChatMessage.seq.desc()).limit(5))]
        if len(short) == 5 and all(len(re.sub(r"\W+", "", t)) < 4 for t in short):
            out.append({"level": "watch", "who": parts[i.id], "interview_id": i.id, "text": "疑似敷衍：连续 5 个回答不到 4 个字", "kind": "perfunctory"})
    out.sort(key=lambda a: a["level"] != "now")
    return out


def sessions(db: Session, proj: Project) -> list[dict]:
    out = []
    now = time.time()
    for i in _real(db, proj):
        if i.status == "live":
            used = (now - i.started_at.timestamp()) / 60 if i.started_at else 0
            out.append({"interview_id": i.id, "who": _label(i.participant), "moderation": i.moderation, "minutes": int(used), "planned": i.duration_min or 45, "current": svc.label_of(db, i.outline_version_id or "", _prev_item(db, i, ChatMessage, ChatMessage.seq) or _prev_item(db, i, CaptionLine, CaptionLine.seq))})
    return out


def focus_detail(db: Session, proj: Project, fid: str, origin: str = "all") -> dict:
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    cas = list(db.scalars(select(CodedAnswer).where(CodedAnswer.focus_id == fid, CodedAnswer.org_id == proj.org_id).order_by(CodedAnswer.created_at.desc())))
    if origin in ("asked", "volunteered"):
        cas = [c for c in cas if c.origin == origin]
    people = {i.id: i for i in _real(db, proj)}
    n = len({c.interview_id for c in cas})
    counts: dict[str, int] = {}
    groups: dict[str, dict[str, int]] = {}
    pending = 0
    for c in cas:
        if not c.code:
            pending += 1
            continue
        counts[c.code] = counts.get(c.code, 0) + 1
        p = people.get(c.interview_id)
        g = (p.participant.tags[0] if p and p.participant and p.participant.tags else "未分组")
        groups.setdefault(g, {})[c.code] = groups.setdefault(g, {}).get(c.code, 0) + 1
    src = _sources(db, [c.line_id for c in cas])
    show_pct = n >= MIN_FOR_PERCENT
    labels = {}
    for i in people.values():
        for it in _items(db, i.outline_snapshot_id):
            labels.setdefault(it["item_uid"], it["label"])
    return {"id": f.id, "text": f.text, "label": labels.get(f.item_uid or "", ""), "n": n, "percent": show_pct, "origin": origin, "coding": f.coding, "codes": list(f.codes or []), "pending": pending,
            "can_cluster": f.coding == "collect" and pending >= CLUSTER_MIN, "during": f.timing == "during",
            "dist": [{"code": k, "n": v, "pct": round(v * 100 / max(1, len(cas))) if show_pct else None} for k, v in sorted(counts.items(), key=lambda x: -x[1])],
            "groups": [{"group": g, "dist": [{"code": k, "n": v} for k, v in sorted(d.items(), key=lambda x: -x[1])], "total": sum(d.values())} for g, d in groups.items()],
            "quotes": [{"id": c.id, "who": _label(people[c.interview_id].participant) if c.interview_id in people else "—", "text": _line_text(db, c), "code": c.code, "confidence": c.confidence,
                        "review": bool(c.code) and c.confidence < REVIEW_BELOW and not c.confirmed, "confirmed": c.confirmed, "by": c.coded_by, "origin": c.origin, "in": labels.get(c.mentioned_in_item_uid or "", ""), "src": src.get(c.line_id, "")} for c in cas[:100]]}


def answer_detail(db: Session, proj: Project, cid: str) -> dict:
    c = db.get(CodedAnswer, cid)
    if c is None or c.org_id != proj.org_id:
        raise svc.OutlineError("没有这条回答", 404)
    f = db.get(FocusQuestion, c.focus_id)
    if f is None or f.project_id != proj.id:
        raise svc.OutlineError("没有这条回答", 404)
    row = db.get(CaptionLine, c.line_id)
    question, zh = "", ""
    if row is not None:
        zh = row.text_zh or ""
        q = db.scalar(select(CaptionLine).where(CaptionLine.interview_id == c.interview_id, CaptionLine.speaker == "moderator", CaptionLine.seq < row.seq).order_by(CaptionLine.seq.desc()).limit(1))
        question = q.text if q else ""
    else:
        crow = db.get(ChatMessage, c.line_id)
        if crow is not None:
            q = db.scalar(select(ChatMessage).where(ChatMessage.interview_id == c.interview_id, ChatMessage.sender == "ai", ChatMessage.seq < crow.seq).order_by(ChatMessage.seq.desc()).limit(1))
            question = q.text if q else ""
    itv = db.get(Interview, c.interview_id)
    return {"id": c.id, "who": _label(itv.participant) if itv else "—", "focus": f.text, "text": _line_text(db, c), "question": question, "translation": zh, "translation_note": "机器翻译，进报告前请人工核对" if zh else "",
            "code": c.code, "ai_code": c.ai_code, "confidence": c.confidence, "confirmed": c.confirmed, "origin": c.origin, "interview_id": c.interview_id, "clip_id": c.clip_id, "can_download": False}


def recode(db: Session, proj: Project, cid: str, code: str) -> dict:
    code = code.strip()[:80]
    if not code:
        raise svc.OutlineError("编码不能为空")
    answer_detail(db, proj, cid)
    svc.human_recode(db, proj.org_id, cid, code)
    LATEST[proj.id] = time.time()
    return answer_detail(db, proj, cid)


def to_report(db: Session, proj: Project, cid: str, user_id: str = "") -> dict:
    """"加入报告引用": a clip of the insight page (the ten seconds around the answer) is made from the answer; no new kind of object. The clip is shown in 洞察与片段 like any other."""
    d = answer_detail(db, proj, cid)
    c = db.get(CodedAnswer, cid)
    if c.clip_id:
        return d
    row = db.get(CaptionLine, c.line_id)
    at = int(getattr(row, "at_ms", 0) or 0) if row is not None else 0
    clip = Clip(org_id=proj.org_id, interview_id=c.interview_id, title=f"{d['focus'][:40]}：{c.code}"[:200], start_ms=max(0, at - 10000), end_ms=at + 10000, created_by=user_id)
    db.add(clip)
    db.flush()
    c.clip_id = clip.id
    return answer_detail(db, proj, cid)


def changes(db: Session, proj: Project) -> dict:
    """A cheap "has anything changed": the page asks every few seconds and only reloads when this moves."""
    n = db.scalar(select(func.count()).select_from(CodedAnswer).where(CodedAnswer.org_id == proj.org_id)) or 0
    live = db.scalar(select(func.count()).select_from(Interview).where(Interview.project_id == proj.id, Interview.status == "live")) or 0
    return {"token": f"{n}:{live}:{LATEST.get(proj.id, 0.0):.0f}"}


# ---- suggestions and the assistant ------------------------------------------------------------------------------------------------------

def suggestions(db: Session, proj: Project, dismissed: set[str] | None = None) -> list[dict]:
    """At most three, from the numbers: a focus question that is close to saturated (stop asking), one many interviews never reached, one with answers waiting for a person."""
    dismissed = dismissed or set()
    out: list[dict] = []
    b = board(db, proj)
    for c in b["cards"]:
        if c["saturated"]:
            out.append({"id": f"sat_{c['id']}", "kind": "saturated", "focus_id": c["id"], "score": 0.9, "title": f"“{c['text'][:14]}”可以不再追问", "reason": f"{c['answered']} 人回答，新到的都落在已有的说法里（最多的是“{c['top']}”），再问的收益很低", "can_adopt": bool(c.get("label"))})
        if c["missing"] >= 3:
            out.append({"id": f"miss_{c['id']}", "kind": "missing", "focus_id": c["id"], "score": 0.8, "title": f"“{c['text'][:14]}”有 {c['missing']} 场没问到", "reason": "需要给后面的场次加一道题（中途加题在下一阶段做）", "can_adopt": False})
        if c["review"]:
            out.append({"id": f"rev_{c['id']}", "kind": "review", "focus_id": c["id"], "score": 0.6, "title": f"“{c['text'][:14]}”有 {c['review']} 条待复核", "reason": f"把握值低于 {REVIEW_BELOW}，需要你确认编码", "can_adopt": True})
    out = [s for s in out if s["id"] not in dismissed]
    out.sort(key=lambda s: -s["score"])
    return [{k: v for k, v in s.items() if k != "score"} for s in out[:3]]


def adopt(db: Session, proj: Project, ids: list[str], user_id: str = "") -> dict:
    """Adopting "stop asking about X" only proposes: a pending revision that sets the question's follow-up depth to none (a person accepts it on the outline page). "Review" just opens the list."""
    todo = [s for s in suggestions(db, proj) if s["id"] in set(ids)]
    made, opened = 0, ""
    from . import session as sess
    for s in todo:
        if s["kind"] == "saturated" and s["can_adopt"]:
            f = db.get(FocusQuestion, s["focus_id"])
            o, v, _ = sess.ensure(db, proj, user_id)
            if f and f.item_uid:
                svc.propose(db, proj.org_id, o.id, v.id, [{"op": "config", "item_uid": f.item_uid, "after": {"config": {"depth": "none"}}, "reason": s["reason"]}], source="board", source_ref=f.id)
                made += 1
        elif s["kind"] == "review":
            opened = s["focus_id"]
    return {"proposed": made, "open_focus": opened}


def ask(db: Session, proj: Project, text: str) -> dict:
    """A plain question to the board, answered from the numbers without a model: "哪些人提到价格偏贵" lists the participants (codes only) whose coded answers or lines contain the words."""
    words = [w for w in re.findall(r"[一-鿿A-Za-z0-9]{2,}", re.sub(r"哪些人|提到|说过|谁|有谁|的人", " ", text)) if w]
    if not words:
        return {"reply": "我可以按关键词查回答，比如“哪些人提到价格偏贵”。", "who": []}
    who: dict[str, int] = {}
    people = {i.id: i for i in _real(db, proj)}
    for c in db.scalars(select(CodedAnswer).where(CodedAnswer.org_id == proj.org_id)):
        if c.interview_id in people and any(w in c.code or w in _line_text(db, c) for w in words):
            lab = _label(people[c.interview_id].participant)
            who[lab] = who.get(lab, 0) + 1
    if not who:
        return {"reply": f"没有找到提到“{'、'.join(words)}”的回答。", "who": []}
    return {"reply": f"{len(who)} 位提到了“{'、'.join(words)}”：" + "、".join(sorted(who)[:12]) + ("…" if len(who) > 12 else "") + "（只显示编号）", "who": sorted(who)}
