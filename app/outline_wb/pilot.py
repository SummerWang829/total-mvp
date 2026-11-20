"""真人 Pilot 多轮（修正方案第 1 轮第 9、10 条，2026-11-19 用户确认效果图 qa_shots/fix1_batch2_mockup.html）.

A round runs on one outline version: one or more real interviews (a participant from the list or a colleague) with the team watching hidden. In the room the
team marks problems (time and question number set by the server); the code adds automatic findings. After the round every mark is decided — accepted,
rejected or deferred (暂缓); accepted changes are written into a NEW version (the round's version stays as it ran). The researcher says "可以正式开始" before
publishing; a client who does not want a pilot can skip it (跳过 Pilot，直接上线: only the red-line check stays).

Plain code, no model call here (suggestions reuse stage3.suggest_fixes). Callers commit. Raises OutlineError with a plain-Chinese message.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import mailer
from ..config import settings
from ..models import CaptionLine, Interview, InterviewObserver, Participant, Project, User, utcnow
from . import service as svc, session as sess, stage3
from .models import Mark, OutlineVersion, PilotRound, Revision

ROOM_ROLES = ("admin", "researcher", "support", "moderator", "observer")   # who may mark in a pilot room (never the participant)
TEAM_ROLES = ("admin", "researcher", "support")                              # who sees the names of the people who marked
INVITE_ROLES = ("admin", "researcher", "support", "observer", "moderator")   # who can be invited by email to watch
DECISIONS = ("accepted", "rejected", "deferred")
ROLE_NAME = {"admin": "研究员", "researcher": "研究员", "support": "研究员", "moderator": "主持人", "observer": "客户", "auto": "AI 自动"}
MAX_NOTE = 500


def _round(db: Session, proj: Project, round_id: str) -> PilotRound:
    o = sess.ensure(db, proj)[0]
    r = db.get(PilotRound, round_id)
    if r is None or r.org_id != proj.org_id or r.outline_id != o.id:
        raise svc.OutlineError("没有这一轮 pilot", 404)
    return r


def _interviews(db: Session, r: PilotRound) -> list[Interview]:
    return list(db.scalars(select(Interview).where(Interview.pilot_round_id == r.id, Interview.org_id == r.org_id, Interview.purged_at.is_(None)).order_by(Interview.created_at)))


def _marks(db: Session, itv_ids: list[str]) -> list[Mark]:
    return list(db.scalars(select(Mark).where(Mark.interview_id.in_(itv_ids or [""])).order_by(Mark.created_at))) if itv_ids else []


def _counts(marks: list[Mark]) -> dict:
    return {"marks": len(marks), "open": sum(1 for m in marks if not m.decision), **{d: sum(1 for m in marks if m.decision == d) for d in DECISIONS}}


def rounds(db: Session, proj: Project) -> dict:
    """The 本轮 Pilot page: every round (version, interviews, state, how its marks were decided), the people who can be invited, and whether the outline was published or skipped."""
    o, v, st = sess.ensure(db, proj)
    rows = list(db.scalars(select(PilotRound).where(PilotRound.outline_id == o.id).order_by(PilotRound.number)))
    nums = {x.id: x.number for x in db.scalars(select(OutlineVersion).where(OutlineVersion.outline_id == o.id))}
    out = []
    for r in rows:
        its = _interviews(db, r)
        parts = {p.id: p for p in db.scalars(select(Participant).where(Participant.id.in_([i.participant_id for i in its] or [""])))}
        out.append({"id": r.id, "number": r.number, "version": nums.get(r.version_id, 0), "status": r.status, "next_version": nums.get(r.next_version_id, 0),
                    "ended": sum(1 for i in its if i.status == "ended"),
                    "interviews": [{"id": i.id, "status": i.status, "moderation": i.moderation, "scheduled_at": i.scheduled_at.isoformat() if i.scheduled_at else None,
                                    "who": (parts[i.participant_id].code if i.participant_id in parts else "")} for i in its],
                    **_counts(_marks(db, [i.id for i in its]))})
    people = [{"id": u.id, "name": u.name, "role": u.role, "role_name": ROLE_NAME.get(u.role, u.role), "email": bool(u.email)}
              for u in db.scalars(select(User).where(User.org_id == proj.org_id, User.active.is_(True), User.role.in_(INVITE_ROLES)).order_by(User.name))]
    published = v.status == "published"
    return {"rounds": out, "version": {"number": v.number, "status": v.status, "skipped": v.pilot_skipped, "ready": bool(v.ready_at)}, "people": people,
            "next_number": (rows[-1].number + 1) if rows else 1, "host": proj.host_mode or "human", "published": published, "mail": mailer.configured(),
            "can_skip": not rows and not published}


def open_round(db: Session, proj: Project, user_id: str) -> PilotRound:
    """The round the next pilot interview joins: the newest round on the current version while it is not reviewed, else a new one."""
    o, v, st = sess.ensure(db, proj, user_id)
    last = db.scalar(select(PilotRound).where(PilotRound.outline_id == o.id).order_by(PilotRound.number.desc()))
    if last and last.version_id == v.id and last.status == "planned":
        return last
    r = PilotRound(org_id=proj.org_id, outline_id=o.id, version_id=v.id, number=(last.number + 1) if last else 1, created_by=user_id)
    db.add(r)
    db.flush()
    return r


def attach(db: Session, proj: Project, itv: Interview, r: PilotRound, watcher_ids: list[str]) -> list[dict]:
    """Make a new interview a pilot of this round and invite the chosen people to watch hidden. Returns, per person, whether an email was queued (or the link
    to copy when emails are not set up). Only the room address goes in the email: the person signs in with their own account (no token in the mail)."""
    itv.is_pilot, itv.pilot_round_id = True, r.id
    users = list(db.scalars(select(User).where(User.org_id == proj.org_id, User.id.in_(watcher_ids or [""]), User.active.is_(True), User.role.in_(INVITE_ROLES))))
    have = {o.user_id for o in itv.observers}
    link = f"{settings.public_base_url}/room.html?i={itv.id}&as=observer"
    when = itv.scheduled_at.strftime("%Y-%m-%d %H:%M") + "（UTC）" if itv.scheduled_at else "时间待定"
    out = []
    for u in users:
        if u.role in ("observer", "moderator") and u.id not in have:   # they only see the interviews they are assigned to
            itv.observers.append(InterviewObserver(user_id=u.id))
        status = "link"
        if mailer.configured() and u.email:
            try:
                mailer.queue(db, u.email, "pilot_observe", f"{u.name}，您好：\n\n邀请您旁听一场大纲 Pilot（第 {r.number} 轮），{when}。\n进入方式：用您自己的账号登录后打开 {link}\n您在房间里是隐身的，可以在右侧“标记问题”。\n",
                             proj.org_id, subject=f"Pilot 第 {r.number} 轮旁听邀请", ref_id=itv.id)
                status = "queued"
            except RuntimeError:
                status = "link"
        out.append({"user_id": u.id, "name": u.name, "status": status, "link": link})
    db.flush()
    return out


# ---- marks in the room ------------------------------------------------------------------------------------------------------------

def _pilot(db: Session, itv: Interview) -> PilotRound:
    if not itv.is_pilot or not itv.pilot_round_id:
        raise svc.OutlineError("这场不是 pilot", 404)
    r = db.get(PilotRound, itv.pilot_round_id)
    if r is None or r.org_id != itv.org_id:
        raise svc.OutlineError("这场不是 pilot", 404)
    return r


def _now_ms(db: Session, itv: Interview) -> tuple[int, CaptionLine | None]:
    """Where in the interview we are now, from the server's clock and the interview's start, snapped to the latest caption line (never the browser's time)."""
    last = db.scalar(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq.desc()))
    started = itv.started_at
    ms = int((utcnow() - started).total_seconds() * 1000) if started else (last.at_ms if last else 0)
    if last and abs(ms - last.at_ms) < 20_000:
        ms = max(ms, last.at_ms)
    return max(0, ms), last


def _current_item(db: Session, itv: Interview, last: CaptionLine | None) -> str | None:
    if itv.current_item_uid:
        return itv.current_item_uid
    row = db.scalar(select(CaptionLine).where(CaptionLine.interview_id == itv.id, CaptionLine.item_uid.is_not(None)).order_by(CaptionLine.seq.desc()))
    return row.item_uid if row else None


def _labels(db: Session, itv: Interview) -> dict[str, str]:
    from .models import OutlineSnapshot
    snap = db.get(OutlineSnapshot, itv.outline_snapshot_id) if itv.outline_snapshot_id else None
    return {i["item_uid"]: i["label"] for i in (snap.content.get("items", []) if snap else [])}


def _mark_view(m: Mark, labels: dict, names: dict, team: bool) -> dict:
    who = ROLE_NAME["auto"] if m.origin == "auto" else (names.get(m.author_id) if team and names.get(m.author_id) else ROLE_NAME.get(m.author_role, "研究员"))
    return {"id": m.id, "who": who, "origin": m.origin, "at_ms": m.at_ms, "label": labels.get(m.item_uid or "", ""), "item_uid": m.item_uid, "note": m.note or m.quote,
            "decision": m.decision, "interview_id": m.interview_id}


def room_marks(db: Session, itv: Interview, role: str) -> dict:
    """The marks panel in a pilot room: everybody's marks and the automatic findings so far, newest last. The research team sees names; a client or observer sees roles."""
    if not itv.is_pilot or not itv.pilot_round_id:
        return {"pilot": False, "marks": [], "label": ""}   # an ordinary room: no panel (a plain answer, not an error the browser would log)
    persist_auto(db, itv)
    labels, team = _labels(db, itv), role in TEAM_ROLES
    ms = _marks(db, [itv.id])
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_([m.author_id for m in ms] or [""])))}
    return {"pilot": True, "marks": [_mark_view(m, labels, names, team) for m in ms], "label": labels.get(_current_item(db, itv, None) or "", "")}


def add_room_mark(db: Session, itv: Interview, user_id: str, role: str, note: str, tag: str = "other") -> Mark:
    """Someone in the pilot room marks a problem. The time and the question are the server's: now, and the question being asked now.

    Raises:
        OutlineError: not a pilot; the role may not mark; nothing written; the interview has ended.
    """
    r = _pilot(db, itv)
    if role not in ROOM_ROLES:
        raise svc.OutlineError("没有权限标记", 403)
    note = (note or "").strip()[:MAX_NOTE]
    if not note:
        raise svc.OutlineError("写一句或说一句是什么问题")
    if itv.status == "ended":
        raise svc.OutlineError("这场 pilot 已结束，到“Pilot 修正”里圈笔录", 409)
    ms, last = _now_ms(db, itv)
    m = Mark(org_id=itv.org_id, interview_id=itv.id, line_id=last.id if last else "", item_uid=_current_item(db, itv, last), quote="", start=0, end=0,
             tag=tag if tag in stage3.TAG_TEXT else "other", note=note, author_id=user_id, author_role="observer" if role == "observer" else "researcher" if role in TEAM_ROLES else role,
             origin="room", at_ms=ms, round_id=r.id)
    db.add(m)
    db.flush()
    return m


def persist_auto(db: Session, itv: Interview) -> int:
    """Write the automatic findings of a pilot interview as marks (once each), so they are decided like any other mark. Returns how many were added."""
    if not itv.is_pilot or not itv.pilot_round_id:
        return 0
    from .models import OutlineSnapshot
    snap = db.get(OutlineSnapshot, itv.outline_snapshot_id) if itv.outline_snapshot_id else None
    if snap is None:
        return 0
    lines = [{"id": ln.id, "speaker": ln.speaker, "text": stage3._text(ln), "at_ms": stage3._at(ln), "item_uid": ln.item_uid} for ln in stage3._lines(db, itv)]
    found = stage3.auto_findings(snap.content.get("items", []), lines) if lines else []
    if itv.status != "ended":   # what was never asked is only known at the end
        found = [f for f in found if f["kind"] == "confused"]
    have = {m.auto_key for m in db.scalars(select(Mark).where(Mark.interview_id == itv.id, Mark.origin == "auto"))}
    at = {ln["id"]: ln["at_ms"] for ln in lines}
    n = 0
    for f in found:
        if f["id"] in have:
            continue
        db.add(Mark(org_id=itv.org_id, interview_id=itv.id, line_id=f.get("line_id") or "", item_uid=f.get("item_uid"), note=f["text"], tag="material" if f.get("destination") == "material" else "unclear",
                    author_role="auto", origin="auto", auto_key=f["id"][:80], at_ms=at.get(f.get("line_id") or ""), round_id=itv.pilot_round_id))
        n += 1
    db.flush()
    return n


# ---- Pilot 修正 -----------------------------------------------------------------------------------------------------------------

def review(db: Session, proj: Project, round_id: str, role: str) -> dict:
    """One round after (or during) its interviews: every mark with what was suggested for it and how it was decided."""
    r = _round(db, proj, round_id)
    its = _interviews(db, r)
    for i in its:
        persist_auto(db, i)
    ms = _marks(db, [i.id for i in its])
    labels: dict[str, str] = {}
    for i in its:
        labels.update(_labels(db, i))
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_([m.author_id for m in ms] or [""])))}
    revs = list(db.scalars(select(Revision).where(Revision.round_id == r.id).order_by(Revision.created_at)))
    by_mark: dict[str, list[dict]] = {}
    for x in revs:
        by_mark.setdefault(x.source_ref, []).append({"id": x.id, "op": x.op, "destination": x.destination, "after": x.after, "before": x.before, "status": x.status, "blocked": bool((x.quality or {}).get("blocked"))})
    nums = {x.id: x.number for x in db.scalars(select(OutlineVersion).where(OutlineVersion.outline_id == r.outline_id))}
    return {"id": r.id, "number": r.number, "version": nums.get(r.version_id, 0), "status": r.status, "next_version": nums.get(r.next_version_id, 0),
            "ended": sum(1 for i in its if i.status == "ended"), "interviews": len(its),
            "marks": [{**_mark_view(m, labels, names, role in TEAM_ROLES), "suggestions": by_mark.get(m.id, [])} for m in ms], "counts": _counts(ms),
            "accepted_changes": sum(1 for x in revs if x.status == "accepted" and x.destination == "outline")}


def suggest(db: Session, proj: Project, round_id: str, chat, user_id: str = "") -> dict:
    """Turn the round's undecided marks into pending changes (per interview, the existing merge-by-question), tied to this round."""
    r = _round(db, proj, round_id)
    for i in _interviews(db, r):
        persist_auto(db, i)
        before = {x.id for x in db.scalars(select(Revision).where(Revision.outline_id == r.outline_id))}
        stage3.suggest_fixes(db, proj, i.id, chat, user_id)
        for x in db.scalars(select(Revision).where(Revision.outline_id == r.outline_id, Revision.source == "pilot")):
            if x.id not in before:
                x.round_id = r.id
    db.flush()
    return review(db, proj, round_id, "researcher")


def decide(db: Session, proj: Project, mark_id: str, decision: str, user_id: str, role: str) -> dict:
    """接受 / 拒绝 / 暂缓 one mark. Accepting or rejecting also decides the changes suggested for it; nothing is written into the outline here (that happens when the next version is made).

    Raises:
        OutlineError: unknown decision or mark; a red line blocks the accepted change.
    """
    if decision not in DECISIONS:
        raise svc.OutlineError("处理方式不对")
    m = db.get(Mark, mark_id)
    if m is None or m.org_id != proj.org_id or not m.round_id:
        raise svc.OutlineError("没有这条标记", 404)
    r = _round(db, proj, m.round_id)
    if r.status == "reviewed":
        raise svc.OutlineError("这一轮已经生成了下一版，不能再改", 409)
    for x in db.scalars(select(Revision).where(Revision.round_id == r.id, Revision.source_ref == m.id, Revision.status == "pending")):
        if decision == "accepted" and x.destination != "host_engine":
            svc.accept(db, proj.org_id, x.id, user_id, role)
        elif decision == "rejected":
            svc.reject(db, proj.org_id, x.id, user_id, role)
    m.decision = decision
    if decision != "deferred":
        m.status = "resolved"
    db.flush()
    return review(db, proj, r.id, role)


def next_version(db: Session, proj: Project, round_id: str, user_id: str = "") -> dict:
    """Make the next version from one round: a copy of the version the round ran on, with the round's accepted outline changes written in. The round's version stays
    exactly as it ran (it becomes 已被替代 when it was a draft).

    Raises:
        OutlineError: no interview of the round has ended; marks are still undecided; the round already made its version.
    """
    r = _round(db, proj, round_id)
    if r.status == "reviewed":
        raise svc.OutlineError("这一轮已经生成过下一版", 409)
    its = _interviews(db, r)
    if not any(i.status == "ended" for i in its):
        raise svc.OutlineError("这一轮还没有一场 pilot 做完")
    for i in its:
        persist_auto(db, i)
    undecided = sum(1 for m in _marks(db, [i.id for i in its]) if not m.decision)
    if undecided:
        raise svc.OutlineError(f"还有 {undecided} 条标记没处理（接受、拒绝或暂缓）")
    base = db.get(OutlineVersion, r.version_id)
    acc = list(db.scalars(select(Revision).where(Revision.round_id == r.id, Revision.status == "accepted", Revision.destination == "outline").order_by(Revision.created_at)))
    nv = svc.new_version(db, base, user_id, f"Pilot 第 {r.number} 轮修正 · {len(acc)} 处")
    for x in acc:
        if x.item_uid and x.op != "insert" and not any(i.item_uid == x.item_uid for i in svc.items_of(db, nv.id)):
            continue   # the question was deleted meanwhile
        svc._apply(db, nv, x)   # noqa: SLF001 - the same writer the editor uses
    if base.status == "draft":
        base.status = "superseded"
    r.status, r.next_version_id = "reviewed", nv.id
    db.flush()
    return {"version": nv.number, "changes": len(acc), "rounds": rounds(db, proj)}


def ready(db: Session, proj: Project, user_id: str, value: bool = True) -> dict:
    """The researcher says the current version can go live (可以正式开始), or takes it back."""
    v = sess.ensure(db, proj, user_id)[1]
    v.ready_by, v.ready_at = (user_id, utcnow()) if value else ("", None)
    db.flush()
    return {"ready": bool(v.ready_at)}


def gates(db: Session, proj: Project) -> list[dict]:
    """The pilot part of the publish checks: at least one round with a finished interview, the researcher's 可以正式开始, every mark decided (deferred counts, and is said)."""
    o, v, st = sess.ensure(db, proj)
    rs = list(db.scalars(select(PilotRound).where(PilotRound.outline_id == o.id)))
    its = [i for r in rs for i in _interviews(db, r)]
    old = db.scalar(select(Interview).where(Interview.project_id == proj.id, Interview.is_pilot.is_(True), Interview.pilot_round_id.is_(None), Interview.status == "ended"))
    done = sum(1 for r in rs if any(i.status == "ended" for i in _interviews(db, r))) + (1 if old else 0)
    marks = _marks(db, [i.id for i in its])
    # 修正方案第 5 轮 (用户 2026-11-20): “可以正式开始” is no longer a separate step — 发布这一版 asks “Pilot 的问题都处理完了吗？” and records it
    out = [{"id": "pilot", "level": "ok", "text": f"已做完 {done} 轮 pilot"} if done else {"id": "pilot", "level": "block", "text": "还没有做完一场 pilot：先在“本轮 Pilot”发起一轮；客户不做的话可以选“跳过 Pilot，直接上线”"}]
    open_n, deferred = sum(1 for m in marks if not m.decision), sum(1 for m in marks if m.decision == "deferred")
    out.append({"id": "marks", "level": "block", "text": f"还有 {open_n} 条标记没处理（接受、拒绝或暂缓）"} if open_n else
               {"id": "marks", "level": "ok", "text": f"所有标记都已处理（接受 {sum(1 for m in marks if m.decision == 'accepted')} · 拒绝 {sum(1 for m in marks if m.decision == 'rejected')} · 暂缓 {deferred}）"})
    if deferred:
        out.append({"id": "deferred", "level": "warn", "text": f"{deferred} 条标记暂缓（不挡发布，记进这个版本的记录）"})
    return out


def skip_publish(db: Session, proj: Project, user_id: str = "") -> dict:
    """跳过 Pilot，直接上线: publish the current version with only the red-line check (the client has seen the outline and wants to start).

    Raises:
        OutlineError: a question breaks a red line, or there are no questions.
    """
    o, v, st = sess.ensure(db, proj, user_id)
    chk = [c for c in stage3.publish_checks(db, proj)["checks"] if c["id"] in ("quality", "empty", "sync")]
    if any(c["level"] == "block" for c in chk):
        raise svc.OutlineError("还不能上线：" + "；".join(c["text"] for c in chk if c["level"] == "block"))
    v.pilot_skipped = True
    return stage3.do_publish(db, proj, o, v, st)

