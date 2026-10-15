"""Candidate interview times: the team offers a few, the participant picks one (Sprint 1)."""
from app.models import utcnow

from conftest import H, Org
from test_week1 import give_consent, let_in


def set_slots(org, itv, starts, role="researcher"):
    return org.c.put(f"/api/interviews/{itv['id']}/timeslots", headers=org.h(role), json={"starts": starts})


def join_info(org, itv):
    return org.c.get(f"/api/public/join/{itv['invite_token']}").json()


SLOTS = ["2099-01-01T09:00:00", "2099-01-01T14:00:00", "2099-01-02T10:00:00"]


def test_no_slots_means_journey_unchanged(org):
    itv = org.interview()
    assert join_info(org, itv)["timeslots"] is None
    give_consent(org, itv)
    let_in(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200


def test_offering_slots_clears_the_fixed_time_and_blocks_entry_until_picked(org):
    now = utcnow().replace(microsecond=0).isoformat()  # a time within the join window, so entry works after picking
    itv = org.interview(scheduled_at="2099-06-01T08:00:00")
    r = set_slots(org, itv, [now, SLOTS[0], SLOTS[1]])
    assert r.status_code == 200 and len(r.json()["slots"]) == 3 and r.json()["chosen"] is None
    info = join_info(org, itv)
    assert info["scheduled_at"] is None and len(info["timeslots"]["slots"]) == 3  # fixed time cleared; must pick
    give_consent(org, itv)
    let_in(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 409  # cannot enter before picking
    chosen = next(s["id"] for s in info["timeslots"]["slots"] if s["start_at"].startswith(now[:16]))
    pr = org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": chosen})
    assert pr.status_code == 200 and pr.json()["scheduled_at"].startswith(now[:16])
    after = join_info(org, itv)
    assert after["timeslots"] is None and after["scheduled_at"].startswith(now[:16])  # picked -> nothing left to choose
    assert org.c.get(f"/api/interviews/{itv['id']}/timeslots", headers=org.h()).json()["chosen"].startswith(now[:16])
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200


def test_cannot_pick_an_unknown_or_foreign_slot(client, org):
    itv = org.interview()
    set_slots(org, itv, SLOTS)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": "nope"}).status_code == 409
    other = org.interview()
    oslot = set_slots(org, other, SLOTS).json()["slots"][0]["id"]  # a slot from a different interview
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": oslot}).status_code == 409


def test_picking_twice_is_refused(org):
    itv = org.interview()
    sid = set_slots(org, itv, SLOTS).json()["slots"][0]["id"]
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": sid}).status_code == 200
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": sid}).status_code == 409  # already chosen


def test_empty_list_removes_candidate_times(org):
    itv = org.interview()
    set_slots(org, itv, SLOTS)
    assert set_slots(org, itv, []).json() == {"slots": [], "chosen": None}
    assert join_info(org, itv)["timeslots"] is None
    give_consent(org, itv)
    let_in(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 200  # no slots -> entry unchanged


def test_duplicate_times_are_merged_and_too_many_refused(org):
    itv = org.interview()
    assert len(set_slots(org, itv, [SLOTS[0], SLOTS[0], SLOTS[1]]).json()["slots"]) == 2  # deduped
    assert set_slots(org, itv, [f"2099-01-0{n}T09:00:00" for n in range(1, 9)]).status_code == 422  # > MAX_SLOTS


def test_only_the_team_configures_slots(org):
    itv = org.interview()
    assert set_slots(org, itv, SLOTS, role="moderator").status_code == 403
    assert set_slots(org, itv, SLOTS, role="observer").status_code == 403
    assert set_slots(org, itv, SLOTS).status_code == 200


def test_slots_are_tenant_scoped(client, org):
    itv = org.interview()
    set_slots(org, itv, SLOTS)
    other = Org(client, "Other", "b@other.com")
    assert client.put(f"/api/interviews/{itv['id']}/timeslots", headers=other.h(), json={"starts": []}).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}/timeslots", headers=other.h()).status_code == 404


def test_retention_purges_candidate_times(org):
    from app.db import SessionLocal
    from app.models import Interview, TimeSlot
    from app.retention import purge_interview

    itv = org.interview()
    set_slots(org, itv, SLOTS)
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.query(TimeSlot).filter_by(interview_id=itv["id"]).count() == 0


def test_pick_is_audited_without_the_token(org):
    itv = org.interview()
    sid = set_slots(org, itv, SLOTS).json()["slots"][0]["id"]
    org.c.post(f"/api/public/join/{itv['invite_token']}/slot", json={"slot_id": sid})
    rows = org.c.get("/api/audit?limit=50", headers=H(org.admin)).json()
    assert "timeslot.pick" in [r["action"] for r in rows] and itv["invite_token"] not in str(rows)
