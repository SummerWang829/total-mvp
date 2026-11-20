"""甄别问卷的 6 步（修正方案第 1 轮第 4 条，方案 docs/甄别问卷重做_方案.md，效果图 qa_shots/fix1_batch5_mockup.html）:
① 读取立项 → ② 要筛进谁 → ③ 要筛掉谁 → ④ 配额 → ⑤ 质量控制 → ⑥ 预览与测试.

Confirming a step remakes only the questions that step made (`Question.step`), keeping the others and the team's own questions; a later step that
used something the change removed is marked "需要重新确认". The model only splits the recruiting need into conditions (②) and adds at most two
project-specific exclusions that quote the need (③); everything else is rules. Where people end (screened out) is still decided in one place only:
recruit_form.next_step. Callers commit.
"""
import json
import logging
import re

from sqlalchemy.orm import Session

from . import formats, plans, recruit, recruit_form
from .models import Project, ProjectPlan, RecruitForm, RecruitSettings

log = logging.getLogger(__name__)

STEPS = ("plan", "include", "exclude", "quota", "qc", "test")
LABELS = {"plan": "读取立项", "include": "要筛进谁", "exclude": "要筛掉谁", "quota": "配额", "qc": "质量控制", "test": "预览与测试"}
MAX_CONDS = 8
MAX_AI_EXCL = 2
SECONDS_PER_Q, MIN_SECONDS = 4, 20
TRAP_FROM = 6   # a trap question is suggested from this many questions on
# ③ the usual exclusions (fixed; ticked by default unless said otherwise). `when`: video = only when the interview has video
EXCLUSIONS = [
    {"id": "industry", "name": "行业从业者", "note": "市场调研、广告、媒体、本品类从业者", "on": True, "type": "multi", "text": "你或你的家人是否在下列行业工作？",
     "options": [["市场调研 / 咨询", False], ["广告 / 公关 / 媒体", False], ["和本次研究的产品或服务相关的行业", False], ["以上都没有", True]], "none_last": True},
    {"id": "recent", "name": "近 3 个月参加过同类调研", "note": "", "on": True, "type": "single", "text": "最近 3 个月，你参加过市场调研的访谈或座谈吗？",
     "options": [["参加过", False], ["没有参加过", True]]},
    {"id": "camera", "name": "不愿开摄像头或麦克风", "note": "访谈是视频形式时才需要", "on": True, "when": "video", "type": "single", "text": "访谈时需要打开摄像头和麦克风，你愿意吗？",
     "options": [["愿意", True], ["不愿意", False]]},
    {"id": "minor", "name": "年龄不满 18 岁", "note": "立项一般已限定 18 岁以上", "on": False, "type": "single", "text": "你的年龄是？",
     "options": [["18 岁以下", False], ["18 岁及以上", True]]},
]
COND_SYSTEM = ("You split a market-research recruiting need into screening conditions. Reply with ONE JSON object: "
               '{"conditions":[{"name":"short name","type":"single|multi","question":"neutral question in the need\'s language",'
               '"options":[{"text":"...","pass":true|false}]}]}. 2-5 conditions; each has 2-6 options; at least one passing and one failing option; '
               "never ask for contact details; never lead.")
EXCL_SYSTEM = ("You suggest at most two exclusions special to THIS project for a screening questionnaire (besides industry workers, recent research "
               "participation, camera refusal, minors). Reply with ONE JSON object: "
               '{"exclusions":[{"name":"short name","question":"...","options":[{"text":"...","pass":true|false}],"basis":"the exact words of the need it rests on"}]}. '
               "An exclusion without a basis quoted word for word from the need is not allowed.")


class StepError(ValueError):
    """A step cannot be confirmed; the message is plain Chinese for the page."""


def _cfg(db: Session, proj: Project) -> plans.PlanConfig | None:
    plan = db.get(ProjectPlan, proj.id)
    if plan is None:
        return None
    try:
        return plans.validate(json.loads(plans.live_json(plan) or "{}") or plans.default_config())
    except ValueError:
        return None


def state(f: RecruitForm) -> dict:
    """The 6 steps' saved state with defaults: {status: {step: todo|confirmed|stale}, conds, excl, quotas, qc}."""
    s = dict(f.plan_steps or {})
    s.setdefault("status", {})
    for k in STEPS:
        s["status"].setdefault(k, "todo")
    s.setdefault("conds", [])
    s.setdefault("excl", [])
    s.setdefault("quotas", None)
    s.setdefault("qc", {})
    return s


def _save(f: RecruitForm, s: dict) -> None:
    f.plan_steps = json.loads(json.dumps(s, ensure_ascii=False))   # a fresh value, so the change is saved


def plan_summary(db: Session, proj: Project, f: RecruitForm) -> dict:
    """① what the 立项 says, read only."""
    langs = [{"zh": "中文", "en": "English", "ms": "马来语", "ta": "泰米尔语"}.get(x, x) for x in (proj.languages or [proj.language])]
    fmts = [formats.FORMATS.get(x, x) for x in formats.of(proj.formats, ["depth"]).get("depth", [])]
    return {"brief": proj.brief or "", "goals": f.goals or [], "need": f.need or "", "languages": langs, "formats": fmts}


# ---- ② 要筛进谁 -------------------------------------------------------------------------------------------------------------------------------

def _cid(prefix: str, n: int) -> str:
    return f"{prefix}{n}"


def _plan_conditions(cfg: plans.PlanConfig | None) -> list[dict]:
    """The 立项's own variables (other than the country) become conditions, so their option names are the quota tags."""
    out = []
    for name, values in ((cfg.vars or {}) if cfg else {}).items():
        if name == plans.COUNTRY_VAR or not values:
            continue
        out.append({"id": _cid("p", len(out) + 1), "name": name, "type": "single", "question": f"你的{name}是？", "use": True, "src": "plan", "quota": True,
                    "options": [{"text": v.name, "pass": True} for v in values] + [{"text": "以上都不是", "pass": False}]})
    return out


def _rule_conditions(need: str) -> list[dict]:
    """Without a model: each clause of the need becomes one condition (rough, but every word comes from the need)."""
    parts = [p.strip(" 。.") for p in re.split(r"[，,；;。\n]", need or "") if len(p.strip()) >= 4]
    return [{"id": _cid("r", i), "name": p[:16], "type": "single", "question": f"下面这一条符合你的情况吗：{p[:60]}？", "use": True, "src": "rule", "quota": False,
             "options": [{"text": "符合", "pass": True}, {"text": "不符合", "pass": False}]} for i, p in enumerate(parts[:5], 1)]


def _model_json(chat, system: str, user: str) -> dict:
    raw = chat.complete([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.2)
    return raw if isinstance(raw, dict) else {}


def _clean_options(raw) -> list[dict]:
    opts = [{"text": str(o.get("text", "")).strip()[:120], "pass": bool(o.get("pass"))} for o in (raw or []) if isinstance(o, dict) and str(o.get("text", "")).strip()][:recruit_form.MAX_OPTIONS]
    return opts if len(opts) >= 2 and any(o["pass"] for o in opts) and not all(o["pass"] for o in opts) else []


def suggest_conditions(db: Session, proj: Project, f: RecruitForm, chat) -> list[dict]:
    """② the conditions: the 立项's variables first, then what the model (or, without one, the rules) reads out of the recruiting need."""
    from .moderator.providers import ChatError, ChatFatalError
    conds = _plan_conditions(_cfg(db, proj))
    found: list[dict] = []
    if chat is not None and (f.need or "").strip():
        try:
            raw = _model_json(chat, COND_SYSTEM, json.dumps({"need": f.need, "goals": f.goals or []}, ensure_ascii=False))
            for c in (raw.get("conditions") or [])[:5]:
                opts = _clean_options(c.get("options")) if isinstance(c, dict) else []
                q = str(c.get("question", "")).strip()[:300] if isinstance(c, dict) else ""
                if opts and q and not recruit_form.CONTACT_WORDS.search(q):
                    found.append({"id": _cid("a", len(found) + 1), "name": str(c.get("name", "")).strip()[:20] or q[:16], "type": "multi" if c.get("type") == "multi" else "single",
                                  "question": q, "use": True, "src": "ai", "quota": False, "options": opts})
        except ChatFatalError:
            raise
        except (ChatError, ValueError) as e:
            log.warning("recruit steps: condition split failed (%s); rules used", type(e).__name__)
    if not found:
        found = _rule_conditions(f.need or "")
    taken = {c["name"] for c in conds}
    return (conds + [c for c in found if c["name"] not in taken])[:MAX_CONDS]


# ---- ③ 要筛掉谁 -------------------------------------------------------------------------------------------------------------------------------

def suggest_exclusions(db: Session, proj: Project, f: RecruitForm, chat) -> list[dict]:
    """③ the fixed list ticked by the interview format, plus at most two the model finds in the need, each quoting the need word for word."""
    from .moderator.providers import ChatError, ChatFatalError
    video = any(x.startswith("video") for x in formats.of(proj.formats, ["depth"]).get("depth", []))
    out = [{"id": e["id"], "name": e["name"], "note": e["note"] if e.get("when") != "video" or video else e["note"], "use": e["on"] and (e.get("when") != "video" or video), "src": "list",
            "type": e["type"], "question": e["text"], "options": [{"text": t, "pass": ok} for t, ok in e["options"]]} for e in EXCLUSIONS]
    need = f.need or ""
    if chat is not None and need.strip():
        try:
            raw = _model_json(chat, EXCL_SYSTEM, json.dumps({"need": need}, ensure_ascii=False))
            for n, e in enumerate((raw.get("exclusions") or [])[:MAX_AI_EXCL], 1):
                basis = str(e.get("basis", "")).strip() if isinstance(e, dict) else ""
                opts = _clean_options(e.get("options")) if isinstance(e, dict) else []
                q = str(e.get("question", "")).strip()[:300] if isinstance(e, dict) else ""
                if basis and basis in need and opts and q and not recruit_form.CONTACT_WORDS.search(q):   # no basis in the need's own words: not shown
                    out.append({"id": f"ai{n}", "name": str(e.get("name", "")).strip()[:20] or q[:16], "note": f"依据：“{basis[:60]}”", "use": True, "src": "ai", "type": "single", "question": q, "options": opts})
        except ChatFatalError:
            raise
        except (ChatError, ValueError) as e:
            log.warning("recruit steps: exclusions failed (%s); the fixed list only", type(e).__name__)
    return out


# ---- ④ 配额 ----------------------------------------------------------------------------------------------------------------------------------

def quota_default(db: Session, proj: Project) -> list[dict]:
    """④ the 立项's quota table, as the project's quotas: one row per group; a group of two variables needs both tags (value "A+B")."""
    cfg = _cfg(db, proj)
    if cfg is None:
        return []
    names = {name: {v.id: v.name for v in vals} for name, vals in (cfg.vars or {}).items()}
    subs = {s.id: s.name for s in cfg.subs}
    table = cfg.lt if any(r.i for r in cfg.lt.rows) else cfg.ft
    out = []
    for r in table.rows:
        target = r.i or r.f
        if not target:
            continue
        parts = []
        for dim, vid in zip(table.dims, r.vs):
            if dim == plans.COUNTRY_VAR:
                if len(cfg.subs) > 1:
                    parts.append(subs.get(vid, vid))
            else:
                parts.append(names.get(dim, {}).get(vid, vid))
        if not parts:
            continue
        out.append({"name": " × ".join(parts), "field": "tag", "question": "", "value": "+".join(parts), "target": int(target)})
    return out


# ---- building the questions --------------------------------------------------------------------------------------------------------------

def _question(qid: str, step: str, cond_id: str, kind: str, text: str, options: list[dict], tags: bool) -> dict:
    opts = []
    for j, o in enumerate(options, 1):
        opts.append({"id": f"o{j}", "text": o["text"], "goto": "" if o["pass"] else recruit_form.END_OUT, "tag": (o["text"][:30] if tags and o["pass"] else ""), "excl": []})
    q = {"id": qid, "type": "multi" if kind == "multi" else "single", "text": text, "required": True, "options": opts, "rules": [], "step": step, "cond_id": cond_id, "qc": "", "pair": ""}
    if kind == "multi":   # "以上都没有 / 都不是" cannot be ticked with anything else
        none = [o for o in opts if re.search(r"以上都没有|都不是|没有以上", o["text"])]
        for n in none:
            n["excl"] = [o["id"] for o in opts if o is not n]
            for o in opts:
                if o is not n:
                    o["excl"] = sorted(set(o["excl"]) | {n["id"]})
    return q


def _next_id(questions: list[dict]) -> str:
    n = max([int(m.group(1)) for q in questions if (m := re.fullmatch(r"q(\d+)", q.get("id", "")))] + [0])
    return f"q{n + 1}"


def build(questions: list[dict], step: str, s: dict) -> tuple[list[dict], list[str]]:
    """The questionnaire with `step`'s questions made again from the saved choices (the others kept). Order: 筛掉谁, 筛进谁, 质量控制, then the rest.
    Returns (questions, ids of the questions just made)."""
    keep = [q for q in questions if q.get("step") != step]
    made: list[dict] = []
    pool = [*keep]
    if step == "include":
        for c in s["conds"]:
            if c.get("use"):
                q = _question(_next_id(pool + made), "include", c["id"], c.get("type", "single"), c["question"], c["options"], tags=True)
                made.append(q)
    elif step == "exclude":
        for e in s["excl"]:
            if e.get("use"):
                made.append(_question(_next_id(pool + made), "exclude", e["id"], e.get("type", "single"), e["question"], e["options"], tags=False))
    elif step == "qc":
        qc = s.get("qc") or {}
        inc = [q for q in keep if q.get("step") == "include"]
        if qc.get("trap"):
            made.append(_question(_next_id(pool + made), "qc", "trap", "single", "为了确认你在认真作答，这一题请选“经常”。",
                                  [{"text": "从不", "pass": False}, {"text": "偶尔", "pass": False}, {"text": "经常", "pass": True}], tags=False) | {"qc": "trap"})
        if qc.get("consistency") and inc:
            first = inc[0]
            again = _question(_next_id(pool + made), "qc", first.get("cond_id", ""), first["type"], "再确认一下：" + first["text"],
                              [{"text": o["text"], "pass": o["goto"] != recruit_form.END_OUT} for o in reversed(first["options"])], tags=False)
            made.append(again | {"qc": "consistency", "pair": first["id"]})
    order = {"exclude": 0, "include": 1, "qc": 2}
    all_q = keep + made
    all_q.sort(key=lambda q: order.get(q.get("step", ""), 3))   # a stable sort: the order inside a group stays
    trap = next((q for q in all_q if q.get("qc") == "trap"), None)
    inc_ids = [q["id"] for q in all_q if q.get("step") == "include"]
    if trap and inc_ids:   # the trap goes right after the first condition, so it is not the obvious last question
        all_q.remove(trap)
        all_q.insert([q["id"] for q in all_q].index(inc_ids[0]) + 1, trap)
    return all_q, [q["id"] for q in made]


def qc_default(n_questions: int) -> dict:
    """⑤ the defaults: a trap question from six questions on, the consistency check, the shortest time (4 seconds a question, at least 20) and the duplicate check."""
    return {"trap": n_questions >= TRAP_FROM, "consistency": True, "min_seconds": max(MIN_SECONDS, n_questions * SECONDS_PER_Q), "dedupe": True}


def confirm(db: Session, proj: Project, f: RecruitForm, step: str, data: dict, questions: list[dict]) -> list[str]:
    """Confirm one step with what the page chose. Remakes that step's questions, saves the quotas (④), and marks later steps that used something now gone.
    Returns the ids of the questions just made.

    Raises:
        StepError: an unknown step, or choices that cannot make a questionnaire (nothing to screen in, a condition without a passing and a failing option, ...).
    """
    if step not in STEPS:
        raise StepError("没有这一步")
    s = state(f)
    made: list[str] = []
    if step == "include":
        conds = [_check_cond(c) for c in (data.get("conds") or [])][:MAX_CONDS]
        if not any(c["use"] for c in conds):
            raise StepError("至少选一个要筛进的条件")
        old_tags = {o["text"] for c in s["conds"] if c.get("use") for o in c["options"] if o["pass"]}
        s["conds"] = conds
        new_tags = {o["text"] for c in conds if c["use"] for o in c["options"] if o["pass"]}
        if old_tags - new_tags and s["status"]["quota"] == "confirmed":
            s["status"]["quota"] = "stale"   # a quota may count a tag that is gone
        questions, made = build(questions, "include", s)
        if s["status"]["qc"] == "confirmed" and (s.get("qc") or {}).get("consistency"):
            questions, more = build(questions, "qc", s)   # the check asks the first condition again: follow it
            made += more
    elif step == "exclude":
        s["excl"] = [_check_cond(e) for e in (data.get("excl") or [])][:12]
        questions, made = build(questions, "exclude", s)
    elif step == "quota":
        try:
            rows = recruit.validate_quotas(data.get("quotas") or [])
        except recruit.RecruitError as e:
            raise StepError(str(e)) from e
        s["quotas"] = rows
        rs = db.get(RecruitSettings, proj.id)
        if rs is None:
            rs = RecruitSettings(project_id=proj.id, org_id=proj.org_id)
            db.add(rs)
        rs.quotas = rows
    elif step == "qc":
        q = data.get("qc") or {}
        try:
            secs = int(q.get("min_seconds") or 0)
        except (TypeError, ValueError) as e:
            raise StepError("最短作答时长要是数字") from e
        s["qc"] = {"trap": bool(q.get("trap")), "consistency": bool(q.get("consistency")), "min_seconds": max(0, min(secs, 1800)), "dedupe": bool(q.get("dedupe", True))}
        f.qc = dict(s["qc"])
        questions, made = build(questions, "qc", s)
    s["status"][step] = "confirmed"
    form = recruit_form.normalize({"source": "ai", "questions": questions, "goals": f.goals or [], "need": f.need or ""})
    f.questions = [x.model_dump() for x in form.questions]
    f.step = STEPS.index(step) + 2 if step != "test" else 6
    _save(f, s)
    return made


def _check_cond(c: dict) -> dict:
    if not isinstance(c, dict):
        raise StepError("条件格式不对")
    q = str(c.get("question", "")).strip()[:300]
    opts = _clean_options(c.get("options"))
    use = bool(c.get("use"))
    if use and (not q or not opts):
        raise StepError(f"“{str(c.get('name', ''))[:20]}”要有题目，并且至少有一个通过和一个终止的选项")
    if use and recruit_form.CONTACT_WORDS.search(q):
        raise StepError("甄别题不能问联系方式或证件号")
    return {"id": str(c.get("id", ""))[:40] or "u", "name": str(c.get("name", "")).strip()[:20] or q[:16], "type": "multi" if c.get("type") == "multi" else "single",
            "question": q, "options": opts or [{"text": "符合", "pass": True}, {"text": "不符合", "pass": False}], "use": use, "src": str(c.get("src", "user"))[:6],
            "quota": bool(c.get("quota")), "note": str(c.get("note", ""))[:120]}
