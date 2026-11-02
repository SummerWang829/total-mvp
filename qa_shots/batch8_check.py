"""Check the eight confirmed UI changes (overview, activity fold, popups, sub-project table, design table, suggestion tabs, roles table, top-right 结项)."""
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
        br = p.chromium.launch(); ctx = br.new_context(viewport={"width": 1920, "height": 1000})
        ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{tok}')}}catch(e){{}}")
        pg = ctx.new_page(); pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
        errs = []; pg.on("pageerror", lambda e: (errs.append(str(e)), print("PAGEERROR", str(e)[:300]))); pg.on("console", lambda m: print("CONSOLE", m.type, m.text[:200]) if m.type == "error" else None)
        # 1, 2, 8: overview page
        pg.goto(B + f"/#/p/{pub}/home"); pg.reload(); pg.wait_for_timeout(3000); print("URL", pg.url, "| main:", pg.evaluate("() => document.querySelector('main') ? document.querySelector('main').innerText.slice(0,200) : 'NO MAIN'").replace(chr(10)," / "), "| band:", pg.locator(".overview-band").count()); pg.wait_for_selector(".overview-band", timeout=5000)
        say(pg.locator(".band-go").count() == 0, "overview: no 进入直播间 button")
        say(pg.locator("details.feedbox").count() == 1 and not pg.locator("details.feedbox").evaluate("e => e.open"), "overview: 最近动态 is folded")
        say(pg.locator(".ctx-modes").count() == 0, "top bar: no 定量/深访 tags")
        r = pg.evaluate("""() => { const c = document.querySelector('#closeproj, #reopen'), t = document.querySelector('.tsw');
          if (!c || !t) return null; const a = c.getBoundingClientRect(), b = t.getBoundingClientRect(); return { closeLeft: Math.round(a.left), themeRight: Math.round(b.right), closeRight: Math.round(a.right), vw: innerWidth }; }""")
        say(bool(r) and r["closeLeft"] >= r["themeRight"] and r["closeRight"] <= r["vw"], f"top bar: 结项 sits right of the theme switch {r}")
        pg.screenshot(path=str(ROOT / "qa_shots" / "b8_overview.png"))
        # 4: draft wizard step 1
        pg.goto(B + f"/#/p/{draft}/wiz"); pg.reload(); pg.wait_for_selector("fieldset.wzbody"); pg.wait_for_timeout(600)
        say(pg.locator("[data-split]").count() == 0, "step 1: no 按国家拆 / 只有一个子项目 switch")
        heads = pg.evaluate("() => [...document.querySelectorAll('.wzsubs thead th')].map(x => x.textContent.trim())")
        say("研究形式" in heads and "研究顺序" not in heads, f"step 1: headers {heads}")
        say("增加语言" in pg.inner_text(".wzsubs") and "增加形式" in pg.inner_text(".wzsubs"), "step 1: 增加语言 / 增加形式 buttons")
        # left alignment: each cell's first content starts at its header's left edge (±10px)
        al = pg.evaluate("""() => { const ths = [...document.querySelectorAll('.wzsubs thead th')], tds = [...document.querySelectorAll('.wzsubs tbody tr:first-child td')];
          return ths.map((h, i) => { const hr = h.getBoundingClientRect(), c = tds[i] && tds[i].firstElementChild ? tds[i].firstElementChild.getBoundingClientRect() : null;
          return c ? Math.round(c.left - hr.left) : null; }); }""")
        say(all(x is None or abs(x) <= 12 for x in al), f"step 1: contents line up with their headers {al}")
        # 3: live popups (published project block)
        r = pg.evaluate("""() => { WZ.live = { languages: ["zh", "en"], default: "zh", formats: { depth: ["video", "audio"] } }; return wzLangFmt(); }""")
        say("语言选择 · 已选 2" in r and "深访访谈形式选择 · 已选 2" in r, "published block: two selection buttons with counts")
        pg.evaluate("""() => wzPick("语言选择", WZ_LANGS.map((l) => [WZ_CODE[l], l]), ["zh"], () => {})""")
        d = pg.locator("[role=dialog]")
        say(d.count() == 1 and d.locator("input[type=search]").count() == 1 and d.locator("input[type=checkbox]").count() == 4, "popup: centred dialog with search and 4 languages")
        box = d.bounding_box(); say(abs((box["x"] + box["width"] / 2) - 960) < 40, "popup: horizontally centred")
        pg.screenshot(path=str(ROOT / "qa_shots" / "b8_popup.png")); pg.locator("[data-cancel]").click()
        # 5, 6: step 2
        pg.evaluate("() => { WZ.live = null; }")
        pg.locator(".chain [data-step='1']").click(); pg.wait_for_selector("table.wzfix"); pg.wait_for_timeout(400)
        heads2 = pg.evaluate("() => [...document.querySelectorAll('table.wzfix th')].map(x => x.textContent.trim())")
        say(heads2 == ["项目", "研究形式", "阶段", "舆情"], f"step 2: headers {heads2}")
        pg.evaluate("""() => { WZ.plan.suggest = { depth_topics: [{ title: "主题一", questions: ["问题甲？", "问题乙？"] }], group_topics: [{ title: "主题二", questions: ["问题丙？"] }], quota_hint: "招募 18 岁以上", listening_keywords: ["关键词一", "关键词二"] }; WZ.sugState = "done"; wzRedraw(); }""")
        pg.wait_for_selector(".sugtabs")
        say(pg.locator(".sugtabs [data-sugtab]").count() == 4 and pg.locator(".sugrow").count() == 1, "step 2: suggestion has 4 tabs, one block shown")
        pg.locator("[data-sugtab='kw']").click(); pg.wait_for_timeout(200)
        say("关键词一" in pg.inner_text(".sugbody") and pg.locator(".sugrow").count() == 0, "step 2: switching tabs works")
        pg.screenshot(path=str(ROOT / "qa_shots" / "b8_step2.png"))
        # 7: step 3 roles
        pg.locator(".chain [data-step='2']").click(); pg.wait_for_selector("table.wzroles"); pg.wait_for_timeout(400)
        al3 = pg.evaluate("""() => { const ths = [...document.querySelectorAll('.wzroles thead th')], tds = [...document.querySelectorAll('.wzroles tbody tr:first-child td')];
          return ths.slice(2, 6).map((h, i) => { const c = tds[i + 2].querySelector('input'); return c ? Math.round(c.getBoundingClientRect().left - h.getBoundingClientRect().left) : null; }); }""")
        say(all(x is not None and abs(x) <= 14 for x in al3), f"step 3: checkboxes line up with their headers {al3}")
        pg.screenshot(path=str(ROOT / "qa_shots" / "b8_step3.png"))
        say(not errs, f"no page errors {errs[:2]}")
        br.close()
finally:
    srv.terminate()
print("\n".join(out))
