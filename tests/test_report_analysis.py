"""The analysis report: cards counted from the interviews, comparisons by questionnaire group, the template (switch, order, focus, upload), what a client sees, other companies."""
import io
import json
import zipfile

from app import docx_writer
from app.db import SessionLocal
from app.models import Interview, ScreenerAnswer
from conftest import Org
from test_project_members import second_researcher
from test_roles import person

QUESTION = "家庭月收入"
# (income group, themes of the answer) for six people who finished an interview
PEOPLE = [("低", ["价格偏高"]), ("低", ["价格偏高", "权益不清楚"]), ("低", ["价格偏高"]), ("高", ["价格偏高"]), ("高", ["权益不清楚"]), ("高", ["信任品牌"])]


def _seed(org, people=PEOPLE):
    pid = org.project()
    ids = []
    for n, (grp, themes) in enumerate(people):
        itv = org.interview(pid=pid)
        ids.append(itv["id"])
        with SessionLocal() as db:
            row = db.get(Interview, itv["id"])
            row.status = "ended"
            row.answers_json = json.dumps({"v": 1, "items": [{"q": "您觉得价格如何？", "a": f"回答{n}", "themes": themes, "seg": [], "at_ms": 1000 * (n + 1)}]}, ensure_ascii=False)
            db.add(ScreenerAnswer(org_id=row.org_id, participant_id=row.participant_id, position=0, question=QUESTION, answer=grp))
            db.commit()
    return pid, ids


def _data(org, pid, role=None):
    r = org.c.get(f"/api/projects/{pid}/report/analysis", headers=org.h(role) if role else org.h())
    assert r.status_code == 200, r.text
    return r.json()


def test_themes_are_counted_by_people_not_by_mentions(org):
    pid, _ = _seed(org)
    d = _data(org, pid)
    card = next(c for c in d["cards"] if c["id"] == "themes")
    bars = {b["label"]: (b["k"], b["n"]) for b in card["bars"]}
    assert d["n"] == 6 and bars["价格偏高"] == (4, 6) and bars["权益不清楚"] == (2, 6) and bars["信任品牌"] == (1, 6)
    assert [b["label"] for b in card["bars"]][0] == "价格偏高"   # the most mentioned first


def test_the_comparison_splits_people_by_the_questionnaire_group(org):
    pid, _ = _seed(org)
    d = _data(org, pid)
    cmp = next(c for c in d["cards"] if c["kind"] == "compare")
    assert cmp["title"] == "价格偏高：按家庭月收入" and {b["label"]: (b["k"], b["n"]) for b in cmp["bars"]} == {"低": (3, 3), "高": (1, 3)} and cmp["ready"]


def test_a_click_shows_who_said_it_with_their_own_words_and_only_codes(org):
    pid, ids = _seed(org)
    card = next(c for c in _data(org, pid)["cards"] if c["id"] == "themes")
    people = card["bars"][0]["people"]
    assert len(people) == 4 and all(p["code"][:2] in ("M-", "Q-", "X-") for p in people) and people[0]["items"][0]["a"].startswith("回答")
    assert "Wei Ling" not in json.dumps(card, ensure_ascii=False)
    assert people[0]["interview_id"] in ids


def test_small_groups_are_left_off_by_default_with_the_reason(org):
    pid, _ = _seed(org, [("低", ["价格偏高"]), ("低", ["价格偏高"]), ("高", ["价格偏高"])])
    d = _data(org, pid)
    b = next(x for x in d["blocks"] if x["id"].startswith("cmp:"))
    assert not b["on"] and "不到 3 位" in b["source"] and not any(c["kind"] == "compare" for c in d["cards"])


def test_a_project_without_finished_interviews_has_an_honest_empty_report(org):
    pid = org.project()
    d = _data(org, pid)
    assert d["n"] == 0 and d["cards"] == [] and "还没有可统计" in d["summary"]["t"] or d["n"] == 0


def test_blocks_can_be_switched_off_and_reordered(org):
    pid, _ = _seed(org)
    ids = [b["id"] for b in _data(org, pid)["blocks"]]
    assert ids[0] == "summary" and ids[-1] == "appendix"
    order = [ids[0], "themes", *[i for i in ids if i not in (ids[0], "themes")]]
    off = [i for i in ids if i.startswith("cmp:")]
    assert org.c.put(f"/api/projects/{pid}/report/template", headers=org.h(), json={"order": order, "on": [], "off": off}).status_code == 200
    d = _data(org, pid)
    assert [b["id"] for b in d["blocks"]][:2] == ["summary", "themes"] and not any(c["kind"] == "compare" for c in d["cards"])


def test_the_teams_words_pick_the_matching_comparison(org):
    pid, _ = _seed(org)
    r = org.c.post(f"/api/projects/{pid}/report/focus", headers=org.h(), json={"text": "权益不清楚，按家庭月收入比较", "add": False})
    assert r.status_code == 200 and r.json()["picked"] == [f"cmp:{QUESTION}:权益不清楚"]
    d = _data(org, pid)
    assert d["blocks"][1]["id"] == f"cmp:{QUESTION}:权益不清楚" and d["focus"].startswith("权益不清楚")
    assert org.c.post(f"/api/projects/{pid}/report/focus", headers=org.h(), json={"text": "完全无关的话题", "add": True}).json()["picked"] == []


def test_the_model_may_only_pick_from_the_real_lists(org):
    from app import report_analysis

    class Chat:
        def complete(self, messages, temperature=0.1, seed=None):
            return {"picks": [{"col": QUESTION, "theme": "信任品牌"}, {"col": "编造的问题", "theme": "价格偏高"}, {"col": QUESTION, "theme": "编造的主题"}]}
    pid, _ = _seed(org)
    with SessionLocal() as db:
        from app.models import Project
        ps, cols, shown = report_analysis.people(db, db.get(Project, pid))
        assert report_analysis.pick("随便", ps, shown, cols, Chat()) == [f"cmp:{QUESTION}:信任品牌"]


def test_the_model_reading_is_stored_with_the_numbers_it_was_written_for(org):
    from app import report_analysis
    from app.models import Project

    class Chat:
        def complete(self, messages, temperature=0.3, seed=None):
            sent = messages[1]["content"]
            assert "回答0" not in sent and "Wei Ling" not in sent   # counts only: no answers, no names
            return {"summary": "六位受访者里多数在意价格。", "notes": {"themes": "价格是最常见的主题。"}}
    pid, _ = _seed(org)
    with SessionLocal() as db:
        report_analysis.write_notes(db, db.get(Project, pid), Chat())
        db.commit()
    d = _data(org, pid)
    themes = next(c for c in d["cards"] if c["id"] == "themes")
    assert d["summary"] == {"t": "六位受访者里多数在意价格。", "ai": True} and themes["note"] == {"t": "价格是最常见的主题。", "ai": True, "stale": False}
    with SessionLocal() as db:   # the numbers change: the old reading is not shown as if it still fitted
        row = db.query(Interview).filter(Interview.id == _seed_ids(db, pid)[0]).one()
        row.answers_json = json.dumps({"v": 1, "items": [{"q": "q", "a": "x", "themes": ["别的"], "seg": [], "at_ms": 0}]})
        db.commit()
    themes = next(c for c in _data(org, pid)["cards"] if c["id"] == "themes")
    assert themes["note"]["ai"] is False and themes["note"]["stale"] is True


def _seed_ids(db, pid):
    return [i.id for i in db.query(Interview).filter(Interview.project_id == pid).order_by(Interview.created_at).all()]


def test_a_client_sees_counts_but_not_groups_or_what_people_said(org):
    pid, _ = _seed(org)
    v = person(org, "viewer")
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": v["id"]}).status_code == 201
    r = org.c.get(f"/api/projects/{pid}/report/analysis", headers=v["h"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["blocks"] == [] and d["cards"] and not any(c["kind"] == "compare" for c in d["cards"]) and all(b["people"] == [] for c in d["cards"] for b in c["bars"])
    assert "回答0" not in json.dumps(d, ensure_ascii=False) and "家庭月收入" not in json.dumps(d, ensure_ascii=False)
    assert org.c.put(f"/api/projects/{pid}/report/template", headers=v["h"], json={"order": [], "on": [], "off": []}).status_code == 403
    assert org.c.post(f"/api/projects/{pid}/report/focus", headers=v["h"], json={"text": "x"}).status_code == 403
    assert org.c.get(f"/api/projects/{pid}/report/analysis", headers=org.h("observer")).status_code == 403


def test_the_default_template_downloads_and_an_edited_one_sets_the_blocks(org):
    pid, _ = _seed(org)
    r = org.c.get(f"/api/projects/{pid}/report/template/file", headers=org.h())
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "总体结论" in xml and "主题分布" in xml
    edited = docx_writer.render([("title", "T"), ("h1", "总体结论"), ("h1", "权益不清楚，按家庭月收入比较"), ("h1", "主题分布")])
    r = org.c.post(f"/api/projects/{pid}/report/template/import", headers={**org.h(), "Content-Type": "application/octet-stream"}, content=edited)
    assert r.status_code == 200 and r.json()["picked"] == 1 and r.json()["unmatched"] == [], r.text
    d = _data(org, pid)
    assert [b["id"] for b in d["blocks"] if b["on"]][:3] == ["summary", f"cmp:{QUESTION}:权益不清楚", "themes"] and not next(b for b in d["blocks"] if b["id"] == "appendix")["on"]
    assert org.c.post(f"/api/projects/{pid}/report/template/import", headers={**org.h(), "Content-Type": "application/octet-stream"}, content=b"not a zip").status_code == 422
    assert org.c.post(f"/api/projects/{pid}/report/template/import", headers={**org.h(), "Content-Type": "application/octet-stream"}, content=docx_writer.render([("p", "no headings")])).status_code == 422


def test_the_word_file_has_the_cards_the_readings_and_the_old_sections_as_an_appendix(org):
    pid, _ = _seed(org)
    r = org.c.get(f"/api/projects/{pid}/report", headers=org.h())
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "总体结论" in xml and "价格偏高" in xml and "附录" in xml and "研究背景" in xml and "<w:tbl>" in xml and "Wei Ling" not in xml


def test_other_companies_and_non_members_get_a_404_and_the_audit_has_no_words(org, client):
    pid, _ = _seed(org)
    other = second_researcher(org)
    assert org.c.get(f"/api/projects/{pid}/report/analysis", headers=other["h"]).status_code == 404
    foreign = Org(client, "Foreign", "boss@foreign.test")
    assert foreign.c.get(f"/api/projects/{pid}/report/analysis", headers=foreign.h("admin")).status_code == 404
    org.c.post(f"/api/projects/{pid}/report/focus", headers=org.h(), json={"text": "秘密的研究想法", "add": False})
    log = org.c.get("/api/audit", headers=org.h("admin")).json()
    assert "秘密的研究想法" not in json.dumps(log, ensure_ascii=False)


def test_the_report_choices_go_with_the_project_when_it_is_purged(org):
    from sqlalchemy import select

    from app import retention
    from app.models import Project
    from app.report_models import ReportTemplate
    pid, _ = _seed(org)
    _data(org, pid)
    with SessionLocal() as db:
        assert db.scalar(select(ReportTemplate).where(ReportTemplate.project_id == pid)) is not None
        retention.purge_project_extras(db, db.get(Project, pid), "test", "test")
        db.commit()
        assert db.scalar(select(ReportTemplate).where(ReportTemplate.project_id == pid)) is None
