"""修正方案第 1 轮第 5 条: the outline's interview length, format and languages come from the 立项 sub-project it is bound to (one outline, one sub-project; the others
share it for now). This is the only place that decides whether the outline and the plan disagree; the outline page and the 立项 page both ask `mismatch`.

The outline may hold its own value for a field (`StudySetting.answers`: minutes / format / languages) only while the plan is locked and an unlock has been asked
for; the plan is otherwise changed directly. Callers commit.
"""
import json

from sqlalchemy.orm import Session

from .. import formats, plans
from ..models import Project, ProjectPlan
from . import service as svc
from .models import Outline, StudySetting

LANG_CODE = {"中文": "zh", "英语": "en", "马来语": "ms", "泰米尔语": "ta"}
CODE_LANG = {v: k for k, v in LANG_CODE.items()}
LANG_TEXT = {"zh": "中文", "en": "English", "ms": "马来语", "ta": "泰米尔语"}
FIELDS = {"minutes": "访谈时长", "format": "访谈形式", "languages": "访谈语言"}
OVERRIDE_KEYS = {"minutes": "minutes", "format": "sync_format", "languages": "sync_langs"}   # where the outline keeps its own value in StudySetting.answers


def _cfg(plan: ProjectPlan, live: bool = True) -> plans.PlanConfig:
    raw = plans.live_json(plan) if live else plan.config_json
    return plans.validate(json.loads(raw or "{}") or plans.default_config())


def sub_values(sub: plans.Sub, cfg: plans.PlanConfig) -> dict:
    """minutes, format (one code) and languages (codes) of a sub-project, as the outline uses them."""
    fmts = (sub.fmt or {}).get("depth") or (cfg.fmt or {}).get("depth") or ["video"]
    langs = [LANG_CODE[x] for x in (sub.langs or [sub.lang]) if x in LANG_CODE] or ["en"]
    return {"minutes": sub.minutes, "format": fmts[0], "languages": langs}


def text_of(field: str, value) -> str:
    if field == "minutes":
        return f"{value} 分钟"
    if field == "format":
        return formats.FORMATS.get(value, "其他")
    return "、".join(LANG_TEXT.get(x, "其他") for x in value)


def state(db: Session, proj: Project, o: Outline, st: StudySetting) -> dict | None:
    """The binding and what the outline uses. None for a project made before plans existed (the outline then reads the project as before)."""
    plan = db.get(ProjectPlan, proj.id)
    if plan is None:
        return None
    cfg = _cfg(plan)
    if not cfg.subs:
        return None
    sub = next((s for s in cfg.subs if s.id == o.sub_id), cfg.subs[0])
    base = sub_values(sub, cfg)
    ans = st.answers or {}
    own = {f: ans[k] for f, k in OVERRIDE_KEYS.items() if k in ans and ans[k] not in (None, "", [])}
    used = {**base, **own}
    out = {"sub_id": sub.id, "sub_name": sub.name, "subs": [{"id": s.id, "name": s.name} for s in cfg.subs], "locked": plan.status == "published", "plan_status": plan.status,
           "values": {f: {"value": used[f], "text": text_of(f, used[f]), "plan": base[f], "own": f in own and own[f] != base[f]} for f in FIELDS},
           "request": json.loads(plan.unlock_request) if plan.unlock_request else None}
    out["mismatch"] = mismatch_rows(cfg, sub.id, used)
    return out


def mismatch_rows(cfg: plans.PlanConfig, sub_id: str, used: dict) -> list[dict]:
    """Every (sub-project, field) where the plan says something else than the outline uses: the bound one (the outline holds its own value) and the ones sharing it."""
    rows = []
    for s in cfg.subs:
        v = sub_values(s, cfg)
        for f in FIELDS:
            same = v[f] == used[f] if f != "languages" else set(v[f]) == set(used[f])
            if not same:
                rows.append({"sub_id": s.id, "sub_name": s.name, "field": f, "label": FIELDS[f], "plan": text_of(f, v[f]), "outline": text_of(f, used[f]), "bound": s.id == sub_id, "value": used[f]})
    return rows


def mismatch(db: Session, proj: Project, o: Outline, st: StudySetting) -> list[dict]:
    s = state(db, proj, o, st)
    return s["mismatch"] if s else []


def bind(db: Session, proj: Project, o: Outline, sub_id: str) -> None:
    plan = db.get(ProjectPlan, proj.id)
    if plan is None or not any(s.id == sub_id for s in _cfg(plan).subs):
        raise svc.OutlineError("没有这个子项目", 404)
    o.sub_id = sub_id


def _clean(field: str, value):
    if field == "minutes":
        v = int(value)
        if not 5 <= v <= 240:
            raise svc.OutlineError("访谈时长应在 5 到 240 分钟之间")
        return v
    if field == "format":
        if value not in formats.FORMATS:
            raise svc.OutlineError("访谈形式不对")
        return value
    vals = [x for x in dict.fromkeys(value or []) if x in CODE_LANG]
    if not vals:
        raise svc.OutlineError("至少选一种语言")
    return vals


def change(db: Session, proj: Project, o: Outline, st: StudySetting, field: str, value, user_name: str = "", user_id: str = "", sub_id: str = "") -> str:
    """The outline changes one of its three values. The plan not locked: the bound sub-project in the plan is changed (it must then be reviewed again if it was under
    review). Locked: the outline keeps the value and an unlock request is written on the plan for an admin or the project manager. Returns "plan" or "requested".

    Raises:
        OutlineError: an unknown field or value, or no plan.
    """
    if field not in FIELDS:
        raise svc.OutlineError("只能改访谈时长、形式或语言")
    value = _clean(field, value)
    plan = db.get(ProjectPlan, proj.id)
    if plan is None:
        raise svc.OutlineError("这个项目没有立项记录")
    bound = o.sub_id or _cfg(plan).subs[0].id
    sid = sub_id or bound
    if sid not in {s.id for s in _cfg(plan, live=False).subs}:
        raise svc.OutlineError("没有这个子项目", 404)
    if plan.status != "published":
        raw = json.loads(plan.config_json or "{}") or plans.default_config()
        for s in raw.get("subs", []):
            if s.get("id") == sid:
                if field == "minutes":
                    s["minutes"] = value
                elif field == "format":
                    s["fmt"] = {**(s.get("fmt") or {}), "depth": [value]}
                else:
                    s["langs"], s["lang"] = [CODE_LANG[x] for x in value], CODE_LANG[value[0]]
        try:
            plan.config_json = json.dumps(plans.validate(raw).model_dump(), ensure_ascii=False)
        except ValueError as e:
            raise svc.OutlineError(str(e)) from e
        if plan.status != "draft":
            plan.status, plan.review_hash, plan.review_note = "draft", "", "生成大纲里改了访谈设定，需要重新提交审查"
        if sid != bound:
            return "plan"   # another sub-project changed in the plan: the outline's own values are not touched
        if plan.version == 0:   # nothing published yet: the outline reads the plan as it stands, so it no longer needs its own value
            st.answers = {k: v for k, v in (st.answers or {}).items() if k != OVERRIDE_KEYS[field]}
        else:
            st.answers = {**(st.answers or {}), OVERRIDE_KEYS[field]: value}   # unlocked: the project still runs on the published version until it is published again
        return "plan"
    if sid == bound:
        st.answers = {**(st.answers or {}), OVERRIDE_KEYS[field]: value}
    req = json.loads(plan.unlock_request) if plan.unlock_request else {"changes": []}
    changes = [c for c in req.get("changes", []) if not (c["sub_id"] == sid and c["field"] == field)]
    changes.append({"sub_id": sid, "field": field, "value": value, "text": text_of(field, value)})
    plan.unlock_request = json.dumps({"by": user_id, "by_name": user_name, "at": svc.utcnow().isoformat(timespec="minutes"), "changes": changes, "from": "生成大纲"}, ensure_ascii=False)
    return "requested"


def apply_request(raw: dict, req: dict) -> dict:
    """The plan config with an unlock request's changes written in (used when an admin or project manager unlocks with them)."""
    for c in req.get("changes", []):
        for s in raw.get("subs", []):
            if s.get("id") == c["sub_id"]:
                if c["field"] == "minutes":
                    s["minutes"] = int(c["value"])
                elif c["field"] == "format":
                    s["fmt"] = {**(s.get("fmt") or {}), "depth": [c["value"]]}
                elif c["field"] == "languages":
                    s["langs"], s["lang"] = [CODE_LANG[x] for x in c["value"] if x in CODE_LANG], CODE_LANG.get(c["value"][0], s.get("lang"))
    return raw
