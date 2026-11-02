"""Check: the wizard's right-side step list is gone and the form fills the page width (1280 / 1920 / 2560)."""
import json, os, pathlib, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(r"C:\Users\PC\total-mvp")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
tmp = pathlib.Path(tempfile.mkdtemp(prefix="wiznav_"))
env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp / 'w.db').as_posix()}", "PYTHONIOENCODING": "utf-8"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
try:
    for _ in range(90):
        try:
            urllib.request.urlopen(B + "/api/health"); break
        except Exception:
            time.sleep(0.5)
    subprocess.run([sys.executable, "-m", "app.seed", "--url", B], cwd=ROOT, env=env, check=True, capture_output=True)

    def call(path, body=None, token=None):
        req = urllib.request.Request(B + "/api" + path, data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
        return json.loads(urllib.request.urlopen(req).read() or b"null")

    tok = call("/auth/login", {"email": "admin@demo.test", "password": "demo12345"})["token"]
    pid = call("/projects/draft", {"name": "向导导航检查"}, tok)["id"]
    with sync_playwright() as p:
        br = p.chromium.launch()
        for w in (1280, 1920, 2560):
            ctx = br.new_context(viewport={"width": w, "height": 900})
            ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
            pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
            pg.goto(B + f"/#/p/{pid}/wiz"); pg.reload(); pg.wait_for_selector("fieldset.wzbody"); pg.wait_for_timeout(600)
            r = pg.evaluate("""() => { const f = document.querySelector('fieldset.wzbody').getBoundingClientRect();
                const m = document.querySelector('main') ? document.querySelector('main').getBoundingClientRect() : null;
                return { toc: document.querySelectorAll('.wztoc').length, formW: Math.round(f.width), formRight: Math.round(f.right), vw: innerWidth,
                         mainW: m ? Math.round(m.width) : null }; }""")
            print(w, r)
            heads = pg.locator("table.tbl thead th").all_inner_texts()
            print("  columns:", heads)
            print("  has 座谈会形式:", any("座谈会形式" in h for h in heads), "| 新增国家 box:", pg.locator("#subnew").count(),
                  "| grey note:", pg.locator("text=改名或删除后").count(), "| 选择形式 buttons:", pg.locator("button[data-pick^='fmt|']").count())
            pg.screenshot(path=str(ROOT / "qa_shots" / f"wiz_v6_{w}.png"), full_page=False)
            ctx.close()
        br.close()
finally:
    srv.terminate()
