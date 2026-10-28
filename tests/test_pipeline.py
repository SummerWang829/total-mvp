"""Recording upload -> mix -> transcript -> insight -> clip (watermarked cut) -> share. Uses real ffmpeg on tiny generated media."""
import json
import os
import subprocess
from datetime import timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Recording, RecordingTrack, utcnow
from app.recording import process_pending

from conftest import H
from test_week1 import give_consent, go_live


def make_track(path, seconds, video=True, freq=440):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc=s=320x240:r=25:d={seconds}"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:d={seconds}"]
    cmd += (["-c:v", "libvpx", "-b:v", "300k"] if video else []) + ["-c:a", "libopus", path]
    subprocess.run(cmd, check=True)
    return open(path, "rb").read()


def pieces(data, n=3):
    k = len(data) // n + 1
    return [data[i:i + k] for i in range(0, len(data), k)]


def start_recording(org, itv):
    go_live(org, itv)
    r = org.c.post(f"/api/interviews/{itv['id']}/recording/start", headers=org.h("moderator"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(org, itv, rid, who, data, offset_ms=0, ctype="video/webm", last=True, part="0"):
    ps = pieces(data)
    for i, p in enumerate(ps):
        if who == "moderator":
            r = org.c.post(f"/api/interviews/{itv['id']}/recording/{rid}/chunk?seq={i}&offset_ms={offset_ms}&part={part}", content=p, headers={**org.h("moderator"), "content-type": ctype})
        else:
            r = org.c.post(f"/api/public/join/{itv['invite_token']}/recording/{rid}/chunk?seq={i}&offset_ms={offset_ms}&part={part}", content=p, headers={"content-type": ctype})
        assert r.status_code == 204, r.text
    if last:
        url = (f"/api/interviews/{itv['id']}/recording/{rid}/chunk" if who == "moderator" else f"/api/public/join/{itv['invite_token']}/recording/{rid}/chunk")
        r = org.c.post(f"{url}?seq={len(ps)}&offset_ms={offset_ms}&last=1&part={part}", content=b"", headers={**(org.h("moderator") if who == "moderator" else {}), "content-type": ctype})
        assert r.status_code == 204, r.text


def finish(org, itv):
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator"))
    with SessionLocal() as db:
        assert process_pending(db) == 1
    return org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]


@pytest.fixture(autouse=True)
def video_server_configured(monkeypatch):
    monkeypatch.setattr(settings, "livekit_url", "ws://127.0.0.1:1")  # "a video server exists" -> browsers record; (nothing listens, kicking the room just fails quietly)


@pytest.fixture
def media(tmp_path):
    return {"mod": make_track(str(tmp_path / "m.webm"), 6, True, 440), "par": make_track(str(tmp_path / "p.webm"), 4, False, 880)}


def probe(path):
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", path], capture_output=True, text=True).stdout)
    return d


def recording_path(itv_id):
    with SessionLocal() as db:
        r = db.query(Recording).filter_by(interview_id=itv_id).one()
        return os.path.join(settings.media_dir, r.storage_key), r.id


def test_two_speakers_are_mixed_with_offsets(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 3000)  # participant's browser started recording 3 s later
    rec = finish(org, itv)
    assert rec["status"] == "ready", rec
    assert 6800 <= rec["duration_ms"] <= 7400  # 3 s offset + 4 s participant, longer than the 6 s moderator track
    path, _ = recording_path(itv["id"])
    d = probe(path)
    kinds = [s["codec_type"] for s in d["streams"]]
    assert "video" in kinds and "audio" in kinds
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    assert v["width"] == 1280 and v["height"] == 360  # two tiles side by side


def test_page_reload_mid_recording_keeps_both_parts(org, tmp_path):
    """The participant refreshes the page 4 s into the recording: two independent streams from the same speaker, laid one after another."""
    a, b = make_track(str(tmp_path / "a.webm"), 3, True, 440), make_track(str(tmp_path / "b.webm"), 3, True, 660)
    mod = make_track(str(tmp_path / "m.webm"), 8, True, 220)
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", mod, 0)
    upload(org, itv, rid, "participant", a, 0, part="p1")
    upload(org, itv, rid, "participant", b, 4500, part="p2")
    rec = finish(org, itv)
    assert rec["status"] == "ready" and 7600 <= rec["duration_ms"] <= 8600, rec
    path, _ = recording_path(itv["id"])
    vol = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    assert "mean_volume" in vol
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 200  # mock: one script per speaker, not duplicated
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    assert len([s for s in segs if s["speaker"] == "participant"]) == 4


def test_part_name_cannot_escape_the_recording_folder(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    r = org.c.post(f"/api/interviews/{itv['id']}/recording/{rid}/chunk?seq=0&part=../../evil", content=b"x", headers={**org.h("moderator"), "content-type": "video/webm"})
    assert r.status_code == 204
    assert not os.path.exists(os.path.join(settings.media_dir, "evil")) and not os.path.exists(os.path.join(settings.media_dir, "private", "evil"))


def test_waits_for_grace_then_finalizes(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator"))
    with SessionLocal() as db:
        assert process_pending(db) == 0  # participant may still be uploading
        assert process_pending(db, now=utcnow() + timedelta(seconds=settings.recording_grace_s + 1)) == 1
    assert org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]["status"] == "ready"


def test_empty_recording_fails_cleanly(org):
    itv = org.interview()
    start_recording(org, itv)
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator"))
    with SessionLocal() as db:
        process_pending(db, now=utcnow() + timedelta(seconds=settings.recording_grace_s + 1))
    rec = org.c.get(f"/api/interviews/{itv['id']}/recordings", headers=org.h()).json()[0]
    assert rec["status"] == "failed" and "没有收到" in rec["error"]
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 409


def test_upload_permissions(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    u = f"/api/interviews/{itv['id']}/recording/{rid}/chunk?seq=0"
    assert org.c.post(u, content=b"x", headers=org.h("observer")).status_code == 403  # observers never record
    assert org.c.post(u, content=b"x").status_code in (401, 403)
    assert org.c.post(f"/api/interviews/{itv['id']}/recording/nope/chunk?seq=0", content=b"x", headers=org.h("moderator")).status_code == 404
    other = org.interview()  # participant of another interview cannot write into this recording
    give_consent(org, other)
    assert org.c.post(f"/api/public/join/{other['invite_token']}/recording/{rid}/chunk?seq=0", content=b"x").status_code == 404
    assert org.c.post(f"/api/public/join/bad-token/recording/{rid}/chunk?seq=0", content=b"x").status_code == 404
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=org.h("moderator"))
    with SessionLocal() as db:
        process_pending(db, now=utcnow() + timedelta(seconds=999))
    assert org.c.post(u, content=b"x", headers=org.h("moderator")).status_code == 409  # finished


def test_participant_cannot_upload_before_consent(org):
    itv = org.interview()
    go_live(org, itv, consent=False)
    with SessionLocal() as db:
        db.add(Recording(id="r1" + "0" * 30, org_id=itv_org(db, itv), interview_id=itv["id"], egress_id="client-x", storage_key="private/x/y/r1.mp4"))
        db.commit()
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/recording/{'r1' + '0' * 30}/chunk?seq=0", content=b"x").status_code == 404


def itv_org(db, itv):
    from app.models import Interview
    return db.get(Interview, itv["id"]).org_id


def test_live_state_for_room_pages(org):
    itv = org.interview()
    h = org.h("moderator")
    s = org.c.get(f"/api/interviews/{itv['id']}/live", headers=h).json()
    assert {k: s[k] for k in ("status", "consented", "recording")} == {"status": "scheduled", "consented": False, "recording": None}
    assert s["participant"] == {"waiting": False, "entered": False, "device_ok": False}
    rid = start_recording(org, itv)
    s = org.c.get(f"/api/interviews/{itv['id']}/live", headers=h).json()
    assert s["status"] == "live" and s["consented"] and s["recording"]["id"] == rid
    p = org.c.get(f"/api/public/join/{itv['invite_token']}/live").json()
    assert p["status"] == "live" and p["recording"]["id"] == rid
    assert org.c.get(f"/api/interviews/{itv['id']}/live", headers=org.h("observer")).status_code == 200
    org.c.post(f"/api/interviews/{itv['id']}/recording/stop", headers=h)
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/live").json()["recording"] is None


def test_transcript_from_tracks_then_insight_clip_share_with_watermark(org, media, client):
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 1000)
    rec = finish(org, itv)
    assert rec["status"] == "ready"
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 200
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    assert {s["speaker"] for s in segs} == {"moderator", "participant"}
    assert all(0 <= s["start_ms"] <= s["end_ms"] <= rec["duration_ms"] for s in segs)
    assert [s["start_ms"] for s in segs] == sorted(s["start_ms"] for s in segs)
    part = next(s for s in segs if s["speaker"] == "participant")
    r = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json={"theme": "信任", "statement": "怕假货", "evidence": [{"segment_id": part["id"], "quote": part["text_zh"][:8], "lang": "zh"}]})
    assert r.status_code == 201, r.text
    ins = r.json()
    org.c.post(f"/api/insights/{ins['id']}/approve", headers=org.h())
    ev = ins["evidence"][0]
    clip = org.c.post(f"/api/interviews/{itv['id']}/clips", headers=org.h(), json={"evidence_id": ev["id"]}).json()
    assert clip["end_ms"] <= rec["duration_ms"]
    sh = org.c.post(f"/api/clips/{clip['id']}/share", headers=org.h(), json={"watermark": "WM-TEST-123 客户甲"}).json()
    view = client.get("/api/public/share/" + sh["token"]).json()
    assert view["playback"]["download"] is False and "storage_key" not in json.dumps(view)
    r = client.get(view["playback"]["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    out = os.path.join(settings.media_dir, "_cut.mp4")
    open(out, "wb").write(r.content)
    d = probe(out)
    dur = float(d["format"]["duration"])
    assert abs(dur - (clip["end_ms"] - clip["start_ms"]) / 1000) < 0.6  # only the clip, not the whole recording
    # the watermark is really burned into the pixels: same frame cut without it must differ clearly
    from PIL import Image
    import numpy as np
    src, rec_id = recording_path(itv["id"])
    a, b = os.path.join(settings.media_dir, "_a.png"), os.path.join(settings.media_dir, "_b.png")
    t = 0.5
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", out, "-frames:v", "1", a], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(clip["start_ms"] / 1000 + t), "-i", src, "-frames:v", "1", b], check=True)
    A, B = np.asarray(Image.open(a).convert("L"), float), np.asarray(Image.open(b).convert("L"), float)
    assert A.shape == B.shape and (np.abs(A - B) > 25).sum() > 400, "watermark not visible in the cut"  # text pixels, not codec noise
    import shutil
    if os.getenv("KEEP_FRAMES"):
        shutil.copy(a, os.getenv("KEEP_FRAMES"))
    # cut is cached and cleaned on purge
    cache = os.path.join(settings.media_dir, "cache", rec_id)
    assert os.path.isdir(cache) and any(f.endswith(".mp4") for f in os.listdir(cache))
    assert org.c.post(f"/api/admin/retention/run", headers=org.h("admin")).status_code == 200
    from app.retention import purge_interview
    from app.models import Interview
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
    assert not os.path.exists(cache) and not os.path.exists(src) and not os.path.isdir(src.rsplit(".", 1)[0])


def test_internal_playback_links(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 0)
    rec = finish(org, itv)
    u = f"/api/interviews/{itv['id']}/recordings/{rec['id']}/play"
    assert org.c.post(u, headers=org.h("observer")).status_code == 403  # clients never watch the raw recording
    r = org.c.post(u, headers=org.h("researcher")).json()
    full = org.c.get(r["url"])
    assert full.status_code == 200 and len(full.content) > 1000


def test_openai_asr_and_translation_adapters(org, media, monkeypatch):
    """The real-provider code path, with the HTTP calls faked: language is passed, silence hallucinations are dropped, offsets applied, zh translated."""
    import httpx
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        req = httpx.Request("POST", url)
        if url.endswith("/audio/transcriptions"):
            assert kw["data"]["language"] == "en" and kw["data"]["model"] == "whisper-1"
            return httpx.Response(200, request=req, json={"language": "english", "segments": [
                {"start": 0.5, "end": 2.0, "text": " I check reviews first. ", "avg_logprob": -0.2, "no_speech_prob": 0.01},
                {"start": 2.0, "end": 3.0, "text": "Thanks for watching!", "avg_logprob": -1.6, "no_speech_prob": 0.9}]})
        body = json.loads(kw["json"]["messages"][0]["content"].split("\n\n", 1)[1])
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": json.dumps({"translations": ["译:" + t for t in body]})}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "asr_provider", "openai")
    monkeypatch.setattr(settings, "asr_api_key", "k")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 2000)
    finish(org, itv)
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 200
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()
    assert [(s["speaker"], s["start_ms"], s["text_orig"], s["text_zh"]) for s in segs] == [
        ("moderator", 500, "I check reviews first.", "译:I check reviews first."),
        ("participant", 2500, "I check reviews first.", "译:I check reviews first.")]


def test_asr_failure_and_missing_key_are_loud(org, media, monkeypatch):
    import httpx
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 0)
    finish(org, itv)
    monkeypatch.setattr(settings, "asr_provider", "openai")
    monkeypatch.setattr(settings, "asr_api_key", None)
    assert org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h()).status_code == 501
    monkeypatch.setattr(settings, "asr_api_key", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    r = org.c.post(f"/api/interviews/{itv['id']}/transcribe", headers=org.h())
    assert r.status_code == 502 and "转写服务" in r.text
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json() == []  # nothing half-written


def test_livekit_url_follows_the_address_the_page_was_opened_with(monkeypatch):
    from app.livekit import url_for_client
    monkeypatch.setattr(settings, "livekit_url", "ws://localhost:7880")
    assert url_for_client("192.168.1.8:8000") == "ws://192.168.1.8:7880"
    assert url_for_client("localhost:8000") == "ws://localhost:7880"
    monkeypatch.setattr(settings, "livekit_url", "wss://x.livekit.cloud")
    assert url_for_client("192.168.1.8:8000") == "wss://x.livekit.cloud"
    monkeypatch.setattr(settings, "livekit_url", None)
    assert url_for_client("a:1") is None
