"""Permanent project deletion, including every row connected by declared foreign keys."""
import logging
from collections.abc import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.schema import Table

from .db import Base
from .models import EmailOutbox, Interview, Job, Project, Recording, Reel, ReelShare
from .media import purge_cache
from .retention import _delete_file

log = logging.getLogger(__name__)


def _project_condition(table: Table, project_ids: tuple[str, ...], visiting: frozenset[str] = frozenset()) -> ColumnElement[bool] | None:
    """Return the condition selecting rows connected to the projects through foreign keys."""
    if table.name in visiting:
        return None
    if table.name == Project.__tablename__:
        return table.c.id.in_(project_ids)
    conditions: list[ColumnElement[bool]] = []
    seen: set[tuple[str, str]] = set()
    for fk in table.foreign_keys:
        parent = fk.column.table
        key = (fk.parent.name, parent.name)
        if key in seen:
            continue
        seen.add(key)
        parent_condition = _project_condition(parent, project_ids, visiting | {table.name})
        if parent_condition is not None:
            conditions.append(fk.parent.in_(select(fk.column).where(parent_condition)))
    return or_(*conditions) if conditions else None


def delete_projects(db: Session, projects: Iterable[Project]) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """Delete projects and all connected rows in the caller's transaction.

    Raises:
        ValueError: If no projects were supplied.
    """
    rows = list(projects)
    if not rows:
        raise ValueError("至少选择一个项目")
    ids = tuple(p.id for p in rows)
    interview_ids = tuple(db.scalars(select(Interview.id).where(Interview.project_id.in_(ids))))
    reel_ids = tuple(db.scalars(select(Reel.id).where(Reel.project_id.in_(ids))))
    recordings = list(db.execute(select(Recording.id, Recording.storage_key).where(Recording.interview_id.in_(interview_ids)))) if interview_ids else []

    # These legacy references intentionally have no foreign key, so remove them explicitly.
    if interview_ids:
        db.execute(delete(EmailOutbox).where(EmailOutbox.ref_id.in_(interview_ids)))
        db.execute(delete(Job).where(Job.interview_id.in_(interview_ids)))
    if reel_ids:
        db.execute(delete(ReelShare).where(ReelShare.reel_id.in_(reel_ids)))

    deleted = 0
    for table in reversed(Base.metadata.sorted_tables):
        condition = _project_condition(table, ids)
        if condition is not None:
            deleted += db.execute(delete(table).where(condition)).rowcount or 0

    db.flush()
    return {"projects": len(ids), "rows": deleted, "recordings": len(recordings)}, recordings


def cleanup_media(recordings: Iterable[tuple[str, str]]) -> None:
    """Remove media after the database transaction has committed."""
    for recording_id, storage_key in recordings:
        try:
            _delete_file(storage_key)
            purge_cache(recording_id)
        except OSError:
            log.exception("project delete: media cleanup failed for recording %s", recording_id)
