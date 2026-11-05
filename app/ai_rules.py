"""规范层 (AI 后台 → 规范): the company's rules for how AI may and may not behave in the product.

Two layers, exactly as the product spec describes:
- the built-in 基础规范 v1 (read-only, written in code) — every company gets it and cannot edit it;
- 我的补充规范 (per-company, versioned, editable) — the team writes, saves as drafts, puts a version live,
  can go back to an older one or back to "只有基础规范", and can import a markdown/text file as a new version.

External skills must not be dropped in verbatim: whoever imports a rule is asked to confirm they have read
the source, rewritten it in their own words and checked it against the red lines. Only the rule text and
version metadata are stored and audited — never the conversation, prompts' inputs or any key.
"""
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base
from .models import uid, utcnow

log = logging.getLogger(__name__)

MAX_RULE_CHARS = 20000
MAX_NOTE_CHARS = 80
MAX_IMPORT_CHARS = 100000  # 导入 .md/.txt 上限 100KB
LIVE, DRAFT, RETIRED = "live", "draft", "retired"
BUILT_IN_LABEL = "基础规范 v1"

# The built-in rules every company follows. Read-only.
BUILT_IN_RULES = """# 基础规范 v1（系统内置，只读）

## 一、数据与隐私
1. 所有查询必须带公司标识（org_id）；越权访问一律返回 404，不泄露资源是否存在。
2. 不得向任何受访者之外的人泄露邀请令牌、分享令牌、录制路径等敏感信息。
3. 受访者的个人信息（姓名、邮箱、联系方式、甄别答案）仅用于研究用途，访谈结束后按保留期规则处理。

## 二、AI 使用边界
1. AI 生成的任何内容（问卷、大纲、建议、报告）必须经过研究员确认后才可对外使用。
2. AI 不得代替研究员做最终决定；主持人模式、甄别判定等关键环节保留人工复核。
3. 模型调用必须有超时与失败重试（指数退避），且可被测试替身（mock）替换。

## 三、合规红线
1. 涉及未成年人、敏感个人信息、医疗/金融等特殊领域的访谈，须先取得明确同意并走合规闸口。
2. 合规闸口"阻断则上游无法外呼"，下游招募链路不可绕过合规闸口。
3. 不得生成或传播违反法律法规的内容；红线一经命中，立即停止并上报。

## 四、代码与质量
1. AI 新增代码：函数不超过 150 行、类不超过 500 行；禁止裸 except。
2. 界面或功能改动先出效果图，用户确认后才改代码；只测改到的部分。
3. 外部 Skill 或资料不得原样塞进产品，必须读过原文、改写、过红线后才可上线。
"""


@dataclass(frozen=True)
class RuleErrorMsg:
    pass


class RuleError(ValueError):
    """The rule cannot be saved or put live; the message is written for the admin."""


class AiRuleVersion(Base):
    __tablename__ = "ai_rule_version"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(MAX_NOTE_CHARS), default="")
    state: Mapped[str] = mapped_column(String(8), default=DRAFT)
    source: Mapped[str] = mapped_column(String(20), default="")  # hand | import（导入的文件名或来源说明）
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    live_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def check(text: str) -> str:
    """The rule text trimmed, or RuleError when it is empty or too long."""
    text = text.strip()
    if not text:
        raise RuleError("规范不能是空的")
    if len(text) > MAX_RULE_CHARS:
        raise RuleError(f"规范太长了（最多 {MAX_RULE_CHARS} 字）")
    return text


def versions(db: Session, org_id: str) -> list[AiRuleVersion]:
    return list(db.scalars(select(AiRuleVersion).where(AiRuleVersion.org_id == org_id).order_by(AiRuleVersion.version.desc())))


def live_version(db: Session, org_id: str) -> AiRuleVersion | None:
    return db.scalar(select(AiRuleVersion).where(AiRuleVersion.org_id == org_id, AiRuleVersion.state == LIVE))


def effective_rules(db: Session, org_id: str) -> str:
    """The rules in force now: the built-in rules, plus the live supplement if any."""
    live = live_version(db, org_id)
    if live is None:
        return BUILT_IN_RULES
    return BUILT_IN_RULES + "\n\n## 我的补充规范（生效中 v" + str(live.version) + "）\n" + live.text


def save_draft(db: Session, org_id: str, user_id: str, text: str, note: str = "", source: str = "") -> AiRuleVersion:
    """Save the text as the next version (a draft: nothing changes until it is put live)."""
    text = check(text)
    last = db.scalar(select(func.max(AiRuleVersion.version)).where(AiRuleVersion.org_id == org_id)) or 0
    row = AiRuleVersion(org_id=org_id, version=last + 1, text=text, note=note.strip()[:MAX_NOTE_CHARS], source=source.strip()[:20], created_by=user_id)
    db.add(row)
    db.flush()
    return row


def set_live(db: Session, org_id: str, version: int | None) -> str:
    """Put a saved version live (the one live before becomes retired), or version None = back to built-in only. Returns the new label."""
    rows = versions(db, org_id)
    target = next((r for r in rows if r.version == version), None) if version is not None else None
    if version is not None and target is None:
        raise RuleError("没有这个版本")
    db.execute(update(AiRuleVersion).where(AiRuleVersion.org_id == org_id, AiRuleVersion.state == LIVE).values(state=RETIRED))
    if target:
        target.state, target.live_at = LIVE, utcnow()
    db.flush()
    return f"v{target.version}" if target else BUILT_IN_LABEL


def validate_import(text: str) -> str:
    """Check an imported markdown/text rule before it becomes a draft version."""
    text = text.strip()
    if not text:
        raise RuleError("导入的内容是空的")
    if len(text) > MAX_IMPORT_CHARS:
        raise RuleError(f"导入的内容太长了（最多 {MAX_IMPORT_CHARS} 字，约 100KB）")
    return text
