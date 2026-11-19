"""真人 Pilot 多轮（修正方案第 1 轮第 9、10 条）: a round on one version, marks in the room (time and question number from the server), each mark decided,
the next version made from the accepted changes (the round's version stays as it ran), the publish gates, and 跳过 Pilot，直接上线."""
from datetime import timedelta

from test_outline_wb_api import _api, _ready, proj  # noqa: F401 - the shared fixture and helpers

from app.db import SessionLocal
from app.models import Interview, utcnow
from app.outline_wb.models import Mark, OutlineVersion


def _start(org, proj, watchers=("observer",)):
    part = org.participant(proj)
    r = _api(org, proj, "post", "/pilot-rounds", json={"participant_id": part["id"], "watcher_ids": [org.users[w] for w in watchers], "send_email": False})
    assert r.status_code == 200, r.text
    return r.json()


def _run_interview(org, iid, minutes_ago=5):
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        itv.status, itv.started_at = "live", utcnow() - timedelta(minutes=minutes_ago)
        db.commit()


def _end(iid):
    with SessionLocal() as db:
        db.get(Interview, iid).status = "ended"
        db.commit()


def test_a_round_starts_on_the_current_version_and_invites_the_watchers(org, proj):
    _ready(org, proj)
    out = _start(org, proj)
    assert out["round"] == 1 and out["watchers"][0]["status"] == "link" and "room.html?i=" in out["watchers"][0]["link"]   # no email set up: the link to copy
    rs = out["rounds"]["rounds"]
    assert rs[0]["number"] == 1 and rs[0]["version"] == 1 and len(rs[0]["interviews"]) == 1
    with SessionLocal() as db:
        itv = db.get(Interview, out["interview"]["id"])
        assert itv.is_pilot and itv.pilot_round_id and org.users["observer"] in {o.user_id for o in itv.observers}   # the observer can now open that room
    assert _start(org, proj)["round"] == 1   # a second pilot interview joins the same open round


def test_a_mark_in_the_room_takes_the_servers_time_and_the_current_question(org, proj):
    _ready(org, proj)
    iid = _start(org, proj)["interview"]["id"]
    _run_interview(org, iid, minutes_ago=3)
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    with SessionLocal() as db:
        itv = db.get(Interview, iid)
        from app.outline_wb.stage3 import snapshot_on_start
        snapshot_on_start(db, itv)
        itv.current_item_uid = uid
        db.commit()
    r = org.c.post(f"/api/interviews/{iid}/pilot-marks", headers=org.h("observer"), json={"note": "这题太长，受访者一直在等", "at_ms": 1})
    assert r.status_code == 200, r.text
    m = r.json()["marks"][-1]
    assert m["note"].startswith("这题太长") and 170_000 <= m["at_ms"] <= 200_000 and m["item_uid"] == uid   # ~3 minutes in, the browser's at_ms is ignored
    assert m["who"] == "客户"                                                                                  # an observer sees roles only
    assert org.c.get(f"/api/interviews/{iid}/pilot-marks", headers=org.h("admin")).json()["marks"][-1]["who"] == "observer"   # the team sees the name
    plain = org.interview(proj)
    assert org.c.get(f"/api/interviews/{plain['id']}/pilot-marks", headers=org.h()).json()["pilot"] is False   # not a pilot: no panel


def test_every_mark_is_decided_and_the_next_version_leaves_the_rounds_version_as_it_ran(org, proj):
    _ready(org, proj)
    out = _start(org, proj)
    iid, rid = out["interview"]["id"], out["rounds"]["rounds"][0]["id"]
    _run_interview(org, iid)
    with SessionLocal() as db:
        from app.outline_wb.stage3 import snapshot_on_start
        snapshot_on_start(db, db.get(Interview, iid))
        db.commit()
    uid = _api(org, proj, "get", "").json()["items"][0]["uid"]
    with SessionLocal() as db:
        db.get(Interview, iid).current_item_uid = uid
        db.commit()
    org.c.post(f"/api/interviews/{iid}/pilot-marks", headers=org.h(), json={"note": "这题太长，受访者听不懂"})
    _end(iid)
    rv = _api(org, proj, "post", f"/pilot-rounds/{rid}/suggest").json()
    m = next(x for x in rv["marks"] if x["origin"] == "room")
    assert rv["counts"]["open"] >= 1
    assert _api(org, proj, "post", f"/pilot-rounds/{rid}/next-version").status_code == 422   # marks still undecided
    before = _api(org, proj, "get", "").json()["items"][0]["text"]
    for x in rv["marks"]:
        assert _api(org, proj, "put", f"/marks/{x['id']}/decision", json={"decision": "accepted" if x["id"] == m["id"] else "deferred"}).status_code == 200
    assert _api(org, proj, "get", "").json()["items"][0]["text"] == before   # accepting writes nothing into the round's version
    nv = _api(org, proj, "post", f"/pilot-rounds/{rid}/next-version").json()
    assert nv["version"] == 2
    with SessionLocal() as db:
        vs = {v.number: v for v in db.query(OutlineVersion).all()}
        assert vs[1].status == "superseded" and vs[2].summary.startswith("Pilot 第 1 轮修正")
    rows = {v["number"]: v for v in _api(org, proj, "get", "/publish/check").json()["versions"]}
    assert rows[1]["pilots"] == 1 and rows[1]["state"] == "superseded" and rows[2]["source"].startswith("Pilot 第 1 轮修正")


def test_publishing_needs_a_round_ready_and_every_mark_decided(org, proj):
    _ready(org, proj)
    chk = _api(org, proj, "get", "/publish/check").json()
    blocks = {c["id"] for c in chk["checks"] if c["level"] == "block"}
    assert {"pilot", "ready"} <= blocks and not chk["can_publish"]
    assert not any(c["id"] == "rehearsal" for c in chk["checks"])                  # the AI quick check is not a gate any more
    assert not any(c["id"] == "focus" and c["level"] == "block" for c in chk["checks"])   # an unmatched focus question only warns
    out = _start(org, proj)
    iid = out["interview"]["id"]
    _run_interview(org, iid)
    org.c.post(f"/api/interviews/{iid}/pilot-marks", headers=org.h(), json={"note": "节奏太快"})
    _end(iid)
    _api(org, proj, "put", "/ready", json={"value": True})
    chk = _api(org, proj, "get", "/publish/check").json()
    assert next(c for c in chk["checks"] if c["id"] == "marks")["level"] == "block"
    with SessionLocal() as db:
        for mk in db.query(Mark).filter(Mark.interview_id == iid):
            mk.decision = "deferred"
        db.commit()
    chk = _api(org, proj, "get", "/publish/check").json()
    assert chk["can_publish"] and any(c["id"] == "deferred" for c in chk["checks"])   # deferred counts as handled, and is said
    assert _api(org, proj, "post", "/publish").status_code == 200


def test_skip_pilot_publishes_with_only_the_red_line_check(org, proj):
    _ready(org, proj)
    assert _api(org, proj, "get", "/pilot-rounds").json()["can_skip"] is True
    r = _api(org, proj, "post", "/publish/skip-pilot")
    assert r.status_code == 200, r.text
    rows = _api(org, proj, "get", "/publish/check").json()["versions"]
    assert rows[0]["state"] == "published" and rows[0]["skipped"] is True
    assert _api(org, proj, "get", "/pilot-rounds").json()["can_skip"] is False


def test_only_the_team_and_watchers_may_mark_and_never_after_the_end(org, proj):
    _ready(org, proj)
    iid = _start(org, proj)["interview"]["id"]
    _run_interview(org, iid)
    assert org.c.post(f"/api/interviews/{iid}/pilot-marks", headers=org.h("moderator"), json={"note": "x"}).status_code in (200, 404)
    _end(iid)
    assert org.c.post(f"/api/interviews/{iid}/pilot-marks", headers=org.h(), json={"note": "x"}).status_code == 409
