"""人群圈选与假名化：数据库发送页的人群定义、标签、锁定、规模估算与发送留痕。

Every query is scoped by org_id and the project is resolved with get_project() (another company's
project is a 404). The estimate is a real SQL COUNT on the project's participant table, run in a
background thread so the page can show 「正在请求中…」; when it finishes the page copies the number
into 「N 人符合筛选」.
"""
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import Ctx, STAFF, require_roles
from ..models import (AudienceCondition, AudienceDefinition, AudienceEstimate, AudienceTag,
                      Participant, ParticipantPseudonym, Project, ProjectPlan, utcnow)
from .projects import get_project
from .recruit import _ai_chat

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["audience"])

DEFAULT_BRIEF = "大湾区九市，18 岁以上，家庭月收入 ≥ ¥15,000，国庆期间到访香港"
DEFAULT_CONDITIONS = [
    {"field_key": "常住地", "op": "in", "value": "大湾区九市"},
    {"field_key": "年龄", "op": "gte", "value": "18 岁以上"},
    {"field_key": "家庭月收入", "op": "gte", "value": "≥ ¥15,000"},
    {"field_key": "访港经历", "op": "in", "value": "国庆期间到访香港"},
]
KNOWN_FIELDS = {"country", "language", "screening", "screen_result"}  # real Participant columns

# The country / interview language come from the project overview (立项向导's sub-projects), never
# from a hard-coded example. Fallbacks are only for projects made before plans existed.
_COUNTRY_FLAGS = {"香港": "🇭🇰", "中国香港": "🇭🇰", "中国": "🇨🇳", "新加坡": "🇸🇬", "马来西亚": "🇲🇾",
                  "印尼": "🇮🇩", "越南": "🇻🇳", "泰国": "🇹🇭", "菲律宾": "🇵🇭", "日本": "🇯🇵",
                  "韩国": "🇰🇷", "德国": "🇩🇪", "美国": "🇺🇸", "英国": "🇬🇧", "印度": "🇮🇳"}
_MARKET_NAMES = {"HK": "香港", "CN": "中国", "SG": "新加坡", "MY": "马来西亚", "ID": "印尼",
                 "VN": "越南", "TH": "泰国", "PH": "菲律宾", "JP": "日本", "KR": "韩国",
                 "DE": "德国", "US": "美国", "GB": "英国", "IN": "印度"}
_LANG_NAMES = {"zh": "中文", "zh-CN": "中文（普通话 · 简体）", "zh-HK": "中文（香港繁体）",
               "en": "English", "ms": "Bahasa Melayu", "ta": "தமிழ் (Tamil)"}


def _rule_tags(text: str) -> list[str]:
    """Strict extraction: only the phrases actually written in the brief. No invented conditions."""
    out: list[str] = []
    for part in re.split(r"[，,、；;。\n]+", text or ""):
        part = part.strip()
        if 2 <= len(part) <= 24 and part not in out:
            out.append(part)
        if len(out) >= 10:
            break
    return out


DEFAULT_TAGS = _rule_tags(DEFAULT_BRIEF)   # 4 conditions, all traceable to the brief


class DefinitionIn(BaseModel):
    brief_text: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[dict] = Field(default_factory=list, max_length=30)
    sub_id: str = Field(default="", max_length=40)  # which sub-project of the project overview to follow


class ExtractIn(BaseModel):
    brief_text: str = Field(default="", max_length=2000)
    mock: bool = False  # offline / test: skip the model and use the rule-based extractor
    sub_id: str = Field(default="", max_length=40)


def _project_market_language(db: Session, proj: Project, sub_id: str = "") -> dict:
    """Read the country + interview language the 项目总览 actually runs on.

    The wizard stores one sub-project per country (config_json.subs[].name / .lang). A published
    plan keeps running on published_json while a new draft is edited, so the live config is used.
    """
    plan = db.get(ProjectPlan, proj.id)
    subs: list[dict] = []
    if plan is not None and plan.org_id == proj.org_id:
        try:
            live = plan.published_json or plan.config_json if plan.version > 0 else plan.config_json
            subs = (json.loads(live or "{}") or {}).get("subs") or []
        except (ValueError, TypeError, AttributeError) as exc:
            log.warning("audience.market: cannot read plan config for %s: %s", proj.id, exc)
    opts = []
    for s in subs:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        langs = s.get("langs") or []
        lang = str(s.get("lang") or (langs[0] if langs else "") or "").strip()
        opts.append({"id": str(s.get("id") or ""), "name": name, "language": lang})
    pick = next((o for o in opts if sub_id and o["id"] == sub_id), None) or (opts[0] if opts else None)
    if pick:
        # Same names as the report / respondent cards: one project, one set of labels.
        from .. import project_context as _pctx
        country, language, picked_id = _pctx.market_label(pick["name"]), _pctx.lang_label(pick["language"]), pick["id"]
    else:
        country = _MARKET_NAMES.get((proj.market or "").upper(), proj.market or "")
        language = _LANG_NAMES.get(proj.language or "", proj.language or "")
        picked_id = ""
    return {"country": country, "language": language,
            "flag": _COUNTRY_FLAGS.get(country, "🏳"), "sub_projects": opts, "sub_id": picked_id}


class SendRecordIn(BaseModel):
    participant_ids: list[str] = Field(default_factory=list, max_length=500)


def _definition(db: Session, org_id: str, project_id: str) -> AudienceDefinition | None:
    return db.scalars(select(AudienceDefinition).where(
        AudienceDefinition.org_id == org_id,
        AudienceDefinition.project_id == project_id,
    ).order_by(AudienceDefinition.created_at.desc())).first()


def _estimate(db: Session, org_id: str, project_id: str) -> AudienceEstimate | None:
    return db.scalars(select(AudienceEstimate).where(
        AudienceEstimate.org_id == org_id,
        AudienceEstimate.project_id == project_id,
    ).order_by(AudienceEstimate.created_at.desc())).first()


def _estimate_out(e: AudienceEstimate | None) -> dict:
    if e is None:
        return {"status": "none", "matched_count": None, "eta_at": None}
    return {"id": e.id, "status": e.status, "matched_count": e.matched_count,
            "eta_at": e.eta_at.isoformat() if e.eta_at else None, "error": e.error}


def _definition_out(db: Session, d: AudienceDefinition | None, e: AudienceEstimate | None, market: dict) -> dict:
    # country / language always mirror the project overview, never a value typed into this card.
    base = {"country": market["country"], "language": market["language"], "market": market,
            "estimate": _estimate_out(e)}
    if d is None:
        return {"id": "", "version": 1, "status": "draft", "saved": False,
                "brief_text": DEFAULT_BRIEF, "tags": DEFAULT_TAGS, "conditions": DEFAULT_CONDITIONS, **base}
    tags = [t.name for t in db.scalars(select(AudienceTag).where(
        AudienceTag.definition_id == d.id, AudienceTag.org_id == d.org_id).order_by(AudienceTag.order_no)).all()]
    conds = [{"field_key": c.field_key, "op": c.op, "value": c.value} for c in db.scalars(
        select(AudienceCondition).where(AudienceCondition.definition_id == d.id,
                                        AudienceCondition.org_id == d.org_id)).all()]
    return {"id": d.id, "version": d.version, "status": d.status, "saved": True,
            "brief_text": d.brief_text, "tags": tags, "conditions": conds, **base}


@router.get("/projects/{pid}/audience/definition")
def get_definition(pid: str, sub_id: str = "", ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The current audience definition (or the untouched draft), plus the latest estimate."""
    proj = get_project(db, ctx, pid)
    market = _project_market_language(db, proj, sub_id)
    return _definition_out(db, _definition(db, ctx.org_id, proj.id), _estimate(db, ctx.org_id, proj.id), market)


@router.post("/projects/{pid}/audience/definition")
def save_definition(pid: str, body: DefinitionIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Save the brief, tags and conditions. A locked definition cannot be changed any more."""
    proj = get_project(db, ctx, pid)
    d = _definition(db, ctx.org_id, proj.id)
    if d is not None and d.status == "locked":
        raise HTTPException(422, "人群已锁定，不能再修改；如需改动请新建版本")
    if d is None:
        d = AudienceDefinition(org_id=ctx.org_id, project_id=proj.id, version=1, status="draft")
        db.add(d)
        db.flush()
    market = _project_market_language(db, proj, body.sub_id)
    d.brief_text = body.brief_text
    d.country, d.language = market["country"], market["language"]  # snapshot of the overview, for the audit trail
    d.updated_at, d.created_by = utcnow(), ctx.actor
    for old in db.scalars(select(AudienceTag).where(AudienceTag.definition_id == d.id)).all():
        db.delete(old)
    for old in db.scalars(select(AudienceCondition).where(AudienceCondition.definition_id == d.id)).all():
        db.delete(old)
    for i, name in enumerate(dict.fromkeys(t.strip() for t in body.tags if t.strip())):
        db.add(AudienceTag(org_id=ctx.org_id, definition_id=d.id, name=name[:120], source="manual", order_no=i))
    for c in body.conditions:
        db.add(AudienceCondition(org_id=ctx.org_id, definition_id=d.id,
                                 field_key=str(c.get("field_key", ""))[:60], op=str(c.get("op", "eq"))[:20],
                                 value=str(c.get("value", ""))[:200]))
    db.commit()
    return {**_definition_out(db, d, _estimate(db, ctx.org_id, proj.id), market), "saved": True}


def _flat(s: object) -> str:
    """A loose form of a phrase, so '家庭月收入 ≥ ¥15,000' and its evidence still match."""
    return re.sub(r"[\s，,、；;。·：:（）()【】\[\]¥￥≥≤><=＝/\\\-_]", "", str(s or "")).lower()


def _anchor_ok(name: str, ev: str) -> bool:
    """The tag must share wording with the phrase it claims as evidence."""
    n, e = _flat(name), _flat(ev)
    if not n or not e:
        return False
    if n in e or e in n:
        return True
    return len(n) >= 2 and any(n[i:i + 2] in e for i in range(len(n) - 1))


def _grounded(items: list, brief: str) -> list[str]:
    """Hard gate: a tag survives only if it is really derived from the 圈选描述.

    Three checks, all on the server (the model is never trusted blindly):
      1. evidence must be a *local* continuous phrase from the brief, not the whole brief;
      2. the tag must share wording with that phrase (an invented tag cannot borrow a random phrase);
      3. the tag cannot be mostly made of Chinese characters absent from the brief.
    So 「中高收入」←「家庭月收入 ≥ ¥15,000」passes (联想/归纳), while 「有换车计划」 is dropped.
    """
    flat = _flat(brief)
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name, ev = str(item.get("name") or "").strip(), str(item.get("evidence") or "").strip()
        else:
            name, ev = str(item).strip(), ""
        if not name or len(name) > 24 or name in out:
            continue
        unknown = sum(1 for c in name if "\u4e00" <= c <= "\u9fff" and c not in flat)
        fev, fname = _flat(ev), _flat(name)
        local = len(fev) >= 2 and fev != flat and fev in flat
        if ((local and _anchor_ok(name, ev) and unknown <= 3) or (fname and fname in flat)):
            out.append(name)
        if len(out) >= 10:
            break
    return out


def _tag_summary(tags: list[str], brief: str) -> str:
    if not tags:
        return "圈选描述为空，先写下目标人群，再提取标签。"
    return (f"仅依据圈选描述「{brief.strip()[:60]}」推导出 {len(tags)} 个标签，"
            "可归纳改写，但未引入描述中没有出现过的条件。")


def _extract_tags(text: str, mock: bool = False) -> dict:
    """Real model derivation, strictly grounded in the brief; falls back to literal phrases.

    chat.complete() returns a parsed JSON *object* (the provider forces response_format=json_object),
    so the reply is read as a dict — treating it as a string silently disabled the model before.
    """
    fallback = _rule_tags(text)
    if not text.strip():
        return {"tags": [], "summary": _tag_summary([], text)}
    if mock or settings.moderator_provider == "mock":
        return {"tags": fallback, "summary": _tag_summary(fallback, text)}
    prompt = [
        {"role": "system", "content":
            "你是受众圈选助手。只能依据用户给出的「圈选描述」真实推导出人群标签。\n"
            "规则：\n"
            "1) 每个标签都要能追溯到描述里的一段原文；evidence 必须是描述中原样出现的连续片段（省略空格和符号也可以）。\n"
            "2) 允许对原文做归纳、改写或合理联想，例如把「家庭月收入 ≥ ¥15,000」归纳为「中高收入」，但 evidence 仍必须指向那段原文。\n"
            "3) 严禁引入描述里完全没有出现的新条件、新维度或新事实。例如描述没提到汽车，就绝不能出现「有换车计划」。\n"
            "4) 输出 3-8 个标签，只输出一个 JSON 对象："
            '{"tags":[{"name":"标签文字","evidence":"描述里的原文片段"}]}。不要解释，不要输出姓名、电话、邮箱或任何个人标识。'},
        {"role": "user", "content": f"圈选描述：{text[:2000]}"},
    ]
    for attempt in range(2):
        pool = None
        try:
            chat = _ai_chat()
            if chat is None:
                break
            pool = ThreadPoolExecutor(max_workers=1)
            reply = pool.submit(chat.complete, prompt, temperature=0.2).result(timeout=20)
            raw = reply.get("tags") if isinstance(reply, dict) else None
            if raw is None and isinstance(reply, dict):  # a model that wraps the list in another key
                raw = next((v for v in reply.values() if isinstance(v, list)), None)
            tags = _grounded(raw if isinstance(raw, list) else [], text)
            if tags:
                return {"tags": tags, "summary": _tag_summary(tags, text)}
            log.warning("audience.extract_tags: no grounded tags in model reply (attempt %s)", attempt + 1)
        except Exception as exc:  # noqa: BLE001 - every failure falls back to the literal phrases
            log.warning("audience.extract_tags attempt %s failed: %s", attempt + 1, exc)
        finally:
            if pool is not None:
                pool.shutdown(wait=False)
    return {"tags": fallback, "summary": _tag_summary(fallback, text)}


@router.post("/projects/{pid}/audience/extract-tags")
def extract_tags(pid: str, body: ExtractIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """✧ 根据描述提取标签 — the model derives tags from the brief, never inventing conditions."""
    get_project(db, ctx, pid)
    try:
        return _extract_tags(body.brief_text, mock=body.mock)
    except Exception as exc:  # noqa: BLE001
        log.exception("audience.extract_tags failed: %s", exc)
        tags = _rule_tags(body.brief_text)
        return {"tags": tags, "summary": _tag_summary(tags, body.brief_text)}


@router.post("/projects/{pid}/audience/lock")
def lock_definition(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """🔒 锁定人群 — after this the definition is read-only and sending can be traced to it."""
    proj = get_project(db, ctx, pid)
    d = _definition(db, ctx.org_id, proj.id)
    if d is None:
        raise HTTPException(422, "请先保存人群定义，再锁定人群")
    if d.status != "locked":
        d.status, d.locked_at, d.updated_at = "locked", utcnow(), utcnow()
        db.commit()
    return {**_definition_out(db, d, _estimate(db, ctx.org_id, proj.id),
                              _project_market_language(db, proj)), "saved": True}


def _count_matching(db: Session, org_id: str, project_id: str) -> int:
    """COUNT with the filters pushed into SQL (never load the list just to count it).

    v1 counts this project's contactable participants; structured conditions on real Participant
    columns are added to the WHERE clause. Free-text tags (age, income, visit history) are kept as
    definition metadata until the external audience database is connected.
    """
    stmt = select(func.count(Participant.id)).where(
        Participant.org_id == org_id,
        Participant.project_id == project_id,
        Participant.anonymized_at.is_(None),
        Participant.screening == "eligible",
    )
    d = _definition(db, org_id, project_id)
    if d is not None:
        for c in db.scalars(select(AudienceCondition).where(
                AudienceCondition.definition_id == d.id, AudienceCondition.org_id == org_id)).all():
            if c.field_key in KNOWN_FIELDS and c.value:
                stmt = stmt.where(getattr(Participant, c.field_key) == c.value)
    return int(db.scalar(stmt) or 0)


def _run_estimate(estimate_id: str, org_id: str, project_id: str) -> None:
    """Background worker: a short async job, then the result the page polls for."""
    time.sleep(2)
    try:
        with SessionLocal() as db:
            e = db.get(AudienceEstimate, estimate_id)
            if e is None:
                return
            e.matched_count, e.status, e.finished_at = _count_matching(db, org_id, project_id), "done", utcnow()
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("audience.estimate %s failed: %s", estimate_id, exc)
        try:
            with SessionLocal() as db:
                e = db.get(AudienceEstimate, estimate_id)
                if e is not None:
                    e.status, e.error = "failed", str(exc)[:300]
                    db.commit()
        except Exception:  # noqa: BLE001
            log.exception("audience.estimate %s could not record its failure", estimate_id)


@router.post("/projects/{pid}/audience/estimate")
def start_estimate(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Submit an audience-size estimate; the page polls GET until it is done."""
    proj = get_project(db, ctx, pid)
    d = _definition(db, ctx.org_id, proj.id)
    e = AudienceEstimate(org_id=ctx.org_id, project_id=proj.id,
                         definition_id=d.id if d else "", status="pending",
                         eta_at=utcnow() + timedelta(seconds=5))
    db.add(e)
    db.commit()
    threading.Thread(target=_run_estimate, args=(e.id, ctx.org_id, proj.id), daemon=True).start()
    return _estimate_out(e)


@router.get("/projects/{pid}/audience/estimate")
def get_estimate(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Poll the latest estimate; a done result is copied into 「N 人符合筛选」 by the page."""
    proj = get_project(db, ctx, pid)
    return _estimate_out(_estimate(db, ctx.org_id, proj.id))


@router.post("/projects/{pid}/audience/record-send")
def record_send(pid: str, body: SendRecordIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Write the pseudonymisation trail for the people the screening link was sent to.

    The e-mail itself is still sent by /recruiting/screen-invite; this only records a stable
    pseudonym so an export can prove which audience version was targeted without exposing names.
    """
    proj = get_project(db, ctx, pid)
    d = _definition(db, ctx.org_id, proj.id)
    if d is None:
        return {"recorded": 0}
    recorded = 0
    for part_id in dict.fromkeys(body.participant_ids):
        person = db.get(Participant, part_id)
        if person is None or person.org_id != ctx.org_id or person.project_id != proj.id:
            continue
        exists = db.scalars(select(ParticipantPseudonym).where(
            ParticipantPseudonym.org_id == ctx.org_id,
            ParticipantPseudonym.definition_id == d.id,
            ParticipantPseudonym.participant_id == part_id)).first()
        if exists:
            continue
        pseudo = "P-" + hashlib.sha256((d.id + part_id).encode()).hexdigest()[:10].upper()
        db.add(ParticipantPseudonym(org_id=ctx.org_id, project_id=proj.id, definition_id=d.id,
                                    participant_id=part_id, pseudonym=pseudo))
        recorded += 1
    db.commit()
    return {"recorded": recorded}
