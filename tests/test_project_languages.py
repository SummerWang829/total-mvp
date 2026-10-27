"""A project allows several languages: read and change them at any time (no new publish), keep the default among them, refuse to switch off a language open interviews use."""
from conftest import Org


def test_a_project_starts_with_its_one_language_and_can_add_more(org):
    pid = org.project()
    d = org.c.get(f"/api/projects/{pid}/languages", headers=org.h()).json()
    assert d["languages"] == ["en"] and d["default"] == "en" and d["can_edit"] is True  # the researcher who made it is its owner
    rows = {r["code"]: r for r in d["rows"]}
    assert rows["zh"]["consent_reviewed"] and rows["zh"]["ai"] and not rows["ms"]["consent_reviewed"] and not rows["ms"]["ai"]
    r = org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"languages": ["en", "zh", "ms"], "default": "zh"})
    assert r.status_code == 200 and r.json()["languages"][0] == "zh" and set(r.json()["languages"]) == {"en", "zh", "ms"}
    listed = next(x for x in org.c.get("/api/projects", headers=org.h()).json() if x["id"] == pid)
    assert listed["language"] == "zh" and listed["languages"][0] == "zh" and "en" in listed["languages"]


def test_refusals(org):
    pid = org.project()
    url = f"/api/projects/{pid}/languages"
    assert org.c.put(url, headers=org.h("admin"), json={"languages": ["en"], "default": "zh"}).status_code == 422  # the default must be allowed
    assert org.c.put(url, headers=org.h("admin"), json={"languages": ["xx"], "default": "xx"}).status_code == 422
    org.c.put(url, headers=org.h("admin"), json={"languages": ["en", "zh"], "default": "en"})
    org.interview(pid=pid, language="zh")  # an open interview in Chinese
    r = org.c.put(url, headers=org.h("admin"), json={"languages": ["en"], "default": "en"})
    assert r.status_code == 422 and "中文" in r.text and "还有场次在用" in r.text


def test_only_admin_manager_or_owner_may_change_and_other_companies_cannot_see(org):
    pid = org.project()
    url = f"/api/projects/{pid}/languages"
    assert org.c.put(url, headers=org.h("moderator"), json={"languages": ["en"], "default": "en"}).status_code in (401, 403, 404)
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.get(url, headers=other.h()).status_code == 404
