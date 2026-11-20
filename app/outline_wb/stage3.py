"""「预览测试」的规则: what a participant will see, a rule-based rehearsal, the pilot transcript with question numbers / automatic findings / marks, the fix suggestions
(three destinations), and publishing (with the bridge that keeps the older pages — respondent table, room outline, board — reading the published outline).

Everything is plain code; the model only words a rewrite (writer.fix_question). Callers commit. Raises OutlineError with a plain-Chinese message.
"""
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import guide_text, outline_versions
from ..models import CaptionLine, Interview, Project, TranscriptSegment
from . import flow, quality, service as svc, session as sess, writer
from .models import FocusQuestion, HostDecision, Mark, Outline, OutlineVersion, RehearsalRun, Revision

CONFUSED = re.compile(r"什么意思|是什么意思|不明白|没听懂|听不懂|不太懂|指什么|指的是|what do you mean|don't understand|not sure what", re.I)
TAG_DEST = {"unclear": "outline", "leading": "outline", "long": "outline", "missed": "outline", "material": "material", "host": "host_engine", "other": "outline"}
TAG_TEXT = {"unclear": "题目不清楚", "leading": "诱导", "missed": "漏追问", "material": "物料问题", "host": "主持问题", "long": "太长", "other": "其他"}
OVERTIME = 1.5     # a question that took more than this × its planned time is reported
REHEARSAL_PERSONAS = (("爽快型", 0.8), ("普通型", 1.0), ("慢热型", 1.3))   # how fast the simulated people answer (× the planned minutes)
HARD_WORDS = ("修护", "功效", "成分", "渗透", "屏障", "活性", "抗氧化")   # words many people do not know: a simulated low-knowledge person asks what they mean


def _lang(proj: Project) -> str:
    return "zh" if proj.language not in ("zh", "en") else proj.language


# ---- preview -----------------------------------------------------------------------------------------------------------------------

def preview(db: Session, proj: Project, lang: str | None = None) -> dict:
    """What a participant will see in this language: the items in order (watermark on a concept card, view-only video, website task in a new tab with screen sharing) and the checks."""
    o, v, st = sess.ensure(db, proj)
    ctx = sess.context_of(db, proj, st)
    lang = lang or ctx["language"]
    items = svc.items_of(db, v.id)
    out, checks, missing = [], [], 0
    for it in items:
        if it.is_backup:
            continue
        text = it.texts.get(lang) or ""
        if not text:
            missing += 1
        out.append({"uid": it.item_uid, "label": it.label, "kind": it.kind, "text": text, "missing": not text, "watermark": it.kind == "concept", "view_only": it.kind in ("concept", "video"),
                    "new_tab": it.kind == "website", "share_screen": it.kind == "website", "config": {k: it.config.get(k) for k in ("title", "url", "time_limit_s", "scale") if it.config.get(k)}})
    kinds = {i["kind"] for i in out}
    if "concept" in kinds:
        checks.append({"level": "ok", "text": "概念卡：水印显示受访者编号，禁止下载"})
    if "video" in kinds:
        checks.append({"level": "ok", "text": "视频：只能观看，不能下载"})
    if "website" in kinds:
        checks.append({"level": "warn", "text": "网站任务：在新标签打开并共享屏幕；地址只做了格式检查，没有真的打开"})
    if missing:
        checks.append({"level": "warn", "text": f"{lang} 版本有 {missing} 条还没有文字，受访者选这个语言时会缺题"})
    if ctx["format"] == "text" and any(re.search(r"出声|说出来|口述", i["text"]) for i in out):
        checks.append({"level": "warn", "text": "文字访谈里有让人出声说的题"})
    if not checks:
        checks.append({"level": "ok", "text": "没有发现问题"})
    return {"language": lang, "languages": [x for x in ("zh", "en") if any(it.texts.get(x) for it in items)] or [lang], "items": out, "checks": checks}


# ---- rehearsal ---------------------------------------------------------------------------------------------------------------------

def rehearse(db: Session, proj: Project, user_id: str = "") -> dict:
    """A rule-based rehearsal of the outline as it is now with three simulated people (quick, slow, low-knowledge). It does NOT call a model: it adds up the planned minutes and
    follow-ups per person, cuts what does not fit into the length, notes which must-cover points were reached and where a person would ask what a word means. Direction only."""
    o, v, st = sess.ensure(db, proj, user_id)
    ctx = sess.context_of(db, proj, st)
    total = float((st.overview or {}).get("minutes") or ctx.get("minutes") or 45)
    items = [i for i in svc.items_of(db, v.id) if i.kind in ("question", "rating", "concept", "video", "website") and not i.is_backup]
    if not items:
        raise svc.OutlineError("大纲里还没有题，先写题目")
    covers = sum(len(i.must_cover) for i in items)
    findings: list[dict] = []
    minutes, reached = [], []
    for name, speed in REHEARSAL_PERSONAS:
        t, got = 0.0, 0
        for it in items:
            take = (it.minutes or flow.MIN_PER_QUESTION) * speed + 0.4 * len(it.followups) * speed
            if t + take > total * 1.15:
                findings.append({"id": f"cut_{it.item_uid}_{name}", "kind": "cut", "item_uid": it.item_uid, "label": it.label, "text": f"“{name}”受访者这里已经超时，{it.label} 没问完", "destination": "outline"})
                break
            t += take
            got += len(it.must_cover)
        minutes.append(round(t, 1))
        reached.append(got)
    seen: set[str] = set()
    for it in items:
        txt = it.texts.get(ctx["language"], "")
        hard = [w for w in HARD_WORDS if w in txt]
        if hard and it.item_uid not in seen:
            seen.add(it.item_uid)
            findings.append({"id": f"hard_{it.item_uid}", "kind": "confused", "item_uid": it.item_uid, "label": it.label, "text": f"{it.label} 里的“{hard[0]}”，不熟悉的人可能会问“什么意思”", "destination": "outline"})
        lim = (it.config or {}).get("time_limit_s")
        if it.kind == "website" and lim and (it.minutes or 0) * 60 > lim * 1.5:
            findings.append({"id": f"limit_{it.item_uid}", "kind": "overtime", "item_uid": it.item_uid, "label": it.label, "text": f"{it.label} 计划 {it.minutes} 分，时间上限只有 {round(lim / 60, 1)} 分钟", "destination": "material"})
    rate = (sum(reached) / (covers * len(REHEARSAL_PERSONAS))) if covers else 1.0
    run = RehearsalRun(org_id=proj.org_id, outline_id=o.id, version_id=v.id, rev=v.rev, sessions=len(REHEARSAL_PERSONAS), avg_minutes=round(sum(minutes) / len(minutes), 1), must_cover_rate=round(rate, 2),
                       findings=findings, eval_report="", created_by=user_id)
    db.add(run)
    db.flush()
    return rehearsal_view(db, proj)


def rehearsal_view(db: Session, proj: Project) -> dict:
    o, v, st = sess.ensure(db, proj)
    runs = list(db.scalars(select(RehearsalRun).where(RehearsalRun.outline_id == o.id).order_by(RehearsalRun.created_at.desc()).limit(5)))
    cur = [{"id": r.id, "at": r.created_at.isoformat(timespec="minutes"), "version_id": r.version_id, "rev": r.rev, "sessions": r.sessions, "avg_minutes": r.avg_minutes, "must_cover_rate": r.must_cover_rate,
            "findings": r.findings, "planned": float((st.overview or {}).get("minutes") or 45), "stale": r.version_id != v.id or r.rev != v.rev} for r in runs]
    return {"runs": cur, "version": {"number": v.number, "rev": v.rev}, "note": "规则模拟，不调用模型；模拟受访者不是真人，分数只说明方向，能不能用最终看真人 pilot。"}


# ---- pilot -------------------------------------------------------------------------------------------------------------------------

def pilots(db: Session, proj: Project) -> dict:
    """The project's pilot interviews, and the other interviews that could be marked as one."""
    rows = list(db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.org_id == proj.org_id, Interview.kind == "depth", Interview.purged_at.is_(None)).order_by(Interview.created_at.desc())))
    fmt = lambda i: {"id": i.id, "title": i.title, "status": i.status, "moderation": i.moderation, "version_id": i.outline_version_id, "rev": i.outline_rev}   # noqa: E731
    return {"pilots": [fmt(i) for i in rows if i.is_pilot], "candidates": [fmt(i) for i in rows if not i.is_pilot]}


def set_pilot(db: Session, proj: Project, iid: str, value: bool) -> dict:
    """Mark (or unmark) an interview of this project as a pilot run: its transcript then shows in Pilot 修正."""
    itv = db.get(Interview, iid)
    if itv is None or itv.org_id != proj.org_id or itv.project_id != proj.id:
        raise svc.OutlineError("没有这场访谈", 404)
    itv.is_pilot = bool(value)
    db.flush()
    return pilots(db, proj)


def _lines(db: Session, itv: Interview):
    caps = list(db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id).order_by(CaptionLine.seq)))
    return caps or list(db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv.id).order_by(TranscriptSegment.idx)))


def _text(ln) -> str:
    """The words of a caption line or a transcript segment."""
    return getattr(ln, "text", None) or getattr(ln, "text_orig", "") or ""


def _at(ln) -> int:
    v = getattr(ln, "at_ms", None)
    return v if v is not None else getattr(ln, "start_ms", 0)


def _itv(db: Session, proj: Project, iid: str) -> Interview:
    itv = db.get(Interview, iid)
    if itv is None or itv.org_id != proj.org_id or itv.project_id != proj.id or not itv.is_pilot:
        raise svc.OutlineError("没有这场 pilot", 404)
    return itv


def auto_findings(items: list[dict], lines: list[dict]) -> list[dict]:
    """What the code notices in a pilot transcript before anyone circles anything: a question the participant did not understand, a must-cover point nobody reached, a question never asked, one that took much longer than planned."""
    out: list[dict] = []
    skipped: list[str] = []
    by_item: dict[str, list[dict]] = {}
    for ln in lines:
        by_item.setdefault(ln["item_uid"] or "", []).append(ln)
        if ln["speaker"] == "participant" and CONFUSED.search(ln["text"]):
            out.append({"id": f"confused_{ln['id']}", "kind": "confused", "item_uid": ln["item_uid"], "line_id": ln["id"], "text": "受访者问“什么意思”", "destination": "outline"})
    for it in items:
        if it["kind"] not in ("question", "rating") or it["is_backup"]:
            continue
        mine = by_item.get(it["item_uid"], [])
        if not mine:
            skipped.append(it["label"])
            continue
        said = " ".join(m["text"] for m in mine if m["speaker"] == "participant")
        for cov in it.get("must_cover", []):
            if cov and not any(tok in said for tok in _tokens(cov)):
                out.append({"id": f"cover_{it['item_uid']}_{abs(hash(cov)) % 9999}", "kind": "uncovered", "item_uid": it["item_uid"], "line_id": "", "text": f"必问要点“{cov}”没有覆盖", "destination": "outline"})
        if it["minutes"] and len(mine) > 1:
            span = (mine[-1]["at_ms"] - mine[0]["at_ms"]) / 60000
            if span > it["minutes"] * OVERTIME and span > 1:
                out.append({"id": f"slow_{it['item_uid']}", "kind": "overtime", "item_uid": it["item_uid"], "line_id": "", "text": f"{it['label']} 用了 {span:.1f} 分，计划 {it['minutes']} 分", "destination": "outline"})
    if skipped:   # one line for all of them: a question nobody asked has nothing to rewrite, it is only worth knowing
        out.append({"id": "skipped", "kind": "skipped", "item_uid": None, "line_id": "", "text": f"有 {len(skipped)} 道题没有问到：{'、'.join(skipped[:8])}{'…' if len(skipped) > 8 else ''}", "destination": "outline"})
    return out


def _tokens(s: str) -> list[str]:
    s = re.sub(r"\W+", "", s)
    return [s[i:i + 2] for i in range(max(1, len(s) - 1))] or [s]


def pilot_view(db: Session, proj: Project, iid: str) -> dict:
    """One pilot's transcript: every line with its question number (the version the pilot ran on, by `item_uid`), the AI host's notes, the automatic findings and the marks."""
    itv = _itv(db, proj, iid)
    snap_items = []
    if itv.outline_snapshot_id:
        from .models import OutlineSnapshot
        snap = db.get(OutlineSnapshot, itv.outline_snapshot_id)
        snap_items = snap.content["items"] if snap else []
    labels = {i["item_uid"]: i["label"] for i in snap_items}
    cur = sess.ensure(db, proj)[1]
    now_labels = svc.labels_map(db, cur.id)
    notes = {d.line_id: d for d in db.scalars(select(HostDecision).where(HostDecision.interview_id == itv.id))}
    raw = [{"id": ln.id, "speaker": ln.speaker, "text": _text(ln), "at_ms": _at(ln), "item_uid": ln.item_uid, "source": ln.item_source or ""} for ln in _lines(db, itv)]
    lines = [{**ln, "label": now_labels.get(ln["item_uid"] or "") or labels.get(ln["item_uid"] or "", ""), "why": ({"action": notes[ln["id"]].action, "reason": notes[ln["id"]].reason, "saturation": notes[ln["id"]].saturation, "uncovered": notes[ln["id"]].uncovered} if ln["id"] in notes else None)} for ln in raw]
    marks = [{"id": m.id, "line_id": m.line_id, "item_uid": m.item_uid, "quote": m.quote, "tag": m.tag, "tag_text": TAG_TEXT.get(m.tag, m.tag), "note": m.note, "role": m.author_role, "status": m.status, "label": now_labels.get(m.item_uid or "", "")}
             for m in db.scalars(select(Mark).where(Mark.interview_id == itv.id).order_by(Mark.created_at))]
    finds = auto_findings(snap_items, raw)
    for f in finds:
        f["label"] = now_labels.get(f["item_uid"] or "") or labels.get(f["item_uid"] or "", "")
    qs = [{"uid": i["item_uid"], "label": now_labels.get(i["item_uid"]) or i["label"], "text": next(iter(i["texts"].values()), "")} for i in snap_items if i["kind"] in ("question", "rating") and not i["is_backup"]]
    return {"id": itv.id, "title": itv.title, "moderation": itv.moderation, "version_id": itv.outline_version_id, "rev": itv.outline_rev, "lines": lines, "findings": finds, "marks": marks, "questions": qs}


def mark(db: Session, proj: Project, iid: str, line_id: str, start: int, end: int, tag: str, note: str, user_id: str, role: str) -> dict:
    """Circle part of a pilot line. A client's circle is only a suggestion (role "client"); the quote is copied by the server."""
    itv = _itv(db, proj, iid)
    if tag not in TAG_TEXT:
        raise svc.OutlineError("标签不对")
    line = next((ln for ln in _lines(db, itv) if ln.id == line_id), None)
    if line is None:
        raise svc.OutlineError("没有这句话", 404)
    svc.add_mark(db, proj.org_id, itv.id, line, start, end, tag, note[:500], user_id, role)
    return pilot_view(db, proj, iid)


def suggest_fixes(db: Session, proj: Project, iid: str, chat, user_id: str = "") -> dict:
    """Turn the open marks and the automatic findings into pending revisions, one group per question: marks and findings about the same question are merged. Each revision names where it goes:
    the outline (a rewrite), the material, or the AI host (nothing changes in the outline; accepting it records a training case)."""
    itv = _itv(db, proj, iid)
    o, v, st = sess.ensure(db, proj, user_id)
    pv = pilot_view(db, proj, iid)
    ctx = sess.context_of(db, proj, st)
    items = {i.item_uid: i for i in svc.items_of(db, v.id)}
    groups: dict[str, dict] = {}
    decided = {x.id for x in db.scalars(select(Mark).where(Mark.interview_id == itv.id, Mark.decision != ""))}
    for m in pv["marks"]:
        if m["status"] == "open" and m["id"] not in decided:
            line = next((ln for ln in pv["lines"] if ln["id"] == m["line_id"]), None)
            g = groups.setdefault(m.get("item_uid") or (line or {}).get("item_uid") or "", {"marks": [], "finds": []})
            g["marks"].append(m)
    kept = {x.auto_key for x in db.scalars(select(Mark).where(Mark.interview_id == itv.id, Mark.origin == "auto"))}   # already a mark (pilot.persist_auto): counted once
    for f in pv["findings"]:
        if f["id"] not in kept:
            groups.setdefault(f["item_uid"] or "", {"marks": [], "finds": []})["finds"].append(f)
    made: list[Revision] = []
    for uid, g in groups.items():
        it = items.get(uid)
        if it is None or not (g["marks"] or g["finds"]):
            continue
        notes = [m["note"] or m["tag_text"] for m in g["marks"]] + [f["text"] for f in g["finds"]]
        dests = {TAG_DEST.get(m["tag"], "outline") for m in g["marks"]} | {f["destination"] for f in g["finds"]}
        before = it.texts.get(ctx["language"], "")
        if "outline" in dests and it.kind in ("question", "rating", "note") and before:
            new = writer.fix_question(chat, before, notes, ctx["language"], ctx["format"])
            q = quality.check_question(new, ctx["language"], ctx["format"])
            made += svc.propose(db, proj.org_id, o.id, v.id, [{"op": "rewrite", "item_uid": uid, "before": {"text": before}, "after": {"lang": ctx["language"], "text": new}, "reason": "；".join(notes)[:300]}],
                                proposed_by="ai", source="pilot", source_ref=g["marks"][0]["id"] if g["marks"] else "", quality=q)
        if "material" in dests and it.kind in ("concept", "video", "website", "rating"):
            made += svc.propose(db, proj.org_id, o.id, v.id, [{"op": "config", "item_uid": uid, "after": {"config": {"note": "；".join(notes)[:200]}}, "reason": "；".join(notes)[:300], "destination": "material"}], source="pilot", source_ref=g["marks"][0]["id"] if g["marks"] else "")
        if "host_engine" in dests or any(f["kind"] == "confused" for f in g["finds"]):
            made += svc.propose(db, proj.org_id, o.id, v.id, [{"op": "rewrite", "item_uid": uid, "after": {"text": "受访者问“什么意思”时，换个说法再问，不要直接解释产品"}, "reason": "；".join(notes)[:300], "destination": "host_engine"}], source="pilot", source_ref=g["marks"][0]["id"] if g["marks"] else "")
    return {"revisions": [{"id": r.id, "op": r.op, "item_uid": r.item_uid, "before": r.before, "after": r.after, "reason": r.reason, "quality": r.quality, "destination": r.destination, "status": r.status} for r in made], "view": pilot_view(db, proj, iid)}


# ---- publishing ----------------------------------------------------------------------------------------------------------------------

def publish_checks(db: Session, proj: Project) -> dict:
    """What stands in the way of publishing (blocks) and what is only worth a look (warns)."""
    o, v, st = sess.ensure(db, proj)
    ctx = sess.context_of(db, proj, st)
    items = svc.items_of(db, v.id)
    checks: list[dict] = []
    qs = [i for i in items if i.kind in ("question", "rating")]
    if not qs:
        checks.append({"id": "empty", "level": "block", "text": "还没有题"})
    blocked = [i.label for i in qs if quality.check_question(i.texts.get(ctx["language"], ""), ctx["language"], ctx["format"])["blocked"]]
    checks.append({"id": "quality", "level": "block", "text": "碰到红线的题：" + "、".join(blocked)} if blocked else {"id": "quality", "level": "ok", "text": "质检通过，没有碰红线的题"})
    sy = (ctx.get("sync") or {}).get("mismatch") or []   # 修正方案第 1 轮第 5 条: the outline and the 立项 must agree on length, format and languages
    checks.append({"id": "sync", "level": "block", "text": "和立项不一致：" + "；".join(f"{m['sub_name']} {m['label']}立项是{m['plan']}、大纲是{m['outline']}" for m in sy[:4])} if sy
                  else {"id": "sync", "level": "ok", "text": "时长、形式、语言和立项一致"})
    mods = (st.overview or {}).get("modules", [])
    todo = [m["name"] for m in mods if m.get("status") != "confirmed"]
    checks.append({"id": "modules", "level": "warn", "text": "还有模块没有确认：" + "、".join(todo)} if todo else {"id": "modules", "level": "ok", "text": "每个模块都已确认，总时长 " + str((st.overview or {}).get("minutes", "")) + " 分"})
    from . import pilot   # the pilot gates (修正方案第 1 轮第 9、10 条): a finished round, 可以正式开始, every mark decided
    checks += pilot.gates(db, proj)
    openrev = db.scalar(select(func.count()).select_from(Revision).where(Revision.outline_id == o.id, Revision.source == "pilot", Revision.status == "pending")) or 0
    if openrev:
        checks.append({"id": "pilot_open", "level": "warn", "text": f"pilot 提出的 {openrev} 条修改建议还没处理"})
    fq = list(db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id)))
    miss = [f.text for f in fq if not f.item_uid and getattr(f, "link_state", "") != "none"]
    none = [f.text for f in fq if getattr(f, "link_state", "") == "none"]
    checks.append({"id": "focus", "level": "warn", "text": f"{len(miss)} 个关注问题没有对应的题（不挡发布）"} if miss else {"id": "focus", "level": "ok", "text": "每个关注问题都对应到一道题"})
    if none:
        checks.append({"id": "focus_none", "level": "warn", "text": f"{len(none)} 个关注问题暂不对应（不挡发布）"})
    return {"checks": checks, "can_publish": not any(c["level"] == "block" for c in checks)}


def versions(db: Session, proj: Project) -> list[dict]:
    """The version table (修正方案第 1 轮第 10 条): where each version came from, its state (草稿 / Pilot 中 / 已发布 / 已被替代), how many pilot interviews ran on it and
    how many interviews used it. A version made by the removed 新建版本 button with no change and no use is left out (it is kept in the database)."""
    o, v, st = sess.ensure(db, proj)
    rows = list(db.scalars(select(OutlineVersion).where(OutlineVersion.outline_id == o.id).order_by(OutlineVersion.number.desc())))
    out = []
    from .models import PilotRound
    rounds = list(db.scalars(select(PilotRound).where(PilotRound.outline_id == o.id)))
    all_pil = list(db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.is_pilot.is_(True), Interview.purged_at.is_(None))))
    for x in rows:
        mine = [r for r in rounds if r.version_id == x.id]
        ended = sum(1 for r in mine if any(i.pilot_round_id == r.id and i.status == "ended" for i in all_pil))
        legacy = [i for i in all_pil if not i.pilot_round_id and (i.outline_version_id == x.id or (i.outline_version_id is None and i.status != "ended" and x.id == v.id))]   # pilots set before rounds existed
        ended += 1 if any(i.status == "ended" for i in legacy) else 0
        live = any(r.status == "planned" for r in mine) or any(i.status != "ended" for i in legacy)
        used = svc.usage_count(db, x.id)
        source = x.summary if x.summary.startswith("Pilot") else ("生成大纲" if not x.base_version_id else "")
        if not source and not used and not mine and not legacy and x.id != v.id:
            continue
        state = x.status if x.status in ("published", "superseded") else ("pilot" if live else "draft")
        out.append({"id": x.id, "number": x.number, "rev": x.rev, "status": x.status, "state": state, "summary": x.summary, "source": source or "手动新建（旧）",
                    "pilots": ended, "pilots_open": 1 if live and not ended else 0, "skipped": x.pilot_skipped, "round": max([r.number for r in mine] or [0]), "used_by": used, "current": x.id == v.id})
    return out


def write_legacy_text(db: Session, proj: Project, v: OutlineVersion, st) -> None:
    """Keep the pages that still read the older outline text (respondent table, room outline, board) in step with the published outline: the text is rebuilt from the version."""
    ctx = sess.context_of(db, proj, st)
    snap = svc.snapshot_for(db, v, goal="；".join(ctx["goals"]) or ctx["background"], topic=(proj.name or "")[:20])
    guide = svc.snapshot_to_guide(snap, ctx["language"], float((st.overview or {}).get("minutes") or 45))
    proj.outline_text = guide_text.guide_to_text(guide)
    outline_versions.put_text(proj, outline_versions.base_key(proj), proj.outline_text)


def publish(db: Session, proj: Project, user_id: str = "") -> dict:
    """Publish the current version: it becomes the one new interviews start on. Not a lock: it can still be edited (each edit counts in `rev`).

    Raises:
        OutlineError: a block in the checks.
    """
    o, v, st = sess.ensure(db, proj, user_id)
    chk = publish_checks(db, proj)
    if not chk["can_publish"]:
        raise svc.OutlineError("还不能发布：" + "；".join(c["text"] for c in chk["checks"] if c["level"] == "block"))
    return do_publish(db, proj, o, v, st)


def do_publish(db: Session, proj: Project, o: Outline, v: OutlineVersion, st) -> dict:
    """Make `v` the published version (the others published before become 已被替代) and keep the older pages' outline text in step."""
    for other in db.scalars(select(OutlineVersion).where(OutlineVersion.outline_id == o.id, OutlineVersion.status == "published", OutlineVersion.id != v.id)):
        other.status = "superseded"
    v.status = "published"
    o.current_version_id = v.id
    write_legacy_text(db, proj, v, st)
    db.flush()
    return {"version": v.number, "versions": versions(db, proj)}


def snapshot_on_start(db: Session, itv: Interview) -> None:
    """When an interview starts: if the project has a published outline, freeze it for this interview (nothing happens for a project without one, and a running interview keeps its snapshot)."""
    if itv.outline_snapshot_id:
        return
    proj = db.get(Project, itv.project_id)
    if proj is None or not proj.outline_id:
        return
    v = db.scalar(select(OutlineVersion).where(OutlineVersion.outline_id == proj.outline_id, OutlineVersion.status == "published"))
    if v is None and itv.is_pilot:   # a pilot is how the outline gets ready to publish: it runs on the current draft
        o = db.get(Outline, proj.outline_id)
        v = db.get(OutlineVersion, o.current_version_id) if o else None
    if v is None:
        return
    st = sess.ensure(db, proj)[2]
    ctx = sess.context_of(db, proj, st)
    svc.start_interview(db, itv, v, goal="；".join(ctx["goals"]) or ctx["background"], topic=(proj.name or "")[:20])


# ---- pilot summary and the update version ---------------------------------------------------------------------------------------------------

DEST_LABEL = {"outline": "大纲要改", "material": "物料要改", "host_engine": "问话方式"}
LEVEL_OF = {"confused": "watch", "uncovered": "watch", "overtime": "watch", "skipped": "info"}
TAG_LEVEL = {"leading": "now", "host": "now", "unclear": "watch", "missed": "watch", "material": "watch", "long": "watch", "other": "info"}


def pilot_summary(db: Session, proj: Project) -> dict:
    """Everything the pilots found, in one place and coloured by how serious it is (now = red, watch = orange, info = grey) and where it goes (outline / material / how the AI host asks).
    It is what the updated outline and the way of asking are made from."""
    o, v, st = sess.ensure(db, proj)
    pl = pilots(db, proj)["pilots"]
    items: list[dict] = []
    for p in pl:
        pv = pilot_view(db, proj, p["id"])
        for m in pv["marks"]:
            items.append({"kind": "mark", "pilot": p["title"], "level": TAG_LEVEL.get(m["tag"], "info"), "dest": TAG_DEST.get(m["tag"], "outline"), "label": m["label"], "text": f"{m['tag_text']}：{m['quote']}" + (f"（{m['note']}）" if m["note"] else ""), "who": "客户" if m["role"] == "client" else "研究员", "status": m["status"]})
        for f in pv["findings"]:
            items.append({"kind": "auto", "pilot": p["title"], "level": LEVEL_OF.get(f["kind"], "info"), "dest": f["destination"], "label": f.get("label", ""), "text": f["text"], "who": "系统", "status": "open"})
    # (the AI 快速检查 — formerly 模拟预演 — is only a reference: its findings are not pilot findings, 修正方案第 1 轮第 9 条)
    revs = list(db.scalars(select(Revision).where(Revision.outline_id == o.id, Revision.source == "pilot").order_by(Revision.created_at)))
    by_dest = {d: {"label": lab, "n": 0, "open": 0} for d, lab in DEST_LABEL.items()}
    for it in items:
        g = by_dest.get(it["dest"], by_dest["outline"])
        g["n"] += 1
        g["open"] += 1 if it["status"] == "open" else 0
    host = [{"id": r.id, "text": (r.after or {}).get("text", ""), "reason": r.reason, "status": r.status, "case": r.train_case_id} for r in revs if r.destination == "host_engine"]
    accepted = [r for r in revs if r.status == "accepted"]
    ended = sum(1 for p in pl if p["status"] == "ended")
    return {"pilots": len(pl), "ended": ended, "items": items, "groups": [{"dest": d, **g} for d, g in by_dest.items()], "host": host,
            "revisions": {"pending": sum(1 for r in revs if r.status == "pending"), "accepted": len(accepted), "rejected": sum(1 for r in revs if r.status == "rejected")},
            "can_update": ended > 0 and bool(accepted), "required": "必须先做完至少一场 pilot，再发布" if not ended else ""}


def update_outline(db: Session, proj: Project, user_id: str = "") -> dict:
    """Make the next version from what the pilots led to: the accepted outline changes are already in the current version, so this freezes that state as a new version (so the pilot's version stays as
    it was), names what changed, and lists the accepted "how to ask" cases for the AI host.

    Raises:
        OutlineError: no pilot is finished, or no suggestion has been accepted yet.
    """
    sm = pilot_summary(db, proj)
    if not sm["ended"]:
        raise svc.OutlineError("必须先做完至少一场 pilot")
    if not sm["can_update"]:
        raise svc.OutlineError("还没有接受任何 pilot 的修改建议")
    o, v, st = sess.ensure(db, proj, user_id)
    acc = list(db.scalars(select(Revision).where(Revision.outline_id == o.id, Revision.source == "pilot", Revision.status == "accepted")))
    n = {d: sum(1 for r in acc if r.destination == d) for d in DEST_LABEL}
    for m in db.scalars(select(Mark).where(Mark.org_id == proj.org_id, Mark.status == "open", Mark.interview_id.in_([p["id"] for p in pilots(db, proj)["pilots"]] or [""]))):
        rid = next((r.id for r in acc if r.source_ref == m.id), None)
        if rid:
            m.status, m.resolved_revision_id = "resolved", rid
    nv = svc.new_version(db, v, user_id, f"Pilot 修正：大纲 {n['outline']} 处、物料 {n['material']} 处、问话方式 {n['host_engine']} 条")
    if v.status == "draft":   # the draft it came from is replaced at once (a published one stays the one new interviews run on until the new one is published)
        v.status = "superseded"
    db.flush()
    return {"version": nv.number, "summary": nv.summary, "host": [h for h in sm["host"] if h["status"] == "accepted"], "versions": versions(db, proj)}
