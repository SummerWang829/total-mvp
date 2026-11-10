"""Prompts of the AI features that an admin can edit in the AI console (AI 后台 → 提示词), with versions and a way back.

The prompt written in the code is every feature's built-in version and never changes here. A company can save its own versions of it and put one live; no live
version = the built-in one. Going back is the same as putting an older version (or the built-in one) live again. The AI moderator's four prompts are not here:
they have their own versioned files and need the red-line evaluation first (app/moderator/prompts).

Nothing in a prompt is ever logged or audited: only the feature, the version number and who did what.
"""
import importlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import ai_calls, assistant_identity
from .db import Base, SessionLocal
from .models import uid, utcnow

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 20000
MAX_NOTE_CHARS = 80
LIVE, DRAFT, RETIRED = "live", "draft", "retired"
BUILT_IN_LABEL = "内置"
PLACEHOLDERS = ("{language}", "{max_topics}", "{minutes}", "{outline_spec}", "{recruit_spec}")


@dataclass(frozen=True)
class Slot:
    module: str
    attr: str
    identity: bool = False  # the assistant-identity line (小星星助手) is added by the code after the text and cannot be edited away


SLOTS: dict[str, Slot] = {
    "outline.chat": Slot("app.outline_chat", "SYSTEM", True),
    "outline.translate": Slot("app.outline_translate", "SYSTEM"),
    "study.draft": Slot("app.study_draft", "SYSTEM"),
    "recruit.draft": Slot("app.recruit_form", "SYSTEM"),
    "recruit.chat": Slot("app.recruit_form", "CHAT_SYSTEM", True),
    "recruit.review": Slot("app.recruit_review", "REVIEW_SYSTEM"),
    "recruit.translate": Slot("app.recruit_translate", "SYSTEM"),
    "recruit.insights": Slot("app.recruit_insights", "SUMMARY_SYSTEM"),
    "plan.suggest": Slot("app.plan_suggest", "SYSTEM"),
    "insight.chat": Slot("app.insight_chat", "SYSTEM", True),
    "answers.tag": Slot("app.interview_answers", "SYSTEM"),
    "answers.match": Slot("app.interview_answers", "MATCH_SYSTEM"),
    "report.pick": Slot("app.report_analysis", "PICK_SYSTEM"),
    "report.note": Slot("app.report_analysis", "NOTE_SYSTEM"),
    "creation.chat": Slot("app.creation.specs", "SYSTEM", True),
    "creation.outline_spec": Slot("app.creation.specs", "OUTLINE_SPEC"),
    "creation.recruit_spec": Slot("app.creation.specs", "RECRUIT_SPEC"),
}


class PromptError(ValueError):
    """The prompt cannot be saved or put live; the message is written for the admin."""


class AiPromptVersion(Base):
    __tablename__ = "ai_prompt_version"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    slot: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(MAX_NOTE_CHARS), default="")
    state: Mapped[str] = mapped_column(String(8), default=DRAFT)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    live_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def built_in(key: str) -> str:
    """The prompt written in the code for this feature, without the identity line (that one is added by the code).

    Raises:
        PromptError: the feature has no editable prompt.
    """
    slot = SLOTS.get(key)
    if not slot:
        raise PromptError("这个功能没有可以修改的提示词")
    text = getattr(importlib.import_module(slot.module), slot.attr)
    return text.removesuffix(" " + assistant_identity.PROMPT_LINE) if slot.identity else text


def get(key: str, default: str) -> str:
    """The prompt to use now for this feature: the live version of the signed-in company, else `default` (the one in the code). Never raises."""
    org = ai_calls.current_org()
    slot = SLOTS.get(key)
    if not org or not slot:
        return default
    try:
        with SessionLocal() as db:
            live = db.scalar(select(AiPromptVersion.text).where(AiPromptVersion.org_id == org, AiPromptVersion.slot == key, AiPromptVersion.state == LIVE))
    except Exception:  # noqa: BLE001 - the feature must keep working with its built-in prompt
        log.warning("could not read the live prompt of %s", key, exc_info=True)
        return default
    if live is None:
        return default
    return live + (" " + assistant_identity.PROMPT_LINE if slot.identity else "")


def live_label(db: Session, org_id: str, key: str) -> str:
    """"v3" for a company's live version, "内置" for the prompt in the code."""
    v = db.scalar(select(AiPromptVersion.version).where(AiPromptVersion.org_id == org_id, AiPromptVersion.slot == key, AiPromptVersion.state == LIVE))
    return f"v{v}" if v else BUILT_IN_LABEL


def check(key: str, text: str) -> str:
    """The text trimmed, or PromptError when it would break the feature: empty, too long, or missing a variable / the JSON instruction the built-in prompt has."""
    text = text.strip()
    if not text:
        raise PromptError("提示词不能是空的")
    if len(text) > MAX_PROMPT_CHARS:
        raise PromptError(f"提示词太长了（最多 {MAX_PROMPT_CHARS} 字）")
    base = built_in(key)
    missing = [p for p in PLACEHOLDERS if p in base and p not in text]
    if missing:
        raise PromptError("缺少变量：" + "、".join(missing) + "（程序会把它们换成真实内容，不能删）")
    if "JSON" in base and "JSON" not in text:
        raise PromptError("这个功能要求模型只回一个 JSON 对象，提示词里要保留这条说明")
    return text


def versions(db: Session, org_id: str, key: str) -> list[AiPromptVersion]:
    return list(db.scalars(select(AiPromptVersion).where(AiPromptVersion.org_id == org_id, AiPromptVersion.slot == key).order_by(AiPromptVersion.version.desc())))


def save_draft(db: Session, org_id: str, user_id: str, key: str, text: str, note: str = "") -> AiPromptVersion:
    """Save the text as the next version (a draft: nothing changes for the feature until it is put live).

    Raises:
        PromptError: see check().
    """
    text = check(key, text)
    last = db.scalar(select(func.max(AiPromptVersion.version)).where(AiPromptVersion.org_id == org_id, AiPromptVersion.slot == key)) or 0
    row = AiPromptVersion(org_id=org_id, slot=key, version=last + 1, text=text, note=note.strip()[:MAX_NOTE_CHARS], created_by=user_id)
    db.add(row)
    db.flush()
    return row


def set_live(db: Session, org_id: str, key: str, version: int | None) -> str:
    """Put a saved version live (the one live before becomes retired), or version None = back to the built-in prompt. Returns the new label.

    Raises:
        PromptError: no such version.
    """
    rows = versions(db, org_id, key)
    target = next((r for r in rows if r.version == version), None) if version is not None else None
    if version is not None and target is None:
        raise PromptError("没有这个版本")
    db.execute(update(AiPromptVersion).where(AiPromptVersion.org_id == org_id, AiPromptVersion.slot == key, AiPromptVersion.state == LIVE).values(state=RETIRED))
    if target:
        target.state, target.live_at = LIVE, utcnow()
    db.flush()
    return f"v{target.version}" if target else BUILT_IN_LABEL
