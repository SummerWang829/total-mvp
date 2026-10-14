"""End-to-end with a REAL LiveKit server and three real browsers (moderator, participant, hidden observer).
Run: python tests/e2e_room.py   (needs bin/livekit-server: scripts/get_livekit.sh)

Checks the whole journey a human tester will do: consent page -> room -> everybody sees/hears the right people -> the observer is
invisible and cannot publish -> start interview -> record (camera toggled off and on in the middle) -> stop -> server mixes the
tracks -> transcribe -> insight -> clip -> share page plays a short WATERMARKED cut -> end interview kicks everyone out.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_site import AUDIT_JS  # the same colour / font-size / tap-target audit the whole-site QA uses

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


def wait_for(fn, timeout=60, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
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
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/e2e.db", "MEDIA_DIR": f"{tmp}/media", "LIVEKIT_URL": f"ws://127.0.0.1:{LKPORT}", "RECORDING_GRACE_S": "25",
           "DISABLE_WORKER": "", "ASR_PROVIDER": "mock", "LLM_PROVIDER": "mock", "ASR_API_KEY": "", "LLM_API_KEY": "",
           "MODERATOR_PROVIDER": "mock", "MODERATOR_API_KEY": "", "MODERATOR_VENDOR": ""}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env,
                           stdout=open(os.path.join(tmp, "srv.log"), "w"), stderr=subprocess.STDOUT)
    try:
        if not wait_for(lambda: urllib.request.urlopen(B + "/api/health").status == 200, 20):
            print("server did not start"); sys.exit(2)
        run(tmp)
    finally:
        srv.terminate(); lk.terminate()
        try:
            srv.wait(5); lk.wait(5)
        except Exception:
            srv.kill(); lk.kill()
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
        pp.wait_for_selector("#enter")
        ok(st() == "consented", "staff sees the consent")
        ok("Waiting for the moderator" in pp.inner_text("#pw") and pp.get_attribute("#enter", "aria-disabled") == "true", "participant waits in the waiting room until the moderator starts")
        # ---- moderator + observer ----
        cm, pm = ctx("moderator", tok["moderator"])
        pm.goto(f"{B}/room.html?i={iid}")
        co, po = ctx("observer", tok["observer"])
        po.goto(f"{B}/room.html?i={iid}&as=observer")
        ok(wait_for(lambda: pm.is_visible("#b-start") and pm.is_disabled("#b-rec"), 15), "record button disabled before the interview starts")
        ok(wait_for(lambda: "在候场室" in pm.inner_text("#wst"), 10), "moderator sees the participant waiting in the waiting room")
        ok(not po.is_visible("#wbox"), "the observer does not see the waiting room controls")
        ok("t-light" in pm.evaluate("document.body.className") and "t-light" in po.evaluate("document.body.className"), "rooms open in the white light theme by default")
        pm.click("#b-start")
        time.sleep(3.5)
        ok(pp.get_attribute("#enter", "aria-disabled") == "true", "started, but the participant waits until the moderator lets them in")
        pm.click("#b-admit")
        ok(wait_for(lambda: "已放行" in pm.inner_text("#wst") and pm.is_disabled("#b-admit"), 10), "moderator sees the participant as let in")
        ok(wait_for(lambda: pp.get_attribute("#enter", "aria-disabled") == "false", 10), "waiting room unlocks once the moderator lets the participant in")
        pp.screenshot(path=f"{SHOTS}/e2e_waiting_room.png")
        pp.click("#enter")

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
        ok(pp.locator("#lpan:visible, #rpan:visible").count() == 0 and pm.is_visible("#lpan") and pm.is_visible("#rpan"), "side panels for staff only, never for the participant")
        ok("Wei Ling" not in pm.inner_text("#lpan") and "Wei Ling" not in po.inner_text("body"), "no participant name in the room panels")

        # ---- run the interview ----
        ok(wait_for(lambda: "in progress" in pp.inner_text("#msg"), 10), "participant sees the interview started")
        ok(wait_for(lambda: not pm.is_disabled("#b-rec"), 10), "record button enabled once live + consented")
        ok(pm.is_disabled("#b-start"), "start button disabled once live")
        pm.click("#b-rec")
        ok(wait_for(lambda: pp.is_visible("#recdot") and "Recording" in pp.inner_text("#recdot"), 10), "participant sees the recording indicator")
        ok(po.is_visible("#recdot"), "observer also sees it is being recorded")
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
        pr2.wait_for_selector("#recs")
        ok("已就绪" in pr2.inner_text("#recs"), "researcher sees the recording as ready")
        ok(bool(wait_for(lambda: pr2.is_visible("#pv") and pr2.get_attribute("#pv", "src"), 10)), "researcher gets a player for the full recording")
        ok(pr2.locator("[data-seek]").count() == len(segs), "every transcript time is a click-to-seek link")
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
        call(f"/interviews/{aid}/admit", {"admit": True}, tok["moderator"])
        cam, pam = ctx("ai-moderator", tok["moderator"])
        cap, pap = ctx("ai-participant")
        cao, pao = ctx("ai-observer", tok["observer"])
        pam.goto(f"{B}/room.html?i={aid}&as=moderator"); pap.goto(f"{B}/room.html?t={atk}"); pao.goto(f"{B}/room.html?i={aid}&as=observer")
        in_room = lambda pg: pg.evaluate("() => room && room.state === 'connected'")
        ok(wait_for(lambda: in_room(pam) and in_room(pap) and in_room(pao), 30), "AI interview: everybody is in the real video room")
        ok(pam.evaluate("() => room.remoteParticipants.size") == 1 and pap.evaluate("() => room.remoteParticipants.size") == 1,
           "AI interview: the observer is still invisible to the moderator and the participant")
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
        ok(wait_for(lambda: pap.locator("#clog .m").count() == n + 1, 10), "AI interview: the participant's line is stored while a human has control")
        time.sleep(3); ok(pap.locator("#clog .m").count() == n + 1, "AI interview: the AI stays silent while a human has control")
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

        finish(br, errs, n_before_end)


def finish(br, errs, n_before_end=None):
    real = [e for i, e in enumerate(errs) if "favicon" not in e and not (n_before_end is not None and i >= n_before_end and "DataChannel error" in e)]  # data channels error out when the server closes the room: expected
    ok(not real, f"no console errors/page errors: {real[:3]}")
    br.close()


if __name__ == "__main__":
    main()
