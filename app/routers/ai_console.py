"""AI 后台 (admins only): which AI features the platform has, which prompt and model each uses, and how their calls have gone.

Read-only for now. Shows counts, durations and error categories from the call log (app/ai_calls.py); never conversation text, prompts' inputs or keys.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ai_prompts
from ..ai_calls import AiCallLog, stats
from ..ai_registry import FEATURES, OTHER_KEY, feature_by_key, prompt_version
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import Ctx, require_roles
from ..models import User
from ..moderator.compliance import moderator_runs_locally

router = APIRouter(prefix="/api/ai-console", tags=["ai-console"])
RECENT_FAILURES = 20


def model_alias() -> dict:
    """The chat model in use as an alias: its kind, never the vendor's model name or address, and never a key."""
    if settings.moderator_provider == "mock":
        return {"alias": "演示模型", "kind": "mock", "ready": True}
    local = moderator_runs_locally(settings)
    return {"alias": "本地模型" if local else "云端模型", "kind": "local" if local else "cloud", "ready": local or bool(settings.moderator_vendor)}


@router.get("/overview")
def overview(hours: int = Query(24, ge=1, le=720), ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Every AI feature with its prompt version, model alias and call numbers for the last `hours` hours, plus the latest failures."""
    numbers = stats(db, ctx.org_id, hours)
    model = model_alias()
    empty = {"calls": 0, "failed": 0, "errors": {}, "avg_ms": 0, "p95_ms": 0}
    features = []
    for f in FEATURES:
        s = numbers.get(f.key, empty)
        features.append({"key": f.key, "domain": f.domain, "name": f.name, "page": f.page, "prompt": ai_prompts.live_label(db, ctx.org_id, f.key) if f.key in ai_prompts.SLOTS else prompt_version(f), "model": "语音识别" if f.key == "asr" else model["alias"],
                         "tracked": f.key != "asr", "editable": f.key in ai_prompts.SLOTS, **s})
    other = numbers.get(OTHER_KEY)
    recent = db.execute(select(AiCallLog.at, AiCallLog.feature, AiCallLog.error_type, AiCallLog.duration_ms).where(AiCallLog.org_id == ctx.org_id, AiCallLog.status == "error")
                        .order_by(AiCallLog.at.desc()).limit(RECENT_FAILURES)).all()
    return {"hours": hours, "model": model, "features": features, "other": other,
            "recent_failures": [{"at": r.at.isoformat(), "feature": r.feature, "error": r.error_type, "ms": r.duration_ms} for r in recent]}


class VersionBody(BaseModel):
    text: str = Field(max_length=ai_prompts.MAX_PROMPT_CHARS + 2000)
    note: str = Field(default="", max_length=ai_prompts.MAX_NOTE_CHARS)


class LiveBody(BaseModel):
    version: int | None = Field(default=None, ge=1)  # None = back to the built-in prompt


def _slot(key: str) -> None:
    if key not in ai_prompts.SLOTS:
        raise HTTPException(404, "这个功能没有可以修改的提示词")


def _prompt_view(db: Session, ctx: Ctx, key: str) -> dict:
    f = feature_by_key(key)
    names = {u.id: u.name for u in db.scalars(select(User).where(User.org_id == ctx.org_id))}
    rows = ai_prompts.versions(db, ctx.org_id, key)
    return {"key": key, "domain": f.domain if f else "", "name": f.name if f else key, "page": f.page if f else "", "built_in": ai_prompts.built_in(key),
            "identity": ai_prompts.SLOTS[key].identity, "placeholders": [p for p in ai_prompts.PLACEHOLDERS if p in ai_prompts.built_in(key)],
            "live": next((r.version for r in rows if r.state == ai_prompts.LIVE), None),
            "versions": [{"version": r.version, "state": r.state, "text": r.text, "note": r.note, "by": names.get(r.created_by, ""), "at": r.created_at.isoformat(),
                          "live_at": r.live_at.isoformat() if r.live_at else None} for r in rows]}


@router.get("/prompts")
def prompts(ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """The features whose prompt can be edited here, with the version each uses now (the AI moderator's prompts are not among them)."""
    out = []
    for f in FEATURES:
        if f.key in ai_prompts.SLOTS:
            out.append({"key": f.key, "domain": f.domain, "name": f.name, "page": f.page, "prompt": ai_prompts.live_label(db, ctx.org_id, f.key)})
    return {"features": out}


@router.get("/prompts/{key}")
def prompt_detail(key: str, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    _slot(key)
    return _prompt_view(db, ctx, key)


@router.post("/prompts/{key}/versions", status_code=201)
def prompt_save(key: str, body: VersionBody, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Save the text as a new version (a draft: nothing changes until it is put live)."""
    _slot(key)
    try:
        row = ai_prompts.save_draft(db, ctx.org_id, ctx.user.id, key, body.text, body.note)
    except ai_prompts.PromptError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "ai.prompt.save", key, version=row.version)
    db.commit()
    return _prompt_view(db, ctx, key)


@router.post("/prompts/{key}/live")
def prompt_live(key: str, body: LiveBody, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Put a saved version live, or go back to the built-in prompt (version null). The version that was live stays in the list, so it can be put live again."""
    _slot(key)
    try:
        label = ai_prompts.set_live(db, ctx.org_id, key, body.version)
    except ai_prompts.PromptError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "ai.prompt.live", key, version=label)
    db.commit()
    return _prompt_view(db, ctx, key)
