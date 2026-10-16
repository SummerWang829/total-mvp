"""Focus-group foundation (Sprint 2): the per-participant seat model and the app/groups.py helper."""
import json

import pytest
from sqlalchemy import select

from app import groups
from app.db import SessionLocal
from app.models import Interview, InterviewParticipant, Participant
from app.security import new_secret_token, token_hash

from conftest import Org


def _group(org: Org, n_parts: int = 3):
    """A group interview with ``n_parts`` seats, built straight in the DB (creation endpoints come in a later step)."""
    pid = org.project()
    parts = [org.participant(pid) for _ in range(n_parts)]
    with SessionLocal() as db:
        org_id = db.get(Participant, parts[0]["id"]).org_id
        itv = Interview(org_id=org_id, project_id=pid, participant_id=parts[0]["id"], title="座谈会", language="zh",
                        kind=groups.KIND_GROUP, invite_hash=token_hash(new_secret_token()))
        db.add(itv)
        db.flush()
        tokens = []
        for p in parts:
            tok = new_secret_token()
            groups.add_seat(db, itv, db.get(Participant, p["id"]), token_hash(tok))
            tokens.append(tok)
        db.commit()
        return itv.id, [p["id"] for p in parts], tokens


def test_depth_interview_has_no_seats_and_is_not_a_group(org):
    itv = org.interview()  # a normal 1:1 interview
    with SessionLocal() as db:
        row = db.get(Interview, itv["id"])
        assert row.kind == groups.KIND_DEPTH and not groups.is_group(row)
        assert groups.seats(db, itv["id"]) == [] and groups.roster_view(db, row) is None


def test_seats_get_compact_numbers_and_duplicates_are_refused(org):
    iid, pids, _ = _group(org, 3)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        assert [s.seat for s in groups.seats(db, iid)] == [1, 2, 3]
        with pytest.raises(ValueError):
            groups.add_seat(db, itv, db.get(Participant, pids[0]), token_hash(new_secret_token()))  # already seated


def test_removing_a_seat_frees_its_number_for_reuse(org):
    iid, pids, _ = _group(org, 3)
    with SessionLocal() as db:
        pid = db.get(Interview, iid).project_id
    extra = org.participant(pid)  # create via the API before opening a write transaction (SQLite has one writer)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        assert groups.remove_seat(db, iid, pids[1])  # free seat 2
        db.flush()
        s = groups.add_seat(db, itv, db.get(Participant, extra["id"]), token_hash(new_secret_token()))
        assert s.seat == 2  # the freed number is reused, numbers stay compact
        db.commit()


def test_group_is_capped_at_max(org):
    pid = org.project()
    ps = [org.participant(pid) for _ in range(groups.MAX_GROUP + 1)]
    with SessionLocal() as db:
        org_id = db.get(Participant, ps[0]["id"]).org_id
        itv = Interview(org_id=org_id, project_id=pid, participant_id=ps[0]["id"], title="大座谈会", language="zh",
                        kind=groups.KIND_GROUP, invite_hash=token_hash(new_secret_token()))
        db.add(itv); db.flush()
        for p in ps[:groups.MAX_GROUP]:
            groups.add_seat(db, itv, db.get(Participant, p["id"]), token_hash(new_secret_token()))
        over = ps[groups.MAX_GROUP]
        with pytest.raises(ValueError):
            groups.add_seat(db, itv, db.get(Participant, over["id"]), token_hash(new_secret_token()))


def test_seat_by_invite_and_speaker_key_and_label(org):
    iid, pids, tokens = _group(org, 2)
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        seat = groups.seat_by_invite(db, tokens[0])
        assert seat and seat.participant_id == pids[0]
        assert groups.seat_by_invite(db, new_secret_token()) is None  # unknown token
        assert groups.speaker_key(itv, seat) == "p1"
        depth = org.interview()
        assert groups.speaker_key(db.get(Interview, depth["id"]), seat) == "participant"
        assert groups.label(seat) == "参与者 ·01"


def test_roster_view_shows_each_seat_without_leaking_more_than_needed(org):
    iid, pids, _ = _group(org, 2)
    with SessionLocal() as db:
        view = groups.roster_view(db, db.get(Interview, iid))
    assert view["max"] == groups.MAX_GROUP and len(view["seats"]) == 2
    assert {s["seat"] for s in view["seats"]} == {1, 2}
    assert all(s["entered"] is False and s["admitted"] is False for s in view["seats"])


def test_retention_purges_group_seats(org):
    iid, _, _ = _group(org, 3)
    from app.retention import purge_interview

    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, iid), "test")
        db.commit()
        assert db.query(InterviewParticipant).filter_by(interview_id=iid).count() == 0


# ---------- creation + roster endpoints (API) ----------
def _create_group(org: Org, pid: str, pids: list[str], role: str = "researcher", **kw):
    body = {"kind": "group", "participant_ids": pids, "title": "座谈会 1", "language": "zh",
            "moderator_id": org.users["moderator"], **kw}
    return org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(role), json=body)


def test_create_group_seats_everyone_and_returns_one_time_invites(org):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(3)]
    r = _create_group(org, pid, pids)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "group" and body["participant"]["name"] == "3 位受访者"
    assert {g["seat"] for g in body["group_invites"]} == {1, 2, 3}
    assert all(g["invite_path"].startswith("/join.html?t=") for g in body["group_invites"])
    assert body["group"]["max"] == 10 and len(body["group"]["seats"]) == 3


def test_group_rejects_ai_moderation_and_bad_sizes(org):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    assert _create_group(org, pid, pids, moderation="ai").status_code == 422  # groups are human-moderated
    assert _create_group(org, pid, []).status_code == 422  # need at least one
    many = [org.participant(pid)["id"] for _ in range(11)]
    assert _create_group(org, pid, many).status_code == 422  # over MAX_GROUP


def test_group_participants_must_be_in_the_project(client):
    org = Org(client)
    pid, other_pid = org.project(), org.project()
    good = org.participant(pid)["id"]
    foreign = org.participant(other_pid)["id"]
    assert _create_group(org, pid, [good, foreign]).status_code == 422


def test_depth_creation_still_works_and_is_not_a_group(org):
    itv = org.interview()  # the existing 1:1 path, unchanged
    assert itv["kind"] == "depth" and "group_invites" not in itv
    got = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()
    assert got["kind"] == "depth" and "group" not in got


def test_add_remove_and_rotate_seats(org):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    iid = _create_group(org, pid, pids).json()["id"]
    extra = org.participant(pid)["id"]
    r = org.c.post(f"/api/interviews/{iid}/participants", headers=org.h(), json={"participant_id": extra})
    assert r.status_code == 201 and r.json()["seat"] == 3
    assert org.c.post(f"/api/interviews/{iid}/participants", headers=org.h(), json={"participant_id": extra}).status_code == 422  # already seated
    tok = org.c.post(f"/api/interviews/{iid}/participants/{extra}/invite/rotate", headers=org.h())
    assert tok.status_code == 200 and tok.json()["invite_token"]
    assert org.c.delete(f"/api/interviews/{iid}/participants/{extra}", headers=org.h()).status_code == 204
    got = org.c.get(f"/api/interviews/{iid}", headers=org.h()).json()
    assert len(got["group"]["seats"]) == 2


def test_roster_endpoints_reject_depth_and_last_seat_and_after_start(org):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    iid = _create_group(org, pid, pids).json()["id"]
    depth = org.interview()
    assert org.c.post(f"/api/interviews/{depth['id']}/participants", headers=org.h(), json={"participant_id": pids[0]}).status_code == 409
    org.c.delete(f"/api/interviews/{iid}/participants/{pids[1]}", headers=org.h())  # down to one
    assert org.c.delete(f"/api/interviews/{iid}/participants/{pids[0]}", headers=org.h()).status_code == 409  # keep at least one
    org.c.post(f"/api/interviews/{iid}/start", headers=org.h("moderator"))
    new = org.participant(pid)["id"]
    assert org.c.delete(f"/api/interviews/{iid}/participants/{pids[0]}", headers=org.h()).status_code == 409  # not after start


def test_observer_sees_group_headcount_but_not_names(org):
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    iid = _create_group(org, pid, pids, observer_ids=[org.users["observer"]]).json()["id"]
    got = org.c.get(f"/api/interviews/{iid}", headers=org.h("observer")).json()
    assert got["participant"]["name"] == "受访者" and "group" not in got  # the client sees no roster or names


def _ready(org, token):
    assert org.c.post(f"/api/public/join/{token}/device", json={"camera": True, "microphone": True, "speaker": True}).json()["passed"]
    assert org.c.post(f"/api/public/join/{token}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": True}).status_code == 201


def test_each_seat_has_its_own_journey(org):
    """Consent, device check, admit and entry belong to one seat: letting one person in does not let the other in."""
    from conftest import decode_unverified

    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    created = _create_group(org, pid, pids, observer_ids=[org.users["observer"]]).json()
    iid = created["id"]
    invites = {g["participant_id"]: g["invite_token"] for g in created["group_invites"]}
    t1, t2 = invites[pids[0]], invites[pids[1]]
    _ready(org, t1)
    assert org.c.get(f"/api/public/join/{t1}").json()["consented"] is True
    assert org.c.get(f"/api/public/join/{t2}").json()["consented"] is False  # the other person has not agreed
    assert org.c.get(f"/api/public/join/{t2}").json()["device_ok"] is False
    org.c.post(f"/api/interviews/{iid}/start", headers=org.h("moderator"))
    assert org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True}).status_code == 422  # a group names the seat
    assert org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True, "participant_id": "nope"}).status_code == 404
    assert org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True, "participant_id": pids[0]}).json() == {"admitted": True, "held": False}
    assert org.c.post(f"/api/public/join/{t1}/enter").status_code == 200
    assert org.c.post(f"/api/public/join/{t2}/enter").status_code == 403  # not consented, and not admitted
    _ready(org, t2)
    assert org.c.post(f"/api/public/join/{t2}/enter").status_code == 409  # consented, but not let in
    org.c.get(f"/api/public/join/{t2}/live?waiting=1")
    live = org.c.get(f"/api/interviews/{iid}/live", headers=org.h("moderator")).json()
    assert "participant" not in live
    by = {p["participant_id"]: p for p in live["participants"]}
    assert by[pids[0]]["admitted"] and by[pids[0]]["entered"] and by[pids[0]]["label"] == "参与者 ·01"
    assert by[pids[1]]["waiting"] and not by[pids[1]]["admitted"]
    assert "participants" not in org.c.get(f"/api/interviews/{iid}/live", headers=org.h("observer")).json()
    org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True, "participant_id": pids[1]})
    ent = org.c.post(f"/api/public/join/{t2}/enter")
    claims = decode_unverified(ent.json()["token"])
    assert ent.status_code == 200 and claims["name"] == "P-02" and json.loads(claims["metadata"])["seat"] == 2
    org.c.post(f"/api/public/join/{t2}/leave")
    assert org.c.post(f"/api/public/join/{t2}/enter").status_code == 409  # leaving needs a fresh admit
    assert org.c.post(f"/api/public/join/{t1}/enter").status_code == 200  # the other person is unaffected


def test_a_group_seat_records_on_its_own_track(org):
    from app.models import RecordingTrack

    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    created = _create_group(org, pid, pids).json()
    iid, t1 = created["id"], created["group_invites"][0]["invite_token"]
    _ready(org, t1)
    org.c.post(f"/api/interviews/{iid}/start", headers=org.h("moderator"))
    org.c.post(f"/api/interviews/{iid}/admit", headers=org.h("moderator"), json={"admit": True, "participant_id": pids[0]})
    org.c.post(f"/api/public/join/{t1}/enter")
    rid = org.c.post(f"/api/interviews/{iid}/recording/start", headers=org.h("moderator")).json()["id"]
    up = org.c.post(f"/api/public/join/{t1}/recording/{rid}/chunk?seq=0&offset_ms=0", content=b"abc", headers={"Content-Type": "video/webm"})
    assert up.status_code == 204
    with SessionLocal() as db:
        roles = [t.role for t in db.scalars(select(RecordingTrack).where(RecordingTrack.recording_id == rid))]
    assert roles == ["p1"]  # seat 1, not the shared "participant" track a depth interview uses


def test_group_roster_is_tenant_scoped(client):
    org = Org(client)
    pid = org.project()
    pids = [org.participant(pid)["id"] for _ in range(2)]
    iid = _create_group(org, pid, pids).json()["id"]
    other = Org(client, "Other", "b@other.com")
    assert client.post(f"/api/interviews/{iid}/participants", headers=other.h(), json={"participant_id": pids[0]}).status_code == 404
