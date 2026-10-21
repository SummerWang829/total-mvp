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


def members_section(br, D):
    """Project membership: a second researcher sees nothing of the project until the owner adds them, and loses it the moment they are removed."""
    adm, rs = D["tok"]["admin"], D["tok"]["researcher"]
    pid = call("/projects", {"name": "成员测试项目", "market": "SG", "language": "en"}, rs)["id"]   # created by the researcher, who is therefore its owner
    other = call("/users", {"name": "第二位研究员", "email": "second@acme.com", "password": "password123", "role": "researcher"}, adm)
    otok = call("/auth/login", {"email": "second@acme.com", "password": "password123"})["token"]
    ok(pid not in [p["id"] for p in call("/projects", token=otok)], "[members] a researcher who is not on the project does not see it in the list")
    ctx, pg, errs = new_page(br, 1280, token=otok)
    pg.goto(B + f"/#/p/{pid}/home"); pg.wait_for_selector("main")
    ok("找不到这个项目" in pg.inner_text("main"), "[members] opening the project anyway says it cannot be found")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/members"); pg.wait_for_selector("#addmem")
    ok(pg.locator("table.tbl tbody tr").count() == 1 and "项目负责人" in pg.inner_text("table.tbl"), "[members] the creator is listed as the project owner")
    pg.click("#addmem"); pg.wait_for_selector("#addm"); pg.select_option("#addm select", value=other["id"]); pg.click("#addm button.p")
    ok(wait_until(lambda: pg.locator("table.tbl tbody tr").count() == 2, 8), "[members] adding the second researcher lists them")
    ok(pid in [p["id"] for p in call("/projects", token=otok)], "[members] the added researcher now sees the project")
    pg.locator(f'[data-owner="{other["id"]}"]').click()
    ok(wait_until(lambda: pg.locator("table.tbl tbody tr", has_text="第二位研究员").locator(".tag.ok").count() == 1, 8), "[members] a member can be made an owner")
    audit_page(pg, "members page/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "project_members_1280.png"), full_page=True)
    pg.locator(f'[data-rm="{other["id"]}"]').click()
    ok(wait_until(lambda: pg.locator("table.tbl tbody tr").count() == 1, 8), "[members] removing a member takes them off the list")
    ok(pid not in [p["id"] for p in call("/projects", token=otok)], "[members] and the removed researcher loses the project at once")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=otok)
    pg.goto(B + f"/#/p/{pid}/members"); pg.wait_for_selector("main")
    ok("找不到这个项目" in pg.inner_text("main"), "[members] a removed researcher cannot open the members page either")
    ctx.close()


def studies_section(br, D):
    """A study done alone: the researcher writes the goal, the AI drafts, the researcher publishes and invites; the participant consents and chats on a phone, leaves and comes back."""
    adm, rs, pid = D["tok"]["admin"], D["tok"]["researcher"], D["pid"]
    call(f"/projects/{pid}/participants", {"name": "异步受访者", "country": "SG", "language": "zh"}, adm)
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/studies"); pg.wait_for_selector("#nf")
    ok("异步研究" in pg.inner_text(".hubtabs") and "异步 AI 研究" in pg.inner_text("nav.side"), "[studies] the project has the asynchronous studies page (tabs) and its menu entry")
    audit_page(pg, "studies list/desktop", errs)
    pg.fill("[name=title]", "QA 异步研究"); pg.fill("[name=goal]", "了解消费者如何选购护肤品"); pg.select_option("[name=language]", "zh"); pg.click("#nf button")
    pg.wait_for_selector("#outline")
    ok(pg.input_value("#outline") == "", "[studies] a new study starts with an empty outline")
    pg.click("#draft")
    ok(wait_until(lambda: "## 暖场" in pg.input_value("#outline"), 15), "[studies] the AI drafts an outline into the editor")
    audit_page(pg, "study detail/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "study_detail_1280.png"), full_page=True)
    pg.fill("#outline", "随便写点什么"); pg.click("#save")
    ok(wait_until(lambda: pg.is_visible("#problem"), 8), "[studies] a broken outline is saved but the problem is shown")
    pg.click("#draft")
    ok(wait_until(lambda: "## 暖场" in pg.input_value("#outline"), 15), "[studies] drafting again replaces the broken text")
    pg.click("#publish"); pg.wait_for_selector("#inv", timeout=15000)
    ok("第 1 版" in pg.inner_text("main"), "[studies] publishing freezes version 1")
    pg.locator("#inv input[name=pid]").first.check(); pg.click("#inv button")
    pg.wait_for_selector("code.copy")
    link = pg.inner_text("code.copy").strip()
    pg.click("#done")
    sid = pg.url.split("/studies/")[1].split("?")[0]
    ok("/join.html?t=" in link, "[studies] an invitation link is produced")
    ok(wait_until(lambda: pg.locator("table.tbl tbody tr").count() == 1 and "已发送" in pg.inner_text("table.tbl"), 8), "[studies] the participant appears as invited")
    ctx2, pg2, errs2 = new_page(br, 390)
    pg2.goto(link); pg2.wait_for_selector("#accept")
    ok(pg2.locator(".psteps i").count() == 3, "[studies] the participant flow has three steps (no device test, no waiting room)")
    audit_page(pg2, "study invitation/phone", errs2)
    pg2.click("#accept"); pg2.wait_for_selector("form#f")
    ok("文字对话" in pg2.inner_text("#box") and "AI" in pg2.inner_text("#box"), "[studies] the consent text says it is a written conversation with an AI")
    audit_page(pg2, "study consent/phone", errs2)
    pg2.select_option("[name=country]", "SG")
    boxes = pg2.locator("input[type=checkbox]")
    for i in range(boxes.count()):
        boxes.nth(i).check()
    pg2.click("form button"); pg2.wait_for_selector("#cin:not([disabled])", timeout=20000)
    ok(wait_until(lambda: pg2.locator(".chat .m").count() >= 1, 8) and "AI" in pg2.inner_text(".chat .m"), "[studies] the AI opens the conversation and says it is an AI")
    audit_page(pg2, "study chat/phone", errs2)
    pg2.fill("#cin", "好的，我准备好了。"); pg2.click("#csend")
    ok(wait_until(lambda: pg2.locator(".chat .m.me").count() == 1 and pg2.locator(".chat .m").count() >= 3, 20), "[studies] the participant's answer is followed by the AI's reply")
    pg2.fill("#cin", "我平时会先看成分，因为皮肤比较敏感。"); pg2.click("#csend")
    ok(wait_until(lambda: pg2.locator(".chat .m").count() >= 5, 20), "[studies] the conversation goes on")
    n = pg2.locator(".chat .m").count()
    pg2.screenshot(path=os.path.join(ROOT, "qa_shots", "study_chat_390.png"), full_page=True)
    pg2.goto(B + "/"); pg2.goto(link)   # leaves and comes back with the same link
    pg2.wait_for_selector("#cin", timeout=20000)
    ok(wait_until(lambda: pg2.locator(".chat .m").count() == n, 10), "[studies] coming back shows the same conversation, it does not start over")
    ctx2.close()
    pg.goto(B + f"/#/p/{pid}/studies/{sid}"); pg.reload(); pg.wait_for_selector("table.tbl")
    ok(wait_until(lambda: "进行中" in pg.inner_text("table.tbl"), 8), "[studies] the researcher sees the session as in progress")
    ok(wait_until(lambda: pg.is_visible("#prog") and "已开始 1" in pg.inner_text("#prog"), 8), "[studies] the progress line counts who has started")
    pg.fill("#olform [name=cap]", "5"); pg.click("#olform button"); pg.wait_for_selector("code.copy")
    olink = pg.inner_text("code.copy").strip(); pg.click("#done2")
    ok("/study.html?s=" in olink, "[studies] a public sign-up link is produced")
    ctx3, pg3, errs3 = new_page(br, 390)
    pg3.goto(olink); pg3.wait_for_selector("#sgo")
    audit_page(pg3, "study sign-up/phone", errs3)
    pg3.screenshot(path=os.path.join(ROOT, "qa_shots", "study_signup_390.png"), full_page=True)
    pg3.fill("[name=nickname]", "小明"); pg3.click("#sgo"); pg3.wait_for_selector("#accept")
    ok("/join.html?t=" in pg3.url and pg3.locator(".psteps i").count() == 3, "[studies] signing up through the public link leads to the personal invitation")
    ctx3.close()
    pg.reload(); pg.wait_for_selector("table.tbl")   # (the list was drawn before the person signed up)
    ok(wait_until(lambda: "小明" in pg.inner_text("table.tbl"), 8), "[studies] the person who signed up appears in the researcher's list by nickname")
    pg.click("#close")
    ok(wait_until(lambda: "提前结束" in pg.inner_text("table.tbl"), 8), "[studies] closing ends the unfinished session")
    ctx.close()


def repo_section(br, D):
    """The research library: import a document, search it, ask a question and see verbatim evidence; say so when the evidence is not enough."""
    rs = D["tok"]["researcher"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/#/repo"); pg.wait_for_selector("#qf")
    ok("研究库" in pg.inner_text("nav.side"), "[library] the menu has the research library")
    audit_page(pg, "library/desktop", errs)
    pg.click("#imp"); pg.wait_for_selector("#f")
    pg.fill("[name=title]", "2023 趋势报告"); pg.fill("[name=text]", "报告指出，新加坡消费者越来越关注成分透明度。" + chr(10) * 2 + "直播带货影响了年轻人的购买决定。")
    pg.click("#f button.p"); pg.wait_for_selector("td:has-text('2023 趋势报告')")
    pg.fill("[name=q]", "成分透明度"); pg.click("#srch")
    ok(wait_until(lambda: pg.locator(".hit").count() >= 1 and "导入资料" in pg.inner_text("#res"), 8), "[library] searching finds the imported document")
    pg.fill("[name=q]", "消费者关注成分透明度吗？"); pg.click("#ask")
    ok(wait_until(lambda: pg.locator("#res details .hit").count() >= 1, 15), "[library] an answer shows the passages it rests on")
    ok("成分透明度" in pg.inner_text("#res details"), "[library] the evidence is the original text")
    audit_page(pg, "library answer/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "library_1280.png"), full_page=True)
    pg.fill("[name=q]", "量子计算机的市场规模如何？"); pg.click("#ask")
    ok(wait_until(lambda: "证据不足" in pg.inner_text("#res"), 15), "[library] a question the library cannot answer is said so plainly")
    pg.click("[data-del]"); ok(wait_until(lambda: pg.locator("[data-del]").count() == 0, 8), "[library] a document can be deleted")
    pg.click("#imp"); pg.wait_for_selector("#file")
    pg.set_input_files("#file", {"name": "渠道笔记.txt", "mimeType": "text/plain", "buffer": "线下门店的体验区最能打动第一次购买的人。".encode("utf-8")})
    ok(pg.input_value("[name=title]") == "渠道笔记", "[library] choosing a file fills in the title")
    pg.click("#f button.p")
    ok(wait_until(lambda: pg.locator("td", has_text="渠道笔记").count() == 1, 10), "[library] an uploaded file is imported")
    pg.fill("[name=q]", "体验区"); pg.click("#srch")
    ok(wait_until(lambda: "渠道笔记" in pg.inner_text("#res") and "导入资料" in pg.inner_text("#res"), 8), "[library] the text of an uploaded file is searchable")
    pg.click("[data-del]"); ok(wait_until(lambda: pg.locator("[data-del]").count() == 0, 8), "[library] the uploaded document can be deleted too")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=rs)
    pg.goto(B + "/#/repo"); pg.wait_for_selector("#qf")
    audit_page(pg, "library/phone", errs)
    ctx.close()


def listening_section(br, D):
    """Public-opinion monitoring: add a (mock) source, run it, see the numbers and the posts, filter, take one down, delete the source."""
    rs, pid = D["tok"]["researcher"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/listening"); pg.wait_for_selector("#addsrc")
    ok("舆情采集" in pg.inner_text("nav.side"), "[listening] the project menu has the monitoring page")
    audit_page(pg, "listening empty/desktop", errs)
    pg.click("#addsrc"); pg.wait_for_selector("#kind")
    pg.fill("[name=name]", "模拟来源"); pg.fill("[name=c_brand]", "Acme"); pg.fill("[name=c_lang]", "en"); pg.click("#f button.p")
    pg.wait_for_selector("[data-run]")
    pg.click("[data-run]")
    ok(wait_until(lambda: pg.locator("#mlist .hitm").count() >= 5, 15), "[listening] running the source brings in posts")
    ok(wait_until(lambda: "负面" in pg.inner_text("main") and "最多人谈的话题" in pg.inner_text("main"), 8), "[listening] the page shows sentiment and topics")
    ok(pg.locator(".sday").count() >= 1, "[listening] the daily volume chart has a bar for today")
    audit_page(pg, "listening/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "listening_1280.png"), full_page=True)
    pg.select_option("#mf [name=sentiment]", "neg"); pg.click("#mf button.p")
    ok(wait_until(lambda: pg.locator("#mlist .hitm").count() >= 1 and all("负面" in t for t in pg.locator("#mlist .hitm").all_inner_texts()), 8), "[listening] the filter shows only negative posts")
    n = pg.locator("#mlist .hitm").count()
    pg.locator("[data-dm]").first.click()
    ok(wait_until(lambda: pg.locator("#mlist .hitm").count() == n - 1, 8), "[listening] taking one post down removes it")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=rs)
    pg.goto(B + f"/#/p/{pid}/listening"); pg.wait_for_selector("#addsrc"); pg.wait_for_selector("#mlist .hitm")
    audit_page(pg, "listening/phone", errs)
    ctx.close()


def recruit_section(br, D):
    """Recruiting: import a spreadsheet, set a rule, see who qualifies and why, filter, pick people and invite them in one go, set a quota."""
    rs, pid = D["tok"]["researcher"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector("#imp2")
    ok(pg.locator("#rules").count() == 0 and pg.locator("#quotas").count() == 1, "[recruit] eligibility rules are gone from the page (the questionnaire replaces them); quotas stay")
    # the recruiting questionnaire: write it on the left (the AI can draft it), test it on the right, publish it as a link and a QR code
    ok(pg.locator("h3", has_text="招募问卷").count() == 1 and pg.locator("#rfidea").count() == 1 and ("不会发给其他公司" in pg.inner_text(".rf") or "发送给" in pg.inner_text(".rf")), "[recruit] the questionnaire card shows the idea box and says where the text goes")
    pg.fill("#rfidea", "想了解新加坡年轻人如何选购护肤品牌，重点是购买渠道。"); pg.click("#rfgen")
    pg.wait_for_selector(".rf-edit", timeout=15000)
    ok(pg.locator(".rf-edit").count() >= 5 and pg.locator(".rf-edit [data-o=goto]").count() >= 8, "[recruit] the AI's draft opens in the editor: questions with options, each with what happens after it")
    ok(pg.locator("#rfrun .rf-q").count() == 1 and pg.locator("#rfrun input[type=radio]").count() >= 3, "[recruit] the right side shows the first question, ready to be tested")
    # editing: add a number question with a rule, move it, then delete it again
    pg.click("[data-add=number]"); pg.wait_for_selector(".rf-edit >> nth=5")
    last = pg.locator(".rf-edit").last
    last.locator("[data-f=text]").fill("您的年龄是多少？"); last.locator("[data-f=text]").dispatch_event("change")
    last.locator("[data-radd]").click(); pg.wait_for_selector(".rf-edit [data-r=op]")
    ok(pg.locator(".rf-edit [data-r=goto]").last.input_value() == "end_out", "[recruit] a number rule defaults to ending as not qualified")
    pg.locator(".rf-edit").last.locator("[data-del]").click(); pg.wait_for_timeout(300)
    ok(pg.locator(".rf-edit").count() == 5, "[recruit] a question can be deleted")
    audit_page(pg, "recruiting questionnaire editor/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "recruit_form_1280.png"), full_page=True)
    # testing on the right: the first option ("Under 18") screens the person out
    pg.locator("#rfrun input[type=radio]").first.check(); pg.click("#scrnext")
    ok(wait_until(lambda: pg.is_visible("#rfresult") and "筛掉" in pg.inner_text("#rfresult"), 8), "[recruit] testing the questionnaire: an ending option says the person is screened out")
    # publish: a link and a QR code
    pg.click("#rfpub"); pg.wait_for_selector("#rflink", timeout=15000)
    link = pg.input_value("#rflink")
    ok("/screen.html?t=" in link and wait_until(lambda: (pg.locator("#rfqr").get_attribute("src") or "").startswith("blob:"), 8), "[recruit] publishing gives a link and a QR code")
    ok("发布中" in pg.inner_text(".rf-pub"), "[recruit] the questionnaire shows as published")
    audit_page(pg, "recruiting questionnaire published/desktop", errs)
    # a candidate on a phone: screened out first, then a qualified run that leaves contact details
    c2, pg2, errs2 = new_page(br, 390)
    pg2.goto(link); pg2.wait_for_selector("#scrgo")
    ok("kept until" in pg2.inner_text("#box") and "TOTAL" in pg2.inner_text("#box"), "[screen] the candidate page says how the answers are used before the first question")
    audit_page(pg2, "screening page intro/phone", errs2)
    pg2.click("#scrgo"); pg2.wait_for_selector("#scrnext")
    pg2.locator("input[type=radio]").first.check(); pg2.click("#scrnext")
    pg2.wait_for_selector("text=not the right fit")
    ok(pg2.locator("form#cf").count() == 0, "[screen] someone screened out is thanked politely and nothing more is asked")
    pg2.goto(link); pg2.wait_for_selector("#scrgo"); pg2.click("#scrgo"); pg2.wait_for_selector("#scrnext")
    audit_page(pg2, "screening page question/phone", errs2)
    pg2.locator("input[type=radio]").nth(1).check(); pg2.click("#scrnext")                    # 18-34
    pg2.wait_for_selector("text=Where do you live"); pg2.locator("input[type=radio]").first.check(); pg2.click("#scrnext")  # Singapore
    pg2.wait_for_selector("text=How often"); pg2.locator("input[type=radio]").first.check(); pg2.click("#scrnext")        # every day
    pg2.wait_for_selector("text=Where do you usually buy"); pg2.locator("input[type=checkbox]").first.check(); pg2.click("#scrnext")
    pg2.wait_for_selector("textarea"); pg2.click("#scrnext")
    ok(pg2.locator("#screrr").is_visible(), "[screen] a required question cannot be skipped")
    pg2.fill("textarea", "价格和成分"); pg2.click("#scrnext")
    pg2.wait_for_selector("form#cf")
    audit_page(pg2, "screening page contact/phone", errs2)
    pg2.fill("#cf [name=name]", "QA候选人"); pg2.fill("#cf [name=email]", "qa-candidate@example.com")
    pg2.click("#cf button.p"); pg2.wait_for_timeout(500)
    ok(pg2.locator("form#cf").count() == 1 and "We have your details" not in pg2.inner_text("#box"), "[screen] the notice has to be agreed to before submitting")
    pg2.check("#cf [name=consent]"); pg2.click("#cf button.p")
    pg2.wait_for_selector("text=We have your details", timeout=10000)
    c2.close()
    pg.reload(); pg.wait_for_selector("td:has-text('QA候选人')", timeout=15000)
    ok(pg.locator("tr", has_text="QA候选人").locator(".tag", has_text="符合条件").count() == 1, "[recruit] a candidate who qualified is in the list, already marked as qualified")
    ok("已合格 1 人" in pg.inner_text(".rf-pub") and "被筛掉 1 人" in pg.inner_text(".rf-pub"), "[recruit] the publish box counts who qualified and who was screened out")
    pg.locator("tr", has_text="QA候选人").locator("[data-scr]").click(); pg.wait_for_selector("textarea[name=text]")
    ok("价格和成分" in pg.input_value("textarea[name=text]"), "[recruit] the candidate's answers are kept with them")
    pg.click("#c")
    pg.click("#rfclose"); pg.wait_for_selector("#rfpub")
    pg.locator("input[name=rfmode][value=html]").check(); pg.wait_for_selector("#rfurl")
    pg.fill("#rfurl", "https://survey.example.com/s/qa"); pg.click("#rfsave"); pg.wait_for_selector("#rftest")
    ok(pg.locator("#rftest").is_visible() and "survey.example.com" in pg.inner_text(".rf"), "[recruit] an HTML questionnaire link is saved and can be opened for testing")
    audit_page(pg, "recruiting html link/desktop", errs)
    call(f"/projects/{pid}/recruiting/rules", {"rules": [{"field": "country", "op": "in", "value": "SG", "effect": "require"}]}, rs, method="PUT")  # a project that already had a rule keeps it
    pg.reload(); pg.wait_for_selector("#rules")
    audit_page(pg, "recruiting/desktop", errs)
    nl = chr(10)
    pg.click("#imp2"); pg.wait_for_selector("[name=csv]")
    pg.fill("[name=csv]", "姓名,邮箱,国家,语言,标签,年龄" + nl + "林小雨QA,qa1@example.com,SG,zh,女性;学生,22" + nl + "王大明QA,qa2@example.com,SG,zh,,17" + nl)
    pg.click("#dry")
    ok(wait_until(lambda: "将新增" in pg.inner_text("#prev") and "2" in pg.inner_text("#prev"), 8), "[recruit] the preview says how many people would be added")
    audit_page(pg, "recruiting import/desktop", errs)
    pg.click("#impgo")
    pg.wait_for_selector("td:has-text('林小雨QA')", timeout=15000)
    ok(pg.locator("tr", has_text="林小雨QA").locator(".tag", has_text="女性").count() == 1, "[recruit] the imported tags are shown")
    pg.click("#rules"); pg.wait_for_selector("#addr"); pg.click("#addr")
    row = pg.locator(".rrow").last
    row.locator(".r-f").select_option("question"); row.locator(".r-q").fill("年龄"); row.locator(".r-o").select_option("between"); row.locator(".r-v").fill("18,65")
    row.locator(".r-e").select_option("require"); row.locator(".r-n").fill("年龄 18–65")
    audit_page(pg, "recruiting rules/desktop", errs)
    pg.click("#f button.p")
    pg.wait_for_selector("tr:has-text('王大明QA') .tag:has-text('不符合')", timeout=15000)
    ok("年龄 18–65" in pg.locator("tr", has_text="王大明QA").inner_text(), "[recruit] a disqualified person shows the reason")
    ok(pg.locator("tr", has_text="林小雨QA").locator(".tag", has_text="符合条件").count() == 1, "[recruit] a person who fits is marked as qualified")
    ok(pg.locator("tr", has_text="王大明QA").locator(".pick").is_disabled(), "[recruit] a disqualified person cannot be picked for an invitation")
    audit_page(pg, "recruiting rules applied/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "recruiting_1280.png"), full_page=True)
    pg.select_option("#f_screen", "qualified")
    ok(wait_until(lambda: pg.locator("tr", has_text="王大明QA").count() == 0 and pg.locator("tr", has_text="林小雨QA").count() == 1, 8), "[recruit] filtering by qualified hides the disqualified")
    pg.locator("tr", has_text="林小雨QA").locator(".pick").check()
    ok(wait_until(lambda: not pg.is_disabled("#batch") and "已选 1" in pg.inner_text("#selcount"), 5), "[recruit] picking a person enables the batch invite")
    pg.click("#batch"); pg.wait_for_selector("[name=title]")
    pg.fill("[name=title]", "QA 批量深访"); pg.click("#f button.p")
    pg.wait_for_selector("code.copy")
    ok("/join.html?t=" in pg.inner_text("code.copy") and pg.locator("code.copy").count() == 1, "[recruit] batch invite produces one link per person")
    pg.click("#done")
    pg.click("#quotas"); pg.wait_for_selector("#addq"); pg.click("#addq")
    qrow = pg.locator(".qrow").last
    qrow.locator(".q-f").select_option("tag"); qrow.locator(".q-v").fill("女性"); qrow.locator(".q-t").fill("2")
    pg.click("#f button.p")
    pg.wait_for_selector(".qbar", timeout=10000)
    ok("1 / 目标 2" in pg.inner_text("main") or "已有 1 / 目标 2" in pg.inner_text("main"), "[recruit] the quota shows progress against the target")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=rs)
    pg.goto(B + f"/#/p/{pid}/people"); pg.wait_for_selector("#imp2")
    audit_page(pg, "recruiting/phone", errs)
    ctx.close()


def wizard_section(br, D):
    """The 7-step project wizard (the original 星桐 立项向导): draft -> every step -> compliance -> review -> publish and lock -> unlock, change, review again;
    then the menu, the project bar and the overview follow each sub-project's research order."""
    adm = D["tok"]["admin"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/#/projects"); pg.wait_for_selector("#np")
    pg.click("#np"); pg.wait_for_selector("#npf")
    pg.fill("#npf input[name=name]", "向导测试项目"); pg.click("#npf button.p")
    pg.wait_for_selector(".chain .step")
    ok(pg.locator(".chain .step").count() == 7 and "草稿" in pg.inner_text("main"), "[wizard] a draft opens the 7-step wizard")
    ok("立项向导" in pg.inner_text("nav.side") and "项目总览" not in pg.inner_text("nav.side"), "[wizard] a project still in the wizard has only the wizard in its menu")
    for n, label in enumerate(["基本信息", "研究设计", "人员与角色", "人群与样本", "渠道与触达", "合规举证", "确认发布"], start=1):
        ok(f"{n} {label}" in pg.locator(".chain .step").nth(n - 1).inner_text(), f"[wizard] step {n} is {label}")
    # step 1: name, retention (30/60/120/180), sub-projects with their own research order, listening and async ticks
    ok(pg.locator("select[data-w=retention] option").count() == 4 and pg.input_value("select[data-w=retention]") == "180" and pg.locator("select[data-sub$='|lang']").count() == 1, "[wizard] retention is chosen from 30/60/120/180 (180 by default) and there is one sub-project")
    pg.select_option("select[data-w=retention]", "60"); pg.wait_for_timeout(1200)  # it saves and redraws
    ok("不会发给其他公司" in pg.inner_text("main") or "发送给" in pg.inner_text("main"), "[wizard] the research background says where the text goes (AI service)")
    pg.fill("[data-meta=brief]", "新品防晒霜进入新加坡市场，想了解年轻人的购买决策。"); pg.click("#wzsug")
    pg.wait_for_selector("text=深访提纲建议", timeout=15000)
    ok(pg.locator("text=座谈会大纲建议").count() == 1 and pg.locator("text=舆情关键词建议").count() == 1, "[wizard] suggestions from the research background: interview and group outlines, quota hint, listening keywords")
    audit_page(pg, "wizard step 1 suggestions/desktop", errs)
    ok(pg.locator("select[data-sub$='|design'] option").count() == 8, "[wizard] a sub-project's research order has the 8 choices")
    pg.fill("#subnew", "马来西亚"); pg.click("#subadd")
    ok(wait_until(lambda: pg.locator("select[data-sub$='|lang']").count() == 2, 5), "[wizard] a sub-project is added")
    pg.locator("select[data-sub$='|design']").nth(1).select_option("i")
    pg.wait_for_timeout(300)
    pg.locator("input[data-subx$='|listening']").nth(1).check(); pg.wait_for_timeout(300)
    pg.locator("input[data-subx$='|async_ai']").nth(1).check(); pg.wait_for_timeout(300)
    ok(pg.locator("#subtpl").count() == 1 and pg.locator("#subimp").count() == 1, "[wizard] the sub-project table has a template to download and a file to import")
    audit_page(pg, "wizard step 1/desktop", errs); pg.click("#wznext")
    # step 2: only the research design per sub-project, to read (no choosing here any more)
    pg.wait_for_selector("text=各子项目的研究设计")
    ok(pg.locator("input[name=design]").count() == 0 and pg.locator("input[data-method]").count() == 0 and pg.locator(".wzbody tbody tr").count() == 2, "[wizard] step 2 only lists each sub-project's research design")
    audit_page(pg, "wizard step 2/desktop", errs); pg.click("#wznext")
    # step 3: people and roles: admins are in automatically, others are added and ticked by role
    pg.wait_for_selector("#memsel")
    ok(pg.locator("input[data-member]").count() == 0 and pg.locator("th", has_text="舆情").count() == 1, "[wizard] step 3 starts with the admins only and has a listening column")
    pg.locator("#memsel option").nth(1).evaluate("o => o.selected = true"); pg.click("#memadd")
    pg.wait_for_selector("input[data-member]")
    ok(pg.locator("input[data-member]").count() >= 1, "[wizard] a person added in step 3 gets ticks by role")
    audit_page(pg, "wizard step 3/desktop", errs); pg.click("#wznext")
    # step 4: sample and quotas: only the methods in use
    pg.wait_for_selector(".bx.q")
    ok(pg.locator(".bx.q").count() >= 2 and pg.locator(".bx.i").count() >= 2 and pg.locator(".bx.f").count() == 0, "[wizard] step 4 shows boxes and quota tables only for the methods in use (定量, 深访)")
    pg.fill("[data-n=i]", "4"); pg.locator("[data-n=i]").dispatch_event("change")
    ok(wait_until(lambda: "应为 4" in pg.inner_text("main"), 5), "[wizard] a target that the quota table does not add up to is flagged")
    pg.click("[data-qrow=lt]"); pg.wait_for_selector("[data-qcell^='lt|'][data-qcell$='|i']")
    pg.fill("[data-qcell^='lt|'][data-qcell$='|i']", "4"); pg.locator("[data-qcell^='lt|'][data-qcell$='|i']").dispatch_event("change")
    ok(wait_until(lambda: "应为 4" not in pg.inner_text("main"), 5), "[wizard] once the table adds up the flag goes away")
    pg.locator("[data-qa^='lt|']").first.check()
    ok(wait_until(lambda: "异步 AI：4 人" in pg.inner_text("main"), 5), "[wizard] a group of people can be marked for the asynchronous AI (the table totals them)")
    pg.click("#wzconfirmn"); pg.wait_for_timeout(1200)
    ok(pg.input_value("[data-n=i]") == "4", "[wizard] a number typed in step 4 stays after saving (and the confirm button works)")
    audit_page(pg, "wizard step 4/desktop", errs); pg.click("#wznext")
    # step 5: channels
    pg.wait_for_selector("[data-tab]")
    pg.click("[data-tab=l]"); pg.wait_for_selector("[data-n2=lead]")
    audit_page(pg, "wizard step 5/desktop", errs); pg.click("#wznext")
    # step 6: compliance, one click ticks every confirmation, review
    pg.wait_for_selector("input[data-cons]")
    ok(pg.is_disabled("#wzsubmit") and "还不能提交" in pg.inner_text("main"), "[wizard] submitting is blocked until the confirmations are ticked")
    pg.click("#wzall")
    ok(wait_until(lambda: pg.is_enabled("#wzsubmit"), 8), "[wizard] 一键全选 makes the plan submittable")
    audit_page(pg, "wizard step 6/desktop", errs)
    pg.click("#wzsubmit"); pg.wait_for_selector("#wzapprove")
    ok("审查中" in pg.inner_text("main"), "[wizard] after submitting the plan is under review")
    pg.click("#wzapprove"); pg.wait_for_selector("text=法务通过，可发布并锁定")
    pg.click("#wznext")
    pg.wait_for_selector("#wzpub"); ok(pg.is_enabled("#wzpub"), "[wizard] approved: publishing is possible")
    ok(pg.locator("#wznext").count() == 0, "[wizard] the last step has no 下一步")
    audit_page(pg, "wizard step 7/desktop", errs)
    pg.click("#wzpub"); pg.wait_for_selector("text=暂时锁定")
    ok(wait_until(lambda: pg.locator("fieldset.wzbody[disabled]").count() == 1, 8), "[wizard] a published plan is locked (read only)")
    ok(pg.locator("a", has_text="去看执行监控").count() == 0 and pg.locator("#wzhome").is_visible(), "[wizard] after publishing the last step offers 去项目总览 (and no 去看执行监控)")
    # unlock, shorten the retention, review and publish again
    pg.click("#wzunlock2"); pg.wait_for_selector("text=修改中")
    ok(pg.locator("fieldset.wzbody[disabled]").count() == 0 and "项目总览" in pg.inner_text("nav.side"), "[wizard] an unlocked plan can be edited and the project stays usable (full menu)")
    pg.click("[data-step='0']"); pg.wait_for_selector("select[data-w=retention]")
    pg.select_option("select[data-w=retention]", "30"); pg.wait_for_timeout(1500)
    pg.click("[data-step='5']"); pg.wait_for_selector("#wzsubmit")
    ok(wait_until(lambda: pg.is_enabled("#wzsubmit"), 8), "[wizard] an unlocked and changed plan has to be submitted for review again")
    pg.click("#wzsubmit"); pg.wait_for_selector("#wzapprove"); pg.click("#wzapprove"); pg.wait_for_selector("text=法务通过，可发布并锁定")
    pg.click("#wznext"); pg.wait_for_selector("#wzpub"); pg.click("#wzpub"); pg.wait_for_selector("text=暂时锁定")
    ok(wait_until(lambda: pg.locator("fieldset.wzbody[disabled]").count() == 1, 8), "[wizard] publishing the new version locks the plan again")
    pg.goto(B + "/#/projects"); pg.wait_for_selector(".pcard"); pg.locator(".pcard", has_text="向导测试项目").click()
    pg.wait_for_selector(".hero2", timeout=15000)
    nav = pg.inner_text("nav.side")
    ok(nav.index("立项向导") < nav.index("项目总览"), "[nav] the wizard comes before the project overview")
    ok("异步 AI 研究" in nav and "舆情采集" in nav and "座谈会" not in nav and "深访" in nav and "定量" in nav, "[nav] the menu shows only what the project uses (stages = its research orders)")
    ok(pg.locator(".track li").count() == 2 and "深访" in pg.inner_text(".track"), "[wizard] the project bar shows the research stages")
    # the original overview: big number, a map with one node per sub-project, the status steps, the sub-project table, the stage list
    ok(pg.locator(".hero2").count() == 1 and pg.locator(".sn").count() == 2 and pg.locator(".hm-st").count() == 5, "[home] the overview has the big number, a map node per sub-project and the 5 status steps (no 草稿)")
    ok(pg.locator(".hm-st.on").inner_text() == "招募中" and pg.locator(".hero2 .hero-k").inner_text() in ("定量完成", "深访就绪"), "[home] the status is 招募中 for a published project with no sessions yet")
    ok(pg.locator("#chm").count() == 0, "[home] there is no 调整研究方法 button any more")
    ok(pg.locator(".hero2 .meter").count() >= 3 and pg.locator(".hero2 .meter dt", has_text="座谈会").count() == 0, "[home] the meters show the methods the project uses")
    ok(pg.locator(".hm-stagelist li").count() == 3 and pg.locator(".hm-stagelist .pill", has_text="招募中").count() == 3, "[home] each sub-project's stages are listed with their state")
    ok(pg.locator(".hm-stagelist .tag", has_text="由异步 AI 执行").count() >= 1, "[home] the overview marks which people are done by the asynchronous AI")
    ok(pg.locator("tbody tr", has_text="马来西亚").count() >= 1, "[home] the sub-project table lists every sub-project")
    pg.locator(".hm-stagelist button").first.click(); pg.wait_for_selector("#imp2")
    ok(pg.url.endswith("/people"), "[home] a stage opens its page (深访 starts at the recruiting setup)")
    pg.go_back(); pg.wait_for_selector(".hm-stages")  # the stage button picked that sub-project
    pg.select_option("#subsel", "all"); pg.wait_for_selector(".hero2")
    pg.locator(".sn").first.click()
    ok(wait_until(lambda: pg.locator("#subsel").input_value() != "all" and pg.locator(".hm-stagelist li").count() == 2, 8), "[home] clicking a map node opens that sub-project (its own stages)")
    audit_page(pg, "sub-project home/desktop", errs)
    pg.select_option("#subsel", "all"); pg.wait_for_selector(".hero2")
    pg.click("nav.side a:has-text('深访')"); pg.wait_for_selector("#imp2")
    ok(pg.url.endswith("/people"), "[nav] 深访 in the menu opens the recruiting setup first, not the sessions list")
    pg.goto(B + "/#/team"); pg.wait_for_selector("nav.side")
    ok("项目总览" in pg.inner_text("nav.side") and "团队" in pg.inner_text("nav.side"), "[nav] the settings pages keep the current project's menu on the left")
    pg.goto(B + "/#/projects"); pg.locator(".pcard", has_text="向导测试项目").click(); pg.wait_for_selector(".hero2")
    with pg.expect_download(timeout=15000) as dl:
        pg.click("#rep")
    ok(dl.value.suggested_filename.endswith(".docx"), "[wizard] the research report downloads as a Word file")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "starmap_1280.png"), full_page=True)
    pid_new = pg.url.split("/p/")[1].split("/")[0]
    pg.click("#hmcopy"); pg.wait_for_selector(".chain .step")
    ok("（副本）" in pg.inner_text(".ctx-name") and "草稿" in pg.inner_text(".ctx"), "[home] copying a project opens a draft of it in the wizard")
    pg.goto(B + "/#/p/" + pid_new + "/monitor"); pg.wait_for_selector(".bx.q")
    ok(pg.locator(".bx.i tbody tr").count() >= 1, "[wizard] the monitor shows the quota boards of a published plan")
    audit_page(pg, "monitor/desktop", errs)
    ctx.close()
    # the wizard on a phone
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + "/#/projects"); pg.wait_for_selector("#np"); pg.click("#np"); pg.wait_for_selector("#npf")
    audit_page(pg, "new project/phone", errs)
    pg.fill("#npf input[name=name]", "手机向导"); pg.click("#npf button.p"); pg.wait_for_selector(".chain .step")
    audit_page(pg, "wizard step 1/phone", errs)
    for _ in range(3):
        pg.click("#wznext"); pg.wait_for_timeout(500)
    audit_page(pg, "wizard step 4/phone", errs)
    ctx.close()


def team_section(br, D):
    """The team page: nine roles (three kinds of researcher, project manager, technical support, ...), brands, and a client who has to belong to a brand."""
    adm = D["tok"]["admin"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/#/team"); pg.wait_for_selector("#nb")
    pg.click("#nb"); pg.wait_for_selector(".modal form#f")
    audit_page(pg, "brands dialog/desktop", errs)
    pg.fill(".modal input[name=name]", "品牌甲QA"); pg.click(".modal button.p")
    pg.wait_for_selector("#nu"); pg.click("#nu"); pg.wait_for_selector(".modal select[name=role]")
    names = pg.locator(".modal select[name=role] option").all_inner_texts()
    ok(names == ["全科研究员", "定量研究员", "定性研究员", "项目经理", "主持人", "观察员", "客户（只读）", "技术支持", "管理员"], "[team] the role list has the three kinds of researcher, project manager, client and technical support")
    ok(not pg.locator("#cl").is_visible(), "[team] the client fields (brand, title, contact) are hidden for other roles")
    pg.select_option(".modal select[name=role]", "viewer|")
    ok(pg.locator("#cl").is_visible() and "品牌甲QA" in pg.inner_text("#cl"), "[team] choosing client asks for a brand, a title and a contact")
    audit_page(pg, "team add client dialog/desktop", errs)
    pg.select_option(".modal select[name=brand_id]", label="品牌甲QA")
    pg.fill(".modal input[name=name]", "客户QA"); pg.fill(".modal input[name=email]", "kehu-qa@client.test"); pg.fill(".modal input[name=password]", "password123")
    pg.fill(".modal input[name=job_title]", "品牌经理"); pg.fill(".modal input[name=phone]", "+65 8000 1111")
    pg.click(".modal button.p")
    ok(wait_until(lambda: "品牌甲QA · 品牌经理 · +65 8000 1111" in pg.inner_text("main"), 8), "[team] the client is listed with brand, title and contact")
    audit_page(pg, "team page/desktop", errs)
    ctx.close()


def quant_section(br, D):
    """Quantitative results: the owner makes a key, an outside platform pushes a survey with it, the page shows bars; a spreadsheet can be imported; the key can be revoked."""
    adm, pid = D["tok"]["admin"], D["pid"]
    call(f"/projects/{pid}/methods", {"methods": ["depth", "group", "async", "listening", "quant"]}, adm, method="PUT")
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/quant"); pg.wait_for_selector("#kf")
    ok(pg.locator("nav.side a[href$='/quant']").count() == 1, "[quant] the menu has the results page once the method is on")
    audit_page(pg, "quant empty/desktop", errs)
    pg.fill("#kf [name=name]", "问卷平台"); pg.click("#kf button.p"); pg.wait_for_selector("code.copy")
    key = pg.inner_text("code.copy").strip()
    ok(key.startswith("xtk_"), "[quant] a key is made and shown once")
    pg.click("#done")
    req = urllib.request.Request(B + "/api/ingest/surveys", data=json.dumps({"survey_id": "S1", "title": "品牌认知问卷", "respondents": 500, "questions": [
        {"text": "你听说过 Acme 吗？", "type": "single", "results": [{"option": "听说过", "count": 320}, {"option": "没听说过", "count": 180}]}]}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    ok(json.loads(urllib.request.urlopen(req).read())["created"] is True, "[quant] the platform can push results with the key")
    pg.reload(); pg.wait_for_selector(".qrow2")
    ok("64%" in pg.inner_text("main") and "品牌认知问卷" in pg.inner_text("main"), "[quant] the page shows the pushed results as percentages")
    c2, g2, e2 = new_page(br, 1280, token=D["tok"]["viewer"])
    g2.goto(B + f"/#/p/{pid}/quant"); g2.wait_for_selector(".qrow2")
    ok(g2.locator("#kf, #impq, [data-ds], [data-rk]").count() == 0 and "xtk_" not in g2.inner_text("main"), "[roles] a viewer sees the survey results without keys or editing")
    audit_page(g2, "quant results (viewer)/desktop", e2); c2.close()
    audit_page(pg, "quant results/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "quant_1280.png"), full_page=True)
    pg.click("#impq"); pg.wait_for_selector("[name=csv]")
    pg.fill("[name=title]", "手工导入"); pg.fill("[name=csv]", "题目,选项,人数" + chr(10) + "渠道,线上,300" + chr(10) + "渠道,线下,100" + chr(10))
    pg.click("#f button.p")
    ok(wait_until(lambda: "手工导入" in pg.inner_text("main") and "75%" in pg.inner_text("main"), 10), "[quant] an imported spreadsheet shows up")
    pg.click("[data-rk]")
    ok(wait_until(lambda: "已撤销" in pg.inner_text("main"), 8), "[quant] a key can be revoked")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/p/{pid}/quant"); pg.wait_for_selector(".qrow2")
    audit_page(pg, "quant/phone", errs)
    ctx.close()


def pool_section(br, D):
    """The insight pool: findings from interviews, public opinion and surveys side by side; a theme in two kinds of source is marked as confirmed; a note joins its theme."""
    rs, adm, pid = D["tok"]["researcher"], D["tok"]["admin"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/pool"); pg.wait_for_selector("#nf")
    ok("洞察池" in pg.inner_text(".hubtabs"), "[pool] the project has the insight pool (tab)")
    ok(pg.locator(".card", has_text="已印证的发现").count() >= 1, "[pool] the summary counts confirmed findings")
    audit_page(pg, "pool/desktop", errs)
    pg.fill("#nf [name=theme]", "价格"); pg.fill("#nf [name=text]", "假设：折扣会放大价格敏感"); pg.click("#nf button.p")
    ok(wait_until(lambda: "假设：折扣会放大价格敏感" in pg.inner_text("main"), 8), "[pool] a note is added to the pool")
    ok(wait_until(lambda: pg.locator(".tag", has_text="已印证").count() >= 1, 8), "[pool] a theme backed by two kinds of source is marked as confirmed")
    audit_page(pg, "pool with notes/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "pool_1280.png"), full_page=True)
    pg.locator("#tri").check()
    ok(wait_until(lambda: pg.locator(".tag", has_text="已印证").count() >= 1 and pg.locator("main .card h3").count() >= 2, 8), "[pool] the filter keeps only confirmed findings")
    pg.locator("#tri").uncheck()
    pg.locator("[data-dn]").first.click()
    ok(wait_until(lambda: "假设：折扣会放大价格敏感" not in pg.inner_text("main"), 8), "[pool] a note can be deleted")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=rs)
    pg.goto(B + f"/#/p/{pid}/pool"); pg.wait_for_selector("#nf")
    audit_page(pg, "pool/phone", errs)
    ctx.close()


def admin_data_section(br, D):
    """Admin pages: the audit trail can be filtered and exported; the retention page lists projects with dates and what they hold."""
    adm, pid = D["tok"]["admin"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/#/audit"); pg.wait_for_selector("#arows tr")
    ok(pg.locator("#arows tr").count() >= 10, "[audit] the trail lists many rows")
    pg.fill("#af [name=action]", "project."); pg.click("#af button.p")
    ok(wait_until(lambda: pg.locator("#arows tr").count() >= 1 and all("project." in t for t in pg.locator("#arows tr").all_inner_texts() if t.strip()), 8), "[audit] filtering by action keeps only matching rows")
    pg.fill("#af [name=action]", "zzz-no-such"); pg.click("#af button.p")
    ok(wait_until(lambda: pg.locator("#arows tr").count() == 0 and "没有符合条件" in pg.inner_text("#acount"), 8), "[audit] a filter with no match says so")
    with pg.expect_download(timeout=15000) as dl:
        pg.click("#aexp")
    ok(dl.value.suggested_filename == "audit.csv", "[audit] the trail exports as CSV")
    audit_page(pg, "audit filters/desktop", errs)
    pg.goto(B + "/#/retention"); pg.wait_for_selector("table.tbl tbody tr")
    ok(pg.locator("tr", has_text="新加坡护肤品牌进入研究").count() == 1 and "现在保有" in pg.inner_text("main"), "[retention] the page lists the projects with what each holds")
    audit_page(pg, "retention/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "retention_1280.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + "/#/retention"); pg.wait_for_selector("table.tbl tbody tr")
    audit_page(pg, "retention/phone", errs)
    pg.goto(B + "/#/audit"); pg.wait_for_selector("#arows tr")
    audit_page(pg, "audit/phone", errs)
    ctx.close()


def segcode_section(br, D):
    """Transcript: search inside it, tag a line with a code (also a new code made on the spot), and see the tag on the line."""
    rs, mi = D["tok"]["researcher"], D["main"]["id"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.goto(B + f"/#/i/{mi}"); pg.wait_for_selector("#tsearch")
    total = pg.locator(".seg").count()
    ok(total >= 2, "[transcript] the transcript has lines")
    pg.fill("#tsearch", "zzzz-no-such-word")
    ok(wait_until(lambda: pg.locator(".seg:visible").count() == 0 and "0 句" in pg.inner_text("#tcount"), 5), "[transcript] searching for a word that is not there hides every line")
    pg.fill("#tsearch", "")
    ok(wait_until(lambda: pg.locator(".seg:visible").count() == total, 5), "[transcript] clearing the search brings every line back")
    pg.locator("[data-tag]").first.click(); pg.wait_for_selector("#f [name=newcode]")
    audit_page(pg, "transcript tag modal/desktop", errs)
    pg.fill("[name=newcode]", "价格敏感QA"); pg.click("#f button.p")
    ok(wait_until(lambda: pg.locator(".segacts .tag", has_text="价格敏感QA").count() == 1, 10), "[transcript] a new code is made and shown on the tagged line")
    audit_page(pg, "transcript tagged/desktop", errs)
    ctx.close()


def vote_section(br, D):
    """Focus group candidate times: each participant ticks the times that suit them, the team sees who can make which and confirms one, and the participant's page moves on."""
    adm, pid = D["tok"]["admin"], D["pid"]
    ids = [call(f"/projects/{pid}/participants", {"name": n, "country": "SG", "language": "zh"}, adm)["id"] for n in ("投票甲", "投票乙")]
    it = call(f"/projects/{pid}/interviews", {"kind": "group", "participant_ids": ids, "title": "投票座谈会", "language": "zh"}, adm)
    iid = it["id"]
    invs = sorted(it["group_invites"], key=lambda g: g["seat"])
    starts = [time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 86400 * d)) for d in (3, 4, 5)]
    call(f"/interviews/{iid}/timeslots", {"starts": starts}, adm, method="PUT")
    slots = call(f"/interviews/{iid}/timeslots", token=adm)["slots"]
    ctx, pg, errs = new_page(br, 390)
    pg.goto(B + invs[0]["invite_path"]); pg.wait_for_selector("input[name=slot]")
    ok(pg.locator("input[name=slot]").count() == 3, "[vote] a focus-group participant sees the candidate times as tick boxes")
    audit_page(pg, "group vote/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_vote_390.png"), full_page=True)
    pg.locator("input[name=slot]").nth(0).check(); pg.locator("input[name=slot]").nth(1).check(); pg.click("#tf button.p")
    ok(wait_until(lambda: "谢谢" in pg.inner_text("#tf") and pg.locator("input[name=slot]:checked").count() == 2, 8), "[vote] saving shows thanks and keeps the ticks")
    call(f"/public/join/{invs[1]['invite_path'].split('t=')[1]}/slot", {"slot_ids": [slots[1]["id"]]})
    ctx2, pg2, errs2 = new_page(br, 1280, token=adm)
    pg2.goto(B + f"/#/i/{iid}"); pg2.wait_for_selector("#timeslotcard [data-confirm]")
    ok("最多人能来" in pg2.inner_text("#timeslotcard") and "2 人能来" in pg2.inner_text("#timeslotcard"), "[vote] the team sees how many can make each time and which is best")
    audit_page(pg2, "group vote staff/desktop", errs2)
    pg2.on("dialog", lambda d: d.accept())
    pg2.locator("#timeslotcard li", has_text="2 人能来").locator("[data-confirm]").click()
    ok(wait_until(lambda: "已确认" in pg2.inner_text("#timeslotcard"), 8), "[vote] confirming fixes the time")
    ctx2.close()
    pg.reload(); pg.wait_for_selector("#accept", timeout=10000)
    ok(pg.locator("input[name=slot]").count() == 0, "[vote] once confirmed the participant's page goes on to the invitation")
    ctx.close()


def gmap_section(br, D):
    """The disagreement map of a focus group: analyse the transcript, see the stances, open one with its verbatim quote and confirm it, export to Word."""
    adm, pid = D["tok"]["admin"], D["pid"]
    ids = [call(f"/projects/{pid}/participants", {"name": n, "country": "SG", "language": "zh"}, adm)["id"] for n in ("地图甲", "地图乙", "地图丙")]
    iid = call(f"/projects/{pid}/interviews", {"kind": "group", "participant_ids": ids, "title": "分歧地图座谈会", "language": "zh"}, adm)["id"]
    lines = [("p1", "我喜欢这个香味，很放心。"), ("p2", "我不喜欢这个香味，香精会让我过敏。"), ("p3", "我觉得价格太贵了。"), ("p1", "价格不错，我推荐。")]
    call(f"/interviews/{iid}/transcript", {"segments": [{"idx": i, "speaker": sp, "start_ms": i * 5000, "end_ms": i * 5000 + 4000, "text_orig": tx} for i, (sp, tx) in enumerate(lines)]}, adm)
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/i/{iid}/disagree"); pg.wait_for_selector("#danalyze")
    ok(pg.locator(".tabs a", has_text="分歧地图").count() == 1 and "还没有分析" in pg.inner_text("#tab"), "[gmap] a focus group has the map tab, empty at first")
    audit_page(pg, "disagreement empty/desktop", errs)
    pg.click("#danalyze"); pg.wait_for_selector("#dmatrix", timeout=15000)
    ok(pg.locator("#dmatrix .dcell").count() >= 4 and "香味" in pg.inner_text("#dmatrix") and "价格" in pg.inner_text("#dmatrix"), "[gmap] the matrix lists topics with each person's stance")
    audit_page(pg, "disagreement/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "disagreement_1280.png"), full_page=True)
    pg.locator(".dcell").first.click(); pg.wait_for_selector("#cacc")
    ok("我喜欢这个香味" in pg.inner_text(".modal") and "建议" in pg.inner_text(".modal"), "[gmap] opening a stance shows the verbatim quote and that it is only a suggestion")
    pg.click("#cacc")
    ok(wait_until(lambda: pg.locator(".dcell", has_text="✓").count() == 1, 8), "[gmap] confirming a stance marks it")
    with pg.expect_download(timeout=15000) as dl:
        pg.click("#dexport")
    ok(dl.value.suggested_filename.endswith(".docx"), "[gmap] the map exports as a Word file")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/i/{iid}/disagree"); pg.wait_for_selector("#dmatrix")
    audit_page(pg, "disagreement/phone", errs)
    ctx.close()


def reel_section(br, D):
    """Highlight reels on the insights page: build one from clips, reorder, share it (link shown once), revoke the link; the shared page lists the clips."""
    rs, pid = D["tok"]["researcher"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector("#rnew")
    ok("精华集锦" in pg.inner_text("#reels"), "[reel] the insights page lists the reels")
    pg.click("#rnew"); pg.wait_for_selector("#rt")
    pg.fill("#rt", "界面做的集锦")
    pg.locator("[data-add]").first.click()
    ok(pg.locator("[data-rm]").count() == 1, "[reel] adding a clip puts it in the chosen list")
    audit_page(pg, "reel editor/desktop", errs)
    pg.click("#rsave")
    ok(wait_until(lambda: "界面做的集锦" in pg.inner_text("#reels"), 8), "[reel] saving adds the reel to the list")
    pg.locator("[data-re]").first.click(); pg.wait_for_selector("#rshare")
    pg.click("#rshare"); pg.wait_for_selector("#rshares input[readonly]")
    link = pg.input_value("#rshares input[readonly]")
    ok("/reel.html?t=" in link, "[reel] sharing shows the link once")
    pg.reload(); pg.wait_for_selector("#rnew"); pg.locator("[data-re]").first.click(); pg.wait_for_selector("[data-rv]")
    pg.locator("[data-rv]").first.click()
    ok(wait_until(lambda: pg.locator("#rshares .tag", has_text="已撤销").count() >= 1, 8), "[reel] a link can be revoked")
    ctx.close()
    ctx, pg, errs = new_page(br, 390)
    pg.goto(B + D["reel_share"]["path"]); pg.wait_for_selector("video.rv")
    ok("精华集锦" in pg.inner_text("#box") and pg.locator("video.rv").count() == 1, "[reel] the shared page shows the title and the clips")
    errs[:] = [e for e in errs if "Failed to load resource" not in e]       # the seeded recording has no media file (same as the single-clip share page)
    audit_page(pg, "reel shared/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "reel_390.png"), full_page=True)
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
    brand = call("/brands", {"name": "示例品牌"}, adm)["id"]
    for role, name in (("researcher", "李研究"), ("moderator", "小周"), ("observer", "客户张"), ("recruiter", "招募员王"), ("viewer", "客户刘")):
        u = call("/users", {"name": name, "email": f"{role}@acme.com", "password": "password123", "role": role, **({"brand_id": brand} if role == "viewer" else {})}, adm)
        ids[role] = u["id"]
        tok[role] = call("/auth/login", {"email": f"{role}@acme.com", "password": "password123"})["token"]
    pid = call("/projects", {"name": "新加坡护肤品牌进入研究", "brief": "了解新加坡消费者如何选购海外护肤品牌", "market": "SG", "language": "en"}, adm)["id"]
    for who in ("researcher", "recruiter", "viewer"):   # these work on / read this project (membership)
        call(f"/projects/{pid}/members", {"user_id": ids[who]}, adm)
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
    reel = call(f"/projects/{pid}/reels", {"title": "精华集锦", "note": "给客户看的三个片段", "clip_ids": [clip["id"]]}, adm)
    reel_share = call(f"/reels/{reel['id']}/share", {}, adm)
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
    return dict(inv=inv["invite_path"], rst=rst["invite_path"], tok=tok, pid=pid, itv=itv, main=main_i, share=share, reel_share=reel_share, ids=ids, people=people, demo_pid=dpid)


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
    staff_routes = [f"#/projects", f"#/p/{pid}/home", f"#/p/{pid}/people", f"#/p/{pid}/sessions", f"#/p/{pid}/analysis", f"#/p/{pid}/members", f"#/p/{pid}/studies", f"#/p/{pid}/listening", f"#/p/{pid}/pool", "#/repo"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights", f"#/i/{mi}/clips"]
    role_routes = {
        "demo_admin": ["#/projects", f"#/p/{D['demo_pid']}/home", "#/demo"],
        "admin": staff_routes + ["#/team", "#/audit", "#/retention"],
        "researcher": staff_routes,
        "moderator": ["#/projects", f"#/p/{pid}"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights"],
        "observer": ["#/projects", f"#/p/{pid}"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights", f"#/i/{mi}/clips"],
        "recruiter": ["#/projects", f"#/p/{pid}", f"#/p/{pid}/people"],
        "viewer": ["#/projects", f"#/p/{pid}", f"#/p/{pid}/pool", f"#/p/{pid}/listening"],
    }
    public_pages = {
        "join-en": f"/join.html?t={D['itv']['en']['invite_token']}", "join-zh": f"/join.html?t={D['itv']['zh']['invite_token']}",
        "join-ms": f"/join.html?t={D['itv']['ms']['invite_token']}", "join-ta": f"/join.html?t={D['itv']['ta']['invite_token']}",
        "invite-join": D["inv"], "invite-reset": D["rst"], "invite-invalid": "/invite.html?t=nope", "join-invalid": "/join.html?t=nope", "share": D["share"]["path"], "share-invalid": "/share.html?t=nope",
        "reel": D["reel_share"]["path"], "reel-invalid": "/reel.html?t=nope",
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
        pg.click("#fg"); pg.wait_for_selector("#done", state="attached")
        ok(pg.locator("#done").is_hidden() and "忘记密码" in pg.inner_text("h2"), "[forgot] the forgot-password form opens with its answer hidden")
        pg.fill("[name=email]", "nobody@nowhere.com"); pg.press("[name=email]", "Enter"); pg.wait_for_selector("#done", state="visible")
        ok("如果这个邮箱对应一个账号" in pg.inner_text("#done") and pg.locator("#fgo").is_disabled(), "[forgot] the answer never says whether the account exists")
        audit_page(pg, "forgot password/desktop", [])   # (the 401 from the wrong password above is expected)
        pg.click("#bk"); pg.wait_for_selector("[name=password]"); pg.fill("[name=email]", "boss@acme.com")
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
        tabs = pg.locator(".hubtabs [aria-current=page]").all_inner_texts()
        ok(tabs == ["场次与邀请"] and len(cur) == 1 and "深访" in cur[0], f"the tab marks 场次与邀请 and the side nav marks exactly one entry (深访 stage), got tab {tabs} nav {cur}")
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
        ok(pg.evaluate("document.querySelector('.tbl.rc tbody td.code').dataset.l") == "编号" and pg.evaluate("document.querySelector('.tbl.rc tbody td').dataset.l") == "选择", "phone: every card line carries its column name")
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
        ok(see("viewer", "#/projects", "#np") == 0 and see("recruiter", "#/projects", "#np") == 0, "a viewer and a recruiter must not see 'new project'")
        ok(see("viewer", f"#/p/{pid}/pool", "#nf, .pick, #imp2") == 0 and see("viewer", f"#/p/{pid}/pool", "#rep") == 1, "[roles] a viewer reads the pool, cannot add notes, and can download the report")
        ok(see("viewer", f"#/p/{pid}/listening", "#addsrc, [data-run], [data-tog], [data-del], [data-dm], [data-au]") == 0, "[roles] a viewer cannot change or take down monitoring content")
        ok(see("recruiter", f"#/p/{pid}/people", "#batch, [data-inv], [data-erase]") == 0 and see("recruiter", f"#/p/{pid}/people", "#imp2") == 1, "[roles] a recruiter can import and screen but not invite or erase")
        for role, other in (("viewer", f"#/p/{pid}/people"), ("viewer", f"#/p/{pid}/sessions"), ("recruiter", f"#/p/{pid}/pool"), ("recruiter", f"#/p/{pid}/insights")):
            c, g, e = new_page(br, 1280, token=D["tok"][role]); g.goto(B + "/" + other); g.wait_for_selector("main"); g.wait_for_timeout(400)
            ok(("/pool" in g.url) if role == "viewer" else ("/people" in g.url), f"[roles] a {role} opening {other.split('/')[-1]} is sent back to their own page"); c.close()
        ok(see("observer", f"#/i/{mi}/clips", "[data-sh]") == 0, "observer must not see 'share link'")
        ok(see("researcher", "#/team", "#nu") == 0 and see("researcher", "#/team", "text=添加成员") == 0, "researcher must not manage the team")
        ok(see("admin", "#/team", "#nu") == 1, "admin should manage the team")
        ok(see("observer", f"#/i/{mi}", "text=Wei Ling") == 0, "observer must not see the participant's name")
        # the recruiting / invitation pages are for the research team only
        for role in ("observer", "moderator"):
            ok(see(role, f"#/p/{pid}/rooms", "nav.side a:has-text('甄别设定'), nav.side a:has-text('场次与邀请')") == 0, f"{role} must not see recruiting / invitation pages in the nav")
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
        members_section(br, D)
        studies_section(br, D)
        repo_section(br, D)
        listening_section(br, D)
        recruit_section(br, D)
        wizard_section(br, D)
        team_section(br, D)
        quant_section(br, D)
        pool_section(br, D)
        admin_data_section(br, D)
        segcode_section(br, D)
        vote_section(br, D)
        gmap_section(br, D)
        reel_section(br, D)

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
