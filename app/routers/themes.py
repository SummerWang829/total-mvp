"""回填「洞察 › 回复」表里所有列名含「· 主题」的列。

Only the short labels are written; the participant's own words in the answer columns stay exactly
as they are. The project is resolved with get_project() (another company's project is a 404) and
every interview read is scoped to that project.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import theme_backfill
from ..db import get_db
from ..deps import Ctx, STAFF, require_roles
from ..moderator.providers import ChatFatalError
from .projects import get_project

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["themes"])


@router.post("/projects/{pid}/insights/themes/backfill")
def backfill(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """给有逐题回答、但主题为空的访谈补上中文主题标签；没有回答的题保持空态。"""
    proj = get_project(db, ctx, pid)
    try:
        chat = theme_backfill.chat_or_none()
    except RuntimeError as e:
        return {"updated": 0, "labels": 0, "reason": str(e)}
    if chat is None:
        return {"updated": 0, "labels": 0, "reason": "离线演示模式：模型未接入"}
    try:
        updated, labels = theme_backfill.backfill(db, proj.id, chat)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    except Exception as e:  # noqa: BLE001
        log.exception("theme backfill failed: %s", e)
        raise HTTPException(502, "主题生成暂时不可用，请稍后重试") from e
    log.info("theme backfill project=%s updated=%s labels=%s", proj.id, updated, labels)
    return {"updated": updated, "labels": labels, "reason": ""}
