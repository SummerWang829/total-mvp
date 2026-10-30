"""Permanent single/bulk project deletion: safety, permissions, tenancy and cascading data."""
from sqlalchemy import func, select

from app.db import Base, SessionLocal
from app.models import AuditLog, Interview, Participant, Project, ProjectMember
from conftest import Org


def _delete(org, ids, confirm="永久删除", role="admin"):
    return org.c.post("/api/projects/delete", headers=org.h(role), json={"ids": ids, "confirm": confirm})


def test_only_admin_can_delete_and_confirmation_is_required(org):
    pid = org.project()
    assert _delete(org, [pid], role="researcher").status_code == 403
    assert _delete(org, [pid], confirm="删除").status_code == 422
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).status_code == 200


def test_a_foreign_or_duplicate_id_cancels_the_whole_batch(org, client):
    own = org.project()
    foreign = Org(client, "Other", "other@other.test").project()
    assert _delete(org, [own, foreign]).status_code == 404
    assert _delete(org, [own, own]).status_code == 422
    assert org.c.get(f"/api/projects/{own}", headers=org.h()).status_code == 200


def test_a_live_interview_cancels_the_whole_batch(org):
    live_project = org.project(name="直播项目")
    interview = org.interview(pid=live_project)
    other = org.project(name="另一个项目")
    assert org.c.post(f"/api/interviews/{interview['id']}/start", headers=org.h("moderator")).status_code == 200
    assert _delete(org, [live_project, other]).status_code == 409
    assert all(org.c.get(f"/api/projects/{pid}", headers=org.h()).status_code == 200 for pid in (live_project, other))


def test_bulk_delete_removes_connected_rows_but_keeps_audit(org):
    first = org.project(name="删除甲")
    second = org.project(name="删除乙")
    keep = org.project(name="保留")
    interview = org.interview(pid=first)
    org.participant(second)

    result = _delete(org, [first, second])
    assert result.status_code == 200, result.text
    assert result.json()["projects"] == 2 and result.json()["rows"] >= 7
    with SessionLocal() as db:
        assert db.get(Project, first) is None and db.get(Project, second) is None
        assert db.get(Project, keep) is not None
        assert db.scalar(select(func.count()).select_from(Interview).where(Interview.id == interview["id"])) == 0
        assert db.scalar(select(func.count()).select_from(Participant).where(Participant.project_id.in_((first, second)))) == 0
        assert db.scalar(select(func.count()).select_from(ProjectMember).where(ProjectMember.project_id.in_((first, second)))) == 0
        for table in Base.metadata.tables.values():
            if table.name != "projects" and "project_id" in table.c:
                count = db.scalar(select(func.count()).select_from(table).where(table.c.project_id.in_((first, second))))
                assert count == 0, table.name
        logs = list(db.scalars(select(AuditLog).where(AuditLog.action == "project.delete")))
        assert {row.target for row in logs} == {first, second}
