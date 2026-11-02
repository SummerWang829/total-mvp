"""The AI call log: one row per call to the chat model - which feature, which prompt version, how long, and whether it worked.

Only counts, durations and an error *category* are kept. Never the conversation, the prompt text, the model's reply, or a name: those may hold what a
participant or a researcher said (the same rule as app/moderator/providers.py). Writing the log can never break the call it describes.
"""
import contextvars
import functools
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .ai_registry import OTHER_KEY, feature_by_key, feature_for_site, prompt_version
from .db import Base, SessionLocal
from .models import uid, utcnow

log = logging.getLogger(__name__)

STACK_DEPTH = 8  # how many callers up to look for the feature that asked
STATUS_OK, STATUS_ERROR = "ok", "error"
ERR_FORMAT, ERR_CUT, ERR_REFUSED, ERR_TIMEOUT, ERR_NETWORK, ERR_OTHER = "格式错误", "被截断", "服务拒绝", "超时", "网络或服务繁忙", "其他"

_org: contextvars.ContextVar[str] = contextvars.ContextVar("ai_call_org", default="")


class AiCallLog(Base):
    __tablename__ = "ai_call_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), default="", index=True)  # "" = made outside a signed-in request (background work): shown to nobody
    feature: Mapped[str] = mapped_column(String(40), index=True)
    prompt_version: Mapped[str] = mapped_column(String(24), default="")
    model: Mapped[str] = mapped_column(String(40), default="")  # an alias ("云端模型" / "本地模型"), not the vendor's model name
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(8))
    error_type: Mapped[str] = mapped_column(String(24), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


def set_org(org_id: str) -> None:
    """Remember whose request this is, so the calls it makes are logged under the right company (attribution only, never authorization)."""
    _org.set(org_id or "")


@contextmanager
def org_scope(org_id: str) -> Iterator[None]:
    """Log the calls made inside the block under this company (for background work that has no request)."""
    token = _org.set(org_id or "")
    try:
        yield
    finally:
        _org.reset(token)


def error_category(error: BaseException) -> str:
    """The error's category in plain Chinese. Looks at the error's type and its short technical tag only, never at anything the model said."""
    kind, text = type(error).__name__, str(error)
    if kind == "ChatFatalError":
        return ERR_REFUSED
    if kind == "ChatFormatError":
        return ERR_FORMAT
    if "cut off" in text:
        return ERR_CUT
    if "Timeout" in text:
        return ERR_TIMEOUT
    if kind in ("ChatError", "_TransientChatError") and ("HTTP" in text or "Error" in text):
        return ERR_NETWORK
    return ERR_OTHER


def _feature_of_caller(skip: int) -> str:
    """The feature whose code asked for this call, found by looking up the call stack for a (module, function) in the registry."""
    try:
        frame = sys._getframe(skip)  # noqa: SLF001 - the stack is the only place that knows who asked
    except ValueError:
        return OTHER_KEY
    for _ in range(STACK_DEPTH):
        if frame is None:
            break
        key = feature_for_site(frame.f_globals.get("__name__", ""), frame.f_code.co_name)
        if key:
            return key
        frame = frame.f_back
    return OTHER_KEY


def _prompt_of(feature: str) -> str:
    f = feature_by_key(feature)
    return prompt_version(f) if f else ""


def record(feature: str, model: str, started: float, error: BaseException | None) -> None:
    """Write one log row. A failure here is logged and forgotten: the business call must not notice."""
    try:
        with SessionLocal() as db:
            db.add(AiCallLog(org_id=_org.get(), feature=feature, prompt_version=_prompt_of(feature), model=model, duration_ms=int((time.monotonic() - started) * 1000),
                             status=STATUS_ERROR if error else STATUS_OK, error_type=error_category(error) if error else ""))
            db.commit()
    except Exception:  # noqa: BLE001 - any failure of the log is swallowed on purpose, and recorded
        log.warning("could not write the AI call log", exc_info=True)


def logged(model_alias: Callable[[object], str]) -> Callable:
    """Decorator for a chat provider's complete(): logs every call, failed ones too, then returns or raises exactly what complete() did."""
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            feature, started = _feature_of_caller(2), time.monotonic()
            try:
                result = fn(self, *args, **kwargs)
            except BaseException as e:
                record(feature, model_alias(self), started, e)
                raise
            record(feature, model_alias(self), started, None)
            return result
        return inner
    return wrap


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))] if ordered else 0


def stats(db: Session, org_id: str, hours: int = 24) -> dict[str, dict]:
    """Per-feature numbers for one company over the last `hours`: calls, failures, error categories, average and p95 duration (ms)."""
    since = utcnow() - timedelta(hours=hours)
    rows = db.execute(select(AiCallLog.feature, AiCallLog.status, AiCallLog.error_type, AiCallLog.duration_ms).where(AiCallLog.org_id == org_id, AiCallLog.at >= since)).all()
    per: dict[str, dict] = {}
    for feature, status, err, ms in rows:
        s = per.setdefault(feature, {"calls": 0, "failed": 0, "errors": {}, "ms": []})
        s["calls"] += 1
        s["ms"].append(ms)
        if status == STATUS_ERROR:
            s["failed"] += 1
            s["errors"][err] = s["errors"].get(err, 0) + 1
    return {k: {"calls": s["calls"], "failed": s["failed"], "errors": s["errors"], "avg_ms": int(sum(s["ms"]) / len(s["ms"])), "p95_ms": _percentile(s["ms"], 0.95)}
            for k, s in per.items()}
