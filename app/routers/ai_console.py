"""AI 后台 (admins only): which AI features the platform has, which prompt and model each uses, and how their calls have gone.

Read-only for now. Shows counts, durations and error categories from the call log (app/ai_calls.py); never conversation text, prompts' inputs or keys.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_calls import AiCallLog, stats
from ..ai_registry import FEATURES, OTHER_KEY, prompt_version
from ..config import settings
from ..db import get_db
from ..deps import Ctx, require_roles
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
        features.append({"key": f.key, "domain": f.domain, "name": f.name, "page": f.page, "prompt": prompt_version(f), "model": "语音识别" if f.key == "asr" else model["alias"],
                         "tracked": f.key != "asr", **s})
    other = numbers.get(OTHER_KEY)
    recent = db.execute(select(AiCallLog.at, AiCallLog.feature, AiCallLog.error_type, AiCallLog.duration_ms).where(AiCallLog.org_id == ctx.org_id, AiCallLog.status == "error")
                        .order_by(AiCallLog.at.desc()).limit(RECENT_FAILURES)).all()
    return {"hours": hours, "model": model, "features": features, "other": other,
            "recent_failures": [{"at": r.at.isoformat(), "feature": r.feature, "error": r.error_type, "ms": r.duration_ms} for r in recent]}
