"""Material marking: a participant taps ♥ like / ✕ dislike on an image, the team reads it back as a heatmap (Sprint 1)."""
from app.db import SessionLocal
from app.models import Interview, StimulusMark
from app.retention import purge_interview

from conftest import Org
from test_concept import _install, _live_room, _stimuli


def _image_id(org, itv):
    return _stimuli(org, itv)["concept_a"]["id"]


def _mark(org, itv, sid, kind="like", x=0.5, y=0.5):
    return org.c.post(f"/api/public/join/{itv['invite_token']}/stimulus/{sid}/mark", json={"kind": kind, "x": x, "y": y})


def _live_with_image(org, **kw):
    itv = _live_room(org, **kw)
    _install(org, itv)
    return itv, _image_id(org, itv)


def test_participant_marks_an_image_and_sees_their_own_pins(org):
    itv, sid = _live_with_image(org)
    r = _mark(org, itv, sid, "like", 0.25, 0.75)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "like" and m["x"] == 0.25 and m["y"] == 0.75 and m["stimulus_id"] == sid
    _mark(org, itv, sid, "dislike", 0.1, 0.2)
    mine = org.c.get(f"/api/public/join/{itv['invite_token']}/stimulus/{sid}/marks").json()["marks"]
    assert len(mine) == 2 and {x["kind"] for x in mine} == {"like", "dislike"}


def test_cannot_mark_before_entering_the_room(org):
    itv = org.interview()
    _install(org, itv)
    sid = _image_id(org, itv)
    assert _mark(org, itv, sid).status_code == 409  # not live / not consented / not admitted


def test_only_an_image_can_be_marked(org):
    itv, _ = _live_with_image(org)
    video = _stimuli(org, itv)["ad_film"]["id"]
    assert _mark(org, itv, video).status_code == 422


def test_unknown_or_foreign_material_is_refused(client):
    org = Org(client)
    itv, sid = _live_with_image(org)
    assert _mark(org, itv, "nope").status_code == 422
    other, osid = _live_with_image(Org(client, "Other", "b@other.com"))
    assert _mark(org, itv, osid).status_code == 422  # another interview's material


def test_coordinates_must_be_within_the_image(org):
    itv, sid = _live_with_image(org)
    assert _mark(org, itv, sid, x=1.5).status_code == 422
    assert _mark(org, itv, sid, y=-0.1).status_code == 422
    assert _mark(org, itv, sid, kind="meh").status_code == 422


def test_participant_removes_only_their_own_mark(org):
    itv, sid = _live_with_image(org)
    mid = _mark(org, itv, sid).json()["id"]
    assert org.c.delete(f"/api/public/join/{itv['invite_token']}/stimulus/{sid}/mark/{mid}").status_code == 204
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/stimulus/{sid}/marks").json()["marks"] == []
    assert org.c.delete(f"/api/public/join/{itv['invite_token']}/stimulus/{sid}/mark/{mid}").status_code == 404  # gone


def test_moderator_sees_the_live_overlay_observer_does_not(org):
    itv, sid = _live_with_image(org)
    _mark(org, itv, sid, "like")
    _mark(org, itv, sid, "dislike")
    mod = org.c.get(f"/api/interviews/{itv['id']}/stimulus/{sid}/marks", headers=org.h("moderator"))
    assert mod.status_code == 200 and len(mod.json()["marks"]) == 2
    assert org.c.get(f"/api/interviews/{itv['id']}/stimulus/{sid}/marks", headers=org.h("observer")).status_code == 403


def test_summary_counts_likes_and_dislikes_per_image_and_skips_videos(org):
    itv, sid = _live_with_image(org)
    _mark(org, itv, sid, "like")
    _mark(org, itv, sid, "like")
    _mark(org, itv, sid, "dislike")
    r = org.c.get(f"/api/interviews/{itv['id']}/stimulus-marks", headers=org.h())
    assert r.status_code == 200
    stims = r.json()["stimuli"]
    assert {s["key"] for s in stims} == {"concept_a", "concept_b"}  # two image cards, no videos
    card = next(s for s in stims if s["stimulus_id"] == sid)
    assert card["like"] == 2 and card["dislike"] == 1 and len(card["marks"]) == 3
    empty = next(s for s in stims if s["key"] == "concept_b")
    assert empty["like"] == 0 and empty["dislike"] == 0 and empty["marks"] == []
    assert org.c.get(f"/api/interviews/{itv['id']}/stimulus-marks", headers=org.h("observer")).status_code == 403


def test_summary_is_tenant_scoped(client):
    org = Org(client)
    itv, sid = _live_with_image(org)
    _mark(org, itv, sid)
    other = Org(client, "Other", "b@other.com")
    assert client.get(f"/api/interviews/{itv['id']}/stimulus-marks", headers=other.h()).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}/stimulus/{sid}/marks", headers=other.h()).status_code == 404


def test_too_many_marks_on_one_material_is_refused(org):
    from app import marks

    itv, sid = _live_with_image(org)
    for _ in range(marks.MAX_MARKS_PER_STIMULUS):
        assert _mark(org, itv, sid).status_code == 201
    assert _mark(org, itv, sid).status_code == 422  # over the cap


def test_retention_purges_marks(org):
    itv, sid = _live_with_image(org)
    _mark(org, itv, sid)
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.query(StimulusMark).filter_by(interview_id=itv["id"]).count() == 0
