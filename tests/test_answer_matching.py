"""Finding which question a moderator turn asked: the outline in the interview's own language, the table's per-cell state and follow-ups, the question numbers."""
import json
from types import SimpleNamespace

from app import respondent_table
from app.db import SessionLocal
from app.models import Interview

EN = """目标: 了解消费者如何选购护肤品
受访者话题: 护肤品选购
语言: en
时长: 30
## 暖场
- How do you usually buy skincare products?
## 话题: 顾虑
- What makes you hesitate to try a new brand?
- How important is the packaging to you?
- Would you pay more for an organic label?
"""
ZH = """目标: 了解消费者如何选购护肤品
受访者话题: 护肤品选购
语言: zh
时长: 30
## 暖场
- 您平时怎么购买护肤品？
## 话题: 顾虑
- 是什么让您犹豫要不要试新品牌？
- 包装对您重要吗？
- 您愿意为有机标签多付钱吗？
"""


def _proj(**kw):
    return SimpleNamespace(outline_text=EN, outline_versions={"zh|live": ZH}, language="en", languages=["en", "zh"], formats={}, methods="depth", **kw)


def test_the_version_in_the_interviews_language_is_used_to_find_the_questions():
    proj = _proj()
    qs = respondent_table.interview_questions(proj)
    assert len(qs) == 4
    texts = respondent_table.match_texts(proj, SimpleNamespace(language="zh", format=""), qs)
    assert texts[0] == "您平时怎么购买护肤品？" and len(texts) == 4
    assert respondent_table.match_texts(proj, SimpleNamespace(language="ms", format=""), qs) == qs          # no such version: the main one
    short = _proj()
    short.outline_versions = {"zh|live": ZH.replace("- 包装对您重要吗？\n", "")}
    assert respondent_table.match_texts(short, SimpleNamespace(language="zh", format=""), qs) == qs        # does not line up question for question


def _finish(itv_id, items):
    with SessionLocal() as db:
        row = db.get(Interview, itv_id)
        row.status = "ended"
        row.answers_json = json.dumps({"v": 2, "items": items})
        db.commit()


def test_a_cell_says_why_it_is_empty_and_carries_the_follow_ups(org):
    itv = org.interview()
    pid = itv["project_id"]
    assert org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": EN}).status_code == 200
    qs = ["How do you usually buy skincare products?", "What makes you hesitate to try a new brand?", "How important is the packaging to you?", "Would you pay more for an organic label?"]
    fu = [{"ask": "Which ones?", "a": "TikTok.", "at_ms": 5000}]
    _finish(itv["id"], [{"q": qs[0], "a": "reviews TikTok.", "main": "reviews", "followups": fu, "asked": True, "seg": [], "at_ms": 1000},
                        {"q": qs[1], "a": "", "main": "", "followups": [], "asked": True, "seg": [], "at_ms": None},
                        {"q": qs[2], "a": "", "main": "", "followups": [], "asked": False, "seg": [], "at_ms": None}])
    d = org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()
    r = next(x for x in d["rows"] if x["id"] == itv["id"])
    assert r["qa"]["a0"] == {"state": "ok", "followups": fu} and r["qa"]["a1"]["state"] == "empty" and r["qa"]["a2"]["state"] == "unmatched" and r["qa"]["a3"]["state"] == "unmatched"
    assert r["cells"]["a0"] == "reviews"                                       # the cell shows the first answer, the follow-ups come with the row
    nos = {c["id"]: c.get("no") for c in d["columns"]}
    assert nos["a0"] == "Q1" and nos["t0"] == "Q1" and nos["a3"] == "Q4"
    assert [c["no"] for c in d["columns"] if c["group"] == "screen"] == [f"S{i + 1}" for i in range(len([c for c in d["columns"] if c["group"] == "screen"]))]


def test_a_row_without_worked_out_answers_has_no_qa_and_a_client_never_gets_it(org):
    itv = org.interview()
    pid = itv["project_id"]
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": EN})
    with SessionLocal() as db:
        db.get(Interview, itv["id"]).status = "ended"
        db.commit()
    row = next(x for x in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h()).json()["rows"] if x["id"] == itv["id"])
    assert row["qa"] is None
    assert "qa" not in org.c.get(f"/api/projects/{pid}/insights/overview", headers=org.h("observer")).json()["rows"][0]
