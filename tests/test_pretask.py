"""Pre-interview task: opt-in questions the participant answers after consent, before the waiting room."""
from conftest import H, Org
from test_week1 import give_consent, let_in


def set_pretask(org, itv, questions, role="researcher"):
    return org.c.put(f"/api/interviews/{itv['id']}/pretask", headers=org.h(role), json={"questions": questions})


def join_info(org, itv):
    return org.c.get(f"/api/public/join/{itv['invite_token']}").json()


def submit(org, itv, answers):
    return org.c.post(f"/api/public/join/{itv['invite_token']}/pretask", json={"answers": answers})


def test_no_pretask_means_no_step_and_entry_is_unchanged(org):
    itv = org.interview()
    assert join_info(org, itv)["pretask"] is None  # nothing configured -> journey unchanged
    give_consent(org, itv)
    let_in(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200


def test_configured_pretask_blocks_entry_until_answered(org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "你平时怎么护肤？", "required": True}, {"prompt": "最近买过什么？", "required": False}])
    give_consent(org, itv)
    let_in(org, itv)
    pt = join_info(org, itv)["pretask"]
    assert pt["done"] is False and len(pt["questions"]) == 2 and pt["questions"][0]["key"] == "q1"
    assert "answer" not in pt["questions"][0]  # the participant never gets other data, only the questions
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/enter")
    assert r.status_code == 403 and "pre-interview" in r.json()["detail"]
    key = pt["questions"][0]["key"]
    assert submit(org, itv, {key: "   "}).status_code == 422  # required left blank
    assert submit(org, itv, {key: "洗面奶加面霜"}).status_code == 201  # optional q2 may stay blank
    assert join_info(org, itv)["pretask"]["done"] is True
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200


def test_pretask_requires_consent_first(org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    assert submit(org, itv, {"q1": "a"}).status_code == 403  # must agree to the privacy notice first
    give_consent(org, itv)
    assert submit(org, itv, {"q1": "a"}).status_code == 201


def test_submit_404_when_no_pretask(org):
    itv = org.interview()
    give_consent(org, itv)
    assert submit(org, itv, {"q1": "a"}).status_code == 404


def test_only_the_team_configures_and_moderator_sees_answers_but_client_does_not(org):
    itv = org.interview()
    assert set_pretask(org, itv, [{"prompt": "Q", "required": True}], role="moderator").status_code == 403
    assert set_pretask(org, itv, [{"prompt": "Q", "required": True}], role="observer").status_code == 403
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    give_consent(org, itv)
    submit(org, itv, {"q1": "我的答案"})
    staff = org.c.get(f"/api/interviews/{itv['id']}/pretask", headers=org.h("moderator")).json()
    assert staff["questions"][0]["answer"] == "我的答案"  # the moderator sees it in the room
    assert org.c.get(f"/api/interviews/{itv['id']}/pretask", headers=org.h("observer")).status_code == 403  # the client never does


def test_empty_list_removes_the_pretask_and_its_answers(org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    give_consent(org, itv)
    submit(org, itv, {"q1": "a"})
    assert set_pretask(org, itv, []).json() == {"questions": []}
    assert join_info(org, itv)["pretask"] is None
    let_in(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200  # no pre-task -> entry unchanged


def test_editing_questions_drops_orphan_answers(org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "A", "required": True}, {"prompt": "B", "required": True}])
    give_consent(org, itv)
    submit(org, itv, {"q1": "ans1", "q2": "ans2"})
    set_pretask(org, itv, [{"prompt": "A only", "required": True}])  # q2 disappears
    staff = org.c.get(f"/api/interviews/{itv['id']}/pretask", headers=org.h("moderator")).json()
    assert [q["key"] for q in staff["questions"]] == ["q1"] and staff["questions"][0]["answer"] == "ans1"


def test_retention_purges_pretask_answers(org):
    from app.db import SessionLocal
    from app.models import Interview, PreTask, PreTaskAnswer
    from app.retention import purge_interview

    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    give_consent(org, itv)
    submit(org, itv, {"q1": "secret"})
    with SessionLocal() as db:
        obj = db.get(Interview, itv["id"])
        purge_interview(db, obj, "test")
        db.commit()
        assert db.get(PreTask, itv["id"]) is None
        assert db.query(PreTaskAnswer).filter_by(interview_id=itv["id"]).count() == 0


def test_pretask_is_tenant_scoped(client, org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    other = Org(client, "Other", "b@other.com")
    assert client.put(f"/api/interviews/{itv['id']}/pretask", headers=other.h(), json={"questions": []}).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}/pretask", headers=other.h()).status_code == 404


def test_submit_is_audited_without_the_token(org):
    itv = org.interview()
    set_pretask(org, itv, [{"prompt": "Q", "required": True}])
    give_consent(org, itv)
    submit(org, itv, {"q1": "a"})
    rows = org.c.get("/api/audit?limit=50", headers=H(org.admin)).json()
    assert "pretask.submit" in [r["action"] for r in rows] and itv["invite_token"] not in str(rows)
