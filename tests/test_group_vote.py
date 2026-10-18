"""Focus-group candidate times: every participant marks the times that suit them, the team sees who can make which and confirms one, nobody enters before that."""
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import SlotVote
from test_groups import _create_group


def group_with_slots(org, n=3, slots=3):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(n)]
    r = _create_group(org, pid, pids).json()
    tokens = [g["invite_path"].split("t=")[1] for g in sorted(r["group_invites"], key=lambda g: g["seat"])]
    base = datetime.utcnow() + timedelta(days=3)
    starts = [(base + timedelta(days=i)).replace(microsecond=0).isoformat() for i in range(slots)]
    assert org.c.put(f"/api/interviews/{r['id']}/timeslots", headers=org.h(), json={"starts": starts}).status_code == 200
    view = org.c.get(f"/api/interviews/{r['id']}/timeslots", headers=org.h()).json()
    return r["id"], tokens, view


def vote(org, token, ids):
    return org.c.post(f"/api/public/join/{token}/slot", json={"slot_ids": ids})


def info(org, token):
    return org.c.get(f"/api/public/join/{token}").json()["timeslots"]


def test_each_participant_sees_the_times_with_vote_counts_but_never_who(org):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    t = info(org, tokens[0])
    assert t["group"] is True and t["voted"] is False and [s["votes"] for s in t["slots"]] == [0, 0, 0] and all(s["mine"] is False for s in t["slots"])
    assert vote(org, tokens[0], [ids[0], ids[1]]).json() == {"voted": True}
    assert vote(org, tokens[1], [ids[1]]).status_code == 200
    mine, other = info(org, tokens[0]), info(org, tokens[1])
    assert [s["votes"] for s in mine["slots"]] == [1, 2, 0] and [s["mine"] for s in mine["slots"]] == [True, True, False] and mine["voted"] is True
    assert [s["mine"] for s in other["slots"]] == [False, True, False]
    assert "voters" not in str(mine) and "参与者" not in str(mine) and info(org, tokens[2])["voted"] is False


def test_voting_again_replaces_the_earlier_choice_and_bad_votes_are_refused(org):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    vote(org, tokens[0], [ids[0], ids[1]])
    vote(org, tokens[0], [ids[2]])
    assert [s["mine"] for s in info(org, tokens[0])["slots"]] == [False, False, True]
    assert vote(org, tokens[0], []).status_code == 409 and vote(org, tokens[0], ["nope"]).status_code == 409
    assert [s["mine"] for s in info(org, tokens[0])["slots"]] == [False, False, True]    # a refused vote changes nothing
    with SessionLocal() as db:
        assert db.query(SlotVote).filter_by(interview_id=iid).count() == 1


def test_the_team_sees_who_can_make_each_time_and_the_best_one(org):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    vote(org, tokens[0], [ids[0], ids[1]])
    vote(org, tokens[1], [ids[1], ids[2]])
    vote(org, tokens[2], [ids[1]])
    v = org.c.get(f"/api/interviews/{iid}/timeslots", headers=org.h()).json()
    assert v["group"] is True and v["seats"] == 3 and v["voted"] == 3 and v["best"] == ids[1] and v["chosen"] is None
    assert [s["votes"] for s in v["slots"]] == [1, 3, 1] and v["slots"][1]["voters"] == ["参与者 ·01", "参与者 ·02", "参与者 ·03"] and "Wei Ling" not in str(v)


def test_a_tie_goes_to_the_earliest_time_and_with_no_votes_there_is_no_best(org):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    assert org.c.get(f"/api/interviews/{iid}/timeslots", headers=org.h()).json()["best"] is None
    vote(org, tokens[0], [ids[2]])
    vote(org, tokens[1], [ids[1]])
    assert org.c.get(f"/api/interviews/{iid}/timeslots", headers=org.h()).json()["best"] == ids[1]


def test_nobody_enters_before_the_team_confirms_and_confirming_fixes_the_time_for_everyone(org):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    vote(org, tokens[0], [ids[1]])
    assert org.c.post(f"/api/public/join/{tokens[0]}/enter").status_code == 409
    r = org.c.post(f"/api/interviews/{iid}/timeslots/{ids[1]}/confirm", headers=org.h())
    assert r.status_code == 200 and r.json()["chosen"][:16] == view["slots"][1]["start_at"][:16]
    for t in tokens:
        assert info(org, t) is None            # nothing left to choose
    assert vote(org, tokens[1], [ids[0]]).status_code == 409    # too late to vote
    assert org.c.get(f"/api/interviews/{iid}", headers=org.h()).json()["scheduled_at"][:16] == view["slots"][1]["start_at"][:16]
    assert any(e["action"] == "timeslot.confirm" for e in org.c.get("/api/audit?action=timeslot", headers=org.h("admin")).json())


def test_the_enter_message_for_a_group_says_the_team_will_confirm(org):
    iid, tokens, view = group_with_slots(org)
    r = org.c.post(f"/api/public/join/{tokens[0]}/enter")
    assert r.status_code == 409 and "研究团队会尽快确认时间" in r.json()["detail"]


def test_confirming_needs_the_projects_staff_and_a_real_slot(org, client):
    iid, tokens, view = group_with_slots(org)
    ids = [s["id"] for s in view["slots"]]
    from conftest import Org
    from test_project_members import second_researcher
    other = second_researcher(org)
    assert org.c.post(f"/api/interviews/{iid}/timeslots/{ids[0]}/confirm", headers=other["h"]).status_code == 404
    for role in ("moderator", "observer"):
        assert org.c.post(f"/api/interviews/{iid}/timeslots/{ids[0]}/confirm", headers=org.h(role)).status_code == 403
    assert org.c.post(f"/api/interviews/{iid}/timeslots/nope/confirm", headers=org.h()).status_code == 404
    foreign = Org(client, "Other", "b@other.com")
    assert org.c.post(f"/api/interviews/{iid}/timeslots/{ids[0]}/confirm", headers=foreign.h("admin")).status_code == 404


def test_votes_go_with_retention(org):
    from app.models import Interview
    from app.retention import purge_interview
    iid, tokens, view = group_with_slots(org)
    vote(org, tokens[0], [view["slots"][0]["id"]])
    with SessionLocal() as db:
        assert db.query(SlotVote).count() == 1
        purge_interview(db, db.get(Interview, iid), "test")
        db.commit()
        assert db.query(SlotVote).count() == 0


def test_a_depth_interview_still_picks_one_time_as_before(org):
    itv = org.interview()
    base = datetime.utcnow() + timedelta(days=3)
    org.c.put(f"/api/interviews/{itv['id']}/timeslots", headers=org.h(), json={"starts": [(base + timedelta(days=i)).replace(microsecond=0).isoformat() for i in range(2)]})
    t = itv["invite_token"]
    slots = org.c.get(f"/api/public/join/{t}").json()["timeslots"]
    assert "group" not in slots
    assert org.c.post(f"/api/public/join/{t}/slot", json={"slot_id": slots["slots"][0]["id"]}).json()["scheduled_at"][:16] == slots["slots"][0]["start_at"][:16]
