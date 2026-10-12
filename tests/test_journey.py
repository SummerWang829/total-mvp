"""Participant journey as the research team sees it: sent -> opened -> device_ok -> consented -> entered -> done."""
from conftest import H
from test_week1 import give_consent, go_live, let_in


def state(org, itv, role="researcher"):
    return org.c.get(f"/api/interviews/{itv['id']}", headers=org.h(role)).json().get("invite_state")


def test_invite_state_follows_the_participant(org):
    itv = org.interview()
    t = itv["invite_token"]
    assert state(org, itv) == "sent"
    org.c.get(f"/api/public/join/{t}")  # a link preview (GET) must not count as "opened"
    assert state(org, itv) == "sent"
    assert org.c.post(f"/api/public/join/{t}/opened").status_code == 204
    assert state(org, itv) == "opened"
    r = org.c.post(f"/api/public/join/{t}/device", json={"camera": False, "microphone": False, "speaker": True})
    assert r.json() == {"passed": False} and state(org, itv) == "opened"  # no microphone: not passed
    r = org.c.post(f"/api/public/join/{t}/device", json={"camera": False, "microphone": True, "speaker": True})
    assert r.json() == {"passed": True} and state(org, itv) == "device_ok"  # camera may stay off
    assert org.c.get(f"/api/public/join/{t}").json()["device_ok"] is True
    give_consent(org, itv)
    assert state(org, itv) == "consented"
    assert org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True}).status_code == 200
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200
    assert state(org, itv) == "entered"
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    assert state(org, itv) == "done"


def test_participant_who_never_came_is_not_done(org):
    itv = org.interview()
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    assert state(org, itv) == "ended_absent"


def test_client_observer_does_not_see_the_journey_and_moderator_does(org):
    itv = org.interview()
    assert "invite_state" not in org.c.get(f"/api/interviews/{itv['id']}", headers=org.h("observer")).json()
    assert state(org, itv, "moderator") == "sent"
    pid = itv["project_id"]
    assert all("invite_state" not in i for i in org.c.get(f"/api/projects/{pid}/interviews", headers=org.h("observer")).json())


def test_journey_endpoints_need_a_valid_invitation(org, client):
    for path, body in (("opened", None), ("device", {"camera": True, "microphone": True, "speaker": True})):
        assert client.post(f"/api/public/join/nope/{path}", json=body).status_code == 404


def test_journey_events_are_audited_without_the_token(org):
    itv = org.interview()
    t = itv["invite_token"]
    org.c.post(f"/api/public/join/{t}/opened")
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": True, "speaker": True})
    rows = org.c.get("/api/audit?limit=50", headers=H(org.admin)).json()
    acts = [r["action"] for r in rows]
    assert "invite.open" in acts and "device.check" in acts
    assert t not in str(rows)


# ---- device gate + waiting room (decisions 2026-10-10: the device test must pass; the moderator admits or holds) ----
def test_cannot_enter_without_a_passed_device_test(org):
    itv = org.interview()
    t = itv["invite_token"]
    give_consent(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True})
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": False, "speaker": True})
    r = org.c.post(f"/api/public/join/{t}/enter")
    assert r.status_code == 403 and "device" in r.json()["detail"]
    org.c.post(f"/api/public/join/{t}/device", json={"camera": False, "microphone": True, "speaker": False})  # camera may stay off
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200


def test_waiting_room_admit_hold_and_leave(org):
    itv = org.interview()
    t, iid = itv["invite_token"], itv["id"]
    give_consent(org, itv)
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": True, "speaker": True})
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 409  # not admitted yet
    live = lambda: org.c.get(f"/api/public/join/{t}/live").json()
    staff = lambda role="moderator": org.c.get(f"/api/interviews/{iid}/live", headers=org.h(role)).json()
    assert staff()["participant"]["waiting"] is False
    org.c.get(f"/api/public/join/{t}/live?waiting=1")  # the waiting room page polls with waiting=1
    assert staff()["participant"]["waiting"] is True and org.c.get(f"/api/interviews/{iid}", headers=org.h()).json()["waiting"] is True
    assert "participant" not in staff("observer")  # the client does not see the waiting room
    # hold, then admit
    assert org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": False}).json() == {"admitted": False, "held": True}
    assert live()["held"] is True and live()["admitted"] is False
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 409
    org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True})
    assert live() == {**live(), "admitted": True, "held": False} and staff()["participant"]["waiting"] is False
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200  # a page reload re-enters without a new admit
    # pressing Leave means the moderator has to let them in again
    assert org.c.post(f"/api/public/join/{t}/leave").status_code == 204
    assert live()["admitted"] is False and org.c.post(f"/api/public/join/{t}/enter").status_code == 409


def test_only_the_team_can_admit(org):
    itv = org.interview()
    assert org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("observer"), json={"admit": True}).status_code == 403
    other = org.interview(moderator=False)  # a moderator who is not assigned cannot see it at all
    assert org.c.post(f"/api/interviews/{other['id']}/admit", headers=org.h("moderator"), json={"admit": True}).status_code == 404
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    assert org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True}).status_code == 409


# ---- project home: progress, next step, recent activity ----
def test_project_overview_progress_next_step_and_activity(org):
    pid = org.project()
    ov = lambda: org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()
    assert ov()["next"]["page"] == "people" and ov()["summary"]["participants"] == 0
    itv = org.interview(pid)
    s = ov()["summary"]
    assert (s["participants"], s["sessions"], s["upcoming"], s["done"]) == (1, 1, 1, 0)
    org.c.post(f"/api/public/join/{itv['invite_token']}/opened")
    give_consent(org, itv)
    o = ov()
    whats = [a["what"] for a in o["activity"]]
    assert "同意了隐私说明" in whats and "打开了邀请" in whats and "新建了场次" in whats
    assert all(a["who"] != "Wei Ling" for a in o["activity"]) and "ip" not in str(o["activity"])  # participants appear as 受访者, no IPs
    assert {a["session"] for a in o["activity"] if a["what"] == "同意了隐私说明"} == {"深访 1"}
    let_in(org, itv); go_live(org, itv, consent=False)
    org.c.post(f"/api/public/join/{itv['invite_token']}/enter")
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    s2 = ov()
    assert s2["summary"]["done"] == 1 and s2["next"]["page"] == "insights"  # done but not transcribed -> transcribe next
    assert org.c.get(f"/api/projects/{pid}/overview", headers=org.h("observer")).status_code == 403
    assert org.c.get(f"/api/projects/{pid}/overview", headers=org.h("moderator")).status_code == 403


def test_project_cards_have_counts_for_the_team_only(org):
    pid = org.project()
    org.interview(pid)
    staff = org.c.get("/api/projects", headers=org.h()).json()[0]
    assert staff["summary"]["participants"] == 1 and staff["summary"]["sessions"] == 1
    assert "summary" not in org.c.get("/api/projects", headers=org.h("observer")).json()[0]


def test_overview_is_tenant_scoped(client, org):
    from conftest import Org
    pid = org.project()
    other = Org(client, "Other", "b@other.com")
    assert client.get(f"/api/projects/{pid}/overview", headers=other.h()).status_code == 404


# ---- admin: clear demo data ----
def test_demo_reset_only_for_the_demo_company(client):
    from conftest import Org
    demo = Org(client, "演示公司", "admin@demo.test")
    real = Org(client, "Acme", "a@acme.com")
    d_itv, r_itv = demo.interview(), real.interview()
    give_consent(demo, d_itv)
    assert client.post("/api/admin/demo/reset", headers=real.h("admin")).status_code == 403  # a real company is never wiped
    assert client.post("/api/admin/demo/reset", headers=demo.h("researcher")).status_code == 403  # admins only
    r = client.post("/api/admin/demo/reset", headers=demo.h("admin"))
    assert r.status_code == 200 and r.json() == {"interviews": 1, "participants": 1}
    assert client.get(f"/api/interviews/{d_itv['id']}", headers=demo.h()).status_code == 404
    assert client.get(f"/api/projects/{d_itv['project_id']}", headers=demo.h()).status_code == 200  # projects and accounts stay
    assert client.get(f"/api/interviews/{r_itv['id']}", headers=real.h()).status_code == 200  # the other company is untouched


def test_activity_says_failed_device_tests_and_folds_repeats(org):
    pid = org.project()
    itv = org.interview(pid)
    t = itv["invite_token"]
    for mic in (False, False, True, True):
        org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": mic, "speaker": True})
    whats = [a["what"] for a in org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()["activity"]]
    assert whats[:2] == ["完成了设备检测", "设备检测未通过"]  # newest first; two repeats each fold into one line
    s = org.c.get(f"/api/projects/{pid}/overview", headers=org.h()).json()["summary"]
    assert s["ended"] == 0 and s["transcribed"] == 0
