"""Check the modern nav icons, the sign-out hover card and the top bar without the design name."""
import json, os, pathlib, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(r"C:\Users\PC\total-mvp")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
tmp = pathlib.Path(tempfile.mkdtemp(prefix="b8_"))
env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp / 'w.db').as_posix()}", "PYTHONIOENCODING": "utf-8"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
out = []
def say(ok, msg): out.append(("PASS " if ok else "FAIL ") + msg)
try:
    for _ in range(90):
        try: urllib.request.urlopen(B + "/api/health"); break
        except Exception: time.sleep(0.5)
    subprocess.run([sys.executable, "-m", "app.seed", "--url", B], cwd=ROOT, env=env, check=True, capture_output=True)
    def call(path, body=None, token=None, method=None):
        req = urllib.request.Request(B + "/api" + path, data=json.dumps(body).encode() if body is not None else None, method=method or ("POST" if body is not None else "GET"), headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
        return json.loads(urllib.request.urlopen(req).read() or b"null")
    tok = call("/auth/login", {"email": "admin@demo.test", "password": "demo12345"})["token"]
    pid = call("/projects", token=tok)[0]["id"]
    draft = call("/projects/draft", {"name": "batch8"}, tok)["id"]
    pub = call("/projects/draft", {"name": "batch8pub"}, tok)["id"]   # a project with a published plan: the overview with the dark band
    cfg = call(f"/projects/{pub}/plan", token=tok)["config"]
    cfg["subs"] = [{**sb, "design": "i"} for sb in cfg["subs"]]; cfg["cons"] = {k: True for k in cfg["cons"]}
    cfg["n"] = {"q": 0, "i": 5, "f": 0, "fs": 6}; cfg["lt"] = {"dims": ["国家"], "rows": [{"id": "r1", "vs": ["s1"], "t": 0, "i": 5, "f": 0}]}
    call(f"/projects/{pub}/plan", {"config": cfg}, tok, "PUT"); call(f"/projects/{pub}/plan/submit", {}, tok); call(f"/projects/{pub}/plan/approve", {}, tok); call(f"/projects/{pub}/plan/publish", {}, tok)
    with sync_playwright() as p:
        br = p.chromium.launch(); 
        for theme in ("light", "orange", "dark"):
            ctx = br.new_context(viewport={"width": 1500, "height": 800})
            ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}');localStorage.setItem('tt_theme','{theme}')}}catch(e){{}}")
            pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(B + f"/#/p/{pub}/home"); pg.reload(); pg.wait_for_selector("nav.side"); pg.wait_for_timeout(700)
            r = pg.evaluate("""() => { const q = (s) => document.querySelectorAll(s).length;
              const masks = [...document.querySelectorAll('nav.side a.nv .ic, nav.side .sic')].map((e) => getComputedStyle(e, '::before').webkitMaskImage || getComputedStyle(e, '::before').maskImage);
              return { icons: masks.length, withMask: masks.filter((m) => m && m !== 'none').length, trackCap: q('.track-cap'), stage: q('.sic.k-depth') + q('.sic.k-quant') + q('.sic.k-group') }; }""")
            say(r["icons"] > 0 and r["icons"] == r["withMask"], f"{theme}: every nav icon has a mask {r}")
            say(r["trackCap"] == 0, f"{theme}: no design-name text in the project bar")
            pg.hover("#out"); pg.wait_for_timeout(900)
            t = pg.evaluate("() => { const c = document.querySelector('.mecard'); const s = getComputedStyle(c); return { shown: s.display !== 'none', text: c.innerText }; }")
            say(t["shown"] and "管理员" in t["text"], f"{theme}: hover card shows {t['text']!r}")
            pg.screenshot(path=str(ROOT / "qa_shots" / f"icons_{theme}.png"), clip={"x": 0, "y": 0, "width": 700, "height": 800}); box = pg.evaluate("() => { const r = document.querySelector('.mecard').getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height), innerHeight]; }"); say(box[0] >= 90 and box[1] >= 0 and box[1] + box[3] <= box[4], f"{theme}: hover card is inside the window {box}")
            say(not errs, f"{theme}: no page errors {errs[:1]}")
            ctx.close()
        br.close()
finally:
    srv.terminate()
print(chr(10).join(out))
