"""Collecting, de-duplicating, tagging and summarising mentions; take-downs; the background job."""
import hashlib
import hmac
import logging
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import content_rules
from ..config import settings
from ..db import SessionLocal
from ..models import ListeningSource, Mention, MentionSuppression, utcnow
from ..providers import LLM, get_llm
from .connectors import CONNECTORS, ConfigError, RawItem
from .safe_http import FetchFailed, Fetcher, UnsafeUrl

log = logging.getLogger(__name__)

BATCH = 20
MAX_ENRICH_ATTEMPTS = 3
POLL_S = 60
ERROR_BACKOFF_MIN = 60   # after a failed run, wait at least this long before the background job tries again
SENTIMENTS = ("neg", "neu", "pos")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def author_hash(author: str) -> str:
    """A salted, shortened hash of an author's name (the name itself is never stored): enough to see 'the same person again' and to take them down."""
    return hmac.new(settings.secret_key.encode(), author.strip().lower().encode(), hashlib.sha256).hexdigest()[:16] if author.strip() else ""


def canonical(url: str) -> str:
    """The address without a fragment and trailing slash, lower-cased host: the same page found twice counts once."""
    u = url.strip().split("#", 1)[0].rstrip("/")
    return re.sub(r"^(https?://)([^/]+)", lambda m: m.group(1) + m.group(2).lower(), u)


def detect_lang(text: str) -> str:
    """Language of a post from its letters (zh / ta / ms / en): good enough to filter and to choose a reading; not a translator's guess."""
    cjk = len(re.findall(r"[㐀-鿿]", text))
    tamil = len(re.findall(r"[஀-௿]", text))
    letters = len(re.findall(r"[A-Za-z㐀-鿿஀-௿]", text)) or 1
    if cjk / letters > 0.2:
        return "zh"
    if tamil / letters > 0.2:
        return "ta"
    malay = len(re.findall(r"\b(yang|dan|tidak|dengan|untuk|ini|itu|saya|sangat|tak|dah|nak|boleh|tapi)\b", text.lower()))
    return "ms" if malay >= 2 else "en"


def _suppressed(db: Session, project_id: str) -> tuple[set[str], set[str]]:
    rows = db.execute(select(MentionSuppression.kind, MentionSuppression.value).where(MentionSuppression.project_id == project_id)).all()
    return {v for k, v in rows if k == "hash"}, {v for k, v in rows if k == "author"}


def ingest(db: Session, source: ListeningSource, items: list[RawItem]) -> int:
    """Store the new items of a collection: contact details are masked, the author only as a hash, duplicates and taken-down items skipped. Returns how many are new."""
    seen_hash, seen_author = _suppressed(db, source.project_id)
    have = set(db.scalars(select(Mention.dedup_hash).where(Mention.project_id == source.project_id)))
    new = 0
    for it in items:
        key = _hash(canonical(it.url)) if it.url else _hash(it.text[:300])
        ah = author_hash(it.author)
        if key in have or key in seen_hash or (ah and ah in seen_author) or not it.text.strip():
            continue
        lang = detect_lang(it.text)
        db.add(Mention(org_id=source.org_id, project_id=source.project_id, source_id=source.id, url=it.url[:2000], title=it.title[:300],
                       text=content_rules.check(it.text, lang).text, lang=lang, posted_at=it.posted_at, author_hash=ah, dedup_hash=key, metrics=it.metrics))
        have.add(key)
        new += 1
    db.flush()  # (sessions here do not flush by themselves: the next query must see these rows)
    return new


def run_source(db: Session, source: ListeningSource, fetcher: Fetcher | None = None) -> dict:
    """Collect one source now. A failure is recorded on the source (as plain words, never page content) and returned, not raised.

    Returns:
        {"ok": bool, "new": int, "error": str}
    """
    fetcher = fetcher or Fetcher()
    try:
        items = CONNECTORS[source.kind].fetch(source.config or {}, fetcher)
        new = ingest(db, source, items)
        source.last_status, source.last_error, source.last_count = "ok", "", new
        result = {"ok": True, "new": new, "error": ""}
    except (ConfigError, UnsafeUrl, FetchFailed) as e:
        source.last_status, source.last_error, source.last_count = "error", str(e)[:300], 0
        result = {"ok": False, "new": 0, "error": str(e)[:300]}
    except Exception as e:  # noqa: BLE001 - one broken source must not stop the others; the type is kept, the content is not
        log.exception("listening source %s failed", source.id)
        source.last_status, source.last_error, source.last_count = "error", f"内部错误：{type(e).__name__}", 0
        result = {"ok": False, "new": 0, "error": source.last_error}
    source.last_run_at = utcnow()
    return result


def enrich_pending(db: Session, llm: LLM | None = None, limit: int = 100) -> int:
    """Tag mentions that are not tagged yet (sentiment and topics, in batches through the language model). A failed batch is retried later, up to MAX_ENRICH_ATTEMPTS.

    Returns how many were tagged.
    """
    db.flush()
    rows = db.scalars(select(Mention).where(Mention.enriched_at.is_(None), Mention.enrich_attempts < MAX_ENRICH_ATTEMPTS).order_by(Mention.fetched_at).limit(limit)).all()
    llm = llm or get_llm()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        for m in batch:
            m.enrich_attempts += 1
        db.flush()
        try:
            got = {int(r["n"]): r for r in llm.classify_mentions([{"n": n, "text": m.text[:600]} for n, m in enumerate(batch)]) if str(r.get("n", "")).lstrip("-").isdigit()}
        except Exception as e:  # noqa: BLE001 - tagging is retried later; the mentions are already stored
            log.warning("tagging failed: %s", type(e).__name__)
            continue
        for n, m in enumerate(batch):
            r = got.get(n)
            if not r:
                continue
            m.sentiment = r.get("sentiment") if r.get("sentiment") in SENTIMENTS else "neu"
            m.topics = [str(t).strip()[:30] for t in (r.get("topics") or []) if str(t).strip()][:3] if isinstance(r.get("topics"), list) else []
            m.enriched_at = utcnow()
            done += 1
    return done


def run_due(db: Session, now: datetime | None = None, fetcher: Fetcher | None = None) -> int:
    """Collect every enabled source whose time has come (the background job's step). Returns how many sources were run."""
    now = now or utcnow()
    n = 0
    for s in db.scalars(select(ListeningSource).where(ListeningSource.enabled.is_(True))):
        wait = max(s.interval_min, ERROR_BACKOFF_MIN if s.last_status == "error" else 0)
        if s.last_run_at is None or s.last_run_at + timedelta(minutes=wait) <= now:
            run_source(db, s, fetcher)
            db.commit()
            n += 1
    return n


def start_worker() -> None:
    """Start the background job (collect due sources, tag new mentions). Off when the other background workers are off (tests)."""
    if not settings.run_worker:
        return

    def loop() -> None:
        while True:
            try:
                with SessionLocal() as db:
                    run_due(db)
                    enrich_pending(db)
                    db.commit()
            except Exception:  # the job must outlive any single failure
                log.exception("listening worker")
            time.sleep(POLL_S)

    threading.Thread(target=loop, daemon=True, name="listening-worker").start()


# ---------- reading ----------
def summary(db: Session, project_id: str, days: int = 30) -> dict:
    """Numbers for the page: totals, sentiment split, volume per day by sentiment, top topics, languages."""
    since = utcnow() - timedelta(days=days)
    stamp = func.coalesce(Mention.posted_at, Mention.fetched_at)
    rows = db.execute(select(Mention.sentiment, Mention.topics, Mention.lang, stamp).where(Mention.project_id == project_id, stamp >= since)).all()
    per_day: dict[str, Counter] = {}
    topics: Counter = Counter()
    langs: Counter = Counter()
    split: Counter = Counter()
    for sent, tps, lang, at in rows:
        per_day.setdefault(at.strftime("%Y-%m-%d"), Counter())[sent or "none"] += 1
        topics.update(tps or [])
        langs[lang] += 1
        split[sent or "none"] += 1
    total = len(rows)
    return {"days": days, "total": total, "sentiment": {k: split.get(k, 0) for k in (*SENTIMENTS, "none")},
            "negative_share": round(split["neg"] / total, 2) if total else None,
            "per_day": [{"day": d, **{k: c.get(k, 0) for k in (*SENTIMENTS, "none")}} for d, c in sorted(per_day.items())],
            "topics": [{"topic": t, "count": c} for t, c in topics.most_common(10)], "languages": dict(langs)}


def mention_out(m: Mention) -> dict:
    return {"id": m.id, "url": m.url, "title": m.title, "text": m.text, "lang": m.lang, "sentiment": m.sentiment, "topics": m.topics or [], "metrics": m.metrics or {},
            "posted_at": m.posted_at.isoformat() if m.posted_at else None, "author": m.author_hash or None, "source_id": m.source_id}


def take_down(db: Session, org_id: str, project_id: str, by: str, url: str = "", author: str = "") -> int:
    """Remove mentions by link or by author hash, and remember it so they are not collected again. Returns how many mentions were removed."""
    removed = 0
    if url.strip():
        h = _hash(canonical(url))
        removed += db.execute(delete(Mention).where(Mention.project_id == project_id, Mention.dedup_hash == h)).rowcount or 0
        if not db.scalar(select(MentionSuppression.id).where(MentionSuppression.project_id == project_id, MentionSuppression.kind == "hash", MentionSuppression.value == h)):
            db.add(MentionSuppression(org_id=org_id, project_id=project_id, kind="hash", value=h, created_by=by))
    if author.strip():
        a = author.strip() if re.fullmatch(r"[0-9a-f]{16}", author.strip()) else author_hash(author)
        removed += db.execute(delete(Mention).where(Mention.project_id == project_id, Mention.author_hash == a)).rowcount or 0
        if not db.scalar(select(MentionSuppression.id).where(MentionSuppression.project_id == project_id, MentionSuppression.kind == "author", MentionSuppression.value == a)):
            db.add(MentionSuppression(org_id=org_id, project_id=project_id, kind="author", value=a, created_by=by))
    return removed
