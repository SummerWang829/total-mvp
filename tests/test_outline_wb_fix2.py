"""修正方案第 2 轮（生成大纲）: goals without their own numbers, no question about what is already known, 价格怎么谈 only when it matters, whole module names in the
order of the questions, the dialog on 目标 / 人群与执行 changes 已经知道的, an accepted question lands in its module and survives 重新生成, and “改回立项的值”."""
from test_outline_wb_api import _api, _ready, proj  # noqa: F401
from test_outline_wb_sync import _two_subs, _wb
from test_plans import draft

from app.outline_wb import flow


def test_goals_are_split_and_lose_their_numbers():
    assert flow.clean_goals(["1. 对比品牌认知。 2. 识别痛点。 3. 比较期待"]) == ["对比品牌认知。", "识别痛点。", "比较期待"]
    assert flow.clean_goals(["1. 1. 对比本田", "", "② 观察城市差异"]) == ["1. 对比本田"[3:], "观察城市差异"]


def test_known_values_are_not_asked_again_and_price_only_when_it_matters(org, proj):
    v = _api(org, proj, "get", "").json()
    asked = {q["slot"] for r in v["round_questions"].values() for q in r}
    assert "materials" not in asked and "research_type" not in asked   # 已经知道的 shows them (with 改), so they are not asked below
    assert "price" not in v["slots"] or v["slots"]["research_type"]["value"] == "pricing"
    _api(org, proj, "post", "/answer", json={"slot": "research_type", "value": "usability"})
    v = _api(org, proj, "post", "/answer", json={"slot": "materials", "value": ["concept"]}).json()
    assert "price" not in v["slots"]   # 用户 2026-11-20 第 3 轮: something to show is no reason to bring price up by itself
    v = _api(org, proj, "post", "/answer", json={"slot": "price", "value": "after_concept"}).json()
    assert "price" in v["slots"]   # once the team says something about price, it shows


def test_modules_have_whole_names_in_the_order_of_the_questions(org, proj):
    v = _api(org, proj, "post", "/overview").json()
    core = [m for m in v["overview"]["modules"] if m["kind"] in ("behaviour", "attitude")]
    assert core and all(2 <= len(m["name"]) <= 10 and not m["name"][0].isdigit() for m in core)
    assert [m["serves"][0] for m in core] == sorted(m["serves"][0] for m in core)
    assert v["overview"]["modules"][0]["kind"] == "warm" and v["overview"]["modules"][-1]["kind"] == "close"


def test_the_dialog_on_the_first_pages_changes_the_known_values(org, proj):
    r = _api(org, proj, "post", "/chat", json={"text": "物料改为看素材，概念卡", "step": "people"}).json()
    assert r["changed"] == ["materials"] and not r["revisions"]
    assert _api(org, proj, "get", "").json()["slots"]["materials"]["value"] == ["concept"]


def test_an_accepted_question_lands_in_its_module_and_survives_a_rewrite(org, proj):
    _ready(org, proj)
    r = _api(org, proj, "post", "/chat", json={"text": "开头加一段主持人介绍", "step": "questions", "module": "warm"}).json()
    assert r["reply"]
    # the offline model only rewrites; propose an insert the way the model would
    from app.db import SessionLocal
    from app.models import Project
    from app.outline_wb import service as svc, session as sess
    with SessionLocal() as db:
        p = db.get(Project, proj)
        o, ver, _ = sess.ensure(db, p)
        first = svc.items_of(db, ver.id)[0]
        rev = svc.propose(db, p.org_id, o.id, ver.id, [{"op": "insert", "item_uid": first.item_uid, "after": {"kind": "question", "module": "warm", "texts": {"zh": "您好，我是今天的主持人，整个过程大约 30 分钟。"},
                                                                       "minutes": 2.0, "config": {"depth": "none", "accepted": True}}}], proposed_by="ai")[0]
        db.commit()
        rid = rev.id
    _api(org, proj, "post", f"/revisions/{rid}/accept", json={})
    texts = lambda: [i["text"] for i in _api(org, proj, "get", "").json()["items"] if i["module"] == "warm"]
    assert any("主持人" in t for t in texts())
    _api(org, proj, "post", "/modules/warm/generate")
    assert any("主持人" in t for t in texts())   # 重新生成本模块 keeps what a person accepted


def test_follow_the_plan_again_drops_the_outlines_own_value(org):
    pid = draft(org)["id"]
    _two_subs(org, pid)
    from app.db import SessionLocal
    from app.outline_wb.models import StudySetting
    _wb(org, pid, "get", "")
    with SessionLocal() as db:
        st = db.query(StudySetting).one()
        st.answers = {**(st.answers or {}), "minutes": 30}
        db.commit()
    v = _wb(org, pid, "get", "").json()
    row = next(m for m in v["context"]["sync"]["mismatch"] if m["bound"] and m["field"] == "minutes")
    assert row["value"] == 30
    back = _wb(org, pid, "post", "/sync/drop", json={"field": "minutes"}).json()
    assert back["context"]["minutes"] == 45
    other = next(m for m in back["context"]["sync"]["mismatch"] if m["sub_id"] == "s2" and m["field"] == "minutes")
    _wb(org, pid, "put", "/sync", json={"field": "minutes", "value": other["value"], "sub_id": "s2"})
    assert not any(m["field"] == "minutes" for m in _wb(org, pid, "get", "").json()["context"]["sync"]["mismatch"])
