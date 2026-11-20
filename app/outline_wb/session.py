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
from . import media as wbmedia
from . import flow, quality, service as svc, writer
from .models import FocusQuestion, Outline, OutlineItem, OutlineVersion, Revision, StudySetting

STEPS = [("goal", "目标"), ("people", "人群与执行"), ("overview", "大纲总览"), ("questions", "题目"), ("focus", "关注问题")]
ROUNDS = (1, 2, 3)
MAX_CHAT_OUTLINE = 60
RESET_UNDO_S = 600   # 撤销清除 is open for 10 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    fmt, langs = (fmts[0] if fmts else "video"), [proj.language]
    from . import sync
    o = db.get(Outline, st.outline_id)
    sy = sync.state(db, proj, o, st) if o is not None else None
    if sy:   # 修正方案第 1 轮第 5 条: the bound 立项 sub-project decides (the outline's own value only while an unlock is asked for)
        minutes, fmt, langs = sy["values"]["minutes"]["value"], sy["values"]["format"]["value"], sy["values"]["languages"]["value"]
    lang = next((x for x in langs if x in ("zh", "en")), proj.language if proj.language in ("zh", "en") else "zh")
    if settings.moderator_provider == "mock":
        lang = "zh"   # the offline demo only has Chinese sample questions
    return {"background": " ".join((proj.brief or "").split()), "goals": flow.clean_goals(f.goals if f else []), "audience": (f.need if f else "") or "", "minutes": minutes,
            "language": lang, "languages": langs, "format": fmt, "host": proj.host_mode, "rotate": bool((st.overview or {}).get("context", {}).get("rotate")), "sync": sy}


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


def _item_view(it: OutlineItem, lang: str, pid: str = "") -> dict:
    return {"uid": it.item_uid, "label": it.label, "kind": it.kind, "text": it.texts.get(lang) or next(iter(it.texts.values()), ""), "followups": it.followups, "must_cover": it.must_cover, "minutes": it.minutes,
            "backup": it.is_backup, "depth": (it.config or {}).get("depth", "shallow"), "config": it.config,
            "media": wbmedia.with_urls(it.config or {}, pid) if pid else []}   # 修正方案第 3 轮: uploaded material, with short-lived addresses


def view(db: Session, proj: Project, user_id: str = "") -> dict:
    """Everything the page needs in one answer."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    slots = _slots(ctx, st)
    ov = _ov(st)
    mods = [dict(m) for m in ov.get("modules", [])]   # a copy: the page adds the questions to each module, and that must not be saved back
    by_mod = _items_by_module(db, v.id)
    asked = len([k for k in (st.answers or {}) if k in {d.name for d in flow.SLOT_DEFS}])
    rounds = {r: flow.next_questions(slots, r, asked) for r in ROUNDS}
    for m in mods:
        m["items"] = [_item_view(i, ctx["language"], proj.id) for i in by_mod.get(m["id"], [])]
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
            "slots": {k: {"value": s["value"], "source": s["source"], "confidence": s["confidence"], "text": flow.slot_text(k, s["value"]), "edit": flow.SLOT_KIND.get(k, "text"),
                          "options": flow.slot_options(k)} for k, s in slots.items() if k != "price" or flow.price_matters(slots)}, "round_questions": rounds, "step": step, "gates": gates,
            "stale": bool(mods) and bool(ov.get("fp")) and ov.get("fp") != flow.fingerprint(slots, ctx["background"]), "reset": _reset_state(db, st, v),
            "steps": [{"key": k, "label": lab} for k, lab in STEPS], "overview": {"modules": mods, "totals": flow.totals(mods) if mods else {}, "findings": flow.findings(mods, ctx, slots) if mods else [],
            "depth_labels": flow.DEPTH_LABELS}, "suggestions": sugg, "revisions": pending, "focus": _focus_views(db, proj, v.id),
            "items": [_item_view(i, ctx["language"], proj.id) | {"module": i.module} for i in svc.items_of(db, v.id)][:MAX_CHAT_OUTLINE * 3]}


def _focus_views(db: Session, proj: Project, version_id: str) -> list[dict]:
    """Each focus question with what it points at (several questions), its state (linked / new_idea / none / ""), the questions recommended for it (same scoring as the board's
    中途新增关注问题) and, while a 新增想法 waits, that drafted question (修正方案第 1 轮第 3 条)."""
    from . import focus_new
    labels = svc.labels_map(db, version_id)
    pend = {r.source_ref: r for r in db.scalars(select(Revision).where(Revision.source == "board", Revision.op == "insert", Revision.status == "pending", Revision.org_id == proj.org_id))}
    out = []
    for f in db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id).order_by(FocusQuestion.created_at)):
        uids = [u for u in dict.fromkeys([*(f.item_uids or []), *([f.item_uid] if f.item_uid else [])]) if u in labels]
        rec = [c["uid"] for c in focus_new.suggest_items(db, proj, f.text)["candidates"] if c["score"] > 0]
        r = pend.get(f.id)
        idea = {"id": r.id, "text": next(iter((r.after or {}).get("texts", {}).values()), ""), "module": (r.after or {}).get("module_name", ""), "blocked": bool((r.quality or {}).get("blocked")),
                "issues": [i["text"] for i in (r.quality or {}).get("issues", [])]} if r else None
        state = "new_idea" if idea else (f.link_state if f.link_state in ("linked", "none") else ("linked" if uids else ""))
        out.append({"id": f.id, "text": f.text, "role": f.asked_role, "timing": f.timing, "item_uid": uids[0] if uids else None, "item_uids": uids, "labels": [labels[u] for u in uids],
                    "label": labels[uids[0]] if uids else "", "coding": f.coding, "state": state, "rec": rec, "idea": idea})
    return out


def _reset_state(db: Session, st: StudySetting, v: OutlineVersion) -> dict:
    """What 重新开始 may do here, and whether 撤销清除 is still open (10 minutes, by the server's clock)."""
    from .models import PilotRound
    used = svc.usage_count(db, v.id)
    in_pilot = db.scalar(select(func.count()).select_from(PilotRound).where(PilotRound.version_id == v.id)) or 0
    why = f"这一版已被 {used} 场使用，只能清除你的回答" if used else "这一版在 Pilot 中，只能清除你的回答" if in_pilot else "这一版已发布，只能清除你的回答" if v.status == "published" else ""
    left = 0
    if st.reset_backup and st.reset_at:
        left = max(0, RESET_UNDO_S - int((_now() - st.reset_at).total_seconds()))
    return {"all_ok": not why, "why": why, "undo_left_s": left, "mode": (st.reset_backup or {}).get("mode", "") if left else ""}


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
    if slot == "minutes" and context_of(db, proj, st).get("sync"):
        set_sync(db, proj, "minutes", value, user_id)
        return
    if slot == "minutes":
        value = int(value)
        if not 10 <= value <= 180:
            raise svc.OutlineError("时长应在 10 到 180 分钟之间")
    st.answers = {**(st.answers or {}), slot: value}


def build_overview(db: Session, proj: Project, user_id: str = "", chat=None) -> dict:
    """The overview table from what is known (all rounds' answers). Makes the table again; modules already written keep their questions only when the module still exists."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    slots = _slots(ctx, st)
    mods = flow.build_modules(slots, ctx)
    writer.name_modules(chat, mods, ctx)   # 修正方案第 2 轮 C13: short whole names (the model when there is one, otherwise the rule)
    st.reset_backup, st.reset_at = None, None   # C8: going on to the overview means the restart is meant
    _save_ov(st, {"modules": mods, "dismissed": _ov(st).get("dismissed", []), "context": {"rotate": ctx["rotate"]}, "minutes": int(slots["minutes"]["value"] or 45), "fp": flow.fingerprint(slots, ctx["background"])})
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


def opening_words(ctx: dict) -> str:
    """The moderator's spoken opening (who, why, how long, recording only for research, no right or wrong, stop any time)."""
    mins = ctx.get("minutes") or 45
    if ctx.get("language") == "en":
        return (f"Hi, thank you so much for joining today. I'd love to hear about your own experiences and views — it will take about {mins} minutes. "
                "There are no right or wrong answers; just tell me what you really think. The session is recorded only for research, and your personal details stay private. "
                "If there's anything you'd rather not answer, just say so. Shall we start?")
    return (f"您好，非常感谢您抽时间参加今天的访谈！今天想听听您自己的真实经历和想法，大概 {mins} 分钟。"
            "回答没有对错之分，您怎么想就怎么说。访谈会录音录像，只用于研究，不会公开您的个人信息。中间有不想回答的，随时告诉我就好。那我们开始吧？")


def closing_words(ctx: dict) -> str:
    """The moderator's spoken thanks at the end (anything to add, how the reward is given, thanks again)."""
    if ctx.get("language") == "en":
        return ("That's all my questions for today. Thank you so much for sharing — it really helps us. Is there anything else you'd like to add? "
                "Your thank-you gift will be sent as agreed after the session. Thanks again, and have a great day!")
    return "今天的问题就到这里。非常感谢您的分享，对我们帮助很大！关于今天聊的内容，您还有什么想补充的吗？礼金会在访谈结束后按约定方式发给您。再次感谢，祝您生活愉快！"


def generate_module(db: Session, proj: Project, mid: str, chat, user_id: str = "") -> dict:
    """Write one module's questions (replacing what it had). Nothing is written for a module before the overview is confirmed."""
    o, v, st = ensure(db, proj, user_id)
    if not (st.gates or {}).get("overview"):
        raise svc.OutlineError("请先确认大纲总览")
    ctx = context_of(db, proj, st)
    ov = _ov(st)
    m = _module(ov, mid)
    kept = [it for it in svc.items_of(db, v.id) if it.module == mid and (it.config or {}).get("accepted")]   # 修正方案第 2 轮 C11: questions accepted from the dialog stay
    qs = writer.write_module(chat, m, ctx, ctx["language"], ctx["format"], keep=[i.texts.get(ctx["language"], "") for i in kept])
    for it in svc.items_of(db, v.id):
        if it.module == mid and it not in kept:
            db.delete(it)
    db.flush()
    lang = ctx["language"]
    if m.get("kind") == "warm":   # 修正方案第 4 轮 (用户 2026-11-20): the warm-up opens with a spoken introduction, the closing ends with thanks — both can be edited
        svc.add_item(db, v, module=mid, kind="note", texts={lang: opening_words(ctx)}, minutes=1.0, config={"depth": "none", "script": "open"})
    for q in qs:
        svc.add_item(db, v, module=mid, kind="question", texts={ctx["language"]: q["text"]}, followups=q["followups"], must_cover=q["must_cover"], minutes=q["minutes"], is_backup=q["backup"],
                     config={"depth": m["depth"]}, key_question=(ctx["goals"][m["serves"][0] - 1] if m["serves"] and m["serves"][0] <= len(ctx["goals"]) else ""))
    if m.get("kind") == "close":
        svc.add_item(db, v, module=mid, kind="note", texts={lang: closing_words(ctx)}, minutes=1.0, config={"depth": "none", "script": "close"})
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


def chat_turn(db: Session, proj: Project, chat, text: str, selected: str, user_id: str = "", refs: list | None = None, step: str = "", module: str = "") -> dict:
    """The side dialog: the model proposes up to three edits; each is checked here and stored as a pending revision (a blocked one is stored too, shown as blocked, and cannot be accepted)."""
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    if step in ("goal", "people"):   # 修正方案第 2 轮 C3: on these pages the dialog changes 已经知道的, never the questions
        return _slot_turn(db, proj, st, ctx, chat, text, user_id)
    items = [{"uid": i.item_uid, "label": i.label, "kind": i.kind, "text": i.texts.get(ctx["language"]) or "", "module": i.module} for i in svc.items_of(db, v.id)][:MAX_CHAT_OUTLINE]
    from .. import refs as refs_mod
    mat, cat = refs_mod.material(db, proj.org_id, proj.id, refs or [])
    reply, ops = writer.chat_ops(chat, items, selected, text, mat)
    reply, cites = refs_mod.cite(reply, cat)
    made: list[Revision] = []
    for op in ops:
        after = dict(op.get("after")) if isinstance(op.get("after"), dict) else {}
        if op.get("op") == "insert":   # a question the AI adds: its words, kind and the module it belongs to (the selected question's, else the page's module)
            near = next((i for i in items if i["uid"] == op.get("item_uid")), None)
            mod = (near or {}).get("module") or module
            if not op.get("item_uid") or near is None:
                last = [i for i in items if i["module"] == mod][-1:] or items[-1:]
                op = {**op, "item_uid": last[0]["uid"] if last else ""}
            after = {**after, "kind": "question", "module": mod, "texts": {ctx["language"]: str(after.get("text", ""))[:300]}, "minutes": 2.0, "config": {"depth": "shallow", "accepted": True}}
        q = quality.check_question(str(after.get("text", "")), ctx["language"], ctx["format"]) if op.get("op") in ("rewrite", "add_followup", "insert") and after.get("text") else {"blocked": False, "issues": []}
        before = {}
        it = next((i for i in svc.items_of(db, v.id) if i.item_uid == op.get("item_uid")), None)
        if it is not None and op.get("op") == "rewrite":
            before = {"text": it.texts.get(ctx["language"], "")}
        try:
            made += svc.propose(db, proj.org_id, o.id, v.id, [{**op, "before": before, "after": after}], proposed_by="ai", source="editor", quality=q)
        except svc.OutlineError:
            continue   # an operation on something that does not exist or does not fit is dropped, never stored
    return {"reply": reply, "cites": cites, "revisions": [{"id": r.id, "op": r.op, "item_uid": r.item_uid, "before": r.before, "after": r.after, "reason": r.reason, "quality": r.quality, "status": r.status} for r in made]}


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


# ---- 修正方案第 1 轮第 6 条：已经知道的每一项都能改 --------------------------------------------------------------------------------

def edit_background(db: Session, proj: Project, text: str, user_id: str = "") -> dict:
    """研究背景 is the project's own (the same one the settings page shows): changing it here changes it there."""
    text = " ".join(str(text or "").split())
    if not text:
        raise svc.OutlineError("研究背景不能为空")
    proj.brief = text[:4000]
    return view(db, proj, user_id)


def keep_overview(db: Session, proj: Project, user_id: str = "") -> dict:
    """"不用重新生成": what was made stays, and the yellow bar goes away (the overview now counts as made from the current settings)."""
    _, _, st = ensure(db, proj, user_id)
    ov = _ov(st)
    if ov.get("modules"):
        ov["fp"] = flow.fingerprint(_slots(context_of(db, proj, st), st), context_of(db, proj, st)["background"])
        _save_ov(st, ov)
    return view(db, proj, user_id)


# ---- 修正方案第 1 轮第 7 条：重新开始（10 分钟内可撤销）------------------------------------------------------------------------------

ITEM_COLS = ("item_uid", "label", "module", "position", "kind", "texts", "followups", "must_cover", "key_question", "hypothesis", "minutes", "is_backup", "config")


def reset(db: Session, proj: Project, mode: str, user_id: str = "") -> dict:
    """重新开始. `answers`: clear the researcher's answers, rounds and gates (what the project says and the questions already written stay). `all`: also the overview, the current
    version's questions and the focus questions' links, and only when that version has not been used by an interview, is not in a pilot round and is not published (no new version is
    ever made here: versions only come from Pilot 修正). What was there is kept for 10 minutes so 撤销清除 can put it back.

    Raises:
        OutlineError: an unknown mode, or `all` on a version that may not be cleared.
    """
    if mode not in ("answers", "all"):
        raise svc.OutlineError("清除方式不对")
    o, v, st = ensure(db, proj, user_id)
    rs = _reset_state(db, st, v)
    if mode == "all" and not rs["all_ok"]:
        raise svc.OutlineError(rs["why"], 409)
    fq = list(db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id)))
    backup = {"mode": mode, "answers": st.answers or {}, "rounds": st.rounds or {}, "gates": st.gates or {}}
    if mode == "all":
        backup |= {"overview": st.overview or {}, "version_id": v.id, "rev": v.rev, "items": [{c: getattr(i, c) for c in ITEM_COLS} for i in svc.items_of(db, v.id)],
                   "focus": {f.id: {"item_uid": f.item_uid, "item_uids": f.item_uids or [], "link_state": f.link_state or ""} for f in fq}}
    st.reset_backup, st.reset_at = backup, _now()
    st.answers, st.rounds, st.gates = {}, {}, {}
    if mode == "all":
        _save_ov(st, {"modules": [], "dismissed": _ov(st).get("dismissed", []), "context": {}})
        for it in svc.items_of(db, v.id):
            db.delete(it)
        for f in fq:
            f.item_uid, f.item_uids, f.link_state = None, [], ""
        svc.bump(db, v)
    db.flush()
    return view(db, proj, user_id)


def undo_reset(db: Session, proj: Project, user_id: str = "") -> dict:
    """撤销清除: put back what 重新开始 cleared, within 10 minutes.

    Raises:
        OutlineError: nothing to undo, or the 10 minutes are over, or the version changed in between.
    """
    o, v, st = ensure(db, proj, user_id)
    b = st.reset_backup
    if not b or not st.reset_at:
        raise svc.OutlineError("没有可以撤销的清除", 409)
    if (_now() - st.reset_at).total_seconds() > RESET_UNDO_S:
        st.reset_backup, st.reset_at = None, None
        raise svc.OutlineError("已超过 10 分钟，不能撤销了", 409)
    if b["mode"] == "all" and b.get("version_id") != v.id:
        raise svc.OutlineError("大纲的版本已经变了，不能撤销", 409)
    st.answers, st.rounds, st.gates = b.get("answers", {}), b.get("rounds", {}), b.get("gates", {})
    if b["mode"] == "all":
        _save_ov(st, b.get("overview", {}))
        for it in svc.items_of(db, v.id):
            db.delete(it)
        db.flush()
        for row in b.get("items", []):
            db.add(OutlineItem(org_id=proj.org_id, version_id=v.id, **{c: row[c] for c in ITEM_COLS if c in row}))
        for f in db.scalars(select(FocusQuestion).where(FocusQuestion.project_id == proj.id)):
            old = b.get("focus", {}).get(f.id)
            if old:
                f.item_uid, f.item_uids, f.link_state = old["item_uid"], old["item_uids"], old["link_state"]
        svc.bump(db, v)
    st.reset_backup, st.reset_at = None, None
    db.flush()
    return view(db, proj, user_id)


# ---- 修正方案第 1 轮第 3 条：关注问题“对应到”多选 -------------------------------------------------------------------------------

def _focus_of(db: Session, proj: Project, fid: str) -> FocusQuestion:
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    return f


def _pending_idea(db: Session, fid: str) -> Revision | None:
    return db.scalar(select(Revision).where(Revision.source == "board", Revision.source_ref == fid, Revision.op == "insert", Revision.status == "pending"))


def set_focus_link(db: Session, proj: Project, fid: str, item_uids: list[str], state: str, chat, user_id: str, role: str) -> dict:
    """The "对应到" drop-down: any number of the outline's questions, and/or 新增想法 (a drafted new question, pending until a person accepts it), or 暂不对应 (alone).
    Choosing 新增想法 again while one waits does not draft a second one.

    Raises:
        OutlineError: no such focus question, a question that is not in the outline, an unknown state.
    """
    f = _focus_of(db, proj, fid)
    if state not in ("linked", "new_idea", "none"):
        raise svc.OutlineError("对应方式不对")
    _, v, _ = ensure(db, proj, user_id)
    labels = svc.labels_map(db, v.id)
    uids = list(dict.fromkeys(u for u in item_uids if u))
    if any(u not in labels for u in uids):
        raise svc.OutlineError("所选的题不在当前大纲里", 404)
    old = _pending_idea(db, f.id)
    if state == "none":
        uids = []
    if old is not None and state != "new_idea":
        svc.reject(db, proj.org_id, old.id, user_id, role)
    f.item_uids, f.item_uid = uids, (uids[0] if uids else None)
    f.link_state = "none" if state == "none" else ("linked" if uids else "")
    if state == "new_idea" and old is None:
        draft_idea(db, proj, f, chat, "", user_id)
    return view(db, proj, user_id)


def _best_module(ov: dict, text: str) -> dict | None:
    from .focus_new import _share
    mods = [m for m in ov.get("modules", []) if m["kind"] not in ("warm", "close")] or ov.get("modules", [])
    if not mods:
        return None
    return max(mods, key=lambda m: _share(text, " ".join([m["name"], m.get("purpose", ""), m.get("content", "")])))


def draft_idea(db: Session, proj: Project, f: FocusQuestion, chat, text: str, user_id: str = "") -> Revision:
    """新增想法: one new question for the focus question, put into the module it fits best, as a pending insert (a person accepts it; a blocked wording cannot be accepted).
    `text` is the person's own wording (改一下); empty means the AI drafts it.

    Raises:
        OutlineError: the outline has no module or no question yet.
    """
    o, v, st = ensure(db, proj, user_id)
    ctx = context_of(db, proj, st)
    ov = _ov(st)
    m = _best_module(ov, f.text)
    if m is None:
        raise svc.OutlineError("还没有大纲总览，先生成总览再加新题")
    items = svc.items_of(db, v.id)
    after = ([i for i in items if i.module == m["id"]] or [i for i in items if i.kind in ("question", "rating")])[-1:]
    if not after:
        raise svc.OutlineError("大纲里还没有题，先写好题目再加新题")
    q = " ".join(str(text).split())[:300] or writer.write_one(chat, m, ctx, f.text, ctx["language"], ctx["format"])
    res = quality.check_question(q, ctx["language"], ctx["format"])
    return svc.propose(db, proj.org_id, o.id, v.id, [{"op": "insert", "item_uid": after[0].item_uid, "after": {"kind": "question", "module": m["id"], "module_name": m["name"], "texts": {ctx["language"]: q},
                       "minutes": 2.0, "config": {"depth": m.get("depth", "shallow")}}, "reason": f"关注问题“{f.text[:20]}”的新增想法"}], proposed_by="human" if text else "ai", source="board", source_ref=f.id, quality=res)[0]


def revise_idea(db: Session, proj: Project, fid: str, text: str, user_id: str, role: str) -> dict:
    """改一下: the waiting new question is replaced by the person's own wording (checked again)."""
    f = _focus_of(db, proj, fid)
    old = _pending_idea(db, f.id)
    if old is None:
        raise svc.OutlineError("没有等待接受的新题", 409)
    if not " ".join(str(text).split()):
        raise svc.OutlineError("先写下新题")
    svc.reject(db, proj.org_id, old.id, user_id, role)
    draft_idea(db, proj, f, None, text, user_id)
    return view(db, proj, user_id)


# ---- 修正方案第 1 轮第 5 条：时长、形式、语言以立项子项目为准 ---------------------------------------------------------------------

def set_sync(db: Session, proj: Project, field: str, value, user_id: str = "", user_name: str = "", sub_id: str = "") -> str:
    """Change the outline's length, format or languages: in the plan when it is not locked, otherwise as an unlock request. Returns "plan" or "requested".
    `sub_id`: another sub-project sharing this outline (修正方案第 2 轮 C1: “把马来西亚也改成 45 分钟”)."""
    from . import sync
    o, _, st = ensure(db, proj, user_id)
    return sync.change(db, proj, o, st, field, value, user_name, user_id, sub_id=sub_id)


def drop_sync(db: Session, proj: Project, field: str, user_id: str = "") -> dict:
    """修正方案第 2 轮 C1 “改回立项的值”: the outline stops holding its own value and follows the 立项 again."""
    from . import sync
    _, _, st = ensure(db, proj, user_id)
    key = sync.OVERRIDE_KEYS.get(field)
    if key is None:
        raise svc.OutlineError("只能改访谈时长、形式或语言")
    st.answers = {k: v for k, v in (st.answers or {}).items() if k != key}
    return view(db, proj, user_id)


def bind_sub(db: Session, proj: Project, sub_id: str, user_id: str = "") -> dict:
    from . import sync
    o, _, _ = ensure(db, proj, user_id)
    sync.bind(db, proj, o, sub_id)
    db.flush()
    return view(db, proj, user_id)


def _slot_turn(db: Session, proj: Project, st: StudySetting, ctx: dict, chat, text: str, user_id: str) -> dict:
    """修正方案第 2 轮 C3: the dialog on 目标 / 人群与执行 changes 已经知道的 (research type, questions, people, materials, price) directly, checked like the “改” buttons."""
    slots = _slots(ctx, st)
    reply, changes = writer.slot_ops(chat, {k: {"value": v["value"], "text": flow.slot_text(k, v["value"]), "options": flow.slot_options(k)} for k, v in slots.items()}, text)
    done, notes = [], []
    for name, value in changes.items():
        try:
            if name in ("minutes", "format", "languages") and ctx.get("sync"):   # 修正方案第 4 轮: the same as 改 → 同步改立项 (or an unlock request when the 立项 is locked)
                res = set_sync(db, proj, name, value, user_id)
                if res != "plan":
                    notes.append("立项已锁定，已申请解锁，等管理员或项目经理处理")
            else:
                answer(db, proj, name, value, user_id)
            done.append(name)
        except (svc.OutlineError, ValueError, TypeError):
            continue
    if notes:
        reply = (reply or "") + "（" + "；".join(dict.fromkeys(notes)) + "）"
    if not done:   # 修正方案第 3 轮: never say something was changed when 已经知道的 did not change (“物料改为看素材” said so and nothing moved)
        mats = "、".join(o["text"] for o in flow.slot_options("materials") or [] if isinstance(o, dict) and o.get("text"))
        reply = ("这个改动没有生效，请用“改”按钮直接改。" if changes else
                 f"已经知道的没有改动。这一页的对话只改研究类型、要回答的问题、访谈对象、物料{('（可选：' + mats + '）') if mats else ''}，请说得具体一点。")
    return {"reply": reply, "cites": [], "revisions": [], "changed": done}
