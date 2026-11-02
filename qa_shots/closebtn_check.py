import json, os, pathlib, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
ROOT = pathlib.Path(r"C:\Users\PC\total-mvp")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"; tmp = pathlib.Path(tempfile.mkdtemp(prefix="cb_"))
env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp / 'w.db').as_posix()}", "PYTHONIOENCODING": "utf-8"}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
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
    with sync_playwright() as p:
        br = p.chromium.launch(); ctx = br.new_context(viewport={"width": 1600, "height": 700})
        ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
        pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
        pg.goto(B + f"/#/p/{pid}/home"); pg.reload(); pg.wait_for_selector("#closeproj"); pg.wait_for_timeout(500)
        f = lambda sel: pg.evaluate(f"() => {{ const c = getComputedStyle(document.querySelector('{sel}')); return [c.backgroundColor, c.color]; }}")
        print("open  ", f("#closeproj")); pg.screenshot(path=str(ROOT / "qa_shots" / "cb_open.png"), clip={"x": 1300, "y": 0, "width": 300, "height": 70})
        pg.once("dialog", lambda d: d.accept()); pg.click("#closeproj"); pg.wait_for_selector("#reopen", timeout=8000); pg.wait_for_timeout(500)
        print("closed", f("#reopen")); pg.screenshot(path=str(ROOT / "qa_shots" / "cb_closed.png"), clip={"x": 1300, "y": 0, "width": 300, "height": 70})
        br.close()
finally:
    srv.terminate()
