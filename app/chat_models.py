"""A person's conversations with the research assistant on the 洞察与片段 page (one row per conversation, private to the person who had it).

Only the questions, the assistant's analysis and suggestions, and the *addresses* of the passages it relied on are kept. The quotes themselves are looked up again
each time the conversation is opened, so a transcript that has been purged under the retention rules disappears from the old conversations too.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import uid, utcnow


class ChatThread(Base):
    __tablename__ = "chat_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(80), default="")
    messages: Mapped[str] = mapped_column(Text, default="")  # JSON list: {"role": "user", "text"} | {"role": "ai", "answer", "suggestions", "followups", "refs": [{"n", "ref"}]}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
