"""Check the site fonts: Inter for English and digits, Noto Sans SC for Chinese, served from this server."""
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
    pid2 = next(x["id"] for x in call("/projects", token=tok) if x["name"].startswith("演示"))
    pub = call("/projects/draft", {"name": "batch8pub"}, tok)["id"]   # a project with a published plan: the overview with the dark band
    cfg = call(f"/projects/{pub}/plan", token=tok)["config"]
    cfg["subs"] = [{**sb, "design": "i"} for sb in cfg["subs"]]; cfg["cons"] = {k: True for k in cfg["cons"]}
    cfg["n"] = {"q": 0, "i": 5, "f": 0, "fs": 6}; cfg["lt"] = {"dims": ["国家"], "rows": [{"id": "r1", "vs": ["s1"], "t": 0, "i": 5, "f": 0}]}
    call(f"/projects/{pub}/plan", {"config": cfg}, tok, "PUT"); call(f"/projects/{pub}/plan/submit", {}, tok); call(f"/projects/{pub}/plan/approve", {}, tok); call(f"/projects/{pub}/plan/publish", {}, tok)
    with sync_playwright() as p:
        br = p.chromium.launch(); ctx = br.new_context(viewport={"width": 1440, "height": 860})
        pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
        bad = []; reqs = []
        pg.on("response", lambda r: reqs.append((r.url.split("/fonts/")[-1], r.status)) if "/fonts/" in r.url else None)
        pg.on("requestfailed", lambda r: bad.append(r.url))
        # logged out: the login page
        pg.goto(B + "/"); pg.wait_for_timeout(1500)
        say(pg.evaluate("() => getComputedStyle(document.body).fontFamily.startsWith('Inter')"), "login page: body font starts with Inter")
        pg.screenshot(path=str(ROOT / "qa_shots" / "font_login.png"))
        ctx2 = br.new_context(viewport={"width": 1440, "height": 860})
        ctx2.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
        pg = ctx2.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
        pg.on("response", lambda r: reqs.append((r.url.split("/fonts/")[-1], r.status)) if "/fonts/" in r.url else None)
        pg.goto(B + f"/#/p/{pub}/wiz"); pg.reload(); pg.wait_for_selector("fieldset.wzbody"); pg.wait_for_timeout(1500)
        r = pg.evaluate("""async () => { await document.fonts.ready; const loaded = [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replace(/"/g, '') + ' ' + f.weight);
          const h1 = document.querySelector('main .head h1'); const cs = h1 && getComputedStyle(h1);
          return { loaded, body: getComputedStyle(document.body).fontFamily.slice(0, 40), h1: cs ? [cs.fontFamily.slice(0, 30), cs.fontWeight] : null,
                   hasInter: document.fonts.check('16px Inter', 'Abc 123'), hasNoto: document.fonts.check('16px "Noto Sans SC"', '访谈') }; }""")
        say(any(x.startswith("Inter") for x in r["loaded"]) and any(x.startswith("Noto Sans SC") for x in r["loaded"]), f"Inter and Noto Sans SC are loaded {r['loaded']}")
        say(r["body"].startswith("Inter") and r["h1"] and r["h1"][0].startswith("Inter"), f"body and page title use the new stack {r['body']} {r['h1']}")
        say(all(st == 200 for _, st in reqs) and reqs, f"font files served 200 {sorted(set(reqs))}")
        say(not bad, f"no failed requests {bad[:2]}")
        pg.screenshot(path=str(ROOT / "qa_shots" / "font_wizard.png"))
        br.close()
finally:
    srv.terminate()
print(chr(10).join(out))
