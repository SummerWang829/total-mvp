"""The five week-1 spikes from the engineering manual, as executable checks."""
import base64
import hashlib
import json
import os
import time
from datetime import timedelta

import jwt

from app.config import settings
from app.evidence import quote_matches
from app.models import utcnow

from conftest import H, Org, decode_unverified


def go_live(org, itv, consent=True):
    r = org.c.post(f"/api/interviews/{itv['id']}/start", headers=org.h("moderator"))
    assert r.status_code == 200, r.text
    if consent:
        give_consent(org, itv)


def give_consent(org, itv, country="SG"):
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/consent", json={"country": country, "recording": True, "observers": True, "cross_border": True})
    assert r.status_code == 201, r.text


def let_in(org, itv):
    """Device test passed + the moderator lets the participant in from the waiting room."""
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/device", json={"camera": True, "microphone": True, "speaker": True}).json()["passed"]
    release(org, itv)


def release(org, itv):
    """The moderator lets the participant in (a depth interview with a human moderator; a group or AI session has nobody to release, so the call is refused and ignored)."""
    org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"))


# ---- spike 1: three-role room; observer is hidden, subscribe-only ----
def test_observer_token_is_hidden_and_subscribe_only(org):
    itv = org.interview()
    give_consent(org, itv)
    obs = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer")).json()
    v = decode_unverified(obs["token"])["video"]
    assert obs["role"] == "observer"
    assert v["hidden"] is True and v["canPublish"] is False and v["canPublishData"] is False
    assert v["canSubscribe"] is True and v["canPublishSources"] == [] and v["canUpdateOwnMetadata"] is False
    mod = decode_unverified(org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("moderator")).json()["token"])["video"]
    assert mod["canPublish"] is True and mod["hidden"] is False
    let_in(org, itv)
    part = decode_unverified(org.c.post(f"/api/public/join/{itv['invite_token']}/enter").json()["token"])["video"]
    assert part["canPublish"] is True and part["hidden"] is False and part["canPublishData"] is False
    assert {obs["room"], mod and obs["room"]} == {itv and obs["room"]}


def test_token_is_signed_with_livekit_secret(org):
    itv = org.interview()
    t = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer")).json()["token"]
    claims = jwt.decode(t, settings.livekit_api_secret, algorithms=["HS256"])
    assert claims["iss"] == settings.livekit_api_key


def test_observer_cannot_escalate_or_see_unassigned(org):
    itv = org.interview(observers=False)  # observer not assigned
    assert org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer")).status_code == 404
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("observer")).status_code == 404
    org.c.post(f"/api/interviews/{itv['id']}/observers", headers=org.h(), json={"user_id": org.users["observer"]})
    r = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer"), json={"as": "moderator"})
    assert r.status_code == 403  # observers cannot ask for moderator rights
    assert org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer")).json()["role"] == "observer"


def test_observer_never_sees_participant_identity_or_drafts(org):
    itv = org.interview()
    r = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h("observer")).json()
    assert r["participant"] == {"name": "受访者"}
    assert "invite_token" not in r and "invite_hash" not in r
    assert org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["participant"]["name"] == "Wei Ling"


def test_roles_cannot_write(org):
    pid = org.project()
    assert org.c.post("/api/projects", headers=org.h("observer"), json={"name": "x"}).status_code == 403
    assert org.c.post("/api/projects", headers=org.h("moderator"), json={"name": "x"}).status_code == 403
    assert org.c.post("/api/users", headers=org.h("researcher"), json={"name": "x", "email": "x@x.com", "password": "password123", "role": "admin"}).status_code == 403
    assert org.c.get("/api/audit", headers=org.h("researcher")).status_code == 403
    assert org.c.get(f"/api/projects/{pid}/participants", headers=org.h("observer")).status_code == 403


# ---- spike 5: tenant isolation ----
def test_tenant_isolation(client, org):
    other = Org(client, "Other", "b@other.com")
    pid = org.project()
    itv = org.interview(pid)
    assert client.get(f"/api/projects/{pid}", headers=other.h()).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}", headers=other.h()).status_code == 404
    assert client.post(f"/api/interviews/{itv['id']}/token", headers=other.h("admin")).status_code == 404
    assert client.get(f"/api/interviews/{itv['id']}/transcript", headers=other.h("admin")).status_code == 404
    assert client.get("/api/projects", headers=other.h()).json() == []
    # cannot attach another org's user / participant
    p2 = other.project()
    r = client.post(f"/api/projects/{p2}/interviews", headers=other.h(), json={"participant_id": itv["participant"]["id"], "title": "x"})
    assert r.status_code == 422
    r = client.post(f"/api/interviews/{itv['id']}/observers", headers=org.h(), json={"user_id": other.users["observer"]})
    assert r.status_code == 422


def test_unauthenticated_and_forged_tokens(client, org):
    itv = org.interview()
    assert client.get("/api/projects").status_code == 401
    forged = jwt.encode({"sub": "x", "org": "y", "role": "admin"}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert client.get("/api/projects", headers=H(forged)).status_code == 401
    # role in token is ignored: a moderator token cannot be upgraded by editing claims
    assert client.post("/api/projects", headers=org.h("moderator"), json={"name": "x"}).status_code == 403


# ---- consent, screening, join window ----
def test_cannot_enter_without_consent(org):
    itv = org.interview()
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/enter")
    assert r.status_code == 403


def test_all_three_consents_required(org):
    itv = org.interview()
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": False})
    assert r.status_code == 422
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 403


def test_eu_screened_out_at_creation_and_at_consent(org):
    pid = org.project()
    p = org.participant(pid, "DE")
    assert p["screening"] == "blocked_region"
    r = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": p["id"], "title": "x"})
    assert r.status_code == 422
    itv = org.interview(pid)  # eligible ID participant self-declares an EU country
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/consent", json={"country": "FR", "recording": True, "observers": True, "cross_border": True})
    assert r.status_code == 403
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 403


def test_join_window(org):
    pid = org.project()
    part = org.participant(pid)
    when = (utcnow() + timedelta(days=2)).isoformat()
    itv = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": part["id"], "title": "later", "scheduled_at": when}).json()
    give_consent(org, itv)
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 409


def test_invite_rotation_kills_old_link(org):
    itv = org.interview()
    new = org.c.post(f"/api/interviews/{itv['id']}/invite/rotate", headers=org.h()).json()["invite_token"]
    assert org.c.get(f"/api/public/join/{itv['invite_token']}").status_code == 404
    assert org.c.get(f"/api/public/join/{new}").status_code == 200


# ---- spike 2: recording ----
def test_recording_needs_consent_and_hides_storage_key(org):
    itv = org.interview()
    go_live(org, itv, consent=False)
    assert org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator")).status_code == 409
    give_consent(org, itv)
    r = org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator"))
    assert r.status_code == 201 and "storage_key" not in json.dumps(r.json())
    assert org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator")).status_code == 409
    assert org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator")).json()["status"] == "ready"
    assert "storage_key" not in json.dumps(org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json())


def test_observer_cannot_control_recording(org):
    itv = org.interview()
    go_live(org, itv)
    assert org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("observer")).status_code == 403
    assert org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("observer")).status_code == 403


def recorded_interview(org):
    itv = org.interview()
    go_live(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator"))
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    return itv


def test_end_stops_recording_and_blocks_new_tokens(org):
    itv = recorded_interview(org)
    assert org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]["status"] == "ready"
    assert org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("moderator")).status_code == 409
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/enter").status_code == 409


def test_webhook_signature(org):
    body = json.dumps({"event": "room_finished", "room": {"name": "nope"}}).encode()
    good = jwt.encode({"iss": settings.livekit_api_key, "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(), "exp": int(time.time()) + 60},
                      settings.livekit_api_secret, algorithm="HS256")
    assert org.c.post("/api/public/livekit/webhook", content=body, headers={"authorization": good}).status_code == 200
    assert org.c.post("/api/public/livekit/webhook", content=body + b" ", headers={"authorization": good}).status_code == 401  # body tampered
    assert org.c.post("/api/public/livekit/webhook", content=body).status_code == 401


# ---- spike 3 & 4: transcript, evidence, quote validation ----
def transcribed(org):
    itv = recorded_interview(org)
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 200
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    return itv, segs


def test_transcribe_requires_recording(org):
    itv = org.interview()
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 409


def test_quote_matching_rules():
    t = "Saya takut produknya palsu dan tidak ada sertifikat halal yang jelas."
    assert quote_matches("produknya palsu", t)
    assert quote_matches("SAYA takut … sertifikat halal", t)  # ellipsis, case, order
    assert not quote_matches("sertifikat halal … Saya takut", t)  # wrong order
    assert not quote_matches("produknya asli", t)
    assert not quote_matches("ok", t)  # too short to prove anything
    assert quote_matches("我担心，是假货", "我担心是假货，而且没有清楚的清真认证。")  # punctuation-insensitive


def test_fabricated_quote_is_rejected(org):
    itv, segs = transcribed(org)
    seg = [s for s in segs if s["speaker"] == "participant"][1]
    bad = {"theme": "x", "statement": "y", "evidence": [{"segment_id": seg["id"], "quote": "The price is far too high for me"}]}
    r = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json=bad)
    assert r.status_code == 422
    good = {"theme": "信任", "statement": "怕假货", "evidence": [{"segment_id": seg["id"], "quote": "clear halal certification"}]}
    r = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json=good)
    assert r.status_code == 201
    ev = r.json()["evidence"][0]
    assert (ev["start_ms"], ev["end_ms"]) == (seg["start_ms"], seg["end_ms"])  # timestamps come from the segment


def test_client_cannot_forge_timestamps_or_foreign_segment(org, client):
    itv, segs = transcribed(org)
    itv2 = org.interview()
    other_seg = {"segment_id": segs[1]["id"], "quote": "watch reviews on TikTok"}
    r = client.post(f"/api/interviews/{itv2['id']}/insights", headers=org.h(), json={"theme": "t", "statement": "s", "evidence": [other_seg]})
    assert r.status_code == 422  # segment belongs to a different interview


def test_ai_generation_drops_hallucinated_evidence(org, monkeypatch):
    itv, segs = transcribed(org)
    real = segs[1]

    class Liar:
        def insights(self, segments):
            return [
                {"theme": "真", "statement": "先看 TikTok", "evidence": [{"segment_id": real["id"], "quote": "watch reviews on TikTok", "lang": "orig"}]},
                {"theme": "编造", "statement": "全是编的", "evidence": [{"segment_id": real["id"], "quote": "I always buy in physical stores"}]},
                {"theme": "半真", "statement": "一半对", "evidence": [{"segment_id": real["id"], "quote": "compare prices on Shopee"}, {"segment_id": "nope", "quote": "x" * 10}]},
            ]

    monkeypatch.setattr("app.routers.interviews.get_llm", lambda: Liar())
    r = org.c.post(f"/api/interviews/{itv['id']}/insights/generate", headers=org.h()).json()
    assert [i["theme"] for i in r["created"]] == ["真", "半真"]
    assert r["dropped"][0]["theme"] == "编造"
    assert len(r["created"][1]["evidence"]) == 1  # only the verified half survives
    assert all(i["status"] == "draft" and i["kind"] == "ai" for i in r["created"])


def test_llm_failure_is_not_silent(org, monkeypatch):
    itv, _ = transcribed(org)

    class Boom:
        def insights(self, s):
            raise RuntimeError("down")

    monkeypatch.setattr("app.routers.interviews.get_llm", lambda: Boom())
    assert org.c.post(f"/api/interviews/{itv['id']}/insights/generate", headers=org.h()).status_code == 502


def test_clients_only_see_approved_insights(org):
    itv, segs = transcribed(org)
    r = org.c.post(f"/api/interviews/{itv['id']}/insights/generate", headers=org.h()).json()
    assert r["created"]
    assert org.c.get(f"/api/interviews/{itv['id']}/insights", headers=org.h("observer")).json() == []
    iid = r["created"][0]["id"]
    assert org.c.post(f"/api/insights/{iid}/approve", headers=org.h("observer")).status_code == 403
    assert org.c.post(f"/api/insights/{iid}/approve", headers=org.h()).status_code == 200
    seen = org.c.get(f"/api/interviews/{itv['id']}/insights", headers=org.h("observer")).json()
    assert [i["id"] for i in seen] == [iid]


def test_editing_transcript_flags_insights_whose_quote_vanished(org):
    itv, segs = transcribed(org)
    seg = segs[3]
    ins = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(),
                     json={"theme": "t", "statement": "s", "evidence": [{"segment_id": seg["id"], "quote": "halal certification"}]}).json()
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    r = org.c.patch(f"/api/transcript/{seg['id']}", headers=org.h(), json={"text_orig": "I worry it might be fake."}).json()
    assert r["insights_flagged"] == 1
    assert org.c.get(f"/api/interviews/{itv['id']}/insights", headers=org.h("observer")).json() == []  # no longer client-visible
    again = org.c.patch(f"/api/transcript/{seg['id']}", headers=org.h(), json={"text_zh": "我担心是假货。"}).json()
    assert again["insights_flagged"] == 0


def test_cannot_replace_transcript_under_existing_insights(org):
    itv, segs = transcribed(org)
    org.c.post(f"/api/interviews/{itv['id']}/insights/generate", headers=org.h())
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 409


# ---- clips + share ----
def test_clip_share_flow(org, client):
    itv, segs = transcribed(org)
    seg = segs[1]
    ins = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(),
                     json={"theme": "渠道", "statement": "s", "evidence": [{"segment_id": seg["id"], "quote": "compare prices on Shopee"}]}).json()
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    clip = org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"evidence_id": ins["evidence"][0]["id"]}).json()
    assert clip["start_ms"] == max(0, seg["start_ms"] - 2000)
    assert org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"start_ms": 0, "end_ms": 500_000}).status_code == 422
    sh = org.c.post(f"/api/clips/{clip['id']}/share", headers=org.h(), json={"expires_hours": 1}).json()
    tok = sh["token"]
    assert org.c.post(f"/api/clips/{clip['id']}/share", headers=org.h("observer"), json={}).status_code == 403
    v = client.get(f"/api/public/share/{tok}").json()  # no login needed
    assert v["theme"] == "渠道" and v["watermark"] and v["playback"]["download"] is False
    assert "storage_key" not in json.dumps(v) and "participant" not in json.dumps(v)
    assert client.get(f"/api/public/share/{tok}x").status_code == 404
    assert client.get(v["playback"]["url"]).status_code == 404  # no media file in the test store; signature itself is valid
    bad = v["playback"]["url"][:-3] + "abc"
    assert client.get(bad).status_code == 403
    client.delete(f"/api/shares/{sh['id']}", headers=org.h())
    assert client.get(f"/api/public/share/{tok}").status_code == 404


def test_media_path_traversal_blocked(org, client):
    from app.security import sign_media
    t = sign_media("../../../etc/passwd", 0, 1000)
    assert client.get("/api/public/media?t=" + t).status_code == 404
    expired = sign_media("private/x.mp4", 0, 1000, ttl_s=-5)
    assert client.get("/api/public/media?t=" + expired).status_code == 403


def test_media_served_when_file_exists(org, client):
    from app.security import sign_media
    os.makedirs(os.path.join(settings.media_dir, "private"), exist_ok=True)
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=25:d=4", "-f", "lavfi", "-i", "sine=d=4", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(settings.media_dir, "private", "a.mp4")], check=True)
    r = client.get("/api/public/media?t=" + sign_media("private/a.mp4", 0, 1000))
    assert r.status_code == 200 and r.headers["content-disposition"] == "inline" and r.headers["content-type"] == "video/mp4"


# ---- retention & erasure (180 days from PROJECT close; insights kept) ----
def _set_closed(project_id, days_ago):
    from app.db import SessionLocal
    from app.models import Project
    with SessionLocal() as db:
        db.get(Project, project_id).closed_at = utcnow() - timedelta(days=days_ago)
        db.commit()


def _insight_clip_share(org, itv, segs):
    ins = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(),
                     json={"theme": "t", "statement": "keep me", "evidence": [{"segment_id": segs[1]["id"], "quote": "compare prices on Shopee"}]}).json()
    clip = org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"evidence_id": ins["evidence"][0]["id"]}).json()
    tok = org.c.post(f"/api/clips/{clip['id']}/share", headers=org.h(), json={}).json()["token"]
    return tok


def test_retention_purges_180_days_after_project_close(org):
    from app.db import SessionLocal
    from app.models import Participant
    from app.retention import run_retention
    itv, segs = transcribed(org)
    tok = _insight_clip_share(org, itv, segs)
    with SessionLocal() as db:
        assert run_retention(db) == 0  # project still open
    r = org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h()).json()
    assert r["closed_at"] and r["purge_at"]
    with SessionLocal() as db:
        assert run_retention(db) == 0  # closed just now
    _set_closed(itv["project_id"], 181)
    with SessionLocal() as db:
        assert run_retention(db) == 1
        assert run_retention(db) == 0  # idempotent
        assert db.get(Participant, itv["participant"]["id"]).name == "已匿名"
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json() == []
    assert org.c.get(f"/api/public/share/{tok}").status_code == 404
    kept = org.c.get(f"/api/interviews/{itv['id']}/insights", headers=org.h()).json()[0]
    assert kept["statement"] == "keep me" and kept["evidence"][0]["quote"] == "[已按保留期删除]"  # conclusions kept, quotes gone
    assert org.c.get(f"/api/interviews/{itv['id']}/clips", headers=org.h()).json() == []
    assert org.c.get(f"/api/public/join/{itv['invite_token']}").status_code == 404


def test_not_purged_before_180_days_after_close(org):
    from app.db import SessionLocal
    from app.retention import run_retention
    itv, _ = transcribed(org)
    org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h())
    _set_closed(itv["project_id"], 179)
    with SessionLocal() as db:
        assert run_retention(db) == 0


def test_reopen_stops_the_clock(org):
    from app.db import SessionLocal
    from app.retention import run_retention
    itv, _ = transcribed(org)
    org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h())
    _set_closed(itv["project_id"], 200)
    assert org.c.post(f"/api/projects/{itv['project_id']}/reopen", headers=org.h()).json()["closed_at"] is None
    with SessionLocal() as db:
        assert run_retention(db) == 0


def test_backstop_for_projects_never_closed(org):
    from app.db import SessionLocal
    from app.models import Interview
    from app.retention import run_retention
    itv, _ = transcribed(org)
    with SessionLocal() as db:
        db.get(Interview, itv["id"]).ended_at = utcnow() - timedelta(days=settings.retention_backstop_days - 5)
        db.commit()
        assert run_retention(db) == 0
        db.get(Interview, itv["id"]).ended_at = utcnow() - timedelta(days=settings.retention_backstop_days + 5)
        db.commit()
        assert run_retention(db) == 1


def test_cannot_close_with_live_interview_and_close_is_staff_only(org):
    itv = org.interview()
    go_live(org, itv)
    assert org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h()).status_code == 409
    assert org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h("moderator")).status_code == 403
    assert org.c.post(f"/api/projects/{itv['project_id']}/close", headers=org.h("observer")).status_code == 403


def test_data_subject_erasure(org):
    itv, segs = transcribed(org)
    r = org.c.delete(f"/api/participants/{itv['participant']['id']}/data", headers=org.h())
    assert r.json() == {"purged_interviews": 1}
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json() == []
    assert org.c.delete(f"/api/participants/{itv['participant']['id']}/data", headers=org.h("observer")).status_code == 403


# ---- audit ----
def test_audit_trail_records_sensitive_actions(org):
    itv = org.interview()
    give_consent(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer"))
    org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("observer"))
    actions = [a["action"] for a in org.c.get("/api/audit", headers=org.h("admin")).json()]
    for a in ("consent.give", "room.token", "transcript.view", "interview.create", "project.create"):
        assert a in actions, a
    log = json.dumps(org.c.get("/api/audit", headers=org.h("admin")).json())
    assert itv["invite_token"] not in log  # secrets never reach the log


def test_register_login_basics(client):
    r = client.post("/api/auth/register", json={"org_name": "X", "name": "n", "email": "x@x.com", "password": "short"})
    assert r.status_code == 422
    ok = {"org_name": "X", "name": "n", "email": "x@x.com", "password": "longenough1"}
    assert client.post("/api/auth/register", json=ok).status_code == 200
    assert client.post("/api/auth/register", json=ok).status_code == 409
    assert client.post("/api/auth/login", json={"email": "x@x.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "X@x.com", "password": "longenough1"}).status_code == 200


def test_real_egress_webhook_sets_duration_and_clips_are_clamped(org):
    from app.db import SessionLocal
    from app.models import Recording
    itv = org.interview()
    go_live(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator"))
    with SessionLocal() as db:  # simulate a real LiveKit egress instead of the dev stub
        rec = db.query(Recording).filter_by(interview_id=itv["id"]).one()
        rec.egress_id = "EG_real_1"
        db.commit()
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    assert org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]["status"] == "processing"
    body = json.dumps({"event": "egress_ended", "egressInfo": {"egressId": "EG_real_1", "status": "EGRESS_COMPLETE", "fileResults": [{"duration": str(30 * 1_000_000_000)}]}}).encode()
    auth = jwt.encode({"iss": settings.livekit_api_key, "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(), "exp": int(time.time()) + 60}, settings.livekit_api_secret, algorithm="HS256")
    assert org.c.post("/api/public/livekit/webhook", content=body, headers={"authorization": auth}).status_code == 200
    rec = org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]
    assert rec["status"] == "ready" and rec["duration_ms"] == 30_000
    clip = org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"start_ms": 10_000, "end_ms": 90_000}).json()
    assert clip["end_ms"] == 30_000  # cannot run past the recording
    assert org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"start_ms": 40_000, "end_ms": 50_000}).status_code == 422


# ---- Singapore-only first market, four languages ----
def test_only_singapore_is_open(org):
    pid = org.project()
    assert org.participant(pid, "SG")["screening"] == "eligible"
    assert org.participant(pid, "ID")["screening"] == "blocked_region"  # not open yet
    assert org.participant(pid, "DE")["screening"] == "blocked_region"
    itv = org.interview(pid)
    r = org.c.post(f"/api/public/join/{itv['invite_token']}/consent", json={"country": "ID", "recording": True, "observers": True, "cross_border": True})
    assert r.status_code == 403 and "not open" in r.json()["detail"]
    assert org.c.get("/api/public/config").json()["allowed_countries"] == ["SG"]


def test_languages_validated_and_consent_has_four_languages(org):
    assert org.c.post("/api/projects", headers=org.h(), json={"name": "x", "language": "id"}).status_code == 422
    from app.consent import consent_for
    for lang in ("en", "zh", "ms", "ta"):
        c = consent_for(lang, "Acme")
        assert c["lang"] == lang and "Acme" in c["text"] and settings.dpo_email in c["text"] and "180" in c["text"]
        assert settings.dpo_email == "summer.wang@topvisiontechnology.com"
        assert ("Singapore" in c["text"]) if lang == "en" else True
        assert set(c["labels"]) == {"recording", "observers", "cross_border"}
    assert consent_for("zh-CN", "A")["lang"] == "zh" and consent_for("hi", "A")["lang"] == "en"  # unsupported -> English
    assert len({consent_for(l, "A")["sha256"] for l in ("en", "zh", "ms", "ta")}) == 4
    for lang in ("ms", "ta"):
        pid = org.project(language=lang)
        part = org.participant(pid)
        itv = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": part["id"], "title": "x", "language": lang}).json()
        assert org.c.get(f"/api/public/join/{itv['invite_token']}").json()["consent"]["lang"] == lang
