"""End-to-end with a REAL LiveKit server and three real browsers (moderator, participant, hidden observer).
Run: python tests/e2e_room.py   (needs bin/livekit-server: scripts/get_livekit.sh)

Checks the whole journey a human tester will do: consent page -> room -> everybody sees/hears the right people -> the observer is
invisible and cannot publish -> start interview -> record (camera toggled off and on in the middle) -> stop -> server mixes the
tracks -> transcribe -> insight -> clip -> share page plays a short WATERMARKED cut -> end interview kicks everyone out.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_asr import FakeASR  # a stand-in for Tencent's real-time speech recognition (same protocol)
from qa_site import AUDIT_JS  # the same colour / font-size / tap-target audit the whole-site QA uses

# what the fake recogniser "hears", handed out in connection order: the moderator's browser connects first, then the participant's
ASR_SCRIPTS = [["我们先聊聊你平时的护肤习惯。", "你为什么会选这一款？"], ["我平时会先看成分，再看价格。", "我最担心买到假货。"]]
FAKE = FakeASR(ASR_SCRIPTS)
NET_JS = """(() => { const f = window.fetch; window.__net = []; window.__pend = {}; let n = 0;
  window.fetch = async (...a) => { const id = ++n, t0 = performance.now(), u = String(a[0]).slice(-38); window.__pend[id] = [u, t0];
    try { const r = await f(...a); window.__net.push([u, Math.round(performance.now() - t0), r.status]); return r; }
    catch (e) { window.__net.push([u, Math.round(performance.now() - t0), 'ERR ' + e]); throw e; }
    finally { delete window.__pend[id]; } }; })();"""  # records how long each of a page's own API calls took (printed when a caption check fails)
CAPSTATE = "() => ({on: CAP.on, tries: CAP.tries, ws: CAP.ws ? CAP.ws.readyState : null, ctx: CAP.ctx ? CAP.ctx.state : null, mic: !!micTrack, live: last && last.status, stopping: CAP.stopping, queued: CAP.queue.length, ended: ended})"  # what the room page's caption code is doing, printed when a caption check fails

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "qa_shots")
FAILS, PASSES = [], 0


def ok(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("  FAIL", msg)
    return cond


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT, LKPORT, LKTCP = free_port(), free_port(), free_port()
B = f"http://127.0.0.1:{PORT}"


def call(path, body=None, token=None, method=None, raw=False):
    req = urllib.request.Request(B + "/api" + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    d = urllib.request.urlopen(req).read()
    return d if raw else json.loads(d or b"null")


def audit(page, label, phone=False):
    """Colour contrast, font size, tap targets, wrapped labels, overflow, unnamed controls on a LIVE room page."""
    if phone:
        page.set_viewport_size({"width": 390, "height": 780})
    page.mouse.move(1, 1)  # a hovered button shows its sweep (a pseudo-element the audit cannot see), so park the pointer, as qa_site.py does
    page.wait_for_timeout(400)
    a = page.evaluate(AUDIT_JS)
    for k, what in (("small", "text too small"), ("contrast", "low contrast"), ("tap", "tap target < 36px"), ("names", "control without a name"), ("wrap", "wrapped label")):
        if k == "tap" and not phone:
            continue
        ok(not a[k], f"[{label}] {what}: {a[k][:3]}")
    ok(a["overflow"] <= 0, f"[{label}] horizontal overflow {a['overflow']}px")
    if phone:
        page.set_viewport_size({"width": 1280, "height": 760})


PUMP = [None]  # a page whose events are serviced between polls: Playwright's sync API runs route handlers and listeners only while the test thread is
#                inside a Playwright call, so a plain time.sleep() would leave every request of a context that has a route paused (this cost 45 s once)


def wait_for(fn, timeout=60, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        if PUMP[0] is not None:
            PUMP[0].wait_for_timeout(int(step * 1000))
        else:
            time.sleep(step)
    return None


def main():
    os.makedirs(SHOTS, exist_ok=True)
    tmp = tempfile.mkdtemp()
    lk_bin = os.path.join(ROOT, "bin", "livekit-server")
    if not os.path.isfile(lk_bin) and os.path.isfile(lk_bin + ".exe"):  # Windows build
        lk_bin += ".exe"
    if not os.path.isfile(lk_bin):
        print("bin/livekit-server missing: run scripts/get_livekit.sh"); sys.exit(2)
    rtc0 = 53000 + (PORT % 1000) * 2
    open(os.path.join(tmp, "lk.yaml"), "w").write(f"""port: {LKPORT}
rtc:
  tcp_port: {LKTCP}
  port_range_start: {rtc0}
  port_range_end: {rtc0 + 200}
  use_external_ip: false
keys:
  devkey: devsecret-devsecret-devsecret-0000
webhook:
  api_key: devkey
  urls: ["{B}/api/public/livekit/webhook"]
logging:
  level: warn
""")
    lk = subprocess.Popen([lk_bin, "--config", os.path.join(tmp, "lk.yaml")], stdout=open(os.path.join(tmp, "lk.log"), "w"), stderr=subprocess.STDOUT)
    asr_port = FAKE.start()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/e2e.db", "MEDIA_DIR": f"{tmp}/media", "LIVEKIT_URL": f"ws://127.0.0.1:{LKPORT}", "RECORDING_GRACE_S": "25",
           "DISABLE_WORKER": "", "ASR_PROVIDER": "mock", "LLM_PROVIDER": "mock", "ASR_API_KEY": "", "LLM_API_KEY": "",
           "MODERATOR_PROVIDER": "mock", "MODERATOR_API_KEY": "", "MODERATOR_VENDOR": "",
           "TENCENT_ASR_APPID": "1250000000", "TENCENT_ASR_SECRET_ID": "AKIDe2e", "TENCENT_ASR_SECRET_KEY": "e2e-secret-key", "ASR_LIVE_WS_BASE": f"ws://127.0.0.1:{asr_port}",
           "LOOP_WATCH_S": "2"}  # the server names whatever blocks its event loop (printed below if it happens)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env,
                           stdout=open(os.path.join(tmp, "srv.log"), "w"), stderr=subprocess.STDOUT)
    try:
        if not wait_for(lambda: urllib.request.urlopen(B + "/api/health").status == 200, 20):
            print("server did not start"); sys.exit(2)
        if os.environ.get("E2E_ONLY") == "ai":
            run_ai(tmp)
        else:
            run(tmp)
            run_group(tmp)
            run_ai(tmp)
    finally:
        FAKE.stop()
        srv.terminate(); lk.terminate()
        try:
            srv.wait(5); lk.wait(5)
        except Exception:
            srv.kill(); lk.kill()
    errs_srv = [ln for ln in open(os.path.join(tmp, "srv.log"), errors="ignore").read().splitlines() if re.search(r"Error|Traceback|locked|Exception", ln) and "slow request" not in ln]
    if errs_srv:
        print(f"  SERVER LOG: {len(errs_srv)} error lines, first: " + " || ".join(x.strip()[:140] for x in errs_srv[:4]))
    slow_reqs = [ln for ln in open(os.path.join(tmp, "srv.log"), errors="ignore").read().splitlines() if "slow request" in ln]
    if slow_reqs:
        print(f"  SLOW REQUESTS: {len(slow_reqs)}; first and worst:")
        print("   " + slow_reqs[0][:300]); print("   " + max(slow_reqs, key=lambda x: float(x.split("slow request ")[1].split("s")[0]))[:300])
    stalls = open(os.path.join(tmp, "srv.log"), errors="ignore").read().split("event loop blocked")[1:]  # see app/loopwatch.py
    secs = [float(s.split("more than ")[1].split("s")[0]) for s in stalls if "more than " in s]
    if secs:
        print(f"  SERVER STALLS: {len(secs)} times the event loop was blocked, seconds: {secs}")
        print("  worst one:" + stalls[secs.index(max(secs))][:5000])
    print(f"\nE2E ROOM: {PASSES} checks passed, {len(FAILS)} failed")
    if FAILS:
        print("\n".join(" - " + f for f in FAILS)); sys.exit(1)


def run(tmp):
    reg = call("/auth/register", {"org_name": "Acme", "name": "Admin", "email": "a@acme.com", "password": "password123"})
    adm = reg["token"]
    ids = {}
    for role in ("moderator", "observer", "researcher"):
        ids[role] = call("/users", {"name": {"moderator": "Mika Moderator", "observer": "Olive Observer", "researcher": "Rae Researcher"}[role], "email": f"{role}@acme.com", "password": "password123", "role": role}, adm)["id"]
    tok = {r: call("/auth/login", {"email": f"{r}@acme.com", "password": "password123"})["token"] for r in ids}
    pid = call("/projects", {"name": "Skincare SG", "market": "SG", "language": "en"}, tok["researcher"])["id"]
    part = call(f"/projects/{pid}/participants", {"name": "Wei Ling", "country": "SG", "language": "en"}, tok["researcher"])
    itv = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "深访 1", "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}, tok["researcher"])
    iid = itv["id"]

    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        errs = []

        def ctx(name, token=None):
            c = br.new_context(viewport={"width": 1280, "height": 760}, permissions=["camera", "microphone"])
            if token:
                c.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
            p = c.new_page()
            p.on("console", lambda m: errs.append(f"[{name}] console: {m.text}") if m.type == "error" else None)
            p.on("pageerror", lambda e: errs.append(f"[{name}] pageerror: {e}"))
            p.on("dialog", lambda d: d.accept())
            return c, p

        # ---- participant: invitation -> device test (fake camera + mic) -> consent -> waiting room ----
        st = lambda: call(f"/interviews/{iid}", token=tok["researcher"])["invite_state"]
        cp, pp = ctx("participant")
        pp.goto(f"{B}/join.html?t={itv['invite_token']}")
        pp.wait_for_selector("#accept")
        ok(wait_for(lambda: st() == "opened", 5), "staff sees the invitation as opened")
        pp.click("#accept")
        pp.wait_for_selector("#devnext:not([disabled])", timeout=20000)
        ok("Working" in pp.inner_text("#c_cam") and "Working" in pp.inner_text("#c_mic"), "device test: camera and microphone work")
        # the preview's size is known only once its first frame has decoded, which on a busy machine can come after the checks finish
        ok(wait_for(lambda: pp.evaluate("() => { const v = document.querySelector('#devv'); return !v.hidden && v.videoWidth > 0; }"), 5),
           "device test shows the participant's own camera")
        pp.click("#play"); ok("Played" in pp.inner_text("#c_spk"), "device test plays a sound for the speaker check")
        pp.screenshot(path=f"{SHOTS}/e2e_device_test.png")
        pp.click("#devnext")
        pp.wait_for_selector("form#f")
        ok(st() == "device_ok", "staff sees the device test as passed")
        pp.select_option("select[name=country]", "SG")
        for n in ("recording", "observers", "cross_border"):
            pp.check(f"input[name={n}]")
        ok("Singapore" in pp.inner_text("form.card") and "China" in pp.inner_text("form.card"), "consent page says data is stored in Singapore and accessed from China")
        pp.screenshot(path=f"{SHOTS}/e2e_consent.png")
        pp.click("form button")
        pp.wait_for_selector("#pw", timeout=20000)
        ok(wait_for(lambda: st() == "consented", 10) and "room.html" not in pp.url, "after consent the participant waits at the door: not in the room until the moderator lets them in")
        # ---- moderator + observer ----
        cm, pm = ctx("moderator", tok["moderator"])
        pm.goto(f"{B}/room.html?i={iid}")
        co, po = ctx("observer", tok["observer"])
        po.goto(f"{B}/room.html?i={iid}&as=observer")
        ok(wait_for(lambda: pm.is_visible("#b-start") and pm.is_disabled("#b-rec"), 15), "record button disabled before the interview starts")
        ok(wait_for(lambda: pm.is_visible("#admitbar") and "已到" in pm.inner_text("#admitwho"), 15), "moderator sees the participant at the door with a release button")
        ok(not po.is_visible("#admitbar"), "the observer has no release bar")
        pm.screenshot(path=f"{SHOTS}/e2e_room_admit.png")
        pm.click("#b-admit")
        pp.wait_for_url("**/room.html*", timeout=30000)
        ok(wait_for(lambda: st() == "entered", 10), "staff sees the participant in the room once the moderator has let them in")
        ok(wait_for(lambda: "moderator" in pp.inner_text("#msg").lower(), 15), "inside the room the participant is told the moderator will start soon")
        ok(wait_for(lambda: "已在房间" in pm.inner_text("#wst"), 10) and pm.is_hidden("#admitbar"), "moderator sees the participant in the room and the release bar is gone")
        ok(not po.is_visible("#wbox"), "the observer does not see who is in the room")
        ok("t-light" in pm.evaluate("document.body.className") and "t-light" in po.evaluate("document.body.className"), "rooms open in the white light theme by default")
        pp.screenshot(path=f"{SHOTS}/e2e_before_start.png")
        pm.click("#b-start")

        def videos(page):
            return page.evaluate("""() => [...document.querySelectorAll('.tile')].map(t => { const v = t.querySelector('video'); return {id: t.id, w: v ? v.videoWidth : 0, playing: v ? (!v.paused && v.readyState >= 2) : false, name: t.querySelector('.nm').textContent}; })""")

        def ready(page, n):
            return lambda: (lambda vs: len(vs) == n and all(v["w"] > 0 and v["playing"] for v in vs))(videos(page))

        ok(wait_for(ready(pm, 2), 30), "moderator sees own + participant video playing")
        ok(wait_for(ready(pp, 2), 30), "participant sees own + moderator video playing")
        ok(wait_for(ready(po, 2), 30), "observer sees moderator + participant video playing")
        vm, vp, vo = videos(pm), videos(pp), videos(po)
        ok(sorted(v["id"] for v in vm) == sorted(["t_me", vm[0]["id"] if vm[0]["id"] != "t_me" else vm[1]["id"]]) and len(vm) == 2, "moderator has exactly 2 tiles (observer invisible)")
        ok(len(vp) == 2 and len(vo) == 2, "participant 2 tiles; observer 2 tiles")
        ok("t_me" not in [v["id"] for v in vo], "observer has no self tile (does not publish)")
        ok(all(v["name"] != "Olive Observer" for v in vm + vp), "nobody sees the observer's name")
        leak = po.evaluate("() => JSON.stringify([...room.remoteParticipants.values()].map(p => [p.identity, p.name, p.metadata]))")
        ok("Wei Ling" not in leak and "Wei Ling" not in po.content(), "observer's browser never receives the participant's real name")
        ok("受访者" in [v["name"] for v in vo] and "受访者" in [v["name"] for v in vm], "participant tile is labelled 受访者")
        ok("Mika" not in " ".join(v["name"] for v in vp), "participant never sees the moderator's real name")
        perms = po.evaluate("() => { const p = room.localParticipant.permissions; return {pub: p.canPublish, data: p.canPublishData, hidden: p.hidden}; }")
        ok(perms == {"pub": False, "data": False, "hidden": True}, f"observer permissions are subscribe-only+hidden (got {perms})")
        ok(po.evaluate("() => room.localParticipant.trackPublications.size") == 0, "observer publishes nothing")
        ok(pm.evaluate("() => room.remoteParticipants.size") == 1 and pp.evaluate("() => room.remoteParticipants.size") == 1, "moderator and participant each see exactly one other person")
        audio_ok = lambda page: page.evaluate("() => [...room.remoteParticipants.values()].every(p => [...p.audioTrackPublications.values()].every(a => a.isSubscribed))")
        ok(audio_ok(pm) and audio_ok(pp) and audio_ok(po), "audio tracks are subscribed everywhere")
        pm.screenshot(path=f"{SHOTS}/e2e_room_moderator.png"); pp.screenshot(path=f"{SHOTS}/e2e_room_participant.png"); po.screenshot(path=f"{SHOTS}/e2e_room_observer.png")
        ok(st() == "entered", "staff sees the participant as entered")
        ok(pp.locator("#lpan:visible, #rpan:visible").count() == 0 and pm.is_visible("#q4") and pm.locator("#lpan:visible, #rpan:visible").count() == 0, "the four-column room is for staff only, never for the participant")
        ok("Wei Ling" not in pm.inner_text("#q4") and "Wei Ling" not in po.inner_text("body"), "no participant name in the room panels")

        # ---- run the interview ----
        ok(wait_for(lambda: "in progress" in pp.inner_text("#msg"), 10), "participant sees the interview started")
        ok(wait_for(lambda: not pm.is_disabled("#b-rec"), 10), "record button enabled once live + consented")
        ok(pm.is_disabled("#b-start"), "start button disabled once live")
        pm.click("#b-rec")
        ok(wait_for(lambda: pp.is_visible("#recdot") and "Recording" in pp.inner_text("#recdot"), 10), "participant sees the recording indicator")
        ok(wait_for(lambda: po.is_visible("#recdot"), 10), "observer also sees it is being recorded")  # the observer page polls every 2 s
        ok(wait_for(lambda: pm.is_disabled("#b-rec") and not pm.is_disabled("#b-stop"), 10), "buttons switch to stop state")
        time.sleep(5)
        pp.click("#b-cam")  # participant turns the camera off for a while, then on
        ok(pp.inner_text("#b-cam") == "Camera off", "camera button shows off state")
        ok(pp.is_visible("#t_me .off"), "participant sees own 'camera off' cover")
        time.sleep(3)
        pp.click("#b-cam")
        time.sleep(3)
        pp.reload()  # the participant refreshes by accident in the middle of the recording
        ok(wait_for(ready(pp, 2), 30), "participant is back in the room with video after a reload mid-recording")
        ok(wait_for(lambda: pp.is_visible("#recdot"), 10), "recording indicator is shown again after the reload")
        time.sleep(4)
        pm.screenshot(path=f"{SHOTS}/e2e_room_recording.png")
        for page, name in ((pm, "moderator"), (pp, "participant"), (po, "observer")):  # both colour themes: light (default) and dark green
            audit(page, f"live room {name}/light/desktop"); audit(page, f"live room {name}/light/phone", phone=True)
            page.click("#theme")
            ok("t-dark" in page.evaluate("document.body.className"), f"{name}: switching to dark green works")
            audit(page, f"live room {name}/dark/desktop"); audit(page, f"live room {name}/dark/phone", phone=True)
            page.screenshot(path=f"{SHOTS}/e2e_room_{name}_dark.png")
            page.click("#theme")
        pp.set_viewport_size({"width": 390, "height": 780}); pp.screenshot(path=f"{SHOTS}/e2e_room_participant_phone.png"); pp.set_viewport_size({"width": 1280, "height": 760})
        pm.click("#b-stop")
        ok(wait_for(lambda: not pp.is_visible("#recdot"), 10), "indicator disappears after stop")
        recs = wait_for(lambda: (lambda r: r if r and r[0]["status"] == "ready" else None)(call(f"/interviews/{iid}/recordings", token=tok["researcher"])), 90)
        ok(bool(recs), f"recording became ready ({call(f'/interviews/{iid}/recordings', token=tok['researcher'])})")
        if not recs:
            return finish(br, errs)
        rec = recs[0]
        ok(14_000 <= rec["duration_ms"] <= 40_000, f"mixed recording length is plausible (got {rec['duration_ms']} ms)")
        media_dir = os.path.join(tmp, "media")
        mp4 = [os.path.join(dp, f) for dp, _, fs in os.walk(media_dir) for f in fs if f.endswith(".mp4")]
        ok(len(mp4) == 1, "one mixed mp4 exists")
        if mp4:
            info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", mp4[0]], capture_output=True, text=True).stdout)
            kinds = {s["codec_type"] for s in info["streams"]}
            ok(kinds == {"video", "audio"}, "mp4 has video and audio")
            vs = next(s for s in info["streams"] if s["codec_type"] == "video")
            ok((vs["width"], vs["height"]) == (1280, 360), "two speakers side by side")
            frame = os.path.join(SHOTS, "e2e_mixed_frame.png")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "3", "-i", mp4[0], "-frames:v", "1", frame], check=True)
            vol = subprocess.run(["ffmpeg", "-v", "info", "-i", mp4[0], "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
            ok("mean_volume" in vol and "mean_volume: -91" not in vol, "mixed audio is not silent")
            # per-speaker tracks: both browsers uploaded
            tracks = sorted(f for f in os.listdir(os.path.dirname(mp4[0]) + "/" + os.path.basename(mp4[0])[:-4]) if f.endswith(".mkv"))
            ok(len([t for t in tracks if t.startswith("moderator.")]) == 1 and len([t for t in tracks if t.startswith("participant.")]) == 2, f"moderator 1 stream, participant 2 streams (reload) were uploaded ({tracks})")

        # ---- after the interview: transcript -> insight -> clip -> share ----
        call(f"/interviews/{iid}/transcribe", {}, tok["researcher"])
        segs = call(f"/interviews/{iid}/transcript", token=tok["researcher"])
        ok({s["speaker"] for s in segs} == {"moderator", "participant"}, "transcript has both speakers")
        gen = call(f"/interviews/{iid}/insights/generate", {}, tok["researcher"])
        ok(len(gen["created"]) >= 1, "insights generated with verified evidence")
        ins = gen["created"][0]
        call(f"/insights/{ins['id']}/approve", {}, tok["researcher"])
        clip = call(f"/interviews/{iid}/clips", {"evidence_id": ins["evidence"][0]["id"]}, tok["researcher"])
        sh = call(f"/clips/{clip['id']}/share", {"expires_hours": 24, "watermark": "星桐 · acme-client@example.com"}, tok["researcher"])
        cs, ps = ctx("share")
        ps.goto(B + sh["path"])
        src = wait_for(lambda: ps.evaluate("() => document.querySelector('video') && document.querySelector('video').getAttribute('src')"), 20)
        ok(bool(src), "share page has a video")
        r = ps.request.get(B + src.split("#")[0])
        ok(r.status == 200 and r.headers["content-type"] == "video/mp4", f"share media endpoint serves mp4 ({r.status})")
        ranged = ps.request.get(B + src.split("#")[0], headers={"Range": "bytes=0-99"})
        ok(ranged.status == 206, f"media supports Range requests, which browsers use for seeking ({ranged.status})")
        cut = os.path.join(tmp, "share_cut.mp4"); open(cut, "wb").write(r.body())
        dur = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", cut], capture_output=True, text=True).stdout)["format"]["duration"])
        want = (clip["end_ms"] - clip["start_ms"]) / 1000
        ok(abs(dur - want) < 1.0, f"client receives only the clip: {dur:.1f}s (clip is {want:.1f}s, recording is {rec['duration_ms'] / 1000:.1f}s)")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", cut, "-frames:v", "1", f"{SHOTS}/e2e_share_cut_frame.png"], check=True)
        h264 = ps.evaluate("() => document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')")
        if h264:  # the test browser is a plain Chromium without H.264; real Chrome/Edge/Safari/Firefox have it
            okv = wait_for(lambda: ps.evaluate("() => { const v = document.querySelector('video'); return v.readyState >= 2 && v.videoWidth > 0 ? v.duration : 0; }"), 40)
            ok(bool(okv), "share page video actually plays in this browser")
        else:
            print("  note: this test browser cannot decode H.264, so in-page playback was checked via the media response + ffprobe instead")
        ok(ps.evaluate("() => document.querySelector('video').controlsList.contains('nodownload')"), "share video has no download control")
        ps.screenshot(path=f"{SHOTS}/e2e_share.png")
        # staff watching the full recording
        pr = cm.new_page()
        play = call(f"/interviews/{iid}/recordings/{rec['id']}/play", {}, tok["researcher"])
        ok(len(urllib.request.urlopen(B + play["url"]).read()) > 10_000, "staff can open the full recording")

        # ---- the researcher's own pages after the interview ----
        cr, pr2 = ctx("researcher", tok["researcher"])
        pr2.goto(f"{B}/#/i/{iid}/transcript")
        pr2.wait_for_selector(".rsp .rsp-ln")
        ok(bool(wait_for(lambda: pr2.locator("#rsp-v").count() == 1 and pr2.get_attribute("#rsp-v", "src"), 15)), "researcher gets a player for the full recording in the respondent window")
        ok(pr2.locator(".rsp-ln").count() == len(segs), "every transcript line is there and a click on it seeks the video")
        pr2.locator(".rsp-ln").nth(min(2, len(segs) - 1)).click()
        ok(wait_for(lambda: pr2.evaluate("document.getElementById('rsp-v').currentTime") > 0 or len(segs) < 3, 5), "clicking a line moves the video to that time")
        ok(not pr2.is_disabled("#tr"), "transcribe button is enabled once the recording is ready")
        pr2.screenshot(path=f"{SHOTS}/e2e_staff_transcript.png", full_page=True)
        # proofread a line that an approved insight quotes -> that insight must flip to "needs review"
        quoted = ins["evidence"][0]["segment_idx"]
        pr2.locator("[data-edit]").nth(quoted).click()
        pr2.wait_for_selector("form#f textarea[name=text_zh]")
        pr2.fill("form#f textarea[name=text_zh]", "（人工校对后的中文）")
        pr2.click("form#f button:has-text('保存')")
        pr2.wait_for_selector(".toast:has-text('需要重新复核')")
        ok(call(f"/interviews/{iid}/insights", token=tok["researcher"])[0]["status"] == "needs_review", "editing the quoted line flips the approved insight to needs_review")
        ok(not any(i["status"] == "approved" for i in call(f"/interviews/{iid}/insights", token=tok["researcher"])), "clients no longer see the insight whose quote vanished")
        # write a new insight by hand from a transcript line (the quote is checked against the transcript)
        pr2.goto(f"{B}/#/i/{iid}/transcript")
        pr2.wait_for_selector("[data-ins]")
        pr2.locator("[data-ins]").nth(1).click()
        pr2.fill("form#f input[name=theme]", "比价习惯")
        pr2.fill("form#f textarea[name=statement]", "受访者会先看评价再比价")
        pr2.click("form#f button:has-text('保存洞察')")
        pr2.wait_for_selector(".card:has-text('比价习惯')")
        ok(any(i["theme"] == "比价习惯" and i["kind"] == "manual" for i in call(f"/interviews/{iid}/insights", token=tok["researcher"])), "a hand-written insight with verified evidence is saved")
        pr2.goto(f"{B}/#/i/{iid}/transcript"); pr2.wait_for_selector("[data-ins]")
        pr2.locator("[data-ins]").nth(1).click()
        pr2.fill("form#f input[name=theme]", "编造"); pr2.fill("form#f textarea[name=statement]", "x")
        pr2.fill("form#f textarea[name=quote]", "this sentence was never said by anyone")
        n0 = len(errs)
        pr2.click("form#f button:has-text('保存洞察')")
        pr2.wait_for_selector(".toast.err")
        del errs[n0:]  # the browser logs the deliberate 422 as a console error: expected here
        ok(not any(i["theme"] == "编造" for i in call(f"/interviews/{iid}/insights", token=tok["researcher"])), "a fabricated quote is refused in the UI")

        pr2.goto(f"{B}/#/i/{iid}/clips")
        pr2.wait_for_selector("[data-pv]")
        pr2.click("[data-pv]")
        pr2.wait_for_selector("#cv")
        ok(bool(pr2.get_attribute("#cv", "src")) and "api/public/media" in pr2.get_attribute("#cv", "src"), "clip preview opens a player")
        pr2.screenshot(path=f"{SHOTS}/e2e_staff_clip_preview.png")
        pr2.click("#cx")
        pr2.click("[data-sl]")
        pr2.wait_for_selector("[data-rv]")
        ok("有效" in pr2.inner_text("[id^=sl_]"), "share records list the active link")
        pr2.click("[data-rv]")  # dialog auto-accepted
        pr2.wait_for_selector(".tag.bad:has-text('已撤销')")
        n0 = len(errs)
        ok(ps.evaluate("async (u) => (await fetch(u)).status", B + "/api/public/share/" + sh["token"]) == 404, "a revoked share link stops working immediately")
        time.sleep(0.5); del errs[n0:]  # deliberate 404
        cr.close()

        # ---- end: everybody is removed ----
        n_before_end = len(errs)
        pm.click("#b-end")
        ok(wait_for(lambda: "thank you" in pp.inner_text("#msg").lower(), 15), "participant sees thank-you after the interview ends")
        ok(wait_for(lambda: pp.evaluate("() => room.state") == "disconnected", 15), "participant is disconnected from the video room")
        ok(wait_for(lambda: po.evaluate("() => room.state") == "disconnected", 15), "observer is disconnected too")
        ok(st() == "done", "staff sees the session as completed")
        try:
            call(f"/interviews/{iid}/token", {}, tok["moderator"])
            ok(False, "ended interview must refuse new tokens")
        except urllib.error.HTTPError as e:
            ok(e.code == 409, "ended interview refuses new tokens")
        pm.screenshot(path=f"{SHOTS}/e2e_room_ended.png")

        # ---- AI moderation (Sprint 5): the same three real browsers + real video room, the moderator is the AI ----
        part2 = call(f"/projects/{pid}/participants", {"name": "Priya Nair", "country": "SG", "language": "en"}, tok["researcher"])
        ai = call(f"/projects/{pid}/interviews", {"participant_id": part2["id"], "title": "AI 深访", "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]],
                                                  "moderation": "ai"}, tok["researcher"])
        aid, atk = ai["id"], ai["invite_token"]
        call(f"/public/join/{atk}/device", {"camera": True, "microphone": True, "speaker": True})
        ok(urllib.request.urlopen(urllib.request.Request(f"{B}/api/public/join/{atk}")).status == 200, "AI interview: the invitation opens")
        try:
            call(f"/public/join/{atk}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
            ok(False, "AI interview: consent without the AI box must be refused")
        except urllib.error.HTTPError as e:
            ok(e.code == 422, "AI interview: consent without the AI box is refused")
        call(f"/public/join/{atk}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
        call(f"/interviews/{aid}/start", {}, tok["moderator"])
        cam, pam = ctx("ai-moderator", tok["moderator"])
        cap, pap = ctx("ai-participant")
        cao, pao = ctx("ai-observer", tok["observer"])
        pam.goto(f"{B}/room.html?i={aid}&as=moderator"); pap.goto(f"{B}/room.html?t={atk}"); pao.goto(f"{B}/room.html?i={aid}&as=observer")
        in_room = lambda pg: pg.evaluate("() => room && room.state === 'connected'")
        ok(wait_for(lambda: in_room(pam) and in_room(pap) and in_room(pao), 30), "AI interview: everybody is in the real video room")
        sizes = lambda: (pam.evaluate("() => room.remoteParticipants.size"), pap.evaluate("() => room.remoteParticipants.size"))
        ok(wait_for(lambda: sizes() == (1, 0), 15), f"AI interview: the team only watches: it sees the participant, and the participant sees nobody from the team ({sizes()})")  # "connected" does not mean the other is listed yet
        pam.wait_for_timeout(1500)
        ok(sizes() == (1, 0), f"AI interview: the team stays invisible a moment later ({sizes()})")
        ok(pao.locator("#a-start, #a-take, #a-back, #wform, #hform").count() == 0, "AI interview: the observer has no controls")
        ok("还没有启动" in pam.inner_text("#aist") and pap.is_disabled("#cin"), "AI interview: nothing is said and the participant cannot type before the AI is started")
        pam.click("#a-start")
        ok(wait_for(lambda: "AI moderator" in pap.inner_text("#clog") and "not a real person" in pap.inner_text("#clog"), 15),
           "AI interview: the participant sees the AI's opening, which says it is an AI")
        ok(wait_for(lambda: pap.is_enabled("#cin"), 10), "AI interview: the participant can type")
        pap.fill("#cin", "I usually order lunch on Grab because it is quick."); pap.click("#csend")
        ok(wait_for(lambda: pap.locator("#clog .m").count() >= 3, 20), "AI interview: the participant's answer is followed by the AI's next question")
        ok("Grab" in pao.inner_text("#clog") or wait_for(lambda: "Grab" in pao.inner_text("#clog"), 10), "AI interview: the hidden observer follows the conversation")
        pam.fill("#win", "Ask about delivery fees next"); pam.click("#wsend")
        ok(wait_for(lambda: "Ask about delivery fees" in pam.inner_text("#clog"), 10), "AI interview: the whisper is in the team's log")
        ok("delivery fees" not in pap.inner_text("body") and "delivery fees" not in pao.inner_text("body"), "AI interview: whispers never reach the participant or the observer")
        pam.click("#a-take")
        ok(wait_for(lambda: pam.is_visible("#hform"), 10), "AI interview: after taking over the moderator can reply")
        n = pap.locator("#clog .m").count(); pap.fill("#cin", "Let me think about it."); pap.click("#csend")
        lines = lambda: [x[:40] for x in pap.locator("#clog .m").all_inner_texts()]
        ok(wait_for(lambda: pap.locator("#clog .m").count() == n + 1, 10), f"AI interview: the participant's line is stored while a human has control (before {n}, now {lines()[n - 1:]})")
        time.sleep(3); ok(pap.locator("#clog .m").count() == n + 1, f"AI interview: the AI stays silent while a human has control (before {n}, now {lines()[n - 1:]})")
        pam.fill("#hin", "Take your time, there is no rush."); pam.click("#hsend")
        ok(wait_for(lambda: "Take your time" in pap.inner_text("#clog"), 10), "AI interview: the moderator's own line reaches the participant")
        pam.click("#a-back")
        ok(wait_for(lambda: "AI 正在主持" in pam.inner_text("#aist"), 10), "AI interview: the AI takes over again after the hand back")
        pap.fill("#cin", "Honestly I want to end my life."); pap.click("#csend")
        ok(wait_for(lambda: pam.is_visible("#aalert") and "红色" in pam.inner_text("#aalert"), 15), "AI interview: a crisis raises a red alert for the moderator")
        ok(wait_for(lambda: "995" in pap.inner_text("#clog"), 10), "AI interview: the participant gets the fixed safety message with the hotline")
        ok("红色" not in pap.inner_text("body") and "红色" not in pao.inner_text("body"), "AI interview: the alert is for the team only")
        pam.screenshot(path=f"{SHOTS}/e2e_ai_room_moderator.png"); pap.screenshot(path=f"{SHOTS}/e2e_ai_room_participant.png")
        for name, page in (("ai-moderator", pam), ("ai-participant", pap)):  # both colour themes in the real room too
            page.click("#theme"); ok("t-dark" in page.evaluate("document.body.className"), f"{name}: dark green works in the AI room")
            audit(page, f"{name}/ai-room/dark")
            page.click("#theme")
        pam.click("#b-end")
        ok(wait_for(lambda: "thank you" in pap.inner_text("#msg").lower(), 15), "AI interview: the participant sees the thank-you after the end")
        aseg = call(f"/interviews/{aid}/transcript", token=tok["researcher"])
        ok({"ai_moderator", "participant", "moderator"} <= {s["speaker"] for s in aseg}, f"AI interview: the transcript labels the AI, the human moderator and the participant ({sorted({s['speaker'] for s in aseg})})")
        ok(not any("delivery fees" in s["text_orig"] for s in aseg), "AI interview: whispers are not in the transcript")
        for c in (cam, cap, cao):
            c.close()

        concept_phase(pw, tok, ids, pid, errs)  # its console errors join the same list (data-channel errors after the end are tolerated by finish())
        finish(br, errs, n_before_end)


def slow(page):
    """A page's own API calls: how many, the failures, the slowest, and the ones still waiting (path, ms or age, status)."""
    try:
        d = page.evaluate("() => ({net: window.__net || [], pend: Object.values(window.__pend || {}).map(([u, t0]) => [u, Math.round(performance.now() - t0)])})")
        bad = [x for x in d["net"] if not str(x[2]).startswith("2")][:6]
        worst = sorted(d["net"], key=lambda x: -x[1])[:4]
        return f"calls={len(d['net'])} not-2xx={bad} slowest={worst} still-waiting={d['pend'][:6]}"
    except Exception as e:
        return f"n/a ({e})"


def timeline():
    t0 = FAKE.phase[0]["at"] if FAKE.phase else 0
    return [(round(c["at"] - t0, 1), c["frames"], c["bad_frames"], c["loud_frames"], c["ended"], len(c["sent"])) for c in FAKE.phase]


def concept_phase(pw, tok, ids, pid, errs):
    """The concept test as a human tester runs it: script + materials, the live captions of two real microphones, quotes and follow-ups."""
    part = call(f"/projects/{pid}/participants", {"name": "概念测试受访者", "country": "SG", "language": "zh"}, tok["researcher"])
    itv = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "护肤概念测试", "language": "zh", "moderator_id": ids["moderator"],
                                               "observer_ids": [ids["observer"]]}, tok["researcher"])
    iid, T = itv["id"], itv["invite_token"]
    FAKE.mark()  # earlier phases (English interviews) also opened recognition sessions: count and hand out scripts from here
    call(f"/interviews/{iid}/concept/template", {"template": "loreal_skincare_concept"}, tok["researcher"])
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    # NOT the autoplay override the other phases use: the real browser rules apply to the caption microphone (the participant has clicked "enter")
    br = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])

    def ctx(name, token=None):
        c = br.new_context(viewport={"width": 1280, "height": 900}, permissions=["camera", "microphone"])
        if token:
            c.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
        c.add_init_script(NET_JS)  # time this page's own API calls
        c.route("https://player.bilibili.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<html><body>video player (test stub)</body></html>"))
        p = c.new_page()
        p.on("console", lambda m: errs.append(f"[{name}] console: {m.text}") if m.type == "error" else None)
        p.on("pageerror", lambda e: errs.append(f"[{name}] pageerror: {e}"))
        p.on("dialog", lambda d: d.accept())
        return c, p

    cm, pm = ctx("concept-moderator", tok["moderator"])
    co, po = ctx("concept-observer", tok["observer"])
    cp, pp = ctx("concept-participant")
    pm.goto(f"{B}/room.html?i={iid}&as=moderator"); po.goto(f"{B}/room.html?i={iid}&as=observer")
    PUMP[0] = pm  # see wait_for(): this phase's contexts have a route, which must keep being serviced while we wait
    in_room = lambda pg: pg.evaluate("() => room && room.state === 'connected'")
    ok(wait_for(lambda: in_room(pm) and in_room(po), 30), "concept test: the moderator and the hidden observer are in the real video room")

    # ---- the script and the materials ----
    pm.wait_for_selector("#plan details", timeout=15000)
    ok(pm.locator("#plan details").count() == 5 and pm.locator("#plan button[data-show]").count() == 3, "concept test: the moderator sees the 5-step script with 3 materials to show")
    ok("访谈开始后" in pm.inner_text("#plantm") and po.locator("#planbox").is_hidden(), "concept test: the timer waits for the start; the observer has no script")
    pm.screenshot(path=f"{SHOTS}/e2e_concept_script.png")

    # ---- start: the moderator's microphone is captioned ----
    pm.click("#b-start")
    ok(wait_for(lambda: "已用" in pm.inner_text("#plantm"), 10), "concept test: after the start the script shows the elapsed time and the current step")
    ok(wait_for(lambda: len(FAKE.phase) >= 1, 25), "concept test: the moderator's browser opened a speech-recognition session")
    first = FAKE.phase[0] if FAKE.phase else {"query": {}}
    ok(first["query"].get("engine_model_type") == "16k_zh" and first["query"].get("voice_format") == "1" and "e2e-secret-key" not in str(first["query"]),
       f"concept test: signed Mandarin 16k PCM address, and the secret key never reached the browser ({sorted(first['query'])})")
    ok(wait_for(lambda: "字幕已开启" in pm.inner_text("#capst"), 10), "concept test: the caption panel says captions are on")

    # ---- the participant arrives the way a person does: invitation page -> waiting room -> "enter" (a real click) ----
    call(f"/interviews/{iid}/admit", {}, tok["moderator"])   # the moderator lets them in, once
    pp.goto(f"{B}/join.html?t={T}")
    pp.wait_for_url("**/room.html*", timeout=20000)   # consented, device-tested and let in: the invitation page sends them straight in
    ok(wait_for(lambda: in_room(pp), 30), "concept test: the participant is in the real video room")
    two = wait_for(lambda: len(FAKE.phase) >= 2, 45)
    ok(two, f"concept test: the participant's browser opened its own speech-recognition session (no autoplay override) {timeline()} participant caption state: {pp.evaluate(CAPSTATE)} slowest calls: {slow(pp)}")

    # ---- live captions: both speakers, each sentence labelled ----
    want = [("主持人", s) for s in ASR_SCRIPTS[0]] + [("受访者", s) for s in ASR_SCRIPTS[1]]
    got = lambda pg: [tuple(x) for x in pg.evaluate("() => [...document.querySelectorAll('#caps .cap')].map((c) => [c.querySelector('b').textContent, c.children[1].textContent])")]
    partial_now = lambda: any(call(f"/interviews/{iid}/captions?after=0", token=tok["observer"])["partial"].values())
    seen_partial = wait_for(partial_now, 30, 0.3)  # while the participant's first sentence is still being recognised
    ok(wait_for(lambda: len(got(pm)) >= 4, 60), f"concept test: four finished sentences reach the moderator's panel ({got(pm)})")
    ok(sorted(got(pm)) == sorted(want), f"concept test: each sentence is attributed to the right speaker ({got(pm)})")
    ok(wait_for(lambda: sorted(got(po)) == sorted(want), 15), "concept test: the hidden observer follows the same captions")
    ok([t for s, t in got(pm) if s == "受访者"] == ASR_SCRIPTS[1], "concept test: sentences keep their order")
    netq = lambda pg: pg.evaluate("() => document.getElementById('netq').textContent")
    ok(wait_for(lambda: all(w in netq(pm) for w in ("主持人：", "受访者：")) and all(w in netq(po) for w in ("主持人：", "受访者：")), 45),
       f"concept test: the header shows how the moderator and the participant are connected (UDP/TCP, round trip, loss): {netq(pm)!r} / {netq(po)!r}")
    ok(any(x in netq(pm) for x in ("UDP", "TCP")) and "ms 丢包" in netq(pm) and pp.evaluate("() => document.getElementById('netq').hidden"), "concept test: it reads like 'UDP 3 ms 丢包 0%' and the participant never sees it")
    ok(seen_partial, "concept test: the sentence still being spoken is shown while it is recognised")
    lines = call(f"/interviews/{iid}/captions?after=0", token=tok["observer"])["lines"]
    ok(all(a["at_ms"] <= b["at_ms"] for a, b in zip(lines, lines[1:])) and lines and lines[0]["at_ms"] > 0, "concept test: caption times come from the server clock and never go backwards")
    pm.screenshot(path=f"{SHOTS}/e2e_concept_captions.png")

    # ---- quote and follow-up ----
    pm.locator("#caps .cap.pt", has_text="假货").locator("button", has_text="引用").click()
    hl = lambda: call(f"/interviews/{iid}/highlights", token=tok["moderator"])
    ok(wait_for(lambda: any(h["kind"] == "quote" and h["text"] == "我最担心买到假货。" and h["speaker"] == "participant" for h in hl()), 8),
       f"concept test: a quote is stored word for word, attributed to the participant ({hl()})")
    pm.locator("#caps .cap.pt", has_text="成分").locator("[data-qid]").click()
    ok(wait_for(lambda: pm.locator("#qq .qqc").count() == 1 and "成分" in pm.inner_text("#qq"), 5), "concept test: clicking a caption sentence quotes it into the follow-up box")
    pm.fill("#pin", "说说为什么在意成分"); pm.click("#psend")
    ok(wait_for(lambda: pm.locator("#probes .pr").count() == 1 and "成分" in pm.inner_text("#probes") and "为什么在意成分" in pm.inner_text("#probes"), 8), "concept test: a follow-up built on a sentence plus guidance is queued for the moderator")
    ok(pm.locator("#qq .qqc").count() == 0, "concept test: the follow-up box is empty again after sending")
    ok(wait_for(lambda: "成分" in po.inner_text("#probes"), 10), "concept test: the observer sees the queued follow-up")
    po.fill("#pin", "价格多少算贵？"); po.click("#psend")
    ok(wait_for(lambda: "价格多少算贵" in pm.inner_text("#probes"), 10), "concept test: a follow-up typed by the observer reaches the moderator")
    pm.locator("#probes .pr", has_text="成分").locator("button", has_text="已问").click()
    ok(wait_for(lambda: pm.locator("#probes .pr.done").count() == 1, 8), "concept test: the moderator marks a follow-up as asked")
    ok(pm.locator("#probes .pr", has_text="价格多少算贵").locator("button", has_text="删除").count() == 1 and po.locator("#probes .pr", has_text="成分").locator("button", has_text="删除").count() == 0,
       "concept test: an observer can delete only their own follow-up")

    # ---- the team's private chat ----
    po.click("#b-chat"); pm.click("#b-chat")   # the private chat is a small button that opens it over the page
    po.fill("#brin", "能追问一下价格吗？"); po.click("#brsend")
    ok(wait_for(lambda: "能追问一下价格吗" in pm.inner_text("#brlog"), 10), "concept test: the observer's private message reaches the moderator")
    pm.fill("#brin", "好的，下一题问。"); pm.click("#brsend")
    ok(wait_for(lambda: "好的，下一题问" in po.inner_text("#brlog"), 10), "concept test: the moderator's reply reaches the observer")
    ok("能追问一下价格吗" not in pp.inner_text("body") and pp.locator("#brlog").count() == 0, "concept test: the participant never sees the private chat")
    # ---- the material on the participant's screen ----
    pm.evaluate("document.querySelectorAll('#plan details').forEach((d) => (d.open = true))")
    ok(pp.locator("#stim").is_hidden(), "concept test: nothing is on the participant's screen before the moderator shows it")
    pm.locator('#plan button[data-show="concept_a"]').click()
    ok(wait_for(lambda: pp.locator("#stim img").is_visible() and pp.evaluate("() => document.querySelector('#stim img').naturalWidth") > 0, 10), "concept test: the participant sees concept card A")
    ok(wait_for(lambda: po.locator("#stim img").is_visible(), 10), "concept test: the observer sees the same material")
    ok(pp.get_attribute("#stim img", "src").endswith("/concepts/concept_a.svg") and "概念 A" not in pp.inner_text("body"), "concept test: the participant gets the picture, not its title")
    ok(wait_for(lambda: "正在展示" in pm.inner_text("#plantm") and "概念 A" in pm.inner_text("#plantm"), 8), "concept test: the script says what is on screen")
    pm.screenshot(path=f"{SHOTS}/e2e_concept_moderator.png"); pp.screenshot(path=f"{SHOTS}/e2e_concept_participant.png")
    pm.locator('#plan button[data-show="ad_film"]').click()
    ok(wait_for(lambda: (pp.get_attribute("#stim iframe", "src") or "").startswith("https://player.bilibili.com/player.html?bvid=BV"), 10), "concept test: the ad film is shown through its embedded player")
    ok(pp.get_attribute("#stim iframe", "sandbox") is not None, "concept test: the embedded player is sandboxed")
    pm.click("#plan-hide")
    ok(wait_for(lambda: pp.locator("#stim").is_hidden(), 10), "concept test: the moderator takes the material off the screen")

    # ---- the participant sees nothing internal; every screen passes the usual layout / contrast audit, in both colour themes ----
    ok(pp.locator("#caps, #probes").count() == 0 and pp.locator("#lpan").is_hidden() and pp.locator("#rpan").is_hidden() and "待追问" not in pp.inner_text("body"),
       "concept test: the participant never sees the script, captions or follow-ups")
    for name, page in (("moderator", pm), ("observer", po), ("participant", pp)):
        audit(page, f"concept/{name}")
    audit(pm, "concept/moderator-phone", phone=True)
    for name, page in (("moderator", pm), ("participant", pp)):
        page.click("#theme"); audit(page, f"concept/{name}/dark"); page.click("#theme")

    # ---- end: both browsers close their recognition sessions properly ----
    pm.click("#b-end")
    done = wait_for(lambda: len(FAKE.phase) == 2 and all(c["ended"] for c in FAKE.phase), 20)
    ok(done, f"concept test: both browsers end their speech-recognition sessions with the end message {timeline()} moderator: {pm.evaluate(CAPSTATE)} participant: {pp.evaluate(CAPSTATE)} slow moderator calls: {slow(pm)} slow participant calls: {slow(pp)}")
    ok(all(c["frames"] >= 20 and c["bad_frames"] == 0 and c["loud_frames"] > 0 for c in FAKE.phase),
       f"concept test: real microphone audio streamed as 200 ms 16 kHz PCM frames ({[(c['frames'], c['bad_frames'], c['loud_frames']) for c in FAKE.phase]})")
    ok(wait_for(lambda: "已结束" in pm.inner_text("#msg"), 15), "concept test: the interview ends for everybody")
    PUMP[0] = None
    for c in (cm, co, cp):
        c.close()
    br.close()


def _mp4s(tmp):
    media = os.path.join(tmp, "media")
    return {os.path.join(dp, f) for dp, _, fs in os.walk(media) for f in fs if f.endswith(".mp4")}


def _join_group_participant(page, token):
    """Invitation, device check, consent, waiting room. The interview language is English, so the copy matches the 1:1 checks."""
    page.goto(f"{B}/join.html?t={token}")
    page.wait_for_selector("#accept")
    page.click("#accept")
    page.wait_for_selector("#devnext:not([disabled])", timeout=20000)
    page.click("#play")
    page.click("#devnext")
    page.wait_for_selector("form#f")
    page.select_option("select[name=country]", "SG")
    for n in ("recording", "observers", "cross_border"):
        page.check(f"input[name={n}]")
    page.click("form button")
    page.wait_for_url("**/room.html*", timeout=20000)


def _tiles(page):
    return page.evaluate("""() => [...document.querySelectorAll('.tile')].map(t => { const v = t.querySelector('video'); return {id: t.id, w: v ? v.videoWidth : 0, playing: v ? (!v.paused && v.readyState >= 2) : false, name: t.querySelector('.nm').textContent}; })""")


def _tiles_ready(page, n):
    return lambda: (lambda vs: len(vs) == n and all(v["w"] > 0 and v["playing"] for v in vs))(_tiles(page))


def run_ai(tmp):
    """AI-moderated depth interview on a real video server: the team only watches (no voice, camera, screen) until a researcher takes over;
    then voice / video / screen sharing work and reach the participant; handing back takes the rights away again."""
    reg = call("/auth/register", {"org_name": "AiCo", "name": "Admin", "email": "a@ai.test", "password": "password123"})
    adm = reg["token"]
    ids = {}
    for role, name in (("moderator", "Mika Moderator"), ("observer", "Olive Observer"), ("researcher", "Rae Researcher")):
        ids[role] = call("/users", {"name": name, "email": f"{role}@ai.test", "password": "password123", "role": role}, adm)["id"]
    tok = {r: call("/auth/login", {"email": f"{r}@ai.test", "password": "password123"})["token"] for r in ids}
    pid = call("/projects", {"name": "AI SG", "market": "SG", "language": "zh"}, tok["researcher"])["id"]
    part = call(f"/projects/{pid}/participants", {"name": "Wei Ling", "country": "SG", "language": "zh"}, tok["researcher"])
    itv = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "AI 深访", "language": "zh", "moderation": "ai", "observer_ids": [ids["observer"]]}, tok["researcher"])
    iid, T = itv["id"], itv["invite_token"]
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    call(f"/interviews/{iid}/start", {}, tok["researcher"])
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required", "--auto-select-desktop-capture-source=Entire screen"])
        errs = []

        def ctx(name, token=None):
            c = br.new_context(viewport={"width": 1280, "height": 800}, permissions=["camera", "microphone"])
            if token:
                c.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
            p = c.new_page()
            p.on("console", lambda m: errs.append(f"[{name}] console: {m.text}") if m.type == "error" else None)
            p.on("pageerror", lambda e: errs.append(f"[{name}] pageerror: {e}"))
            return c, p

        cp, pp = ctx("participant")
        pp.goto(f"{B}/room.html?t={T}")
        cr, pr = ctx("researcher", tok["researcher"])
        pr.goto(f"{B}/room.html?i={iid}&as=moderator")
        co, po = ctx("observer", tok["observer"])
        po.goto(f"{B}/room.html?i={iid}&as=observer")
        PUMP[0] = pr
        call(f"/interviews/{iid}/ai/start", {}, tok["researcher"])
        pubs = lambda pg: pg.evaluate("() => room && room.localParticipant ? room.localParticipant.permissions.canPublish : null")
        remote = lambda pg: pg.evaluate("() => room ? [...room.remoteParticipants.values()].map(p => [p.identity.split(':')[0], p.trackPublications.size]) : null")
        ok(wait_for(lambda: pr.is_visible("#a-take") and pubs(pr) is False, 30), "[ai takeover] the researcher joins the video room without the right to publish")
        ok(pr.locator("#b-mic, #b-cam, #b-hmic").count() == 0, "[ai takeover] no microphone or camera buttons while the AI moderates")
        ok(wait_for(lambda: pubs(po) is False, 20), "[ai takeover] the observer cannot publish either")
        ok(wait_for(lambda: all(r[0] != "staff" and r[0] != "obs" for r in (remote(pp) or [("x", 0)])), 20), "[ai takeover] the participant sees nobody from the team in the room")
        pr.click("#a-take")
        ok(wait_for(lambda: pr.locator("#b-hmic").count() == 1 and pubs(pr) is True, 40), "[ai takeover] after taking over the researcher may publish and gets voice, video and screen buttons")
        pr.screenshot(path=f"{SHOTS}/e2e_ai_takeover_bar.png")
        pr.click("#b-hmic"); pr.click("#b-hcam")
        ok(wait_for(lambda: pr.get_attribute("#b-hmic", "aria-pressed") == "true" and pr.get_attribute("#b-hcam", "aria-pressed") == "true", 20), "[ai takeover] voice and video switch on")
        ok(wait_for(lambda: any(r[0] == "staff" and r[1] >= 2 for r in (remote(pp) or [])), 30), "[ai takeover] the participant receives the researcher's voice and video")
        pr.click("#b-hscr")
        shared = wait_for(lambda: pr.get_attribute("#b-hscr", "aria-pressed") == "true", 15)
        ok(shared and wait_for(lambda: any(r[0] == "staff" and r[1] >= 3 for r in (remote(pp) or [])), 30), "[ai takeover] screen sharing reaches the participant")
        pp.screenshot(path=f"{SHOTS}/e2e_ai_takeover_participant.png")
        pr.click("#a-back")
        ok(wait_for(lambda: pr.locator("#b-hmic").count() == 0 and pubs(pr) is False, 40), "[ai takeover] handing back removes the buttons and the right to publish")
        ok(wait_for(lambda: not any(r[0] == "staff" and r[1] > 0 for r in (remote(pp) or [])), 30), "[ai takeover] the participant no longer receives anything from the team")
        ok(not errs, f"[ai takeover] no console errors: {errs[:3]}")
        br.close()


def run_group(tmp):
    """Focus group: three participants, one moderator, one hidden observer, on the same LiveKit server as the 1:1 run."""
    reg = call("/auth/register", {"org_name": "GroupCo", "name": "Admin", "email": "a@group.test", "password": "password123"})
    adm = reg["token"]
    ids = {}
    for role, name in (("moderator", "Mika Moderator"), ("observer", "Olive Observer"), ("researcher", "Rae Researcher")):
        ids[role] = call("/users", {"name": name, "email": f"{role}@group.test", "password": "password123", "role": role}, adm)["id"]
    tok = {r: call("/auth/login", {"email": f"{r}@group.test", "password": "password123"})["token"] for r in ids}
    pid = call("/projects", {"name": "Focus SG", "market": "SG", "language": "en"}, tok["researcher"])["id"]
    names = ("Aisha Tan", "Ben Lim", "Cara Ng")
    people = [call(f"/projects/{pid}/participants", {"name": n, "country": "SG", "language": "en"}, tok["researcher"]) for n in names]
    itv = call(f"/projects/{pid}/interviews", {"kind": "group", "participant_ids": [p["id"] for p in people], "title": "三人座谈会",
                                              "language": "en", "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}, tok["researcher"])
    iid = itv["id"]
    invites = sorted(itv["group_invites"], key=lambda g: g["seat"])
    ok([g["seat"] for g in invites] == [1, 2, 3], "the group is created with three seats")

    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        errs = []

        def ctx(name, token=None):
            c = br.new_context(viewport={"width": 1280, "height": 760}, permissions=["camera", "microphone"])
            if token:
                c.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
            p = c.new_page()
            p.on("console", lambda m: errs.append(f"[{name}] console: {m.text}") if m.type == "error" else None)
            p.on("pageerror", lambda e: errs.append(f"[{name}] pageerror: {e}"))
            p.on("dialog", lambda d: d.accept())
            return c, p

        pages, contexts = [], []
        for i, g in enumerate(invites):
            c, p = ctx(f"p{i + 1}")
            contexts.append(c)
            pages.append(p)
            _join_group_participant(p, g["invite_token"])
            ok("room.html" in p.url, f"seat {i + 1} is in the room right after consent")
        cm, pm = ctx("moderator", tok["moderator"])
        co, po = ctx("observer", tok["observer"])
        contexts += [cm, co]
        pm.goto(f"{B}/room.html?i={iid}")
        po.goto(f"{B}/room.html?i={iid}&as=observer")
        ok(wait_for(lambda: pm.locator("#gwait .row").count() == 3, 15), "moderator sees one line per seat")
        ok(pm.locator("[data-admit], [data-hold]").count() == 0, "and nothing to admit or hold")
        ok("参与者 ·01" in pm.inner_text("#gwait") and "参与者 ·03" in pm.inner_text("#gwait"), "waiting list uses seat labels")
        ok(not po.is_visible("#wbox"), "the observer does not see the list or anyone's name")
        pm.click("#b-start")
        ok(wait_for(_tiles_ready(pm, 4), 45), "moderator sees self and three participants")
        for i, page in enumerate(pages):
            ok(wait_for(_tiles_ready(page, 4), 45), f"seat {i + 1} sees the moderator and the other two")
        ok(wait_for(_tiles_ready(po, 4), 45), "observer sees four tiles and no self tile")
        vm, vo = _tiles(pm), _tiles(po)
        labels = [v["name"] for v in vm + vo]
        ok(all(f"参与者 ·0{n}" in labels for n in (1, 2, 3)), f"staff tiles are numbered ({labels})")
        ok("t_me" not in [v["id"] for v in vo], "observer publishes nothing")
        blob = " ".join(p.content() for p in pages + [pm, po])
        ok(all(n not in blob for n in names), "real names never reach any browser")
        leak = po.evaluate("() => JSON.stringify([...room.remoteParticipants.values()].map(p => [p.identity, p.name, p.metadata]))")
        ok(all(n not in leak for n in names), "observer metadata has no real names")
        seen = pages[0].evaluate("() => [...document.querySelectorAll('.tile .nm')].map(n => n.textContent).join(' ')")
        ok("Participant ·02" in seen and "Participant ·03" in seen and "You" in seen and "Aisha" not in seen, f"an English participant sees numbered seats ({seen})")
        perms = po.evaluate("() => { const p = room.localParticipant.permissions; return {pub: p.canPublish, data: p.canPublishData, hidden: p.hidden}; }")
        ok(perms == {"pub": False, "data": False, "hidden": True}, f"observer stays subscribe-only ({perms})")
        pubs = [p.evaluate("() => room.localParticipant.permissions.canPublish") for p in pages]
        ok(pubs == [True, True, True], f"each participant can publish ({pubs})")
        pm.screenshot(path=f"{SHOTS}/e2e_group_moderator.png")
        pages[0].screenshot(path=f"{SHOTS}/e2e_group_participant.png")
        po.screenshot(path=f"{SHOTS}/e2e_group_observer.png")
        call(f"/public/join/{invites[0]['invite_token']}/captions", {"text": "I look at the ingredients first.", "final": True, "dur_ms": 900})
        ok(wait_for(lambda: "参与者 ·01" in pm.inner_text("#caps") and "I look at the ingredients first." in pm.inner_text("#caps"), 12), "live captions name the seat")
        ok("p1" not in pm.inner_text("#caps"), "live captions do not show the raw speaker key")
        pm.screenshot(path=f"{SHOTS}/e2e_group_captions.png")
        # the speaking-fairness panel: seat 1 talks a lot, seat 2 a little, seat 3 not at all
        time.sleep(7)   # nobody waits for an admit any more, so the interview is only seconds old: let the clock pass the longest sentence below, or its span would be cut at zero
        for text, ms in (("I read every label before I buy anything, even in a hurry.", 6000), ("Price matters less to me than the texture and the smell.", 5000)):
            call(f"/public/join/{invites[0]['invite_token']}/captions", {"text": text, "final": True, "dur_ms": ms})
        call(f"/public/join/{invites[1]['invite_token']}/captions", {"text": "I usually ask my sister first.", "final": True, "dur_ms": 2000})
        ok(wait_for(lambda: pm.locator("#fairbody .frow").count() == 3, 15), "group room: the moderator sees a speaking row for each of the three seats")
        ok(wait_for(lambda: "参与者 ·01" in pm.inner_text("#fairbody") and "公平指数" in pm.inner_text("#fairbody"), 10), "group room: the panel names the seats and shows the fairness index")
        def shares():  # the panel refreshes every 5 s: wait until it has seen seat 2 speak
            rows = pm.evaluate("() => [...document.querySelectorAll('#fairbody .frow .fbar')].map((b) => b.getAttribute('aria-label'))")
            return [int(r.split("占发言时间的 ")[1].split("%")[0]) for r in rows]
        ok(wait_for(lambda: len(shares()) == 3 and shares()[1] > 0, 15) and shares()[0] > shares()[1] >= shares()[2], f"group room: shares follow who talked (01 > 02 >= 03): {shares()}")  # (seat 3's own browser may add a line from the fake recogniser)
        ok(wait_for(lambda: po.locator("#fairbody .frow").count() == 3, 15), "group room: the hidden observer sees the panel too")
        ok(all(pg.locator("#fairbox, #fairbody").count() == 0 for pg in pages), "group room: participants never get the speaking panel")
        panel = pm.inner_text("#fairbody") + po.inner_text("#fairbody")
        ok(all(n.split()[0] not in panel for n in names), "group room: the speaking panel shows numbers, never names")
        m = call(f"/interviews/{iid}/group/metrics", token=tok["researcher"])
        talk = [x["talk_s"] for x in m["participants"]]  # the three sentences of seat 1 arrive at once, so their spans overlap and are counted once
        ok(talk[0] > talk[1] > 0 and talk[1] >= talk[2] and m["participants"][1]["turns"] >= 1, f"the metrics endpoint agrees ({talk})")
        # cue cards: a very personal remark makes a card for the moderator, and only for the moderator
        call(f"/public/join/{invites[2]['invite_token']}/captions", {"text": "I was diagnosed with depression two years ago.", "final": True, "dur_ms": 3000})
        ok(wait_for(lambda: pm.locator("#cuebody .cue.k-compliance").count() == 1, 15), "cue cards: the moderator gets a card about the personal remark")
        card = pm.locator("#cuebody .cue.k-compliance").first.text_content()  # (the participant's words sit in a collapsed "依据" block)
        ok("参与者 ·03" in card and "diagnosed with depression" in card, f"cue cards: the card names the seat and shows the participant's own words ({card[:80]!r})")
        ok(po.locator("#cuebox, #cuebody").count() == 0 and all(pg.locator("#cuebox, #cuebody").count() == 0 for pg in pages), "cue cards: observers and participants never see them")
        pm.locator("#cuebody .cue.k-compliance button", has_text="忽略").click()
        ok(wait_for(lambda: pm.locator("#cuebody .cue.k-compliance.done").count() == 1 and "已忽略" in pm.inner_text("#cuebody"), 10), "cue cards: dismissing a card marks it as answered")
        cues = call(f"/interviews/{iid}/cues", token=tok["moderator"])["cards"]
        ok(any(c["kind"] == "compliance" and c["status"] == "dismissed" for c in cues), "cue cards: the dismissal is saved on the server")
        # emoji reactions: a participant taps one, the moderator and the hidden observer see it, the other participants do not get a feed
        ok(pages[0].locator("#b-react").count() == 1, "reactions: a participant has the emoji button")
        ok(pm.locator("#b-react, #rxpop").count() == 0 and po.locator("#b-react, #rxpop").count() == 0, "reactions: the moderator and the observer have no emoji button")
        pages[0].click("#b-react")
        ok(pages[0].locator("#rxpop").is_visible() and pages[0].locator("#rxpop .rxq button").count() == 8, "reactions: the quick row of eight opens")
        audit(pages[0], "group room participant/reactions open")
        pages[0].click('#rxpop button[data-k="😕"]')
        ok(wait_for(lambda: "参与者 ·01：" in pm.inner_text("#rxfeed") and "😕" in pm.inner_text("#rxfeed"), 12), "reactions: the moderator sees who reacted and with what")
        ok(wait_for(lambda: "😕" in po.inner_text("#rxfeed"), 12), "reactions: the hidden observer sees it too")
        ok(all(pg.locator("#rxfeed").count() == 0 for pg in pages), "reactions: participants get no reaction feed")
        time.sleep(1.2)   # (one tap a second at most)
        pages[0].click("#b-react")
        pages[0].click("#rx-more")
        ok(wait_for(lambda: pages[0].locator("emoji-picker").count() == 1 and pages[0].locator("emoji-picker").is_visible(), 15), "reactions: the full emoji panel opens from our own server")
        pages[0].locator("emoji-picker input[type=search]").fill("bubble tea")
        ok(wait_for(lambda: pages[0].locator("emoji-picker button.emoji[id^='emo-']").count() >= 1, 20), "reactions: searching the panel finds emoji")
        pages[0].wait_for_timeout(600)
        pages[0].screenshot(path=f"{SHOTS}/e2e_group_participant_emoji.png")
        pages[0].locator("emoji-picker button.emoji[id^='emo-']").first.click()
        ok(wait_for(lambda: "🧋" in pm.inner_text("#rxfeed"), 12), "reactions: any emoji from the panel reaches the moderator (not only the quick eight)")
        ok(pages[0].locator("#rxpop").is_hidden(), "reactions: the panel closes after a choice")
        # how it was said: a slow, quiet sentence after a steady run is marked for the moderator, not for the observer
        for _ in range(8):
            call(f"/public/join/{invites[0]['invite_token']}/captions", {"text": "我平时一般先看看成分表再决定买不买", "final": True, "dur_ms": 4200, "vol_db": -30.0})
        call(f"/public/join/{invites[0]['invite_token']}/captions", {"text": "这个我还得想一想吧不太确定怎么说", "final": True, "dur_ms": 12000, "vol_db": -45.0})
        ok(wait_for(lambda: pm.locator(".cap .hints", has_text="语速偏慢").count() >= 1, 15), "signals: the moderator sees the hint beside a slow, quiet sentence")
        ok("仅供参考" in pm.locator(".cap .hints").last.inner_text(), "signals: the hint says it is only a guide")
        ok(wait_for(lambda: po.locator(".cap").count() >= 5, 15) and po.locator(".cap .hints").count() == 0, "signals: the hidden observer sees the captions without the hints")
        sig = call(f"/interviews/{iid}/signals", token=tok["researcher"])
        one = next(x for x in sig["speakers"] if x["speaker"] == "p1")
        ok(one["reactions"].get("😕") == 1 and one["reactions"].get("🧋") == 1 and one["tags"].get("slow", 0) >= 1 and sig["moments"], f"signals: the overview counts the reaction and the slow sentence ({one['tags']})")
        pm.locator("#fairbox").scroll_into_view_if_needed()
        pm.screenshot(path=f"{SHOTS}/e2e_group_fairness.png")
        pm.locator("#cuebox").scroll_into_view_if_needed()
        pm.screenshot(path=f"{SHOTS}/e2e_group_cues.png")
        audit(pm, "group room moderator/light/speaking panel")
        before = _mp4s(tmp)
        pm.click("#b-rec")
        ok(wait_for(lambda: all(p.is_visible("#recdot") for p in pages), 15), "every participant sees the recording indicator")
        time.sleep(8)
        pm.click("#b-stop")
        recs = wait_for(lambda: (lambda r: r if r and r[0]["status"] == "ready" else None)(call(f"/interviews/{iid}/recordings", token=tok["researcher"])), 90)
        ok(bool(recs), f"group recording became ready ({call(f'/interviews/{iid}/recordings', token=tok['researcher'])})")
        made = _mp4s(tmp) - before
        ok(len(made) == 1, f"one new mixed file ({len(made)})")
        if made:
            info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", next(iter(made))], capture_output=True, text=True).stdout or "{}")
            width = next((s.get("width") for s in info.get("streams", []) if s.get("codec_type") == "video"), 0)
            ok(width == 640 * 4, f"four seats sit side by side (width {width})")
        logs = call("/audit?limit=30", token=adm)
        ready = next((r for r in logs if r["action"] == "recording.ready"), None)
        speakers = set((ready or {}).get("meta", {}).get("speakers") or [])
        ok(speakers >= {"moderator", "p1", "p2", "p3"}, f"the mix kept one track per seat ({sorted(speakers)})")
        pages[2].click("#b-leave")
        left = invites[2]["participant_id"]
        ok(wait_for(lambda: "进过房间" in pm.inner_text("#gwait"), 15), "the person who left shows as having been in the room (and can come back without anyone's say-so)")
        ok(wait_for(_tiles_ready(pm, 3), 20), "moderator's grid drops the person who left")
        pm.click("#theme")
        ok("t-dark" in pm.evaluate("document.body.className"), "group room switches to dark green")
        pm.screenshot(path=f"{SHOTS}/e2e_group_moderator_dark.png")
        pages[0].set_viewport_size({"width": 390, "height": 780})
        pages[0].screenshot(path=f"{SHOTS}/e2e_group_participant_phone.png")
        audit(pm, "group room moderator/dark/desktop")
        real = [e for e in errs if "favicon" not in e and "DataChannel error" not in e]
        ok(not real, f"group room: no console errors: {real[:3]}")


def finish(br, errs, n_before_end=None):
    real = [e for i, e in enumerate(errs) if "favicon" not in e and not (n_before_end is not None and i >= n_before_end and "DataChannel error" in e)]  # data channels error out when the server closes the room: expected
    ok(not real, f"no console errors/page errors: {real[:3]}")
    br.close()


if __name__ == "__main__":
    main()
