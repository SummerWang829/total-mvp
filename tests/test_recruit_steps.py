"""甄别问卷的 6 步（修正方案第 1 轮第 4 条）: conditions from the 立项 and the need, exclusions first, quotas taken from the 立项, the trap and the
consistency check (a contradiction ends it), the shortest answering time kept by the server, and going back remakes only one step's questions."""
from test_plans import approved, draft, plan

from app.db import SessionLocal
from app.models import RecruitForm, RecruitSettings
from app.recruit_form import Question, next_step
from app.routers.screen_public import sign_start, too_fast


def url(pid, tail=""):
    return f"/api/projects/{pid}/recruiting/form{tail}"


def _need(org, pid, need="近 12 个月在 4S 店保养过；一线或新一线城市；本田车主"):
    org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": [], "need": need, "goals": ["了解保养决策"]})


def _confirm(org, pid, step, data, questions=None):
    r = org.c.put(url(pid, f"/steps/{step}"), headers=org.h(), json={"data": data, **({"questions": questions} if questions is not None else {})})
    assert r.status_code == 200, r.text
    return r.json()


def test_conditions_come_from_the_need_and_make_one_question_each(org):
    pid = org.project()
    _need(org, pid)
    s = org.c.get(url(pid, "/steps"), headers=org.h()).json()
    assert s["plan"]["need"].startswith("近 12 个月") and [s["steps"]["status"][k] for k in s["order"]] == ["todo"] * 6
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    assert len(conds) == 3 and all(c["src"] == "rule" for c in conds)   # no model in the tests: one condition per clause of the need
    out = _confirm(org, pid, "include", {"conds": conds})
    inc = [q for q in out["questions"] if q["step"] == "include"]
    assert len(inc) == 3 and set(out["fresh"]) == {q["id"] for q in inc} and out["steps"]["status"]["include"] == "confirmed"
    assert all(any(o["goto"] == "end_out" for o in q["options"]) and any(o["tag"] for o in q["options"]) for q in inc)


def test_exclusions_go_first_and_going_back_remakes_only_one_step(org):
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    out = _confirm(org, pid, "include", {"conds": conds})
    excl = org.c.post(url(pid, "/steps/exclude/suggest"), headers=org.h(), json={}).json()["excl"]
    assert {e["id"] for e in excl if e["use"]} >= {"industry", "recent"} and not next(e for e in excl if e["id"] == "minor")["use"]
    out = _confirm(org, pid, "exclude", {"excl": excl})
    steps = [q["step"] for q in out["questions"]]
    assert steps.index("exclude") < steps.index("include") and steps.count("exclude") == sum(1 for e in excl if e["use"])
    industry = next(q for q in out["questions"] if q["cond_id"] == "industry")
    none = next(o for o in industry["options"] if o["text"] == "以上都没有")
    assert industry["type"] == "multi" and len(none["excl"]) == len(industry["options"]) - 1   # "以上都没有" cannot be ticked with the others
    mine = {"id": "q99", "type": "text", "text": "还有什么想说的？", "required": False}
    excl_ids = [q["id"] for q in out["questions"] if q["step"] == "exclude"]
    again = _confirm(org, pid, "include", {"conds": conds[:2]}, out["questions"] + [mine])
    assert [q["id"] for q in again["questions"] if q["step"] == "exclude"] == excl_ids and any(q["id"] == "q99" for q in again["questions"])
    assert sum(1 for q in again["questions"] if q["step"] == "include") == 2


def test_the_trap_ends_it_and_nothing_is_asked_twice(org):
    """用户 2026-11-20 第 3 轮: 重复的题目都不留 — the old “再确认一下” consistency question is no longer made; the trap still ends it."""
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    _confirm(org, pid, "include", {"conds": conds})
    qc = org.c.post(url(pid, "/steps/qc/suggest"), headers=org.h(), json={}).json()["qc"]
    assert not qc["consistency"] and qc["timed"]   # 修正方案第 5 轮: the shortest time is worked out (65% of the expected time), not typed
    out = _confirm(org, pid, "qc", {"qc": {**qc, "trap": True, "consistency": True}})
    qs = [Question.model_validate(q) for q in out["questions"]]
    assert not any(q.qc == "consistency" for q in qs) and not any(q.text.startswith("再确认") for q in qs)
    trap = next(q for q in qs if q.qc == "trap")
    answers = {q.id: (next(o.id for o in q.options if o.text == "经常") if q is trap else next(o.id for o in q.options if o.goto != "end_out")) for q in qs}
    assert next_step(qs, answers).result == "qualified"
    wrong = next(o.id for o in trap.options if o.text != "经常")
    assert next_step(qs, {**answers, trap.id: wrong}).result == "out"


def test_quotas_come_from_the_plan_and_the_list_counts_with_them(org):
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    years = next(k for k in cfg["vars"] if k != "国家")
    v1, v2 = cfg["vars"][years][0], cfg["vars"][years][1]
    approved(org, pid, lt={"dims": [years], "rows": [{"id": "r1", "vs": [v1["id"]], "t": 0, "i": 3, "f": 0}, {"id": "r2", "vs": [v2["id"]], "t": 0, "i": 2, "f": 0}]},
             n={"q": 0, "i": 5, "f": 0, "fs": 6})
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    assert conds[0]["src"] == "plan" and conds[0]["name"] == years and [o["text"] for o in conds[0]["options"][:2]] == [v1["name"], v2["name"]]
    quotas = org.c.post(url(pid, "/steps/quota/suggest"), headers=org.h(), json={}).json()["quotas"]
    assert quotas == [{"name": v1["name"], "field": "tag", "question": "", "value": v1["name"], "target": 3}, {"name": v2["name"], "field": "tag", "question": "", "value": v2["name"], "target": 2}]
    quotas[0]["target"] = 4
    _confirm(org, pid, "quota", {"quotas": quotas + [{"name": "加一组", "field": "tag", "value": "A+B", "target": 1}]})
    with SessionLocal() as db:
        assert [q["target"] for q in db.get(RecruitSettings, pid).quotas] == [4, 2, 1]


def test_the_shortest_time_is_kept_by_the_server(client, org):
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    out = _confirm(org, pid, "include", {"conds": conds[:1]})
    _confirm(org, pid, "qc", {"qc": {"trap": False, "consistency": False, "timed": True, "dedupe": True}})
    tok = org.c.post(url(pid, "/publish"), headers=org.h()).json()["link"].split("t=")[1]
    first = client.post(f"/api/public/screen/{tok}/step", json={"answers": {}}).json()
    q = out["questions"][0]
    answers = {q["id"]: next(o["id"] for o in q["options"] if o["goto"] != "end_out")}
    fast = client.post(f"/api/public/screen/{tok}/step", json={"answers": answers, "started": first["started"]}).json()
    assert fast["result"] == "out"   # answered at once: less than 65% of the expected time
    with SessionLocal() as db:
        f = db.get(RecruitForm, pid)
        assert f.out_by.get("__time__") == 1
        assert too_fast(f, tok, "") and too_fast(f, tok, "123.forged")
        old = sign_start(tok, 1_000_000)
        from app import recruit_form as _rf
        limit = _rf.too_fast_limit(_rf.load(f.questions))
        assert limit == round(sum(_rf.SECONDS_BY_TYPE[q.type] for q in _rf.load(f.questions)) * 0.65)
        assert not too_fast(f, tok, old, now=1_000_000 + limit + 1) and too_fast(f, tok, old, now=1_000_000 + limit - 1)
