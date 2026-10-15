"""Insight export (Word + CSV): staff only, tenant-isolated, participants as codes, quotes verbatim, spreadsheet formulas neutralised."""
import io
import zipfile

from app import export
from conftest import Org
from test_week1 import transcribed


def _with_insight(org):
    itv, segs = transcribed(org)
    seg = [s for s in segs if s["speaker"] == "participant"][1]
    r = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json={"theme": "信任 & 认证", "statement": "怕假货", "evidence": [{"segment_id": seg["id"], "quote": "clear halal certification"}]})
    assert r.status_code == 201
    return itv, r.json()


def _pid(org, itv):
    return org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]


def test_csv_and_docx_export(org):
    itv, ins = _with_insight(org)
    pid = _pid(org, itv)
    c = org.c.get(f"/api/projects/{pid}/insights/export?format=csv", headers=org.h())
    assert c.status_code == 200 and "attachment" in c.headers["content-disposition"]
    text = c.content.decode("utf-8-sig")
    assert "clear halal certification" in text and "怕假货" in text and "P-001" in text and "Wei Ling" not in text
    d = org.c.get(f"/api/projects/{pid}/insights/export?format=docx", headers=org.h())
    assert d.status_code == 200
    with zipfile.ZipFile(io.BytesIO(d.content)) as z:
        assert z.testzip() is None
        xml = z.read("word/document.xml").decode("utf-8")
    assert "信任 &amp; 认证" in xml and "clear halal certification" in xml and "Wei Ling" not in xml


def test_export_approved_only_and_bad_format(org):
    itv, ins = _with_insight(org)
    pid = _pid(org, itv)
    assert "怕假货" not in org.c.get(f"/api/projects/{pid}/insights/export?format=csv&approved_only=true", headers=org.h()).content.decode("utf-8-sig")
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    assert "怕假货" in org.c.get(f"/api/projects/{pid}/insights/export?format=csv&approved_only=true", headers=org.h()).content.decode("utf-8-sig")
    assert org.c.get(f"/api/projects/{pid}/insights/export?format=pdf", headers=org.h()).status_code == 422


def test_export_permissions_and_isolation(org, client):
    itv, _ = _with_insight(org)
    pid = _pid(org, itv)
    assert client.get(f"/api/projects/{pid}/insights/export", headers=org.h("observer")).status_code == 403  # clients do not download
    assert client.get(f"/api/projects/{pid}/insights/export").status_code in (401, 403)
    other = Org(client, "B", "b@b.com")
    assert client.get(f"/api/projects/{pid}/insights/export", headers=other.h()).status_code == 404


def test_csv_neutralises_formulas():
    row = {"pcode": "P-001", "interview": "=cmd", "theme": "t", "statement": "+SUM(A1)", "status": "draft", "kind": "ai",
           "evidence": [{"quote": "@x", "speaker": "participant", "start_ms": 1500}]}
    out = export.to_csv([row]).decode("utf-8-sig")
    assert "'=cmd" in out and "'+SUM(A1)" in out and "'@x" in out
