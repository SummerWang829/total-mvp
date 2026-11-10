"""Fill the 「· 主题」 columns of the 洞察 › 回复 table.

Each outline question has two columns: the participant's own words, and one to three short theme
labels. The answers themselves are NEVER rewritten here: a project may no longer keep its
transcript segments, and re-running answer extraction would wipe the stored answers. This module
only adds the labels, in place, from the model, and only where an answer exists and labels are
missing.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Interview
from .moderator.compliance import moderator_runs_locally
from .moderator.providers import ChatError, ChatFatalError, get_moderator_chat

log = logging.getLogger(__name__)

MAX_ANSWERS = 40       # one model call can label this many answers
MAX_THEMES = 3
THEME_CHARS = 12
TIMEOUT_S = 25

SYSTEM = """你是市场研究的编码员。你会收到一组带编号的受访者原话。请为每一条归纳 1 到 3 个简短的中文主题标签（每个不超过 8 个汉字，用名词或短语，例如「家庭出行」「购物消费」「价格敏感」；同一个意思必须始终用同一个标签）。不要引用原话，不要解释，不要输出姓名或个人标识。只输出一个 JSON 对象：{"themes": [["标签", ...], ...]}，数组与输入编号一一对应。"""


def chat_or_none():
    """The model that writes the labels. None = offline demo; a cloud vendor must be named."""
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise RuntimeError("云端模型还没有在 .env 里写明服务商，没有发送任何内容")
    return get_moderator_chat()


def _label(chat, items: list[dict], pending: list[int]) -> int:
    """Label the answers at `pending` positions, in place. Returns how many got labels."""
    idx = pending[:MAX_ANSWERS]
    numbered = "\n".join(f"{n + 1}. {items[i].get('a') or ''}"[:420] for n, i in enumerate(idx))
    reply = chat.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": numbered}], temperature=0.2)
    lists = reply.get("themes") if isinstance(reply, dict) else None
    if not isinstance(lists, list):
        raise ChatError("themes reply has no list")
    added = 0
    for k, i in enumerate(idx):
        got = lists[k] if k < len(lists) and isinstance(lists[k], list) else []
        tags = [str(x).strip()[:THEME_CHARS * 2] for x in got if str(x).strip()][:MAX_THEMES]
        if tags:
            items[i]["themes"] = tags
            added += 1
    return added


def backfill(db: Session, project_id: str, chat) -> tuple[int, int]:
    """(interviews updated, answers labelled). Scoped by project; answers without labels only."""
    if chat is None:
        return 0, 0
    rows = db.scalars(select(Interview).where(
        Interview.project_id == project_id,
        Interview.purged_at.is_(None),
        Interview.answers_json.is_not(None))).all()
    updated = labels = 0
    for itv in rows:
        try:
            data = json.loads(itv.answers_json) if itv.answers_json else None
        except (TypeError, ValueError):
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            continue
        pending = [i for i, x in enumerate(items)
                   if isinstance(x, dict) and (x.get("a") or "").strip() and not x.get("themes")]
        if not pending:
            continue
        pool = None
        try:
            pool = ThreadPoolExecutor(max_workers=1)
            labels += pool.submit(_label, chat, items, pending).result(timeout=TIMEOUT_S)
            itv.answers_json = json.dumps(data, ensure_ascii=False)
            db.add(itv)
            updated += 1
        except ChatFatalError as e:
            log.error("theme backfill: model refused: %s", e)
            raise
        except Exception as e:  # noqa: BLE001 - one bad interview must not stop the rest
            log.warning("theme backfill failed for interview %s: %s: %s", itv.id, type(e).__name__, e)
        finally:
            if pool is not None:
                pool.shutdown(wait=False)
    if updated:
        db.commit()
    return updated, labels
