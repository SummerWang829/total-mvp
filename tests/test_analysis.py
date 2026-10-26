"""Coding and analysis v1: codebook, codes on insights, model suggestions that need acceptance, the overview, permissions and isolation."""
from conftest import Org
from test_week1 import transcribed


def _setup(org):
    itv, segs = transcribed(org)
    seg = [s for s in segs if s["speaker"] == "participant"][1]
    ins = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json={"theme": "信任", "statement": "怕假货", "evidence": [{"segment_id": seg["id"], "quote": "clear halal certification"}]}).json()
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    return itv, ins, pid


def _code(org, pid, name):
    r = org.c.post(f"/api/projects/{pid}/codes", headers=org.h(), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_codebook_and_manual_coding_feed_the_overview(org):
    itv, ins, pid = _setup(org)
    a, b = _code(org, pid, "信任"), _code(org, pid, "价格")
    assert _code(org, pid, "信任") == a  # same name = same code
    assert org.c.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [a, a]}).json() == {"code_ids": [a]}
    d = org.c.get(f"/api/projects/{pid}/analysis", headers=org.h()).json()
    by = {c["name"]: c for c in d["codes"]}
    assert by["信任"]["total"] == 1 and by["信任"]["by_interview"] == {itv["id"]: 1} and by["价格"]["total"] == 0
    assert d["interviews"][0]["pcode"] == "M-001" and "Wei Ling" not in str(d)
    assert d["insights"][0]["codes"] == [{"id": a, "suggested": False}]


def test_suggestions_do_not_count_until_accepted(org):
    itv, ins, pid = _setup(org)
    r = org.c.post(f"/api/projects/{pid}/codes/suggest", headers=org.h())
    assert r.status_code == 200 and r.json()["suggested"] >= 1
    d = org.c.get(f"/api/projects/{pid}/analysis", headers=org.h()).json()
    assert d["insights"][0]["codes"][0]["suggested"] is True and all(c["total"] == 0 for c in d["codes"])
    cid = d["insights"][0]["codes"][0]["id"]
    org.c.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [cid]})  # accept
    d = org.c.get(f"/api/projects/{pid}/analysis", headers=org.h()).json()
    assert d["insights"][0]["codes"] == [{"id": cid, "suggested": False}] and d["codes"][0]["total"] == 1
    assert org.c.post(f"/api/projects/{pid}/codes/suggest", headers=org.h()).json()["suggested"] == 0  # nothing left to suggest for


def test_model_failure_is_reported_not_hidden(org, monkeypatch):
    itv, ins, pid = _setup(org)

    class Down:
        def suggest_codes(self, *a):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.routers.analysis.get_llm", lambda: Down())
    assert org.c.post(f"/api/projects/{pid}/codes/suggest", headers=org.h()).status_code == 502


def test_delete_code_and_delete_insight_clean_up(org):
    itv, ins, pid = _setup(org)
    a = _code(org, pid, "信任")
    org.c.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [a]})
    assert org.c.delete(f"/api/codes/{a}", headers=org.h()).status_code == 204
    d = org.c.get(f"/api/projects/{pid}/analysis", headers=org.h()).json()
    assert d["codes"] == [] and d["insights"][0]["codes"] == []
    b = _code(org, pid, "价格")
    org.c.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [b]})
    assert org.c.delete(f"/api/insights/{ins['id']}", headers=org.h()).status_code == 200
    assert org.c.get(f"/api/projects/{pid}/analysis", headers=org.h()).json()["codes"][0]["total"] == 0


def test_permissions_and_isolation(org, client):
    itv, ins, pid = _setup(org)
    a = _code(org, pid, "信任")
    for role in ("observer",):
        assert client.get(f"/api/projects/{pid}/analysis", headers=org.h(role)).status_code == 403
        assert client.post(f"/api/projects/{pid}/codes", headers=org.h(role), json={"name": "x"}).status_code == 403
        assert client.put(f"/api/insights/{ins['id']}/codes", headers=org.h(role), json={"code_ids": []}).status_code == 403
    other = Org(client, "B", "b@b.com")
    assert client.get(f"/api/projects/{pid}/analysis", headers=other.h()).status_code == 404
    assert client.delete(f"/api/codes/{a}", headers=other.h()).status_code == 404
    assert client.put(f"/api/insights/{ins['id']}/codes", headers=other.h(), json={"code_ids": [a]}).status_code == 404
    pid2 = other.project()
    b = _code(other, pid2, "别家")
    assert client.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [b]}).status_code == 404  # another company's code
    pid_own2 = org.project()
    c2 = _code(org, pid_own2, "别的项目")
    assert client.put(f"/api/insights/{ins['id']}/codes", headers=org.h(), json={"code_ids": [c2]}).status_code == 422  # another project's code
