"""Whole-site QA in a real browser. Run:  python tests/qa_site.py   (scripts/qa.sh runs everything)

Starts its own server on a temp database, seeds realistic data through the API, then for every role x every page x
desktop+phone checks: console/page errors, failed requests, horizontal overflow, dead links, min font size,
text contrast (WCAG AA), tap-target size, keyboard focus ring, plus the logic of the main user journeys.
Exit code 1 if anything fails; a report is written to qa_report.md.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []
PASSES = 0
NOTES: list[str] = []


def ok(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("  FAIL", msg)
    return cond


def wait_until(fn, timeout=6.0):
    t = time.time()
    while time.time() - t < timeout:
        if fn():
            return True
        time.sleep(0.25)
    return False


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
B = f"http://127.0.0.1:{PORT}"


def call(path, body=None, token=None, method=None):
    req = urllib.request.Request(B + "/api" + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    return json.loads(urllib.request.urlopen(req).read() or b"null")


# ---------------- in-page audit helpers ----------------
AUDIT_JS = r"""
() => {
  const out = {small: [], contrast: [], tap: [], names: [], wrap: [], topH: (document.querySelector('.top')||{offsetHeight:0}).offsetHeight, overflow: document.documentElement.scrollWidth - innerWidth};
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  const over = (top, bot) => { const a = top[3]; return [top[0] * a + bot[0] * (1 - a), top[1] * a + bot[1] * (1 - a), top[2] * a + bot[2] * (1 - a), 1]; };
  const bgOf = (el) => {
    const chain = []; for (let e = el; e; e = e.parentElement) chain.push(e);
    let bg = [255, 255, 255, 1];
    for (const e of chain.reverse()) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== 'none') return null;           // gradients/images: cannot judge
      const c = parse(cs.backgroundColor); if (c && c[3] > 0) bg = over(c, bg);
    } return bg;
  };
  const opacityOf = (el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode, t = n.textContent.trim(); if (!t) continue;
    const el = n.parentElement; if (!el || seen.has(el) || /SCRIPT|STYLE|OPTION|NOSCRIPT/.test(el.tagName) || el.closest('svg') || el.closest('pre') || el.closest('[aria-hidden=true]')) continue;
    if (!visible(el)) continue; seen.add(el);
    const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    if (fs < 12) out.small.push(`${fs}px "${t.slice(0, 30)}"`);
    else if (t.length > 40 && fs < 14) out.small.push(`${fs}px (long text) "${t.slice(0, 30)}"`);
    const fg = parse(cs.color), bg = bgOf(el);
    if (fg && bg) {
      const eff = over([fg[0], fg[1], fg[2], fg[3] * opacityOf(el)], bg);
      const L1 = lum(eff), L2 = lum(bg), ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
      const large = fs >= 24 || (fs >= 18.66 && parseInt(cs.fontWeight) >= 700);
      if (ratio < (large ? 3 : 4.5)) out.contrast.push(`${ratio.toFixed(2)} "${t.slice(0, 30)}" ${cs.color} on rgb(${bg.slice(0, 3).map(Math.round)})`);
    }
  }
  const mobile = innerWidth <= 480;
  document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[role=button]').forEach((el) => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    let w = r.width, h = r.height;
    if (el.type === 'checkbox' || el.type === 'radio') { const l = el.closest('label'); if (l) { const lr = l.getBoundingClientRect(); w = lr.width; h = lr.height; } }
    if (el.tagName === 'A' && el.closest('p,li,.sm,code')) return;   // inline text links
    if (mobile && (h < 36 || w < 36)) out.tap.push(`${Math.round(w)}x${Math.round(h)} ${el.tagName} "${(el.textContent || el.getAttribute('aria-label') || el.name || '').trim().slice(0, 20)}"`);
    const name = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || (el.labels && el.labels[0] && el.labels[0].textContent) || el.placeholder || '').trim();
    if (!name) out.names.push(`${el.tagName}#${el.id}.${el.className}`);
  });
  document.querySelectorAll('button, a.btn, .tag, .top nav a, .tabs a').forEach((el) => {
    if (!visible(el) || !el.textContent.trim()) return;
    const rg = document.createRange(); rg.selectNodeContents(el);
    const tops = new Set([...rg.getClientRects()].map((r) => Math.round(r.top / 4)));
    if (tops.size > 1) out.wrap.push(`"${el.textContent.trim().slice(0, 16)}"`);
  });
  return out;
}
"""


SETTLE_JS = """async () => {
  for (let i = 0; i < 3; i++) {   // finite entrance animations only (the recording dot / logo loop forever on purpose)
    const running = document.getAnimations().filter((a) => a.effect && a.effect.getComputedTiming().iterations !== Infinity);
    if (!running.length) break;
    await Promise.all(running.map((a) => a.finished.catch(() => null)));
  }
}"""


def settle(pg):
    """Wait until the SPA has finished loading data and playing its entrance fade, so the audit never measures a page mid-transition
    (on a slow machine the old route's <main> is still on screen while the new view is being fetched and faded in)."""
    try:
        pg.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001 - a page that never goes idle (polling) is audited after the animation wait below
        pass
    pg.evaluate(SETTLE_JS)


def audit_page(pg, label, errs, strict_contrast=True):
    pg.mouse.move(1, 1)  # a hovered button shows its orange sweep (a pseudo-element the audit cannot see), so park the pointer
    pg.wait_for_timeout(350)
    settle(pg)
    a = pg.evaluate(AUDIT_JS)
    ok(a["overflow"] <= 0, f"[{label}] horizontal overflow {a['overflow']}px")
    ok(not a["small"], f"[{label}] text too small: {a['small'][:4]}")
    if strict_contrast:
        ok(not a["contrast"], f"[{label}] low contrast: {a['contrast'][:4]}")
    ok(not a["tap"], f"[{label}] tap targets < 36px: {a['tap'][:4]}")
    ok(not a["names"], f"[{label}] controls without a name: {a['names'][:4]}")
    ok(not a["wrap"], f"[{label}] text wraps inside buttons/tabs/tags: {a['wrap'][:4]}")
    ok(a["topH"] <= 66, f"[{label}] top bar too tall ({a['topH']}px)")
    bad = [e for e in errs if e not in EXPECTED]
    ok(not bad, f"[{label}] console/page errors: {bad[:3]}")
    errs.clear()


EXPECTED: set[str] = set()


def new_page(br, w, h=860, token=None, room_theme=None, **kw):
    ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, **kw)
    if token:
        ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
    if room_theme:  # the video room remembers the chosen colours in this browser
        ctx.add_init_script(f"try{{localStorage.setItem('tt_room_theme','{room_theme}')}}catch(e){{}}")
    pg = ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
    pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
    return ctx, pg, errs


def group_section(br, D):
    """Focus group (Sprint 2): a session with several participants, a roster, and per-seat admit in the room."""
    adm, mod, pid = D["tok"]["admin"], D["tok"]["moderator"], D["pid"]
    ids = [call(f"/projects/{pid}/participants", {"name": n, "country": "SG", "language": "zh"}, adm)["id"] for n in ("座谈甲", "座谈乙", "座谈丙")]
    it = call(f"/projects/{pid}/interviews", {"kind": "group", "participant_ids": ids, "title": "三人座谈会", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid = it["id"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/p/{pid}/sessions"); pg.wait_for_selector(".tbl")
    ok("三人座谈会" in pg.inner_text(".tbl"), "[group] the session list shows the focus group")
    audit_page(pg, "group sessions/desktop", errs)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector("#groupcard")
    ok(pg.locator("#groupcard li").count() == 3 and "参与者 ·01" in pg.inner_text("#groupcard"), "[group] the roster lists three numbered seats")
    audit_page(pg, "group roster/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_roster_1280.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/p/{pid}/sessions"); pg.wait_for_selector("#ni"); pg.click("#ni"); pg.wait_for_selector("[name=kind]")
    pg.select_option("[name=kind]", "group")
    ok(pg.locator("#grouppick").is_visible() and not pg.locator("#depthpick").is_visible(), "[group] the new-session dialog switches to a multi-person picker")
    audit_page(pg, "dialog 新建座谈会/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_new_session_390.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=mod)
    pg.goto(B + f"/room.html?i={iid}&as=moderator"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#gwait [data-admit]").count() == 3, 8), "[group room] one admit button per seat")
    ok(pg.locator("#wadmit").is_hidden(), "[group room] the single-person admit row is hidden")
    audit_page(pg, "group room moderator/desktop", errs)
    pg.locator("#gwait [data-admit]").first.click()
    ok(wait_until(lambda: pg.locator("#gwait .sm", has_text="已放行").count() == 1, 8), "[group room] admitting one seat leaves the others unadmitted")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_room_moderator_1280.png"), full_page=True)
    ctx.close()
    seat = it["group_invites"][1]  # seat 1 was admitted above without consent; seat 2 walks the real path
    tok = seat["invite_token"]
    call(f"/public/join/{tok}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{tok}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {"admit": True, "participant_id": seat["participant_id"]}, mod)
    call(f"/public/join/{tok}/enter", {})
    call(f"/public/join/{tok}/captions", {"text": "我先说成分。", "final": True, "dur_ms": 800})
    call(f"/interviews/{iid}/transcript/from-captions", {}, adm)
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{iid}/transcript"); pg.wait_for_selector(".seg")
    said = pg.locator(".seg .s").all_inner_texts()
    ok("参与者 ·02" in said and "p2" not in said and "我先说成分。" in pg.inner_text("#tab"), "[group] the transcript names the seat, not the raw key")
    audit_page(pg, "group transcript/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_transcript_1280.png"), full_page=True)
    pg.goto(B + f"/#/i/{iid}/highlights"); pg.wait_for_selector(".quote")
    ok("参与者 ·02" in pg.inner_text("#tab") and "p2" not in pg.inner_text("#tab") and "我先说成分。" in pg.inner_text("#tab"),
       "[group] captions and quotes name the seat, not the raw key")
    pg.goto(B + f"/#/i/{iid}/fairness"); pg.wait_for_selector(".fair .frow")
    panel = pg.inner_text("#fairhost")  # (the roster card above it shows names to the team; the panel itself must not)
    ok(pg.locator(".fair .frow").count() == 3 and "公平指数" in panel and "p2" not in panel and not any(n in panel for n in ("座谈甲", "座谈乙", "座谈丙")),
       "[group] the speaking tab lists three numbered seats and shows no names")
    audit_page(pg, "group speaking tab/desktop", errs)
    call(f"/public/join/{tok}/reaction", {"emoji": "😕"})
    pg.goto(B + f"/#/i/{iid}/signals"); pg.wait_for_selector("#tab table.tbl")   # (the table appears once the data has arrived)
    ok("参与者 ·02" in pg.inner_text("#tab") and "😕" in pg.inner_text("#tab") and "仅供参考" in pg.inner_text("#tab"), "[group] the signals tab shows the reaction and says it is only a guide")
    audit_page(pg, "group signals tab/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_signals_1280.png"), full_page=True)
    pg.goto(B + f"/#/i/{iid}/fairness"); pg.wait_for_selector(".fair .frow")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_fairness_1280.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/i/{iid}/fairness"); pg.wait_for_selector(".fair .frow")
    audit_page(pg, "group speaking tab/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_fairness_390.png"), full_page=True)
    ctx.close()


def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/qa.db", "MEDIA_DIR": f"{tmp}/media", "LIVEKIT_URL": "", "ASR_PROVIDER": "mock", "LLM_PROVIDER": "mock", "ASR_API_KEY": "", "LLM_API_KEY": "",
           "MODERATOR_PROVIDER": "mock", "MODERATOR_API_KEY": "", "MODERATOR_VENDOR": ""}  # never use the developer's real keys or video server in QA
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(B + "/api/health"); break
            except Exception:
                time.sleep(0.25)
        run()
    finally:
        srv.terminate()
    report()


def seed():
    r = call("/auth/register", {"org_name": "Acme 出海", "name": "王总", "email": "boss@acme.com", "password": "password123"})
    adm = r["token"]
    tok = {"admin": adm}
    ids = {}
    for role, name in (("researcher", "李研究"), ("moderator", "小周"), ("observer", "客户张")):
        u = call("/users", {"name": name, "email": f"{role}@acme.com", "password": "password123", "role": role}, adm)
        ids[role] = u["id"]
        tok[role] = call("/auth/login", {"email": f"{role}@acme.com", "password": "password123"})["token"]
    pid = call("/projects", {"name": "新加坡护肤品牌进入研究", "brief": "了解新加坡消费者如何选购海外护肤品牌", "market": "SG", "language": "en"}, adm)["id"]
    mk = lambda name, country="SG", lang="en": call(f"/projects/{pid}/participants", {"name": name, "country": country, "language": lang}, adm)
    people = {"en": mk("Wei Ling"), "zh": mk("陈美玲", lang="zh"), "ms": mk("Nur Aisyah", lang="ms"), "ta": mk("Kavitha", lang="ta"), "id": mk("Siti", "ID")}
    itv = {}
    for lang in ("en", "zh", "ms", "ta"):
        itv[lang] = call(f"/projects/{pid}/interviews", {"participant_id": people[lang]["id"], "title": f"深访 {lang}", "language": lang,
                                                         "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}, adm)
    p2 = mk("Priya")
    itv["en2"] = call(f"/projects/{pid}/interviews", {"participant_id": p2["id"], "title": "深访 en2", "language": "en"}, adm)  # untouched: used for the consent journey
    for lang in ("en", "zh", "ms", "ta"):  # AI-moderated sessions: their consent page must disclose the AI (task doc part 3)
        pa = mk(f"AI {lang}", lang=lang)
        itv[f"ai_{lang}"] = call(f"/projects/{pid}/interviews", {"participant_id": pa["id"], "title": f"AI 深访 {lang}", "language": lang, "moderation": "ai",
                                                            "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}, adm)
    main_i = itv["en"]
    # drive the main interview through the whole pipeline
    call(f"/public/join/{main_i['invite_token']}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{main_i['id']}/start", {}, tok["moderator"])
    call(f"/interviews/{main_i['id']}/recording/start", {}, tok["moderator"])
    call(f"/interviews/{main_i['id']}/end", {}, tok["moderator"])
    call(f"/interviews/{main_i['id']}/transcribe", {}, adm)
    gen = call(f"/interviews/{main_i['id']}/insights/generate", {}, adm)
    call(f"/insights/{gen['created'][0]['id']}/approve", {}, adm)
    clip = call(f"/interviews/{main_i['id']}/clips", {"evidence_id": gen["created"][0]["evidence"][0]["id"]}, adm)
    share = call(f"/clips/{clip['id']}/share", {}, adm)
    # a second organisation: the demo company (every account @demo.test) is the only one allowed to clear its data
    dadm = call("/auth/register", {"org_name": "演示公司", "name": "管理员", "email": "admin@demo.test", "password": "demo12345"})["token"]
    dpid = call("/projects", {"name": "演示项目", "market": "SG", "language": "en"}, dadm)["id"]
    dpart = call(f"/projects/{dpid}/participants", {"name": "测试受访者", "country": "SG", "language": "en"}, dadm)
    call(f"/projects/{dpid}/interviews", {"participant_id": dpart["id"], "title": "演示访谈"}, dadm)
    tok["demo_admin"] = dadm
    ccode = call(f"/projects/{pid}/codes", {"name": "信任与认证"}, adm)
    call(f"/projects/{pid}/codes/suggest", {}, adm)  # one accepted code below, the rest stay suggestions
    inv = call("/users/invites", {"email": "newbie@acme.com", "role": "researcher"}, adm)
    rst = call(f"/users/{ids['moderator']}/reset-link", {}, adm)
    return dict(inv=inv["invite_path"], rst=rst["invite_path"], tok=tok, pid=pid, itv=itv, main=main_i, share=share, ids=ids, people=people, demo_pid=dpid)


def concept_room_section(br, D):
    """The concept-test room in dev mode (no video): the script, the material on screen, captions with quote / follow-up, for every role."""
    pid, adm, mod, obs = D["pid"], D["tok"]["admin"], D["tok"]["moderator"], D["tok"]["observer"]
    part = call(f"/projects/{pid}/participants", {"name": "概念测试受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "概念测试", "language": "zh", "moderator_id": D["ids"]["moderator"],
                                              "observer_ids": [D["ids"]["observer"]]}, adm)
    iid, T = it["id"], it["invite_token"]
    call(f"/interviews/{iid}/concept/template", {"template": "loreal_skincare_concept"}, adm)
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {"admit": True}, mod)
    call(f"/public/join/{T}/enter", {})
    for who, text in (("m", "先聊聊你平时的护肤习惯。"), ("p", "我平时先看成分，再看价格，最担心买到假货。"), ("m", "你为什么会选这一款？"), ("p", "因为朋友推荐，而且旗舰店有保障。")):
        if who == "m":
            call(f"/interviews/{iid}/captions", {"text": text, "final": True}, mod)
        else:
            call(f"/public/join/{T}/captions", {"text": text, "final": True})
    lines = call(f"/interviews/{iid}/captions?after=0", token=mod)["lines"]
    call(f"/interviews/{iid}/highlights", {"kind": "quote", "caption_id": lines[1]["id"]}, obs)
    call(f"/interviews/{iid}/highlights", {"kind": "probe", "caption_id": lines[1]["id"]}, mod)
    call(f"/interviews/{iid}/highlights", {"kind": "probe", "text": "价格多少算贵？"}, obs)
    call(f"/interviews/{iid}/backroom", {"text": "能追问一下价格吗？"}, obs)
    call(f"/interviews/{iid}/backroom", {"text": "好的，我下一题问。"}, mod)
    shown = next(s for s in call(f"/interviews/{iid}/concept", token=mod)["stimuli"] if s["key"] == "concept_a")
    call(f"/interviews/{iid}/stimulus/show", {"stimulus_id": shown["id"]}, mod)
    for w, vp in ((1280, "desktop"), (390, "phone")):
        for theme in (None, "dark"):
            tag = f"concept room/{theme or 'light'}/{vp}"
            for role, asr in (("moderator", "moderator"), ("observer", "observer")):
                ctx, pg, errs = new_page(br, w, token=D["tok"][role], room_theme=theme)
                pg.goto(B + f"/room.html?i={iid}&as={asr}"); pg.wait_for_selector("#claims", timeout=8000)
                ok(wait_until(lambda: pg.locator("#caps .cap").count() == 4 and pg.locator("#probes .pr").count() == 2, 10), f"[{tag} {role}] four sentences and two follow-ups are on screen")
                ok(wait_until(lambda: pg.locator("#brlog .m").count() == 2, 8) and pg.locator("#brform").count() == 1, f"[{tag} {role}] the private chat shows both messages and has an input")
                ok(pg.locator("#caps .cap .hd button", has_text="引用").count() == 4 and pg.locator("#caps .cap .hd button", has_text="追问").count() == 4, f"[{tag} {role}] every sentence has quote / follow-up buttons")
                ok(wait_until(lambda: pg.locator("#stim img").is_visible(), 8), f"[{tag} {role}] the material on screen is visible")
                if role == "moderator":
                    ok(wait_until(lambda: pg.locator("#plan details").count() == 5 and "已用" in pg.inner_text("#plantm"), 8), f"[{tag}] the script and the timer are shown")
                    ok(pg.locator("#plan button[data-show]").count() == 3 and pg.locator("#plan .q input").count() == 18, f"[{tag}] 3 materials to show and 18 tick boxes for the questions")
                    pg.evaluate("document.querySelectorAll('#plan details').forEach((d) => (d.open = true))")  # audit the opened script too
                else:
                    ok(pg.locator("#planbox").is_hidden() and pg.locator("#probes .pr button", has_text="已问").count() == 0, f"[{tag}] the observer has no script and cannot mark a follow-up as asked")
                audit_page(pg, f"{tag} {role}", errs)
                ctx.close()
            ctx, pg, errs = new_page(br, w, room_theme=theme)  # the participant: the picture only, nothing internal
            pg.goto(B + f"/room.html?t={T}"); pg.wait_for_selector("#claims", timeout=8000)
            ok(wait_until(lambda: pg.locator("#stim img").is_visible(), 8), f"[{tag} participant] the participant sees the material the moderator shows")
            ok(pg.locator("#caps, #probes, #brlog, #brform").count() == 0 and pg.locator("#lpan").is_hidden() and pg.locator("#rpan").is_hidden(), f"[{tag} participant] no script, captions, follow-ups or private chat")
            audit_page(pg, f"{tag} participant", errs)
            ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=adm)  # after the interview: quotes, follow-ups and the caption record on the interview page (team only)
    pg.goto(B + f"/#/i/{iid}/highlights"); pg.wait_for_selector("#tab .card")
    ok(pg.locator("#tab .card").count() == 3 and "最担心买到假货" in pg.inner_text("#tab"), "[interview page] quotes, follow-ups and the caption record are listed")
    audit_page(pg, "interview highlights/desktop", errs)
    ctx.close()
    for role in ("moderator", "observer"):
        c, g, e = new_page(br, 1280, token=D["tok"][role])
        g.goto(B + f"/#/i/{iid}"); g.wait_for_selector("main .tabs")
        ok(g.locator(".tabs a:has-text('引用与追问')").count() == 0, f"[interview page] {role} does not get the quotes tab")
        c.close()


def ai_room_section(br, D):
    """An AI-moderated interview in the room, seen by the participant, the moderator and a hidden observer."""
    it, mod, obs, adm = D["itv"]["ai_zh"], D["tok"]["moderator"], D["tok"]["observer"], D["tok"]["admin"]
    tk = it["invite_token"]
    call(f"/public/join/{tk}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{tk}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    call(f"/interviews/{it['id']}/start", {}, mod)
    call(f"/interviews/{it['id']}/admit", {"admit": True}, mod)
    call(f"/public/join/{tk}/enter", {})
    log_texts = lambda pg: pg.locator("#clog .m").all_inner_texts()
    for theme in (None, "dark"):
        for w, vp in ((1280, "desktop"), (390, "phone")):
            tag = f"{theme or 'light'}/{vp}"
            if theme is None and vp == "desktop":
                # the moderator before the AI starts: the panel, the start button, no reply box
                ctx, pg, errs = new_page(br, w, token=mod, room_theme=theme)
                pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#aist")
                ok("还没有启动" in pg.inner_text("#aist") and pg.is_enabled("#a-start") and pg.is_disabled("#a-take"), f"[ai room moderator {tag}] before starting: start is on, take over is off")
                ok(pg.locator("#hform:visible").count() == 0, f"[ai room moderator {tag}] the human reply box is hidden while the AI is in control")
                audit_page(pg, f"ai room moderator before start/{tag}", errs)
                ctx.close()
                call(f"/interviews/{it['id']}/ai/start", {}, mod)
            ctx, pg, errs = new_page(br, w, token=mod, room_theme=theme)
            pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#clog .m")
            ok("AI 正在主持" in pg.inner_text("#aist"), f"[ai room moderator {tag}] status line shows who is moderating")
            ok(any("AI" in x and "不是真人" in x for x in log_texts(pg)), f"[ai room moderator {tag}] the AI's opening says it is an AI")
            ok(pg.locator("#clog .m details").count() >= 1, f"[ai room moderator {tag}] the AI's reasoning is available to the team")
            ok(pg.is_visible("#wform") and pg.is_visible("#win"), f"[ai room moderator {tag}] whisper box present")
            audit_page(pg, f"ai room moderator running/{tag}", errs)
            ctx.close()
            # the participant: a chat in the interview's language, nothing internal
            ctx, pg, errs = new_page(br, w, room_theme=theme)
            pg.goto(B + f"/room.html?t={tk}"); pg.wait_for_selector("#cin")
            ok(pg.locator("#lpan:visible, #rpan:visible").count() == 0, f"[ai room participant {tag}] no internal panels")
            ok(wait_until(lambda: any("AI 主持人" in x for x in log_texts(pg)), 8), f"[ai room participant {tag}] sees the AI's opening, labelled")
            ok(pg.locator("#clog .m details").count() == 0 and "悄悄话" not in pg.inner_text("body"), f"[ai room participant {tag}] sees no notes or whispers")
            audit_page(pg, f"ai room participant/{tag}", errs)
            if theme is None and vp == "phone":
                pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_room_participant_390.png"), full_page=True)
            ctx.close()
            # the hidden observer: reads, cannot act
            ctx, pg, errs = new_page(br, w, token=obs, room_theme=theme)
            pg.goto(B + f"/room.html?i={it['id']}&as=observer"); pg.wait_for_selector("#clog .m")
            ok(pg.locator("#a-start, #a-take, #a-back, #wform, #hform, #a-ack").count() == 0, f"[ai room observer {tag}] no controls")
            ok(pg.locator("#clog .m details").count() == 0 and "悄悄话" not in pg.inner_text("#clog"), f"[ai room observer {tag}] sees the conversation only")
            audit_page(pg, f"ai room observer/{tag}", errs)
            ctx.close()
    # a live conversation, once: the participant types, the AI answers; whisper, take over, hand back; a crisis shows a red alert to the team only
    ctx, pg, errs = new_page(br, 1280, token=mod)
    pgp_ctx, pgp, perrs = new_page(br, 390)
    pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#clog .m")
    pgp.goto(B + f"/room.html?t={tk}"); pgp.wait_for_selector("#cin")
    ok(wait_until(lambda: pgp.is_enabled("#cin"), 8), "[ai room] the participant can type once the AI has started")
    pgp.fill("#cin", "我平时在网上买护肤品，因为方便。"); pgp.click("#csend")
    ok(wait_until(lambda: len(log_texts(pgp)) >= 3, 12) and "我平时在网上买护肤品" in pgp.inner_text("#clog"), "[ai room] the participant's line and the AI's reply appear")
    pg.fill("#win", "多问问退货的体验"); pg.click("#wsend")
    ok(wait_until(lambda: any("多问问退货" in x for x in log_texts(pg)), 8), "[ai room] the whisper shows in the team's log")
    ok("多问问退货" not in pgp.inner_text("body"), "[ai room] the participant never sees a whisper")
    pg.click("#a-take"); ok(wait_until(lambda: pg.is_visible("#hform"), 8) and "已由你接管" in pg.inner_text("#aist"), "[ai room] taking over shows the reply box")
    n = len(log_texts(pgp)); pgp.fill("#cin", "我想想怎么说。"); pgp.click("#csend")
    ok(wait_until(lambda: "我想想怎么说" in pgp.inner_text("#clog"), 8), "[ai room] the participant's line arrives while a human has control")
    pgp.wait_for_timeout(2500); after = log_texts(pgp); ok(len(after) == n + 1, f"[ai room] the AI stays silent while a human has control (lines before {n}, after {len(after)}: {[x[:30] for x in after[n:]]})")
    pg.fill("#hin", "不着急，慢慢说。"); pg.click("#hsend")
    ok(wait_until(lambda: "不着急，慢慢说" in pgp.inner_text("#clog"), 8), "[ai room] the human's line reaches the participant")
    audit_page(pg, "ai room moderator in control/desktop", errs)
    pg.click("#a-back"); ok(wait_until(lambda: "AI 正在主持" in pg.inner_text("#aist"), 8), "[ai room] handing back returns the AI")
    pgp.fill("#cin", "我不想活了。"); pgp.click("#csend")
    ok(wait_until(lambda: pg.is_visible("#aalert") and "红色" in pg.inner_text("#aalert"), 10), "[ai room] a crisis shows the team a red alert")
    ok("红色" not in pgp.inner_text("body") and pgp.locator("#aalert").count() == 0, "[ai room] the participant sees no alert")
    audit_page(pg, "ai room moderator red alert/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_room_moderator_1280.png"), full_page=True)
    pg.click("#a-ack"); ok(wait_until(lambda: not pg.is_visible("#aalert"), 8), "[ai room] acknowledging clears the alert")
    ctx.close(); pgp_ctx.close()
    # ending turns the chat into a transcript with the AI labelled
    call(f"/interviews/{it['id']}/end", {}, mod)
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{it['id']}/transcript"); pg.wait_for_selector(".seg")
    ok(pg.locator(".seg .s:has-text('AI 主持人')").count() >= 1 and pg.locator(".seg .s:has-text('受访者')").count() >= 1, "[ai room] the transcript labels the AI moderator")
    audit_page(pg, "ai interview transcript/desktop", errs)
    ctx.close()


def pretask_section(br, D):
    """Pre-interview task (Sprint 1): opt-in questions the participant answers after consent, before the waiting room."""
    adm, mod, pid = D["tok"]["admin"], D["tok"]["moderator"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "预访问受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "预访问深访", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid, T = it["id"], it["invite_token"]
    # (a) staff config card on the session page: one required + one optional question
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector("#pretaskcard #pted")
    pg.click("#pted"); pg.wait_for_selector(".modal #ptrows .ptrow")
    pg.locator(".modal .ptq").first.fill("你平时的护肤步骤是怎样的？")
    pg.click(".modal #ptadd"); pg.wait_for_selector(".modal .ptrow:nth-child(2)")
    pg.locator(".modal .ptq").nth(1).fill("最近一次买护肤品是什么情况？")
    pg.locator(".modal .ptrow").nth(1).locator(".ptr").uncheck()  # the second question is optional
    audit_page(pg, "pretask config dialog/desktop", errs)
    pg.click(".modal form button.p"); pg.wait_for_selector("#pretaskcard .ptlist li")
    ok(pg.locator("#pretaskcard .ptlist li").count() == 2, "[pretask] the configured questions show on the session page")
    audit_page(pg, "pretask session card/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "pretask_session_card_1280.png"), full_page=True)
    ctx.close()
    # (b) participant journey: device + consent via the API, then the browser resumes at the new pre-task step
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    ctx, pg, errs = new_page(br, 390)
    pg.goto(B + f"/join.html?t={T}"); pg.wait_for_selector("#pf")
    ok(pg.locator(".psteps i").count() == 6, "[pretask] the step bar gains a pre-task step (6 in all)")
    ok("访谈前问题" in pg.inner_text(".pstep") and pg.locator("#pf textarea").count() == 2, "[pretask] the participant sees the configured questions")
    ok("预访问深访" not in pg.inner_text("#box"), "[pretask] the internal session title never leaks to the participant")
    audit_page(pg, "pretask participant step/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "join_pretask_390.png"), full_page=True)
    pg.locator("#pf textarea").first.fill("洁面、爽肤水、面霜")
    pg.click("#pf button"); pg.wait_for_selector("#pw")
    ok("候场室" in pg.inner_text("#box"), "[pretask] submitting the answers reaches the waiting room")
    ctx.close()
    # (c) the moderator sees the answers in the room; the client observer never does
    ctx, pg, errs = new_page(br, 1280, token=mod)
    pg.goto(B + f"/room.html?i={iid}&as=moderator"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#prebox:visible").count() == 1, 8), "[pretask] the moderator sees the pre-task answers in the room")
    ok("洁面、爽肤水、面霜" in pg.inner_text("#prebox"), "[pretask] the participant's answer is shown to the moderator")
    audit_page(pg, "pretask room moderator/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "pretask_room_moderator_1280.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=D["tok"]["observer"])
    pg.goto(B + f"/room.html?i={iid}&as=observer"); pg.wait_for_selector("#claims")
    ok(not pg.is_visible("#prebox"), "[pretask] the client observer never sees the pre-task answers")
    ctx.close()


def timeslot_section(br, D):
    """Candidate interview times (Sprint 1): the team offers a few, the participant picks one."""
    adm, pid = D["tok"]["admin"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "选时间受访者", "country": "SG", "language": "en"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "选时间深访", "language": "en",
                                              "moderator_id": D["ids"]["moderator"]}, adm)
    iid, T = it["id"], it["invite_token"]
    # (a) staff config card: offer two candidate times through the dialog
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector("#timeslotcard #tsed")
    pg.click("#tsed"); pg.wait_for_selector(".modal #tsrows .tsq")
    pg.fill(".modal .tsq", "2099-01-01T09:00")
    pg.click(".modal #tsadd"); pg.wait_for_selector(".modal .ptrow:nth-child(2) .tsq")
    pg.locator(".modal .tsq").nth(1).fill("2099-01-02T14:00")
    audit_page(pg, "timeslot config dialog/desktop", errs)
    pg.click(".modal form button.p"); pg.wait_for_selector("#timeslotcard .ptlist li")
    ok(pg.locator("#timeslotcard .ptlist li").count() == 2, "[timeslot] configured candidate times show on the session page")
    audit_page(pg, "timeslot session card/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "timeslot_session_card_1280.png"), full_page=True)
    ctx.close()
    # (b) participant: the invitation step offers the times; picking one confirms it and moves on to the invitation card
    ctx, pg, errs = new_page(br, 390)
    pg.goto(B + f"/join.html?t={T}"); pg.wait_for_selector("#tf input[name=slot]")
    ok(pg.locator("#tf input[name=slot]").count() == 2 and pg.locator("#accept").count() == 0, "[timeslot] the participant must choose a time before anything else")
    ok("选时间深访" not in pg.inner_text("#box"), "[timeslot] the internal session title never leaks to the participant")
    audit_page(pg, "timeslot participant pick/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "join_timeslot_390.png"), full_page=True)
    pg.locator("#tf input[name=slot]").first.check(); pg.click("#tf button")
    pg.wait_for_selector("#accept")
    ok(call(f"/interviews/{iid}", token=adm)["scheduled_at"] is not None, "[timeslot] picking a time writes it back to the session")
    ctx.close()
    # (c) the session page now marks which time the participant chose
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector("#timeslotcard .ptlist")
    ok(pg.locator("#timeslotcard .tag.ok").count() == 1, "[timeslot] the session page marks the time the participant chose")
    ctx.close()


def marks_section(br, D):
    """Material marking (Sprint 1): the participant taps ♥ like / ✕ dislike on an image, the team reads it as a heatmap."""
    adm, mod, obs, pid = D["tok"]["admin"], D["tok"]["moderator"], D["tok"]["observer"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "圈选受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "素材圈选深访", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid, T = it["id"], it["invite_token"]
    call(f"/interviews/{iid}/concept/template", {"template": "loreal_skincare_concept"}, adm)
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {"admit": True}, mod)
    call(f"/public/join/{T}/enter", {})
    shown = next(s for s in call(f"/interviews/{iid}/concept", token=mod)["stimuli"] if s["key"] == "concept_a")
    call(f"/interviews/{iid}/stimulus/show", {"stimulus_id": shown["id"]}, mod)
    # (a) participant: a toolbar and tap-to-mark on the image (like, then dislike); own pins can be removed
    ctx, pg, errs = new_page(br, 390)
    pg.goto(B + f"/room.html?t={T}"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#stim .stimwrap img").is_visible(), 8), "[marks participant] the image is shown with a mark layer")
    ok(pg.locator("#stim .marktools .mk").count() == 2, "[marks participant] the ♥ like / ✕ dislike toolbar is present")
    pg.locator("#stim .stimwrap").click(position={"x": 60, "y": 60})
    ok(wait_until(lambda: pg.locator("#stim .pins button.pin.like").count() == 1, 8), "[marks participant] tapping adds a ♥ like pin")
    pg.click("#stim .marktools .mk.dislike")
    pg.locator("#stim .stimwrap").click(position={"x": 160, "y": 90})
    ok(wait_until(lambda: pg.locator("#stim .pins button.pin.dislike").count() == 1, 8), "[marks participant] switching to ✕ adds a dislike pin")
    audit_page(pg, "marks participant/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "marks_participant_390.png"), full_page=True)
    pg.locator("#stim .pins button.pin.dislike").first.click()
    ok(wait_until(lambda: pg.locator("#stim .pins button.pin.dislike").count() == 0, 8), "[marks participant] tapping a own pin removes it")
    ctx.close()
    # (b) moderator: a read-only live overlay of the participant's marks (polled)
    ctx, pg, errs = new_page(br, 1280, token=mod)
    pg.goto(B + f"/room.html?i={iid}&as=moderator"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#stim .pins span.pin").count() >= 1, 10), "[marks moderator] sees the live overlay of the participant's marks")
    ok(pg.locator("#stim .marktools").count() == 0, "[marks moderator] the moderator cannot place marks (overlay only)")
    audit_page(pg, "marks room moderator/desktop", errs)
    ctx.close()
    # (c) the client observer never sees the marks
    ctx, pg, errs = new_page(br, 1280, token=obs)
    pg.goto(B + f"/room.html?i={iid}&as=observer"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#stim img").is_visible(), 8), "[marks observer] sees the image")
    ok(not wait_until(lambda: pg.locator("#stim .pins .pin").count() >= 1, 3), "[marks observer] never sees any marks")
    ctx.close()
    # (d) staff heatmap card on the session page: the image with counts and pins
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector("#markcard .hmstim")
    ok(pg.locator("#markcard .hmstim").count() >= 1 and pg.locator("#markcard .pins .pin").count() >= 1, "[marks] the heatmap card shows the image with marks overlaid")
    ok("♥ 1" in pg.inner_text("#markcard"), "[marks] the heatmap shows the like count")
    audit_page(pg, "marks heatmap card/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "marks_heatmap_card_1280.png"), full_page=True)
    ctx.close()


def run():
    os.makedirs(os.path.join(ROOT, "qa_shots"), exist_ok=True)
    D = seed()
    mi, pid = D["main"]["id"], D["pid"]
    proj = [f"#/p/{pid}/rooms", f"#/p/{pid}/insights"]
    staff_routes = [f"#/projects", f"#/p/{pid}/home", f"#/p/{pid}/people", f"#/p/{pid}/sessions", f"#/p/{pid}/analysis"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights", f"#/i/{mi}/clips"]
    role_routes = {
        "demo_admin": ["#/projects", f"#/p/{D['demo_pid']}/home", "#/demo"],
        "admin": staff_routes + ["#/team", "#/audit"],
        "researcher": staff_routes,
        "moderator": ["#/projects", f"#/p/{pid}"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights"],
        "observer": ["#/projects", f"#/p/{pid}"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights", f"#/i/{mi}/clips"],
    }
    public_pages = {
        "join-en": f"/join.html?t={D['itv']['en']['invite_token']}", "join-zh": f"/join.html?t={D['itv']['zh']['invite_token']}",
        "join-ms": f"/join.html?t={D['itv']['ms']['invite_token']}", "join-ta": f"/join.html?t={D['itv']['ta']['invite_token']}",
        "invite-join": D["inv"], "invite-reset": D["rst"], "invite-invalid": "/invite.html?t=nope", "join-invalid": "/join.html?t=nope", "share": D["share"]["path"], "share-invalid": "/share.html?t=nope",
    }
    with sync_playwright() as p:
        br = p.chromium.launch()
        # ---------- A. every role x page x viewport ----------
        for w, vp in ((1280, "desktop"), (390, "phone")):
            for role, routes in role_routes.items():
                ctx, pg, errs = new_page(br, w, token=D["tok"][role])
                for r in routes:
                    EXPECTED.clear()
                    pg.goto(B + "/" + r); pg.wait_for_selector("main", timeout=8000)
                    body = pg.inner_text("main")
                    ok("请求失败" not in body and "不存在" not in body, f"[{role}/{vp}] {r} shows an error: {body[:60]!r}")
                    audit_page(pg, f"{role}/{vp} {r}", errs)
                ctx.close()
            # dark mode (design spec: follows the system): same contrast / size rules for the staff pages
            if vp == "desktop":
                ctx, pg, errs = new_page(br, w, token=D["tok"]["admin"], color_scheme="dark")
                for r in role_routes["admin"]:
                    EXPECTED.clear()
                    pg.goto(B + "/" + r); pg.wait_for_selector("main", timeout=8000)
                    audit_page(pg, f"admin/dark {r}", errs)
                ctx.close()
            for name, path in public_pages.items():
                ctx, pg, errs = new_page(br, w)
                pg.goto(B + path); pg.wait_for_timeout(500)
                EXPECTED.clear()
                # the invalid pages (join, invite) and the share page's missing media file legitimately produce 4xx console lines
                errs[:] = [e for e in errs if "Failed to load resource" not in e]
                audit_page(pg, f"public/{vp} {name}", errs)
                ctx.close()
            # room pages (dev mode shows the real grants), in both colour themes: white light (default) and dark green
            for theme in (None, "dark"):
                for role, asr in (("moderator", "moderator"), ("observer", "observer"), ("admin", "observer")):
                    ctx, pg, errs = new_page(br, w, token=D["tok"][role], room_theme=theme)
                    # the main interview has ended; use a live-able one
                    it = D["itv"]["zh"]
                    pg.goto(B + f"/room.html?i={it['id']}&as={asr}"); pg.wait_for_selector("#claims", timeout=8000)
                    claims = json.loads(pg.inner_text("#claims"))
                    want = "t-dark" if theme else "t-light"
                    ok(want in pg.evaluate("document.body.className"), f"[room {role}/{vp}] theme should be {want}")
                    if asr == "observer":
                        ok(claims["hidden"] is True and claims["canPublish"] is False and claims["canPublishData"] is False, f"[room {role}/{vp}] observer grants wrong: {claims}")
                        ok(pg.locator("button:has-text('开始录制')").count() == 0, f"[room {role}/{vp}] observer sees record button")
                        ok(not pg.is_visible("#wbox"), f"[room {role}/{vp}] observer must not see the waiting room controls")
                    else:
                        ok(claims["canPublish"] is True and claims["hidden"] is False, f"[room {role}/{vp}] moderator grants wrong")
                        ok(pg.is_visible("#b-admit") and pg.is_visible("#b-hold"), f"[room {role}/{vp}] moderator has admit / hold")
                    audit_page(pg, f"room-{role}/{theme or 'light'}/{vp}", errs)
                    ctx.close()
            # theme switch: one click, takes effect at once, remembered in this browser
            ctx, pg, errs = new_page(br, w, token=D["tok"]["moderator"])
            it = D["itv"]["zh"]
            pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#claims")
            ok("深绿" in pg.inner_text("#theme"), f"[{vp}] theme button offers dark green")
            pg.click("#theme"); ok("t-dark" in pg.evaluate("document.body.className") and pg.evaluate("localStorage.getItem('tt_room_theme')") == "dark", f"[{vp}] switching to dark green works and is remembered")
            pg.reload(); pg.wait_for_selector("#claims"); ok("t-dark" in pg.evaluate("document.body.className") and "浅色" in pg.inner_text("#theme"), f"[{vp}] the room opens in the remembered theme")
            pg.click("#theme"); ok("t-light" in pg.evaluate("document.body.className"), f"[{vp}] switching back to light works")
            ctx.close()

        # ---------- B. journeys & logic ----------
        ctx, pg, errs = new_page(br, 1280)
        pg.goto(B + "/#/projects"); pg.wait_for_selector("form#f")
        ok("/#/login" in pg.url or "login" in pg.url, "logged-out visit to a protected page should redirect to login")
        pg.fill("[name=email]", "boss@acme.com"); pg.fill("[name=password]", "wrong-password"); pg.click("form button")
        pg.wait_for_selector(".toast.err"); ok("不对" in pg.inner_text(".toast"), "wrong password should show an error toast")
        pg.fill("[name=password]", "password123"); pg.click("form button"); pg.wait_for_selector("text=新建项目")
        ok(pg.locator("nav.side .brandsvg").count() == 1, "logo missing in the side nav")
        ok("projects" in pg.url, "after login should land on projects")
        # logo plays on login, then settles; hover replays; reduced motion respected
        try:  # the page's own timer ends the animation 3.2 s after it starts; wait for that (up to 8 s) instead of guessing a fixed delay
            pg.wait_for_function("!document.querySelector('.brandsvg').classList.contains('play')", timeout=8000); settled = True
        except Exception:  # noqa: BLE001 - a timeout here IS the failure being tested
            settled = False
        ok(settled, "logo animation never settles")
        pg.hover("nav.side .brandsvg"); pg.wait_for_timeout(120); ok(pg.evaluate("document.querySelector('.brandsvg').classList.contains('play')"), "hovering the logo should replay the wings")
        # the side nav marks exactly one current page, and it is the one shown
        pg.goto(B + f"/#/p/{pid}/sessions"); pg.wait_for_selector("h1:has-text('场次与邀请')")
        cur = pg.locator("nav.side [aria-current=page]").all_inner_texts()
        ok(cur == ["场次与邀请"], f"side nav should mark only 场次与邀请 as current, got {cur}")
        # project home: big progress number, a next step that links to the right page, recent activity in plain words
        pg.goto(B + f"/#/p/{pid}"); pg.wait_for_selector("h1:has-text('项目总览')")
        ok(pg.locator(".hero-n b").inner_text().isdigit() and pg.locator(".meters .meter").count() == 3, "project home shows the big number and three progress bars")
        ok(pg.inner_text("#nextstep").strip() != "" and pg.locator(".next a.btn").count() == 1, "project home says what to do next, with one button")
        feed = pg.inner_text(".feed")  # newest 12 events; older ones are covered by test_journey.py
        ok(1 <= pg.locator(".feed li").count() <= 12 and "受访者 打开了邀请" in feed and "客户 客户" not in feed, f"recent activity in plain words: {feed[:80]!r}")
        ok("Wei Ling" not in feed and "127.0.0.1" not in feed, "activity shows no participant names or IP addresses")
        # project list: cards with counts, search and status filter
        pg.goto(B + "/#/projects"); pg.wait_for_selector(".pcard")
        ok("受访者" in pg.inner_text(".pcard") and "场次完成" in pg.inner_text(".pcard"), "project cards show participants and finished sessions")
        pg.fill("#pq", "没有这个项目"); ok(pg.is_visible("#pnone") and pg.locator(".pcard:visible").count() == 0, "search with no match explains what to do")
        pg.fill("#pq", "护肤"); ok(pg.locator(".pcard:visible").count() == 1, "search finds the project by name")
        pg.fill("#pq", "")
        # project: close / reopen shows the deletion date
        pg.goto(B + f"/#/p/{pid}"); pg.wait_for_selector("#closeproj")
        pg.once("dialog", lambda d: d.accept()); pg.click("#closeproj"); pg.wait_for_selector("#reopen")
        ok("自动删除" in pg.inner_text("main"), "closed project should state the auto-delete date")
        pg.goto(B + "/#/projects"); pg.wait_for_selector("[data-pf=closed]")
        ok(pg.locator(".pcard:visible").count() == 0, "a closed project is not in the default 'in progress' filter")
        pg.click("[data-pf=closed]"); ok(pg.locator(".pcard:visible").count() == 1, "the 'closed' filter shows it"); pg.click("[data-pf=open]")
        pg.goto(B + f"/#/p/{pid}"); pg.wait_for_selector("#reopen"); pg.click("#reopen"); pg.wait_for_selector("#closeproj")
        # SG only: add participant dialog offers only Singapore; staff cannot create other-country interviews
        pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector("#np")
        pg.click("#np"); opts = pg.locator("#f [name=country] option").all_inner_texts(); ok(len(opts) == 1 and "SG" in opts[0], f"only Singapore should be selectable, got {opts}")
        pg.keyboard.press("Escape"); pg.mouse.click(5, 5)
        # id participant shows 'not open'
        pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector("text=地区暂未开放")
        # rooms are grouped: in progress / upcoming / ended
        pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector(".group h2")
        groups = pg.locator(".group h2").all_inner_texts(); ok(groups == ["待开始", "已结束"], f"room list grouped by status, got {groups}")
        # team page: observer role label
        pg.goto(B + "/#/team"); pg.wait_for_selector("text=客户张"); ok("观察员" in pg.inner_text("main"), "team page should show role names")
        ok(pg.locator("nav.side a:has-text('演示数据')").count() == 0, "a real company has no 'clear demo data' entry")
        pg.goto(B + "/#/demo"); pg.wait_for_selector("h1"); ok("demo" not in pg.url, "a real company cannot open the demo data page")
        # logout
        pg.click("#out"); pg.wait_for_selector("form#f"); ok(pg.evaluate("localStorage.getItem('tt_token')") is None, "logout should clear the token")
        ctx.close()
        # phones: list tables turn into labelled cards
        ctx, pg, errs = new_page(br, 390, token=D["tok"]["admin"])
        pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector(".tbl.rc")
        ok(pg.evaluate("getComputedStyle(document.querySelector('.tbl.rc thead')).display") == "none", "phone: table header hidden, rows shown as cards")
        ok(pg.evaluate("document.querySelector('.tbl.rc tbody td').dataset.l") == "编号", "phone: every card line carries its column name")
        ctx.close()
        # demo company: the admin can clear the demo data (and only that)
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["demo_admin"])
        pg.goto(B + "/#/demo"); pg.wait_for_selector("#demoreset")
        ok(pg.locator("nav.side a:has-text('演示数据')").count() == 1, "the demo company's admin sees the 'demo data' entry")
        pg.once("dialog", lambda d: d.accept()); pg.click("#demoreset"); pg.wait_for_selector("#demodone:not([hidden])")
        ok("1 场访谈" in pg.inner_text("#demodone") and "python -m app.seed" in pg.inner_text("#demodone"), "after clearing, says what was removed and how to get fresh demo data")
        ok(call(f"/projects/{pid}/interviews", token=D["tok"]["admin"]) != [], "clearing demo data never touches a real company")
        ctx.close()

        # role-based UI: what each role must NOT see
        def see(role, route, sel):
            c, g, e = new_page(br, 1280, token=D["tok"][role]); g.goto(B + "/" + route); g.wait_for_selector("main"); g.wait_for_timeout(300)
            n = g.locator(sel).count(); c.close(); return n
        ok(see("observer", "#/projects", "#np") == 0, "observer must not see 'new project'")
        ok(see("moderator", "#/projects", "#np") == 0, "moderator must not see 'new project'")
        ok(see("observer", f"#/i/{mi}/insights", "#gen") == 0, "observer must not see 'generate insights'")
        ok(see("observer", f"#/i/{mi}", "#inv, #rot") == 0, "observer must never see the invite link")
        ok(see("observer", f"#/i/{mi}/clips", "[data-sh]") == 0, "observer must not see 'share link'")
        ok(see("researcher", "#/team", "#nu") == 0 and see("researcher", "#/team", "text=添加成员") == 0, "researcher must not manage the team")
        ok(see("admin", "#/team", "#nu") == 1, "admin should manage the team")
        ok(see("observer", f"#/i/{mi}", "text=Wei Ling") == 0, "observer must not see the participant's name")
        # the recruiting / invitation pages are for the research team only
        for role in ("observer", "moderator"):
            ok(see(role, f"#/p/{pid}/rooms", "nav.side a:has-text('受访者招募'), nav.side a:has-text('场次与邀请')") == 0, f"{role} must not see recruiting / invitation pages in the nav")
            ok(see(role, f"#/p/{pid}/people", "text=添加受访者") == 0, f"{role} must not reach the recruiting page by URL")
        ok(see("observer", f"#/p/{pid}/rooms", "a:has-text('进入 TA 的房间')") == 0 and see("observer", f"#/p/{pid}/rooms", "a:has-text('隐身观察')") >= 1, "observer can only enter rooms hidden")
        ok(see("observer", f"#/p/{pid}/rooms", "text=Wei Ling") == 0, "observer's room list never shows participant names")
        ok(see("moderator", f"#/p/{pid}/rooms", "a:has-text('隐身观察')") == 0, "moderator enters as moderator, not as a hidden observer")
        ok(see("researcher", f"#/p/{pid}/sessions", ".tag:has-text('未到场')") >= 1, "sessions page shows a participant who never came (ended without entering)")

        # ---------- C. participant journey (4 languages, phone): invitation -> device test -> consent -> waiting room -> room ----------
        expect = {"en2": ("Interview invitation", "Before you start", "Microphone"), "zh": ("访谈邀请", "开始之前", "麦克风"),
                  "ms": ("Jemputan temu bual", "Sebelum anda mula", "Mikrofon"), "ta": ("நேர்காணல் அழைப்பு", "தொடங்குவதற்கு முன்", "ஒலிவாங்கி")}
        state = lambda it: call(f"/interviews/{it['id']}", token=D["tok"]["researcher"])["invite_state"]
        fake = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])  # a simulated camera + microphone
        media = {"permissions": ["camera", "microphone"]}
        # (a) no camera / microphone: every check fails visibly with what to do, and the participant cannot go on (decision 2026-10-10)
        ctx, pg, errs = new_page(br, 390)
        it = D["itv"]["en2"]
        pg.goto(B + f"/join.html?t={it['invite_token']}"); pg.click("#accept"); pg.wait_for_selector("#micwarn:not([hidden])", timeout=15000)
        ok(pg.is_disabled("#devnext"), "device test: without a working microphone the participant cannot continue")
        ok(pg.is_visible("#e_mic") and pg.is_visible("#e_cam"), "device test: failed checks show red text with what to do")
        audit_page(pg, "join en device test failed/phone", errs)
        pg.screenshot(path=os.path.join(ROOT, "qa_shots", "join_en_2device_failed_390.png"), full_page=True)
        ok(state(it) == "opened", "a failed device test must not count as 'device ok'")
        ctx.close()
        # (b) each language with a working (simulated) camera and microphone
        for key, (t_inv, t_consent, t_mic) in expect.items():
            ctx, pg, errs = new_page(fake, 390, **media)
            it = D["itv"][key]
            lang = key[:2]
            pg.goto(B + f"/join.html?t={it['invite_token']}"); pg.wait_for_selector("#accept")
            ok(t_inv in pg.inner_text("#box"), f"[join {lang}] invitation not in the right language")
            ok(pg.evaluate("document.documentElement.lang") == ("zh-CN" if lang == "zh" else lang), f"[join {lang}] html lang attribute wrong")
            ok(pg.locator(".psteps i.on").count() == 1, f"[join {lang}] step bar shows the current step")
            audit_page(pg, f"join {lang} invitation/phone", errs)
            shot = lambda step: pg.screenshot(path=os.path.join(ROOT, "qa_shots", f"join_{lang}_{step}_390.png"), full_page=True)
            shot("1invite")
            ok(wait_until(lambda: state(it) == "opened"), f"[join {lang}] staff should see the invitation as opened")
            pg.click("#accept"); pg.wait_for_selector("#devnext:not([disabled])", timeout=15000)
            ok(t_mic in pg.inner_text("#box"), f"[join {lang}] device test not in the right language")
            ok(pg.locator("#c_mic.ok").count() == 1 and not pg.is_visible("#micwarn"), f"[join {lang}] a working microphone passes")
            audit_page(pg, f"join {lang} device test/phone", errs)
            shot("2device")
            pg.click("#devnext"); pg.wait_for_selector("form#f")
            ok(state(it) == "device_ok", f"[join {lang}] staff should see the device test as passed")
            ok(t_consent in pg.inner_text("#box"), f"[join {lang}] consent page not in the right language")
            ok("深访" not in pg.inner_text("#box"), f"[join {lang}] internal interview title leaked to the participant")
            # no checkbox ticked -> browser keeps the form; tick two of three -> server refuses
            boxes = pg.locator("input[type=checkbox]"); ok(boxes.count() == 3, f"[join {lang}] needs 3 separate consents")
            audit_page(pg, f"join {lang} consent/phone", errs)
            shot("3consent")
            pg.select_option("[name=country]", "SG"); boxes.nth(0).check(); boxes.nth(1).check(); pg.click("form button")
            pg.wait_for_selector(".toast.err"); ok(pg.locator("form").count() == 1, f"[join {lang}] must stay on the form with 2 of 3 consents")
            if lang == "en":
                pg.select_option("[name=country]", "ID"); boxes.nth(2).check(); pg.click("form button")
                pg.wait_for_selector("h1:has-text('cannot take part')"); ok("not open" in pg.inner_text("#box"), "non-Singapore participants should be turned away politely")
            ctx.close()
        # (b2) AI-moderated sessions: the consent page discloses the AI first, in every language, and needs its own checkbox
        ai_word = {"en": "AI interviewer", "zh": "AI 主持人", "ms": "pewawancara AI", "ta": "AI நேர்காணலாளர்"}
        for lang, word in ai_word.items():
            it = D["itv"][f"ai_{lang}"]
            call(f"/public/join/{it['invite_token']}/device", {"camera": True, "microphone": True, "speaker": True})  # device test covered above
            ctx, pg, errs = new_page(br, 390)
            pg.goto(B + f"/join.html?t={it['invite_token']}"); pg.wait_for_selector("form#f")
            text = pg.inner_text("form#f p")
            ok(text.startswith(word) or text.find(word) < 40, f"[join AI {lang}] the AI disclosure must come first in the consent text")
            boxes = pg.locator("input[type=checkbox]")
            ok(boxes.count() == 4 and pg.locator("input[name=ai]").count() == 1, f"[join AI {lang}] needs a separate 'the interviewer is an AI' consent")
            ok("AI" in pg.locator("label.chk").first.inner_text(), f"[join AI {lang}] the AI checkbox comes first")
            ok("深访" not in pg.inner_text("#box"), f"[join AI {lang}] internal interview title leaked to the participant")
            audit_page(pg, f"join AI {lang} consent/phone", errs)
            pg.screenshot(path=os.path.join(ROOT, "qa_shots", f"join_ai_{lang}_3consent_390.png"), full_page=True)
            pg.select_option("[name=country]", "SG")
            for k in ("recording", "observers", "cross_border"):
                pg.check(f"input[name={k}]")
            pg.click("form button"); pg.wait_for_selector(".toast.err")
            ok(pg.locator("form#f").count() == 1, f"[join AI {lang}] must not go on without agreeing to the AI interviewer")
            pg.check("input[name=ai]"); pg.click("form button"); pg.wait_for_selector("#pw")
            ok(state(it) == "consented", f"[join AI {lang}] agreeing to all four items reaches the waiting room")
            ctx.close()
        # (c) full journey: consent -> waiting room (locked until started AND let in) -> hold -> admit -> room -> leave
        ctx, pg, errs = new_page(fake, 390, **media)
        it = D["itv"]["zh"]
        mod = D["tok"]["moderator"]
        pg.goto(B + f"/join.html?t={it['invite_token']}"); pg.wait_for_selector("form#f")  # the device test passed above, so it resumes at consent
        pg.select_option("[name=country]", "SG")
        for i in range(3): pg.locator("input[type=checkbox]").nth(i).check()
        pg.click("form button"); pg.wait_for_selector("#enter")
        ok(state(it) == "consented", "staff should see the participant as consented")
        ok(pg.get_attribute("#enter", "aria-disabled") == "true" and "等待主持人开始" in pg.inner_text("#pw"), "waiting room: the entry is locked")
        pg.click("#enter", force=True); pg.wait_for_timeout(300); ok("join.html" in pg.url, "a locked entry button must not open the room")
        ok(wait_until(lambda: call(f"/interviews/{it['id']}/live", token=mod)["participant"]["waiting"], 8), "the moderator sees the participant in the waiting room")
        audit_page(pg, "join zh waiting room/phone", errs)
        pg.screenshot(path=os.path.join(ROOT, "qa_shots", "join_zh_4waiting_390.png"), full_page=True)
        pg.reload(); pg.wait_for_selector("#enter"); ok("候场室" in pg.inner_text("#box"), "reopening the link after consent goes straight back to the waiting room")
        call(f"/interviews/{it['id']}/start", {}, mod)
        pg.wait_for_timeout(3500); ok(pg.get_attribute("#enter", "aria-disabled") == "true", "started but not let in yet: still locked")
        call(f"/interviews/{it['id']}/admit", {"admit": False}, mod)
        ok(wait_until(lambda: "稍等" in pg.inner_text("#pw"), 8), "held: the participant is asked to wait a little longer")
        audit_page(pg, "join zh waiting room held/phone", errs)
        call(f"/interviews/{it['id']}/admit", {"admit": True}, mod)
        ok(wait_until(lambda: pg.get_attribute("#enter", "aria-disabled") == "false", 8), "let in: the waiting room unlocks")
        pg.click("#enter")
        pg.wait_for_selector("#claims"); cl = json.loads(pg.inner_text("#claims")); ok(cl["canPublish"] and not cl["hidden"] and not cl["canPublishData"], f"participant grants wrong {cl}")
        ok(state(it) == "entered", "staff should see the participant as entered")
        ok(pg.locator("#lpan:visible, #rpan:visible").count() == 0, "participant room shows no internal side panels")
        ok("t-light" in pg.evaluate("document.body.className") and "Dark" not in pg.inner_text("#theme"), "participant room: light by default, theme button in the interview language")
        audit_page(pg, "participant room/light/phone", errs)
        pg.click("#theme"); audit_page(pg, "participant room/dark/phone", errs); pg.click("#theme")
        pg.click("#b-leave"); pg.wait_for_timeout(500)
        ok("邀请链接" in pg.inner_text("#msg") and not call(f"/interviews/{it['id']}/live", token=mod)["participant"]["admitted"], "after leaving, coming back needs the moderator to let them in again")
        ctx.close(); fake.close()


        # ---------- C3. AI moderation in the room (Sprint 5): participant chat, the team's AI panel, observer, alerts; both colour themes, desktop + phone ----------
        ai_room_section(br, D)
        concept_room_section(br, D)
        pretask_section(br, D)
        timeslot_section(br, D)
        marks_section(br, D)
        group_section(br, D)

        # ---------- C2. dialogs and expandable panels on the staff pages (both screen sizes) ----------
        for w, vp in ((1280, "desktop"), (390, "phone")):
            ctx, pg, errs = new_page(br, w, token=D["tok"]["admin"])
            pg.goto(B + f"/#/i/{mi}/transcript"); pg.wait_for_selector("[data-edit]")
            ok(pg.locator("#recs").count() == 1 and pg.locator("#tr").count() == 1, f"[{vp}] recordings card + transcribe button present")
            pg.locator("[data-edit]").first.click(); pg.wait_for_selector(".modal form#f textarea")
            audit_page(pg, f"dialog 校对/{vp}", errs)
            ok(pg.locator(".modal textarea[name=text_orig]").input_value() != "", f"[{vp}] edit dialog is prefilled")
            pg.keyboard.press("Escape"); pg.mouse.click(3, 3); pg.wait_for_timeout(100)
            pg.goto(B + f"/#/i/{mi}/transcript"); pg.wait_for_selector("[data-ins]")
            pg.locator("[data-ins]").first.click(); pg.wait_for_selector(".modal form#f input[name=theme]")
            audit_page(pg, f"dialog 写洞察/{vp}", errs)
            ok(pg.evaluate("document.querySelectorAll('.modal').length") == 1, f"[{vp}] exactly one dialog open")
            pg.goto(B + f"/#/i/{mi}/clips"); pg.wait_for_selector("[data-sl]")
            ok(pg.evaluate("document.querySelectorAll('.modal').length") == 0, f"[{vp}] a dialog must not survive a page change")
            pg.click("[data-sl]"); pg.wait_for_selector("[data-rv]")
            audit_page(pg, f"分享记录/{vp}", errs)
            pg.click("[data-sh]"); pg.wait_for_selector(".modal code.copy")
            audit_page(pg, f"dialog 分享链接/{vp}", errs)
            # recruiting / sessions dialogs
            pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector("#np"); pg.click("#np"); pg.wait_for_selector(".modal form#f")
            audit_page(pg, f"dialog 添加受访者/{vp}", errs)
            pg.keyboard.press("Escape"); ok(pg.locator(".modal").count() == 0, f"[{vp}] Escape closes the dialog")
            pg.goto(B + f"/#/p/{pid}/sessions"); pg.wait_for_selector("#ni"); pg.click("#ni"); pg.wait_for_selector(".modal form#f [name=participant_id]")
            audit_page(pg, f"dialog 新建场次/{vp}", errs)
            pg.keyboard.press("Escape")
            # sessions filter: picking a progress state shows only those rows
            pg.click("[data-f=done]"); pg.wait_for_selector("[data-f=done][aria-pressed=true]")
            ok(pg.locator(".tbl tbody tr").count() == 0 or all("已完成" in t for t in pg.locator(".tbl tbody tr").all_inner_texts()), f"[{vp}] sessions filter shows only matching rows")
            pg.click("[data-f=all]")
            ctx.close()

        # ---------- D. keyboard focus visible ----------
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["admin"])
        pg.goto(B + "/#/projects"); pg.wait_for_selector("main")
        pg.keyboard.press("Tab"); pg.keyboard.press("Tab")
        ring = pg.evaluate("(()=>{const e=document.activeElement;const c=getComputedStyle(e);return [e.tagName,c.outlineStyle,c.outlineWidth]})()")
        ok(ring[1] != "none" and ring[2] not in ("0px",), f"focus ring not visible on {ring}")
        ctx.close()

        # ---------- E. reduced motion ----------
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["admin"], reduced_motion="reduce")
        pg.goto(B + "/#/projects"); pg.wait_for_selector("main"); pg.wait_for_timeout(300)
        ok(not pg.evaluate("document.querySelector('.brandsvg').classList.contains('play')"), "reduced motion: logo must stay still")
        ctx.close()

        # ---------- E2. rapid navigation: a slow page finishing after the user moved on must neither error nor paint over the new page ----------
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["admin"])
        pg.goto(B + f"/#/i/{mi}/insights"); pg.wait_for_selector("main")
        for r in (f"#/p/{pid}/insights", f"#/i/{mi}/transcript", f"#/i/{mi}/clips", f"#/i/{mi}/insights", f"#/p/{pid}/rooms", f"#/i/{mi}/insights", f"#/p/{pid}/home"):
            pg.evaluate("h => { location.hash = h }", r); pg.wait_for_timeout(25)
        pg.wait_for_timeout(300); settle(pg); pg.wait_for_timeout(600)
        body = pg.inner_text("main")
        ok("打不开这个页面" not in pg.inner_text("body") and "Cannot set properties" not in body, "rapid navigation never shows an error page")
        ok(pg.url.endswith(f"/p/{pid}/home") and pg.locator("main .tabs").count() == 0, "rapid navigation: the last page clicked is the one left on screen (no stale interview page painted over it)")
        ctx.close()

        # ---------- F. screenshots for a human glance ----------
        os.makedirs(os.path.join(ROOT, "qa_shots"), exist_ok=True)
        shots = [("admin", f"#/projects"), ("admin", f"#/p/{pid}/home"), ("admin", f"#/p/{pid}/people"), ("admin", f"#/p/{pid}/sessions"), ("admin", f"#/p/{pid}/rooms"), ("admin", f"#/p/{pid}/insights"),
                 ("admin", f"#/i/{mi}/insights"), ("observer", f"#/p/{pid}/rooms"), ("observer", f"#/i/{mi}/insights")]
        for role, r in shots:
            for w in (1280, 390):
                ctx, pg, errs = new_page(br, w, token=D["tok"][role]); pg.goto(B + "/" + r); pg.wait_for_selector("main"); pg.wait_for_timeout(2600)
                parts = r.strip("#/").split("/")
                name = parts[0] if len(parts) == 1 else f"{parts[0]}_{parts[-1] if len(parts) > 2 else 'detail'}"
                pg.screenshot(path=os.path.join(ROOT, "qa_shots", f"{role}_{name}_{w}.png"), full_page=True); ctx.close()
        br.close()


def report():
    lines = [f"# QA report\n", f"- checks passed: **{PASSES}**", f"- failures: **{len(FAILS)}**\n"]
    lines += [f"- ❌ {f}" for f in FAILS]
    open(os.path.join(ROOT, "qa_report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"\nQA: {PASSES} checks passed, {len(FAILS)} failed  (report: qa_report.md, screenshots: qa_shots/)")
    for f in FAILS[:60]:
        print(" -", f)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
