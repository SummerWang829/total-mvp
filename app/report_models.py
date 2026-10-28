"""The project's report template: which analysis blocks the report has, in what order, and the AI's short readings of them (one row per project).

Only choices and short readings are kept here: the numbers and the participants' words are worked out again from the data every time the report is opened,
so a purged interview disappears from the report too.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import uid, utcnow


class ReportTemplate(Base):
    __tablename__ = "report_templates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    focus: Mapped[str] = mapped_column(Text, default="")  # what the team wrote they most want to see (sent to the AI service when blocks are picked)
    picked: Mapped[list] = mapped_column(JSON, default=list)  # block ids picked from the focus text (and from blocks added by hand)
    order: Mapped[list] = mapped_column(JSON, default=list)  # block ids in the order the team set
    on: Mapped[list] = mapped_column(JSON, default=list)  # block ids the team switched on although the system would leave them off
    off: Mapped[list] = mapped_column(JSON, default=list)  # block ids the team switched off
    notes: Mapped[dict] = mapped_column(JSON, default=dict)  # block id -> {"t": the AI's reading, "fp": fingerprint of the numbers it was written for}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
