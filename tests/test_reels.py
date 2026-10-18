"""Highlight reels: ordered clips of one project shared as one watermarked, expiring, revocable link; closed to outsiders; clips that were purged simply drop out."""
import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Clip, Evidence, Interview, Recording, ReelItem, ReelShare, utcnow
from app.retention import purge_interview
from test_export import _with_insight
from test_project_members import second_researcher


def make_clips(org, n=2):
    """An interview with an approved insight and n clips (rows made directly: no video is cut here)."""
    itv, ins = _with_insight(org)
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    ev = ins["evidence"][0]
    with SessionLocal() as db:
        oid = db.get(Interview, itv["id"]).org_id
        if not db.scalar(select(Recording).where(Recording.interview_id == itv["id"], Recording.status == "ready")):
            db.add(Recording(org_id=oid, interview_id=itv["id"], egress_id="t", storage_key="private/x/y/r.mp4", status="ready"))
        ids = []
        for i in range(n):
            c = Clip(org_id=oid, interview_id=itv["id"], evidence_id=ev["id"] if i == 0 else None, title=f"片段{i + 1}", start_ms=i * 2000, end_ms=i * 2000 + 1500)
            db.add(c)
            db.flush()
            ids.append(c.id)
        db.commit()
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    return itv, pid, ids


def new_reel(org, pid, ids, title="精华"):
    r = org.c.post(f"/api/projects/{pid}/reels", headers=org.h(), json={"title": title, "note": "给客户看", "clip_ids": ids})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_reel_keeps_the_chosen_order_and_can_be_reordered(org):
    _, pid, ids = make_clips(org, 3)
    r = new_reel(org, pid, [ids[2], ids[0], ids[1]])
    assert [i["id"] for i in r["items"]] == [ids[2], ids[0], ids[1]] and r["clips"] == 3
    e = org.c.put(f"/api/reels/{r['id']}", headers=org.h(), json={"title": "新名", "note": "", "clip_ids": [ids[1], ids[0]]}).json()
    assert e["title"] == "新名" and [i["id"] for i in e["items"]] == [ids[1], ids[0]]
    assert [x["id"] for x in org.c.get(f"/api/projects/{pid}/reels", headers=org.h()).json()] == [r["id"]]


def test_a_reel_needs_clips_of_this_project_without_duplicates_limit_or_strangers(org):
    _, pid, ids = make_clips(org, 2)
    other_itv, other_pid, other_ids = make_clips(org, 1)
    assert org.c.post(f"/api/projects/{pid}/reels", headers=org.h(), json={"title": "x", "clip_ids": []}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/reels", headers=org.h(), json={"title": "x", "clip_ids": [other_ids[0]]}).status_code == 422   # a clip of another project
    assert org.c.post(f"/api/projects/{pid}/reels", headers=org.h(), json={"title": "x", "clip_ids": ["nope"]}).status_code == 422
    assert new_reel(org, pid, [ids[0], ids[0], ids[1]])["clips"] == 2


def test_only_the_project_team_can_see_or_change_reels(org):
    _, pid, ids = make_clips(org, 1)
    r = new_reel(org, pid, ids)
    h = second_researcher(org)["h"]
    for method, url, body in [("get", f"/api/projects/{pid}/clips", None), ("get", f"/api/projects/{pid}/reels", None), ("post", f"/api/projects/{pid}/reels", {"title": "x", "clip_ids": ids}),
                              ("get", f"/api/reels/{r['id']}", None), ("put", f"/api/reels/{r['id']}", {"title": "x", "clip_ids": ids}), ("delete", f"/api/reels/{r['id']}", None),
                              ("post", f"/api/reels/{r['id']}/share", {})]:
        resp = getattr(org.c, method)(url, headers=h, **({"json": body} if body is not None else {}))
        assert resp.status_code == 404, (method, url, resp.status_code)
    for role in ("observer", "moderator"):
        assert org.c.get(f"/api/projects/{pid}/reels", headers=org.h(role)).status_code in (403, 404)
    assert org.c.get(f"/api/reels/{r['id']}", headers=org.h()).status_code == 200


def test_the_shared_page_plays_signed_watermarked_cuts_and_never_shows_storage_or_unapproved_text(org, client):
    _, pid, ids = make_clips(org, 2)
    r = new_reel(org, pid, ids)
    sh = org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={"watermark": "WM-REEL-1 客户甲"}).json()
    token = sh["path"].split("t=")[1]
    v = client.get(f"/api/public/reel/{token}").json()
    assert v["title"] == "精华" and v["watermark"] == "WM-REEL-1 客户甲" and [i["title"] for i in v["items"]] == ["片段1", "片段2"]
    assert all(i["playback"]["download"] is False and i["playback"]["url"].startswith("/api/public/media?t=") for i in v["items"])
    assert v["items"][0]["quote"] and v["items"][1]["quote"] is None            # the clip with an approved insight carries its quote, the other none
    assert "storage_key" not in json.dumps(v) and "private/" not in json.dumps(v)
    assert org.c.get(f"/api/reels/{r['id']}", headers=org.h()).json()["shares"][0]["views"] == 1


def test_a_link_stops_working_when_revoked_expired_or_the_reel_deleted(org, client):
    _, pid, ids = make_clips(org, 1)
    r = new_reel(org, pid, ids)
    a = org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={}).json()
    b = org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={}).json()
    c = org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={}).json()
    tok = lambda s: s["path"].split("t=")[1]
    assert client.get(f"/api/public/reel/{tok(a)}").status_code == 200
    assert org.c.delete(f"/api/reel-shares/{a['id']}", headers=org.h()).status_code == 200
    assert client.get(f"/api/public/reel/{tok(a)}").status_code == 404
    with SessionLocal() as db:
        db.get(ReelShare, b["id"]).expires_at = utcnow().replace(year=2000)
        db.commit()
    assert client.get(f"/api/public/reel/{tok(b)}").status_code == 404
    assert client.get("/api/public/reel/nope").status_code == 404
    assert org.c.delete(f"/api/reels/{r['id']}", headers=org.h()).status_code == 204
    assert client.get(f"/api/public/reel/{tok(c)}").status_code == 404
    assert org.c.get(f"/api/reels/{r['id']}", headers=org.h()).status_code == 404


def test_a_purged_interviews_clips_drop_out_of_the_reel(org, client):
    itv, pid, ids = make_clips(org, 2)
    r = new_reel(org, pid, ids)
    tok = org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={}).json()["path"].split("t=")[1]
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.scalar(select(ReelItem).where(ReelItem.reel_id == r["id"])) is None
    assert client.get(f"/api/public/reel/{tok}").json()["items"] == []
    assert org.c.post(f"/api/reels/{r['id']}/share", headers=org.h(), json={}).status_code == 409   # nothing left to share
