"""Focus-group foundation (Sprint 2): the per-participant seat model and the app/groups.py helper."""
import pytest

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
