"""The state of one project's "生成大纲" page: what the page shows (`view`) and what each action does. It stitches the rules (flow.py), the model's help (writer.py), the quality gate
(quality.py) and the stored data (models.py / service.py) together. Callers commit.

Raises OutlineError (service.py) with a plain-Chinese message when an action is not possible.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..creation import stages
from ..models import Project, RecruitForm
from . import flow, quality, service as svc, writer
from .models import FocusQuestion, Outline, OutlineItem, OutlineVersion, Revision, StudySetting

STEPS = [("goal", "目标"), ("people", "人群与执行"), ("overview", "大纲总览"), ("questions", "题目"), ("focus", "关注问题")]
ROUNDS = (1, 2, 3)
MAX_CHAT_OUTLINE = 60


def ensure(db: Session, proj: Project, user_id: str = "") -> tuple[Outline, OutlineVersion, StudySetting]:
    """The project's outline, its current version and its setting row (all made on first use)."""
    o = db.get(Outline, proj.outline_id) if proj.outline_id else None
    if o is None:
        o, v = svc.create_outline(db, proj.org_id, proj.id, "访谈大纲", user_id)
    else:
        v = db.get(OutlineVersion, o.current_version_id)
    st = db.scalar(select(StudySetting).where(StudySetting.outline_id == o.id))
    if st is None:
        st = StudySetting(org_id=proj.org_id, outline_id=o.id, background=proj.brief or "", gates={}, answers={}, overview={}, rounds={})
        db.add(st)
        db.flush()
    return o, v, st


def context_of(db: Session, proj: Project, st: StudySetting) -> dict:
    """What the project already says (read, not asked again): background, goals, the people, minutes, language, format, and flags kept with the overview."""
    f = db.get(RecruitForm, proj.id)
    try:
        minutes = stages.load(proj).decisions.get("minutes")
    except (ValueError, TypeError, KeyError):
        minutes = None
    fmts = (proj.formats or {}).get("depth") or []
    lang = proj.language if proj.language in ("zh", "en") else "zh"
    if settings.moderator_provider == "mock":
        lang = "zh"   # the offline demo only has Chinese sample questions
    return {"background": " ".join((proj.brief or "").split()), "goals": [g for g in (f.goals if f else []) if str(g).strip()], "audience": (f.need if f else "") or "", "minutes": minutes,
            "language": lang, "format": fmts[0] if fmts else "video", "host": proj.host_mode, "rotate": bool((st.overview or {}).get("context", {}).get("rotate"))}


def _slots(ctx: dict, st: StudySetting) -> dict:
    return flow.known(ctx, st.answers or {})


def _ov(st: StudySetting) -> dict:
    """A copy of the overview to work on. Never change `st.overview` in place: the database would see the old and new value as equal and save nothing; `_save_ov` stores the copy."""
    return json.loads(json.dumps(st.overview or {"modules": [], "dismissed": [], "context": {}}))


def _save_ov(st: StudySetting, ov: dict) -> None:
    st.overview = ov
    st.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _items_by_module(db: Session, version_id: str) -> dict[str, list[OutlineItem]]:
    out: dict[str, list[OutlineItem]] = {}
    for it in svc.items_of(db, version_id):
        out.setdefault(it.module, []).append(it)
    return out


def _item_view(it: OutlineItem, lang: str) -> dict:
    return {"uid": it.item_uid, "label": it.label, "kind": it.kind, "text": it.texts.get(lang) or next(iter(it.texts.values()), ""), "followups": it.followups, "must_cover": it.must_cover, "minutes": it.minutes,
            "backup": it.is_backup, "depth": (it.config or {}).get("depth", "shallow"), "config": it.config}


def view(db: Session, proj: Project, user_id: str = "") -> dict:
    """Everything the page needs in one answer."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    slots = _slots(ctx, st)
    ov = _ov(st)
    mods = [dict(m) for m in ov.get("modules", [])]   # a copy: the page adds the questions to each module, and that must not be saved back
    by_mod = _items_by_module(db, v.id)
    asked = len(st.answers or {})
    rounds = {r: flow.next_questions(slots, r, asked) for r in ROUNDS}
    for m in mods:
        m["items"] = [_item_view(i, ctx["language"]) for i in by_mod.get(m["id"], [])]
    gates = st.gates or {}
    step = "focus" if gates.get("questions") else "questions" if gates.get("overview") else "overview" if mods else "people" if gates.get("goal") else "goal"
    pending = [{"id": r.id, "op": r.op, "item_uid": r.item_uid, "before": r.before, "after": r.after, "reason": r.reason, "quality": r.quality, "destination": r.destination, "status": r.status}
               for r in db.scalars(select(Revision).where(Revision.outline_id == o.id).order_by(Revision.created_at.desc()).limit(30))]
    dismissed = set(ov.get("dismissed", []))
    sugg = flow.suggest(mods, ctx, slots, dismissed) if mods else []
    from . import stage3
    from ..models import Interview
    done = db.scalar(select(func.count()).select_from(Interview).where(Interview.project_id == proj.id, Interview.is_pilot.is_(True), Interview.status == "ended")) or 0
    return {"pilot_done": done, "versions": stage3.versions(db, proj), "outline_id": o.id, "version": {"id": v.id, "number": v.number, "rev": v.rev, "status": v.status, "used_by": svc.usage_count(db, v.id)}, "context": ctx,
            "slots": {k: {"value": s["value"], "source": s["source"], "confidence": s["confidence"]} for k, s in slots.items()}, "round_questions": rounds, "step": step, "gates": gates,
            "steps": [{"key": k, "label": lab} for k, lab in STEPS], "overview": {"modules": mods, "totals": flow.totals(mods) if mods else {}, "findings": flow.findings(mods, ctx, slots) if mods else [],
            "depth_labels": flow.DEPTH_LABELS}, "suggestions": sugg, "revisions": pending, "focus": [_focus_view(f, v.id, db) for f in db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id))],
            "items": [_item_view(i, ctx["language"]) | {"module": i.module} for i in svc.items_of(db, v.id)][:MAX_CHAT_OUTLINE * 3]}


def _focus_view(f: FocusQuestion, version_id: str, db: Session) -> dict:
    return {"id": f.id, "text": f.text, "role": f.asked_role, "timing": f.timing, "item_uid": f.item_uid, "label": svc.label_of(db, version_id, f.item_uid) if f.item_uid else "", "coding": f.coding}


def confirm_goal(db: Session, proj: Project, user_id: str = "") -> None:
    _, _, st = ensure(db, proj, user_id)
    st.gates = {**(st.gates or {}), "goal": True}


def answer(db: Session, proj: Project, slot: str, value, user_id: str = "") -> None:
    """Record the researcher's answer to one slot (it then counts as sure)."""
    if slot not in {s.name for s in flow.SLOT_DEFS}:
        raise svc.OutlineError("没有这个问题")
    _, _, st = ensure(db, proj, user_id)
    if slot == "key_questions" and isinstance(value, str):
        value = [x.strip() for x in value.splitlines() if x.strip()][:4]
    if slot == "minutes":
        value = int(value)
        if not 10 <= value <= 180:
            raise svc.OutlineError("时长应在 10 到 180 分钟之间")
    st.answers = {**(st.answers or {}), slot: value}


def build_overview(db: Session, proj: Project, user_id: str = "") -> dict:
    """The overview table from what is known (all rounds' answers). Makes the table again; modules already written keep their questions only when the module still exists."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    slots = _slots(ctx, st)
    mods = flow.build_modules(slots, ctx)
    _save_ov(st, {"modules": mods, "dismissed": _ov(st).get("dismissed", []), "context": {"rotate": ctx["rotate"]}, "minutes": int(slots["minutes"]["value"] or 45)})
    st.gates = {**(st.gates or {}), "goal": True, "people": True}
    _drop_orphans(db, v, mods)
    return view(db, proj, user_id)


def _drop_orphans(db: Session, v: OutlineVersion, mods: list[dict]) -> None:
    """Questions of a module that no longer exists are removed."""
    ids = {m["id"] for m in mods}
    for it in svc.items_of(db, v.id):
        if it.module not in ids:
            db.delete(it)
    db.flush()


def adopt(db: Session, proj: Project, ids: list[str], user_id: str = "") -> dict:
    """Apply the chosen suggestions to the overview. Only the modules that change are marked "todo" again; the others keep their questions."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    slots = _slots(ctx, st)
    ov = _ov(st)
    chosen = [s for s in flow.suggest(ov["modules"], ctx, slots, set(ov.get("dismissed", []))) if s["id"] in set(ids)]
    if not chosen:
        raise svc.OutlineError("所选建议已经不存在，请刷新")
    before = {m["id"]: (m["minutes"], m["depth"]) for m in ov["modules"]}
    mods, new_ctx = flow.apply_suggestions(ov["modules"], ctx, [s["op"] for s in chosen], int(ov.get("minutes", 45)))
    for m in mods:
        if m["id"] not in before or before[m["id"]] != (m["minutes"], m["depth"]):
            m["status"] = "todo" if m["id"] not in before else m.get("status", "todo")
    ov.update(modules=mods, context={**ov.get("context", {}), "rotate": new_ctx.get("rotate", False)})
    _save_ov(st, ov)
    _drop_orphans(db, v, mods)
    return view(db, proj, user_id)


def dismiss(db: Session, proj: Project, sid: str, user_id: str = "") -> None:
    _, _, st = ensure(db, proj, user_id)
    ov = _ov(st)
    ov["dismissed"] = sorted(set(ov.get("dismissed", [])) | {sid})
    _save_ov(st, ov)


def edit_module(db: Session, proj: Project, mid: str, fields: dict, user_id: str = "") -> dict:
    """The researcher edits a module row: name, purpose, content, minutes, depth. Changing minutes moves time to or from the largest other module so the total stays."""
    o, v, st = ensure(db, proj, user_id)
    ov = _ov(st)
    m = next((x for x in ov["modules"] if x["id"] == mid), None)
    if m is None:
        raise svc.OutlineError("没有这个模块", 404)
    for k in ("name", "purpose", "content"):
        if k in fields:
            m[k] = str(fields[k]).strip()[:200]
    if "depth" in fields:
        if fields["depth"] not in flow.DEPTHS:
            raise svc.OutlineError("追问深度不对")
        m["depth"] = fields["depth"]
    if "minutes" in fields:
        new = int(fields["minutes"])
        if new < 1:
            raise svc.OutlineError("时长至少 1 分钟")
        diff = new - m["minutes"]
        others = sorted((x for x in ov["modules"] if x is not m), key=lambda x: -x["minutes"])
        if diff > 0 and not others:
            raise svc.OutlineError("没有别的模块可以让出时间")
        if diff and others:
            if others[0]["minutes"] - diff < 1:
                raise svc.OutlineError("别的模块已经没有多余的时间可以让出")
            others[0]["minutes"] -= diff
        m["minutes"] = new
    for x in ov["modules"]:
        x["questions"] = max(1 if x["kind"] in ("warm", "close") else flow.MIN_QUESTIONS, int(x["minutes"] / flow.MIN_PER_QUESTION))
        x["tight"] = x["kind"] not in ("warm", "close") and x["minutes"] < flow.MIN_QUESTIONS * flow.MIN_PER_QUESTION
    _save_ov(st, ov)
    return view(db, proj, user_id)


def confirm_overview(db: Session, proj: Project, user_id: str = "") -> dict:
    _, _, st = ensure(db, proj, user_id)
    if not _ov(st).get("modules"):
        raise svc.OutlineError("还没有大纲总览")
    st.gates = {**(st.gates or {}), "overview": True}
    return view(db, proj, user_id)


def _module(ov: dict, mid: str) -> dict:
    m = next((x for x in ov.get("modules", []) if x["id"] == mid), None)
    if m is None:
        raise svc.OutlineError("没有这个模块", 404)
    return m


def generate_module(db: Session, proj: Project, mid: str, chat, user_id: str = "") -> dict:
    """Write one module's questions (replacing what it had). Nothing is written for a module before the overview is confirmed."""
    o, v, st = ensure(db, proj, user_id)
    if not (st.gates or {}).get("overview"):
        raise svc.OutlineError("请先确认大纲总览")
    ctx = context_of(db, proj, st)
    ov = _ov(st)
    m = _module(ov, mid)
    qs = writer.write_module(chat, m, ctx, ctx["language"], ctx["format"])
    for it in svc.items_of(db, v.id):
        if it.module == mid:
            db.delete(it)
    db.flush()
    for q in qs:
        svc.add_item(db, v, module=mid, kind="question", texts={ctx["language"]: q["text"]}, followups=q["followups"], must_cover=q["must_cover"], minutes=q["minutes"], is_backup=q["backup"],
                     config={"depth": m["depth"]}, key_question=(ctx["goals"][m["serves"][0] - 1] if m["serves"] and m["serves"][0] <= len(ctx["goals"]) else ""))
    m["status"] = "draft"
    _save_ov(st, ov)
    return view(db, proj, user_id)


def confirm_module(db: Session, proj: Project, mid: str, user_id: str = "") -> dict:
    _, v, st = ensure(db, proj, user_id)
    ov = _ov(st)
    m = _module(ov, mid)
    if m["status"] == "todo":
        raise svc.OutlineError("这个模块还没有题目")
    m["status"] = "confirmed"
    if all(x["status"] == "confirmed" for x in ov["modules"]):
        st.gates = {**(st.gates or {}), "questions": True}
    _save_ov(st, ov)
    return view(db, proj, user_id)


def chat_turn(db: Session, proj: Project, chat, text: str, selected: str, user_id: str = "") -> dict:
    """The side dialog: the model proposes up to three edits; each is checked here and stored as a pending revision (a blocked one is stored too, shown as blocked, and cannot be accepted)."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    items = [{"uid": i.item_uid, "label": i.label, "kind": i.kind, "text": i.texts.get(ctx["language"]) or ""} for i in svc.items_of(db, v.id)][:MAX_CHAT_OUTLINE]
    reply, ops = writer.chat_ops(chat, items, selected, text)
    made: list[Revision] = []
    for op in ops:
        after = op.get("after") if isinstance(op.get("after"), dict) else {}
        q = quality.check_question(str(after.get("text", "")), ctx["language"], ctx["format"]) if op.get("op") in ("rewrite", "add_followup", "insert") and after.get("text") else {"blocked": False, "issues": []}
        before = {}
        it = next((i for i in svc.items_of(db, v.id) if i.item_uid == op.get("item_uid")), None)
        if it is not None and op.get("op") == "rewrite":
            before = {"text": it.texts.get(ctx["language"], "")}
        try:
            made += svc.propose(db, proj.org_id, o.id, v.id, [{**op, "before": before, "after": after}], proposed_by="ai", source="editor", quality=q)
        except svc.OutlineError:
            continue   # an operation on something that does not exist or does not fit is dropped, never stored
    return {"reply": reply, "revisions": [{"id": r.id, "op": r.op, "item_uid": r.item_uid, "before": r.before, "after": r.after, "reason": r.reason, "quality": r.quality, "status": r.status} for r in made]}


def decide(db: Session, proj: Project, rid: str, accept: bool, user_id: str, role: str, train_case_id: str = "") -> dict:
    """Accept or reject a revision. Accepting one for the AI host records a training case: its number is made here from the revision when the person did not type one."""
    rev = db.get(Revision, rid)
    if accept and rev is not None and rev.org_id == proj.org_id and rev.destination == "host_engine" and not train_case_id.strip():
        train_case_id = "TC-" + rev.id[:8]
    if accept:
        done = svc.accept(db, proj.org_id, rid, user_id, role, train_case_id)
        from . import focus_new
        focus_new.link_after_accept(db, done)
    else:
        svc.reject(db, proj.org_id, rid, user_id, role)
    return view(db, proj, user_id)


def edit_item(db: Session, proj: Project, uid: str, fields: dict, user_id: str = "") -> dict:
    """The researcher edits a question directly (text, follow-ups, depth, minutes, backup). The text is checked; a blocked text is refused."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    it = next((i for i in svc.items_of(db, v.id) if i.item_uid == uid), None)
    if it is None:
        raise svc.OutlineError("条目不存在", 404)
    upd: dict = {}
    if "text" in fields:
        res = quality.check_question(str(fields["text"]), ctx["language"], ctx["format"])
        if res["blocked"]:
            raise svc.OutlineError("这句话碰到红线：" + "、".join(i["text"] for i in res["issues"] if i["level"] == "block"))
        upd["texts"] = {**it.texts, ctx["language"]: str(fields["text"]).strip()[:300]}
    if "followups" in fields:
        upd["followups"] = [str(x)[:120] for x in fields["followups"]][:5]
    if "minutes" in fields:
        upd["minutes"] = float(fields["minutes"])
    if "backup" in fields:
        upd["is_backup"] = bool(fields["backup"])
    if "depth" in fields:
        if fields["depth"] not in flow.DEPTHS:
            raise svc.OutlineError("追问深度不对")
        upd["config"] = {**it.config, "depth": fields["depth"]}
    svc.update_item(db, v, uid, **upd)
    return view(db, proj, user_id)


def add_focus(db: Session, proj: Project, text: str, user_id: str, role: str, item_uid: str | None = None) -> dict:
    svc.add_focus(db, proj.org_id, proj.id, text, user_id, role, item_uid=item_uid)
    return view(db, proj, user_id)


def link_focus(db: Session, proj: Project, fid: str, item_uid: str | None) -> dict:
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    f.item_uid = item_uid
    return view(db, proj)


MATERIAL_KINDS = ("concept", "video", "website", "rating")
KIND_MINUTES = {"concept": 3.0, "video": 3.0, "website": 6.0, "rating": 2.0, "question": 2.0, "note": 0.0}


def precheck(kind: str, config: dict) -> dict:
    """The checks that can be made without opening the address: https, not a private or local host, a login-looking path. It does NOT open the address (no reachability, load time or phone check yet)."""
    from urllib.parse import urlparse
    url = str(config.get("url") or "").strip()
    if kind not in ("website", "video") or not url:
        return {}
    u = urlparse(url)
    host = (u.hostname or "").lower()
    private = host in ("localhost", "") or host.endswith((".local", ".internal")) or host.replace(".", "").isdigit() and host.startswith(("10.", "127.", "192.168.", "169.254."))
    return {"https": u.scheme == "https", "public_host": not private, "maybe_login": any(w in u.path.lower() for w in ("login", "signin", "account", "member")), "opened": False}


def insert_item(db: Session, proj: Project, fields: dict, user_id: str = "") -> dict:
    """Add a question or a material (concept card, video clip, website task, rating) after another item, or at the end of a module. A material's address must pass the basic check."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    kind = fields.get("kind", "question")
    if kind not in svc.KINDS:
        raise svc.OutlineError("条目类型不对")
    text = str(fields.get("text", "")).strip()
    if not text:
        raise svc.OutlineError("请写一句要对受访者说的话")
    if kind in ("question", "rating"):
        res = quality.check_question(text, ctx["language"], ctx["format"])
        if res["blocked"]:
            raise svc.OutlineError("这句话碰到红线：" + "、".join(i["text"] for i in res["issues"] if i["level"] == "block"))
    cfg = dict(fields.get("config") or {})
    pre = precheck(kind, cfg)
    if kind in ("website", "video") and pre and (not pre["https"] or not pre["public_host"]):
        raise svc.OutlineError("物料地址必须是公开的 https 地址")
    if pre:
        cfg["precheck"] = pre
    after = fields.get("after_uid") or None
    mod = fields.get("module") or ""
    if after:
        mod = next((i.module for i in svc.items_of(db, v.id) if i.item_uid == after), mod)
    if not mod:
        raise svc.OutlineError("请指定放在哪个模块")
    svc.add_item(db, v, after_uid=after, module=mod, kind=kind, texts={ctx["language"]: text[:300]}, minutes=float(fields.get("minutes") or KIND_MINUTES[kind]), config=cfg)
    return view(db, proj, user_id)


def delete_item(db: Session, proj: Project, uid: str, user_id: str = "") -> dict:
    _, v, _ = ensure(db, proj, user_id)
    svc.delete_item(db, v, uid)
    return view(db, proj, user_id)
