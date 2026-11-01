"""AI interview: what the researcher says while in control is recorded (their own tracks) and transcribed onto the chat's timeline."""
from sqlalchemy import select

from datetime import timedelta

from app.db import SessionLocal
from app.models import TranscriptSegment, utcnow
from app.recording import process_pending

from test_ai_room import _say, _start, _walk_into_the_room
from test_pipeline import make_track, media, upload, video_server_configured  # noqa: F401 (fixtures)


def _finish(org, itv):
    """Stop the recording and let the worker mix it (after the grace period, as in test_pipeline)."""
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator"))
    with SessionLocal() as db:
        process_pending(db, now=utcnow() + timedelta(seconds=999))
    return org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]


def _speech_rec(org, itv):
    r = org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_takeover_speech_is_recorded_and_transcribed_onto_the_chat_timeline(org, media, tmp_path):
    itv = _walk_into_the_room(org)
    assert _start(org, itv).status_code == 200
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher")).status_code == 200
    rid = _speech_rec(org, itv)
    voice = make_track(str(tmp_path / "voice.webm"), 6, video=False)
    upload(org, itv, rid, "moderator", voice, offset_ms=3000, part="t1")
    _say(org, itv, "Yes, ready.")
    rec = _finish(org, itv)
    assert rec["status"] == "ready", rec
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h("researcher")).status_code == 200
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("researcher")).json()
    assert {s["speaker"] for s in segs} >= {"ai_moderator", "participant", "moderator"}
    assert "Yes, ready." in [s["text_orig"] for s in segs]  # the typed chat is kept
    speech = [s for s in segs if s["speaker"] == "moderator"]
    assert speech and all(s["start_ms"] >= 3000 for s in speech)  # placed after the moment the recording reached 3 s
    assert [s["start_ms"] for s in segs] == sorted(s["start_ms"] for s in segs)
    assert [s["idx"] for s in segs] == list(range(len(segs)))
    n = len(segs)
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h("researcher")).status_code == 200
    again = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("researcher")).json()
    assert len(again) == n  # running it twice gives the same transcript


def test_no_takeover_means_the_chat_alone_is_the_transcript(org, media, tmp_path):
    itv = _walk_into_the_room(org)
    assert _start(org, itv).status_code == 200
    _say(org, itv, "I order from Grab.")
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    with SessionLocal() as db:
        rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == itv["id"])).all()
    assert "I order from Grab." in [r.text_orig for r in rows]
