"""Project membership for databases that existed before it did.

Until now every researcher could open every project of the company. When project membership arrives, nobody may lose the access they had that day:
projects that have no member at all get every active researcher of their company as a member (not an owner). Admins need no entry (they see all).
Runs at every start and does nothing once every project has members.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, ProjectMember, User

log = logging.getLogger(__name__)


def backfill(db: Session) -> int:
    """Give each project that has no member the active researchers of its company. Returns how many memberships were added."""
    have = set(db.scalars(select(ProjectMember.project_id).distinct()))
    added = 0
    for project in db.scalars(select(Project)):
        if project.id in have:
            continue
        for user in db.scalars(select(User).where(User.org_id == project.org_id, User.role == "researcher", User.active.is_(True))):
            db.add(ProjectMember(org_id=project.org_id, project_id=project.id, user_id=user.id, added_by="system"))
            added += 1
    if added:
        db.commit()
        log.info("project membership: %d memberships added to projects that had none", added)
    return added
