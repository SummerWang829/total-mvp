"""Check the merged top bar, duplicate titles, the audit pager and the sign-out card."""
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
        br = p.chromium.launch()
        for w in (1280, 1440, 1920):
            ctx = br.new_context(viewport={"width": w, "height": 860})
            ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
            pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
            for name, route in (("people", f"/p/{pid2}/people"), ("outline", f"/p/{pid2}/outline"), ("rooms", f"/p/{pid2}/rooms"), ("insights", f"/p/{pid2}/insights"), ("home", f"/p/{pub}/home")):
                pg.goto(B + "/#" + route); pg.reload(); pg.wait_for_selector(".app.shfix .work > main"); pg.wait_for_timeout(900)
                r = pg.evaluate("""() => { const q = (s) => document.querySelector(s), top = (e) => e ? Math.round(e.getBoundingClientRect().top) : null, bot = (e) => e ? Math.round(e.getBoundingClientRect().bottom) : null;
                  const main = q('.work > main'), tabs = q('.work > .hubtabs.pin'), sub = q('.work > .subrow'), ctx = q('.work > .ctx');
                  const tabsTops = [...document.querySelectorAll('.hubtabs.pin a')].map((a) => Math.round(a.getBoundingClientRect().top));
                  return { mainTop: top(main), ctxBottom: bot(ctx), tabsBottom: bot(tabs), subTop: sub && sub.children.length ? top(sub) : null, sameRow: tabsTops.every((t) => t === tabsTops[0]), sw: document.documentElement.scrollWidth, vw: innerWidth, track: document.querySelectorAll('.track').length && getComputedStyle(q('.track')).display }; }""")
                ok_row = r["mainTop"] is not None and (r["mainTop"] <= 60 if w >= 1400 else r["mainTop"] <= 110) and r["sameRow"] and r["sw"] <= r["vw"] and (r["subTop"] is None or r["subTop"] < 10 or w < 1400)
                say(ok_row, f"{w} {name}: one-line top bar {r}")
                pg.screenshot(path=str(ROOT / "qa_shots" / f"bar_{w}_{name}.png"), clip={"x": 0, "y": 0, "width": min(w, 1500), "height": 300})
            say(not errs, f"{w}: no page errors {errs[:1]}")
            ctx.close()
        # duplicate titles and the audit pager
        ctx = br.new_context(viewport={"width": 1500, "height": 860}); ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
        pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
        for name, route in (("team", "/team"), ("retention", "/retention"), ("audit", "/audit"), ("repo", "/repo"), ("ai", "/ai")):
            pg.goto(B + "/#" + route); pg.reload(); pg.wait_for_selector("nav.side"); pg.wait_for_timeout(900)
            vis = pg.evaluate("() => { const h = document.querySelector('main > .head h1'); return h ? getComputedStyle(h).display : 'none'; }")
            say(vis == "none", f"{name}: no duplicate big title ({vis})")
        for i in range(20): call("/projects/draft", {"name": f"fill{i}"}, tok)   # many audit rows for the pager
        pg.goto(B + "/#/audit"); pg.reload(); pg.wait_for_selector("#arows tr"); pg.wait_for_timeout(500)
        n1 = pg.locator("#arows tr").count(); has_pg = pg.locator("#apg [data-pg]").count()
        say(n1 == 15 and has_pg >= 3, f"audit: 15 rows on page 1 ({n1}), pager buttons {has_pg}")
        pg.locator("#apg [data-pg]:has-text('下一页')").click(); pg.wait_for_timeout(500)
        say(pg.locator("#arows tr").count() >= 1 and "on" in (pg.locator("#apg .on").get_attribute("class") or ""), "audit: next page shows rows and highlights page 2")
        pg.screenshot(path=str(ROOT / "qa_shots" / "audit_pager.png"))
        # sign-out card inside the narrow sidebar
        pg.goto(B + f"/#/p/{pid2}/people"); pg.reload(); pg.wait_for_selector("nav.side"); pg.wait_for_timeout(700); pg.hover("#out"); pg.wait_for_timeout(500)
        box = pg.evaluate("() => { const c = document.querySelector('.mecard').getBoundingClientRect(), n = document.querySelector('nav.side').getBoundingClientRect(), b = document.querySelector('#out').getBoundingClientRect(); return [Math.round(c.left), Math.round(c.right), Math.round(n.right), Math.round(c.bottom), Math.round(b.top)]; }")
        say(box[1] <= box[2] and box[3] <= box[4], f"sign-out card stays inside the sidebar and above the button {box}")
        pg.screenshot(path=str(ROOT / "qa_shots" / "mecard_rail.png"), clip={"x": 0, "y": 560, "width": 300, "height": 300})
        br.close()
finally:
    srv.terminate()
print(chr(10).join(out))
