"""Data retention for admins (hold, delete now), the personal data outside interviews that retention now also removes, and the audit trail's filters and export."""
from datetime import timedelta

from app import retention
from app.db import SessionLocal
from app.models import Mention, Participant, Project, RepoDocument, ScreenerAnswer, utcnow
from test_export import _with_insight


def closed_project(org, days_ago=0):
    itv, ins = _with_insight(org)
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    r = org.c.post(f"/api/projects/{pid}/close", headers=org.h())
    assert r.status_code in (200, 409), r.text
    with SessionLocal() as db:
        p = db.get(Project, pid)
        p.closed_at = utcnow() - timedelta(days=days_ago)
        db.commit()
    return itv, pid


def add_extras(org, pid):
    org.c.post(f"/api/projects/{pid}/participants/import", headers=org.h(), json={"csv": "name,email,年龄\n没访谈的人,n@example.com,30\n"})
    sid = org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json={"kind": "mock", "name": "m", "config": {"brand": "A", "lang": "en"}}).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    org.c.post("/api/repo/documents", headers=org.h(), json={"title": "项目文档", "text": "只属于这个项目的笔记", "project_id": pid})
    org.c.post("/api/repo/documents", headers=org.h(), json={"title": "公司文档", "text": "全公司的方法论"})


def counts():
    with SessionLocal() as db:
        return {"people": db.query(Participant).filter(Participant.anonymized_at.is_(None), Participant.name == "没访谈的人").count(), "mentions": db.query(Mention).count(),
                "docs": db.query(RepoDocument).count(), "answers": db.query(ScreenerAnswer).count()}


# ---------- retention reaches everything personal ----------
def test_when_the_clock_runs_out_people_nobody_interviewed_collected_posts_and_project_documents_go_too(org):
    itv, pid = closed_project(org, days_ago=181)
    add_extras(org, pid)
    before = counts()
    assert before["people"] == 1 and before["mentions"] == 6 and before["docs"] == 2
    with SessionLocal() as db:
        retention.run_retention(db)
    after = counts()
    assert after["people"] == 0 and after["mentions"] == 0 and after["docs"] == 1          # the company-wide document stays
    with SessionLocal() as db:
        assert db.query(Participant).filter(Participant.project_id == pid, Participant.email == "n@example.com").count() == 0     # the e-mail address is gone
    log = org.c.get("/api/audit?action=project.purge_extras", headers=org.h("admin")).json()
    assert log and log[0]["meta"]["mentions"] == 6


def test_an_open_or_recently_closed_project_keeps_everything(org):
    itv, pid = closed_project(org, days_ago=10)
    add_extras(org, pid)
    with SessionLocal() as db:
        retention.run_retention(db)
    assert counts()["mentions"] == 6 and counts()["docs"] == 2


# ---------- the admin's page ----------
def test_the_overview_shows_dates_and_what_each_project_still_holds(org):
    itv, pid = closed_project(org, days_ago=100)
    add_extras(org, pid)
    other = org.project()
    got = org.c.get("/api/admin/data", headers=org.h("admin")).json()
    assert got["retention_days"] == 180
    row = next(r for r in got["projects"] if r["id"] == pid)
    assert 78 <= row["days_left"] <= 80 and row["holds"]["mentions"] == 6 and row["holds"]["documents"] == 1 and row["holds"]["participants"] >= 2 and row["holds"]["interviews"] == 1
    assert next(r for r in got["projects"] if r["id"] == other)["purge_at"] is None
    assert got["projects"][0]["id"] == pid     # the one closest to deletion first


def test_a_hold_pushes_the_deletion_back_and_the_clock_respects_it(org):
    itv, pid = closed_project(org, days_ago=181)
    add_extras(org, pid)
    r = org.c.post(f"/api/admin/data/{pid}/hold", headers=org.h("admin"), json={"days": 30})
    assert r.status_code == 200 and 29 <= r.json()["days_left"] <= 30 and r.json()["hold_until"]
    with SessionLocal() as db:
        assert retention.run_retention(db) == 0       # held: nothing deleted
    assert counts()["mentions"] == 6
    org.c.delete(f"/api/admin/data/{pid}/hold", headers=org.h("admin"))
    with SessionLocal() as db:
        assert retention.run_retention(db) == 1
    assert counts()["mentions"] == 0
    assert org.c.post(f"/api/admin/data/{org.project()}/hold", headers=org.h("admin"), json={"days": 5}).status_code == 409   # not closed: no date to move
    for bad in (0, 731):
        assert org.c.post(f"/api/admin/data/{pid}/hold", headers=org.h("admin"), json={"days": bad}).status_code == 422


def test_delete_now_needs_a_closed_project_and_its_exact_name(org):
    itv, pid = closed_project(org)
    add_extras(org, pid)
    h = org.h("admin")
    assert org.c.post(f"/api/admin/data/{pid}/purge", headers=h, json={"confirm": "wrong"}).status_code == 422
    assert counts()["mentions"] == 6
    openp = org.project()
    assert org.c.post(f"/api/admin/data/{openp}/purge", headers=h, json={"confirm": "P1"}).status_code == 409
    r = org.c.post(f"/api/admin/data/{pid}/purge", headers=h, json={"confirm": "P1"})
    assert r.status_code == 200 and r.json()["purged_interviews"] == 1 and r.json()["mentions"] == 6 and r.json()["documents"] == 1
    assert counts()["people"] == 0 and counts()["mentions"] == 0
    assert any(e["action"] == "retention.purge_now" for e in org.c.get("/api/audit?action=retention", headers=h).json())
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json() == []


def test_only_admins_of_the_same_company_may_use_it(org, client):
    itv, pid = closed_project(org)
    for role in ("researcher", "moderator", "observer"):
        for method, url, body in (("get", "/api/admin/data", None), ("post", f"/api/admin/data/{pid}/hold", {"days": 5}), ("post", f"/api/admin/data/{pid}/purge", {"confirm": "P1"})):
            assert getattr(org.c, method)(url, headers=org.h(role), **({"json": body} if body else {})).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.post(f"/api/admin/data/{pid}/purge", headers=foreign.h("admin"), json={"confirm": "P1"}).status_code == 404
    assert all(r["id"] != pid for r in foreign.c.get("/api/admin/data", headers=foreign.h("admin")).json()["projects"])


# ---------- the audit trail ----------
def test_the_audit_trail_can_be_filtered_paged_and_exported(org):
    pid = org.project()
    org.interview(pid=pid)
    h = org.h("admin")
    everything = org.c.get("/api/audit?limit=500", headers=h).json()
    assert len(everything) >= 5
    only = org.c.get("/api/audit?action=interview.", headers=h).json()
    assert only and all(e["action"].startswith("interview.") for e in only) and len(only) < len(everything)
    assert org.c.get("/api/audit?action=zzz", headers=h).json() == []
    page1, page2 = org.c.get("/api/audit?limit=2", headers=h).json(), org.c.get("/api/audit?limit=2&offset=2", headers=h).json()
    assert len(page1) == 2 and page1 != page2 and everything[:4] == page1 + page2
    mine = org.c.get("/api/audit?actor=user:", headers=h).json()
    assert mine and all("user:" in e["actor"] for e in mine)
    today = utcnow().strftime("%Y-%m-%d")
    assert len(org.c.get(f"/api/audit?since={today}&until={today}&limit=500", headers=h).json()) >= len(everything) - 1
    assert org.c.get("/api/audit?until=2000-01-01", headers=h).json() == []
    assert org.c.get("/api/audit?since=yesterday", headers=h).status_code == 422
    assert org.c.get("/api/audit?action=%25", headers=h).json() == []          # a percent sign is text, not a wildcard


def test_the_export_is_a_csv_that_is_itself_audited_and_for_admins_only(org):
    org.project()
    h = org.h("admin")
    r = org.c.get("/api/audit/export?action=project.", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("时间(UTC),操作者,动作") and "project.create" in text
    assert any(e["action"] == "audit.export" for e in org.c.get("/api/audit?action=audit.", headers=h).json())
    for role in ("researcher", "moderator"):
        assert org.c.get("/api/audit/export", headers=org.h(role)).status_code == 403
        assert org.c.get("/api/audit", headers=org.h(role)).status_code == 403
