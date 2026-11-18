"""Rules of the outline workbench: versions that are never locked but count their edits, snapshots that freeze what an interview runs on, items with a permanent id,
revisions only a person can accept, marks, and coded answers that keep the AI's first code. Nothing here calls a model; callers commit.

Raises OutlineError (with an HTTP-style `status`) when a rule is broken; the message is plain Chinese for the page.
"""
import hashlib
import json
import re
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import CaptionLine, Interview, TranscriptSegment, utcnow
from .models import (CodedAnswer, FocusQuestion, HostDecision, Mark, Outline, OutlineItem, OutlineSnapshot, OutlineVersion, Revision)

ACCEPT_ROLES = ("admin", "researcher", "support")   # who may accept a revision (a client, observer or project manager may not)
KINDS = ("question", "note", "concept", "video", "website", "rating")
LABEL_PREFIX = {"question": "Q", "note": "N", "concept": "C", "video": "V", "website": "W", "rating": "R"}
OPS = ("rewrite", "add_followup", "insert", "rename", "config", "minutes")
OPS_FOR_KIND = {"rewrite": ("question", "note", "rating"), "add_followup": ("question", "rating"), "rename": KINDS, "minutes": KINDS,
                "config": ("concept", "video", "website", "rating")}
DESTINATIONS = ("outline", "material", "host_engine")
MAX_OPS_PER_REQUEST = 3
ITEM_FIELDS = ("module", "position", "kind", "texts", "followups", "must_cover", "key_question", "hypothesis", "minutes", "is_backup", "config")
WARMUP_MODULES = ("warmup", "暖场", "开场")
GUESS_MIN_SCORE = 0.2      # below this a transcript line is not given a question number at all
GUESS_FORWARD_BONUS = 0.15  # the current question and the next one are the likely ones
GUESS_BACK_PENALTY = 0.1    # jumping back to an earlier question is unlikely


class OutlineError(ValueError):
    """A rule of the workbench is broken. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---- outlines, versions, items ---------------------------------------------------------------------------------------

def create_outline(db: Session, org_id: str, project_id: str, name: str, user_id: str = "") -> tuple[Outline, OutlineVersion]:
    """A new outline with an empty version 1; it becomes the project's current outline."""
    from ..models import Project
    proj = db.get(Project, project_id)
    if proj is None or proj.org_id != org_id:
        raise OutlineError("项目不存在", 404)
    o = Outline(org_id=org_id, project_id=project_id, name=name[:200], created_by=user_id)
    db.add(o)
    db.flush()
    v = OutlineVersion(org_id=org_id, outline_id=o.id, number=1, created_by=user_id)
    db.add(v)
    db.flush()
    o.current_version_id = v.id
    proj.outline_id = o.id
    return o, v


def get_version(db: Session, org_id: str, version_id: str) -> OutlineVersion:
    v = db.get(OutlineVersion, version_id)
    if v is None or v.org_id != org_id:
        raise OutlineError("版本不存在", 404)
    return v


def items_of(db: Session, version_id: str) -> list[OutlineItem]:
    return list(db.scalars(select(OutlineItem).where(OutlineItem.version_id == version_id).order_by(OutlineItem.position, OutlineItem.id)))


def new_version(db: Session, version: OutlineVersion, user_id: str = "", summary: str = "") -> OutlineVersion:
    """A copy of `version` as the next version number. Items keep their permanent `item_uid` and their labels."""
    last = db.scalar(select(func.max(OutlineVersion.number)).where(OutlineVersion.outline_id == version.outline_id)) or 0
    nv = OutlineVersion(org_id=version.org_id, outline_id=version.outline_id, number=last + 1, base_version_id=version.id, summary=summary[:300], created_by=user_id)
    db.add(nv)
    db.flush()
    for it in items_of(db, version.id):
        db.add(OutlineItem(org_id=it.org_id, version_id=nv.id, item_uid=it.item_uid, label=it.label, **{f: _copy(getattr(it, f)) for f in ITEM_FIELDS}))
    outline = db.get(Outline, version.outline_id)
    outline.current_version_id = nv.id
    db.flush()
    return nv


def _copy(v: Any) -> Any:
    return json.loads(json.dumps(v)) if isinstance(v, (dict, list)) else v


def bump(db: Session, version: OutlineVersion) -> int:
    """Count one edit of the version. Every change of an item goes through here."""
    version.rev = (version.rev or 0) + 1
    db.flush()
    return version.rev


def _next_label(db: Session, version_id: str, kind: str) -> str:
    prefix = LABEL_PREFIX.get(kind, "Q")
    used = {i.label for i in items_of(db, version_id)}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def add_item(db: Session, version: OutlineVersion, label: str = "", after_uid: str | None = None, **fields: Any) -> OutlineItem:
    """Add an item (at the end, or right after `after_uid`). The label defaults to the next free one of its kind (Q1, C1 ...)."""
    kind = fields.get("kind", "question")
    if kind not in KINDS:
        raise OutlineError("条目类型不对")
    bad = set(fields) - set(ITEM_FIELDS)
    if bad:
        raise OutlineError("条目字段不对：" + "、".join(sorted(bad)))
    label = label or _next_label(db, version.id, kind)
    if any(i.label == label for i in items_of(db, version.id)):
        raise OutlineError(f"题号 {label} 已被用")
    its = items_of(db, version.id)
    pos = len(its)
    if after_uid:
        idx = next((n for n, i in enumerate(its) if i.item_uid == after_uid), None)
        if idx is None:
            raise OutlineError("找不到要插在它后面的条目", 404)
        pos = idx + 1
        for i in its[pos:]:
            i.position += 1
    fields["position"] = pos
    it = OutlineItem(org_id=version.org_id, version_id=version.id, label=label, **fields)
    db.add(it)
    bump(db, version)
    return it


def _item(db: Session, version: OutlineVersion, item_uid: str) -> OutlineItem:
    it = db.scalar(select(OutlineItem).where(OutlineItem.version_id == version.id, OutlineItem.item_uid == item_uid))
    if it is None:
        raise OutlineError("条目不存在", 404)
    return it


def update_item(db: Session, version: OutlineVersion, item_uid: str, **fields: Any) -> OutlineItem:
    """Change fields of an item (not its permanent id; use `set_label` for the number people see)."""
    bad = set(fields) - set(ITEM_FIELDS)
    if bad:
        raise OutlineError("条目字段不对：" + "、".join(sorted(bad)))
    it = _item(db, version, item_uid)
    for k, v in fields.items():
        setattr(it, k, v)
    bump(db, version)
    return it


def set_label(db: Session, version: OutlineVersion, item_uid: str, label: str) -> OutlineItem:
    """Change the number people see. Everything that points at the item stores `item_uid`, so history stays correct."""
    label = label.strip()[:20]
    if not label:
        raise OutlineError("题号不能为空")
    it = _item(db, version, item_uid)
    if any(i.label == label and i.item_uid != item_uid for i in items_of(db, version.id)):
        raise OutlineError(f"题号 {label} 已被用")
    it.label = label
    bump(db, version)
    return it


def delete_item(db: Session, version: OutlineVersion, item_uid: str) -> None:
    it = _item(db, version, item_uid)
    db.delete(it)
    bump(db, version)


def labels_map(db: Session, version_id: str) -> dict[str, str]:
    """item_uid -> the number shown in this version."""
    return {i.item_uid: i.label for i in items_of(db, version_id)}


def label_of(db: Session, version_id: str, item_uid: str | None) -> str:
    """The number to show for an item_uid, '' when it is not in that version (the item was deleted there)."""
    return labels_map(db, version_id).get(item_uid or "", "")


def usage_count(db: Session, version_id: str) -> int:
    """How many interviews ran on this version: the page says "这份大纲已被 N 场使用"."""
    return db.scalar(select(func.count()).select_from(Interview).where(Interview.outline_version_id == version_id)) or 0


# ---- snapshots ---------------------------------------------------------------------------------------------------------

def _content(db: Session, version: OutlineVersion, goal: str, topic: str) -> dict:
    return {"version": version.number, "rev": version.rev, "goal": goal, "topic": topic,
            "items": [{"item_uid": i.item_uid, "label": i.label, "module": i.module, "position": i.position, "kind": i.kind, "texts": i.texts, "followups": i.followups,
                       "must_cover": i.must_cover, "key_question": i.key_question, "hypothesis": i.hypothesis, "minutes": i.minutes, "is_backup": i.is_backup,
                       "config": i.config} for i in items_of(db, version.id)]}


def snapshot_for(db: Session, version: OutlineVersion, goal: str = "", topic: str = "") -> OutlineSnapshot:
    """The frozen copy of (version, rev); made once and shared by every interview that starts on that state."""
    snap = db.scalar(select(OutlineSnapshot).where(OutlineSnapshot.version_id == version.id, OutlineSnapshot.rev == version.rev))
    if snap:
        return snap
    content = _content(db, version, goal, topic)
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    snap = OutlineSnapshot(org_id=version.org_id, version_id=version.id, rev=version.rev, content=content, content_hash=digest)
    db.add(snap)
    db.flush()
    return snap


def start_interview(db: Session, itv: Interview, version: OutlineVersion, goal: str = "", topic: str = "") -> OutlineSnapshot:
    """Record on the interview which version and edit count it runs on and freeze the content. An interview that already has a snapshot keeps it
    (a running interview is never moved to a newer state of the outline).

    Raises:
        OutlineError: the version belongs to another company.
    """
    if version.org_id != itv.org_id:
        raise OutlineError("版本不存在", 404)
    if itv.outline_snapshot_id:
        return db.get(OutlineSnapshot, itv.outline_snapshot_id)
    snap = snapshot_for(db, version, goal, topic)
    itv.outline_version_id, itv.outline_rev, itv.outline_snapshot_id = version.id, version.rev, snap.id
    db.flush()
    return snap


def snapshot_to_guide(snap: OutlineSnapshot, language: str, duration_min: float) -> dict:
    """The AI host's configuration (the dict `InterviewGuide` validates) built from a snapshot, so the running interview never reads the live outline.

    Questions and ratings become topics per module (the module named like a warm-up supplies the warm-up questions, notes become the section guide); materials become stimuli;
    backup items are left out.

    Raises:
        OutlineError: the snapshot has no question to ask in `language`.
    """
    c = snap.content
    def text(it: dict) -> str:
        return (it["texts"].get(language) or it["texts"].get("zh") or it["texts"].get("en") or "").strip()
    warm: list[str] = []
    topics: dict[str, dict] = {}
    stimuli: list[dict] = []
    for it in sorted(c["items"], key=lambda x: x["position"]):
        if it["is_backup"] or not text(it):
            continue
        mod = it["module"] or "main"
        if it["kind"] in ("concept", "video", "website"):
            stimuli.append({"id": it["item_uid"], "description": (it["config"].get("title") or text(it))[:300], "questions": [text(it)]})
        elif mod in WARMUP_MODULES and it["kind"] in ("question", "rating"):
            warm.append(text(it))
        elif it["kind"] == "note":
            topics.setdefault(mod, {"id": it["item_uid"], "title": mod, "questions": [], "must_cover": [], "section_guide": ""})["section_guide"] = text(it)[:500]
        else:
            t = topics.setdefault(mod, {"id": it["item_uid"], "title": mod, "questions": [], "must_cover": [], "section_guide": ""})
            t["questions"].append(text(it))
            t["must_cover"] += [{"id": f"{it['item_uid']}_{n}", "text": m[:300]} for n, m in enumerate(it["must_cover"])]
    topic_list = [t for t in topics.values() if t["questions"]]
    if not topic_list:
        raise OutlineError("这份大纲还没有可问的题")
    return {"research_goal": c.get("goal") or "（未写研究目标）", "participant_topic": (c.get("topic") or "")[:20], "language": language, "duration_min": float(duration_min),
            "warmup_questions": warm or [topic_list[0]["questions"][0]], "topics": topic_list, "stimuli": stimuli}


# ---- transcript lines and question numbers -----------------------------------------------------------------------------

def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\W+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def guess_item_uid(text: str, items: list[dict], last_uid: str | None = None) -> str | None:
    """Which question this line belongs to when nobody said: text similarity plus the order of the outline (the current question and the next one are favoured,
    jumping back is not). Returns None when nothing is similar enough; the caller records the result as a guess.
    """
    qs = [i for i in items if i["kind"] in ("question", "rating") and not i["is_backup"]]
    if not qs:
        return None
    last_pos = next((n for n, i in enumerate(qs) if i["item_uid"] == last_uid), None)
    words = _bigrams(text)
    best, best_score = None, 0.0
    for n, q in enumerate(qs):
        qb = _bigrams(" ".join(q["texts"].values()))
        sim = len(words & qb) / len(words | qb) if words and qb else 0.0
        if sim == 0:
            continue
        if last_pos is not None:
            sim += GUESS_FORWARD_BONUS if n in (last_pos, last_pos + 1) else -GUESS_BACK_PENALTY if n < last_pos else 0.0
        if sim > best_score:
            best, best_score = q["item_uid"], sim
    return best if best_score >= GUESS_MIN_SCORE else None


def assign_line(line: CaptionLine | TranscriptSegment, item_uid: str | None, source: str) -> None:
    """Put a transcript line under an outline item. `source`: ai (the AI host's own question) | host (the moderator pointed at it) | guess | edited (a researcher fixed it)."""
    if source not in ("ai", "host", "guess", "edited"):
        raise OutlineError("题号来源不对")
    line.item_uid, line.item_source = item_uid, source


# ---- revisions ---------------------------------------------------------------------------------------------------------

def propose(db: Session, org_id: str, outline_id: str, version_id: str, ops: list[dict], proposed_by: str = "ai", source: str = "editor", source_ref: str = "",
            quality: dict | None = None) -> list[Revision]:
    """Pending revisions for one request. At most three; each must target an existing item of a matching type. Nothing changes until a person accepts.

    Raises:
        OutlineError: too many operations, an unknown operation, a missing item or a type that does not fit the operation.
    """
    if len(ops) > MAX_OPS_PER_REQUEST:
        raise OutlineError(f"一次最多 {MAX_OPS_PER_REQUEST} 项修改")
    version = get_version(db, org_id, version_id)
    if version.outline_id != outline_id:
        raise OutlineError("版本不属于这份大纲", 404)
    out = []
    for o in ops:
        op = o.get("op")
        if op not in OPS:
            raise OutlineError("修改操作不对")
        if op != "insert":
            it = _item(db, version, o.get("item_uid", ""))
            if it.kind not in OPS_FOR_KIND[op]:
                raise OutlineError("这种条目不能做这个修改")
        out.append(Revision(org_id=org_id, outline_id=outline_id, base_version_id=version_id, item_uid=o.get("item_uid"), op=op, before=o.get("before", {}),
                            after=o.get("after", {}), reason=str(o.get("reason", ""))[:500], minutes_delta=float(o.get("minutes_delta", 0)),
                            destination=o.get("destination", "outline"), quality=quality or o.get("quality", {}), proposed_by=proposed_by, source=source, source_ref=source_ref))
    for r in out:
        if r.destination not in DESTINATIONS:
            raise OutlineError("去向不对")
    db.add_all(out)
    db.flush()
    return out


def accept(db: Session, org_id: str, revision_id: str, user_id: str, role: str, train_case_id: str = "") -> Revision:
    """A person accepts a pending revision: it is written into the version (destination outline) and the version's edit count goes up.

    Raises:
        OutlineError: not found; not pending; the role may not accept; a red line blocks it; a host-engine revision has no training case number.
    """
    r = db.get(Revision, revision_id)
    if r is None or r.org_id != org_id:
        raise OutlineError("修订不存在", 404)
    if role not in ACCEPT_ROLES:
        raise OutlineError("没有权限接受修改", 403)
    if r.status != "pending":
        raise OutlineError("这条修改已处理过", 409)
    if (r.quality or {}).get("blocked"):
        raise OutlineError("这条修改碰到红线，不能接受", 409)
    if r.destination == "host_engine":
        if not train_case_id.strip():
            raise OutlineError("去向是主持引擎，要先写入训练集并填用例编号")
        r.train_case_id = train_case_id.strip()[:60]
    elif r.destination == "outline":
        _apply(db, get_version(db, org_id, r.base_version_id or ""), r)
    r.status, r.decided_by, r.decided_at = "accepted", user_id, utcnow()
    db.flush()
    return r


def reject(db: Session, org_id: str, revision_id: str, user_id: str, role: str) -> Revision:
    r = db.get(Revision, revision_id)
    if r is None or r.org_id != org_id:
        raise OutlineError("修订不存在", 404)
    if role not in ACCEPT_ROLES:
        raise OutlineError("没有权限处理修改", 403)
    if r.status != "pending":
        raise OutlineError("这条修改已处理过", 409)
    r.status, r.decided_by, r.decided_at = "rejected", user_id, utcnow()
    return r


def _apply(db: Session, version: OutlineVersion, r: Revision) -> None:
    a = r.after or {}
    if r.op == "insert":
        fields = {k: v for k, v in a.items() if k in ITEM_FIELDS}
        add_item(db, version, label=a.get("label", ""), after_uid=r.item_uid, **fields)
        return
    it = _item(db, version, r.item_uid or "")
    if r.op == "rewrite":
        it.texts = {**it.texts, a.get("lang", "zh"): a["text"]}
    elif r.op == "add_followup":
        it.followups = [*it.followups, a["text"]]
    elif r.op == "rename":
        set_label(db, version, it.item_uid, a["label"])
        return
    elif r.op == "config":
        it.config = {**it.config, **a["config"]}
    elif r.op == "minutes":
        it.minutes = float(a["minutes"])
    bump(db, version)


# ---- marks -------------------------------------------------------------------------------------------------------------

def add_mark(db: Session, org_id: str, interview_id: str, line: CaptionLine, start: int, end: int, tag: str, note: str, author_id: str, author_role: str) -> Mark:
    """Circle part of a transcript line. The quote is copied from the line by the server; the question number comes from the line."""
    if line.org_id != org_id or line.interview_id != interview_id:
        raise OutlineError("笔录不存在", 404)
    if not 0 <= start < end <= len(line.text):
        raise OutlineError("圈选范围不对")
    m = Mark(org_id=org_id, interview_id=interview_id, line_id=line.id, item_uid=line.item_uid, quote=line.text[start:end], start=start, end=end, tag=tag, note=note,
             author_id=author_id, author_role="client" if author_role == "viewer" else "researcher")
    db.add(m)
    db.flush()
    return m


def resolve_mark(db: Session, org_id: str, mark_id: str, revision_id: str | None = None) -> Mark:
    """A mark is solved, normally by the revision that fixed it."""
    m = db.get(Mark, mark_id)
    if m is None or m.org_id != org_id:
        raise OutlineError("标记不存在", 404)
    if revision_id:
        r = db.get(Revision, revision_id)
        if r is None or r.org_id != org_id:
            raise OutlineError("修订不存在", 404)
    m.status, m.resolved_revision_id = "resolved", revision_id
    return m


# ---- focus questions and coded answers ---------------------------------------------------------------------------------

def add_focus(db: Session, org_id: str, project_id: str, text: str, asked_by: str, asked_role: str, timing: str = "before", item_uid: str | None = None) -> FocusQuestion:
    f = FocusQuestion(org_id=org_id, project_id=project_id, text=text[:500], asked_by=asked_by, asked_role="client" if asked_role == "viewer" else "researcher",
                      timing=timing, item_uid=item_uid)
    db.add(f)
    db.flush()
    return f


def add_coded(db: Session, org_id: str, focus_id: str, interview_id: str, line_id: str, code: str, confidence: float, origin: str = "asked",
              mentioned_in_item_uid: str | None = None) -> CodedAnswer:
    """A code the AI gave. It is remembered in `ai_code` and never overwritten."""
    f = db.get(FocusQuestion, focus_id)
    if f is None or f.org_id != org_id:
        raise OutlineError("关注问题不存在", 404)
    c = CodedAnswer(org_id=org_id, focus_id=focus_id, interview_id=interview_id, line_id=line_id, code=code, ai_code=code, confidence=max(0.0, min(1.0, confidence)),
                    origin=origin, mentioned_in_item_uid=mentioned_in_item_uid if origin == "volunteered" else None)
    db.add(c)
    db.flush()
    return c


def human_recode(db: Session, org_id: str, coded_id: str, code: str) -> CodedAnswer:
    """A person changes the code (or confirms it by giving the same one). The AI's first code stays in `ai_code` for calibration."""
    c = db.get(CodedAnswer, coded_id)
    if c is None or c.org_id != org_id:
        raise OutlineError("编码回答不存在", 404)
    c.code, c.coded_by, c.confirmed = code, "human", True
    return c


def add_decision(db: Session, org_id: str, interview_id: str, line_id: str, action: str, reason: str, saturation: float, uncovered: list[str]) -> HostDecision:
    d = HostDecision(org_id=org_id, interview_id=interview_id, line_id=line_id, action=action, reason=reason[:300], saturation=saturation, uncovered=uncovered)
    db.add(d)
    db.flush()
    return d


def purge_interview(db: Session, interview_id: str) -> None:
    """Delete what the workbench keeps about one interview's people (retention): marks, coded answers and the AI host's decision notes. Snapshots hold only the outline."""
    for model in (Mark, CodedAnswer, HostDecision):
        db.execute(delete(model).where(model.interview_id == interview_id))
