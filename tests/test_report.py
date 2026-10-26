"""The research report: one Word file with summary, methods and sample, findings (quotes word for word), public opinion, survey results and limits."""
import io
import re
import zipfile

from app import docx_writer
from app.db import SessionLocal
from app.models import Insight
from test_export import _with_insight
from test_project_members import second_researcher


def blocks_of(org, pid):
    r = org.c.get(f"/api/projects/{pid}/report?format=json", headers=org.h())
    assert r.status_code == 200, r.text
    return [tuple(b) for b in r.json()["blocks"]]


def texts(blocks):
    out = []
    for b in blocks:
        out += [str(x) for x in b[1:] if isinstance(x, str)] + [c for x in b[1:] if isinstance(x, list) for row in ([x] if x and isinstance(x[0], str) else x) for c in (row if isinstance(row, list) else [row])]
    return "\n".join(out)


def approved(org):
    itv, ins = _with_insight(org)
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    return itv, ins, org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]


def test_an_empty_project_still_gets_an_honest_report(org):
    pid = org.project()
    b = blocks_of(org, pid)
    t = texts(b)
    assert b[0] == ("title", "P1") and "还没有可汇总的数据" in t and "还没有发现" in t and "证据的局限" in t


def test_interview_findings_carry_verbatim_quotes_with_codes_and_never_names(org):
    itv, ins, pid = approved(org)
    t = texts(blocks_of(org, pid))
    assert "clear halal certification" in t and "M-001" in t and "Wei Ling" not in t
    quote = next(b[1] for b in blocks_of(org, pid) if b[0] == "quote")
    assert quote.startswith("“") and re.search(r"\d\d:\d\d$", quote)
    assert "访谈：共 1 场" in t


def test_the_report_has_every_section_when_every_method_has_data(org):
    itv, ins, pid = approved(org)
    org.c.put(f"/api/projects/{pid}/methods", headers=org.h(), json={"methods": ["depth", "async", "listening", "quant"]})
    sid = org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json={"kind": "mock", "name": "m", "config": {"brand": "Acme", "lang": "en"}}).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    key = org.c.post(f"/api/projects/{pid}/quant/keys", headers=org.h(), json={"name": "k"}).json()["key"]
    org.c.post("/api/ingest/surveys", headers={"Authorization": f"Bearer {key}"}, json={"survey_id": "S", "title": "满意度", "respondents": 200, "questions": [
        {"text": "总体满意吗", "type": "single", "results": [{"option": "满意", "count": 150}, {"option": "不满意", "count": 50}]}]})
    b = blocks_of(org, pid)
    heads = [x[1] for x in b if x[0] == "h1"]
    assert heads == ["摘要", "研究背景", "研究方法与样本", "核心发现", "舆情摘要", "问卷结果", "证据的局限"]
    t = texts(b)
    assert "舆情：采集公开内容 6 条" in t and "问卷：1 份，合计 200 份有效回答" in t and "满意" in t and "75%" in t and "负面" in t


def test_findings_confirmed_by_two_kinds_of_source_are_marked_and_come_first(org):
    itv, ins, pid = approved(org)
    with SessionLocal() as db:
        db.get(Insight, ins["id"]).theme = "价格敏感"
        db.commit()
    org.c.post(f"/api/projects/{pid}/pool/notes", headers=org.h(), json={"theme": "价格敏感", "text": "团队假设：折扣放大"})
    b = blocks_of(org, pid)
    first_h2 = next(x[1] for x in b if x[0] == "h2")
    assert "价格敏感" in first_h2 and "已印证" in first_h2
    assert "被多种来源印证的发现：价格敏感" in texts(b)


def test_the_word_file_is_a_valid_package_with_tables_and_the_text(org):
    itv, ins, pid = approved(org)
    r = org.c.get(f"/api/projects/{pid}/report", headers=org.h())
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.testzip() is None and {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= set(z.namelist())
        xml = z.read("word/document.xml").decode("utf-8")
    assert "<w:tbl>" in xml and "clear halal certification" in xml and "Wei Ling" not in xml and "研究报告" in xml


def test_the_writer_escapes_and_drops_control_characters():
    data = docx_writer.render([("title", "A & B <x>"), ("p", "bad\x00char\x0b"), ("table", ["h"], [["<td>"]]), ("pagebreak",), ("bullet", "b")])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "A &amp; B &lt;x&gt;" in xml and "badchar" in xml and "\x00" not in xml and "&lt;td&gt;" in xml and 'w:br w:type="page"' in xml


def test_formats_and_permissions(org, client):
    pid = org.project()
    assert org.c.get(f"/api/projects/{pid}/report?format=pdf", headers=org.h()).status_code == 422
    other = second_researcher(org)
    assert org.c.get(f"/api/projects/{pid}/report", headers=other["h"]).status_code == 404
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/projects/{pid}/report", headers=org.h(role)).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/projects/{pid}/report", headers=foreign.h("admin")).status_code == 404
    log = org.c.get("/api/audit?limit=20", headers=org.h("admin")).json()
    assert org.c.get(f"/api/projects/{pid}/report", headers=org.h("admin")).status_code == 200 and isinstance(log, list)
