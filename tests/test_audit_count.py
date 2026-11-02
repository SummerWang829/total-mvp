"""The audit page's page numbers: how many rows match the same filter as the list."""
from conftest import Org


def test_the_count_matches_the_list_and_the_filter_and_is_admin_only(org: Org):
    org.project()
    rows = org.c.get("/api/audit?limit=500", headers=org.h("admin")).json()
    total = org.c.get("/api/audit/count", headers=org.h("admin")).json()["total"]
    assert total == len(rows) and total >= 1
    assert org.c.get("/api/audit/count?action=zzz.nothing", headers=org.h("admin")).json()["total"] == 0
    first = rows[0]["action"].split(".")[0]
    narrowed = org.c.get(f"/api/audit/count?action={first}", headers=org.h("admin")).json()["total"]
    assert narrowed == len(org.c.get(f"/api/audit?limit=500&action={first}", headers=org.h("admin")).json())
    assert org.c.get("/api/audit/count", headers=org.h("researcher")).status_code == 403
