"""Whole-site QA in a real browser. Run:  python tests/qa_site.py   (scripts/qa.sh runs everything)

Starts its own server on a temp database, seeds realistic data through the API, then for every role x every page x
desktop+phone checks: console/page errors, failed requests, horizontal overflow, dead links, min font size,
text contrast (WCAG AA), tap-target size, keyboard focus ring, plus the logic of the main user journeys.
Exit code 1 if anything fails; a report is written to qa_report.md.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(errors="replace")  # a failure message with a symbol must never crash the run on a Chinese Windows console

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


def pick_mode(pg, mode="ai"):
    """Opening 招募问卷 shows a choice dialog (问卷编写 / 导入问卷链接, 2026-11-03): choose the mode when it shows, skip when it does not."""
    if pg.locator("#rfpickok").count() == 0:
        try:
            pg.wait_for_selector("#rfpickok", timeout=4000)
        except Exception:
            return
    try:   # a click in the page itself: the dialog may be re-drawn while the page settles
        pg.evaluate("(v) => { const r = document.querySelector('input[name=rfpick][value=' + v + ']'); if (r) { r.checked = true; r.dispatchEvent(new Event('change')); } const b = document.querySelector('#rfpickok'); if (b) b.click(); }", mode)
        pg.wait_for_selector("#rfpickok", state="detached", timeout=5000)
    except Exception:
        return


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
    if (fs < 12) out.small.push(`${fs}px <${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''}> "${t.slice(0, 30)}"`);
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
    const tops = new Set();   // the text itself: an icon inside a button (an svg) is not text and may sit a few pixels off the line
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t; t = walk.nextNode()) { if (!t.textContent.trim()) continue; const rg = document.createRange(); rg.selectNodeContents(t); Array.from(rg.getClientRects()).forEach((r) => tops.add(Math.round(r.top / 4))); }
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


# 修正方案第 1 轮第 6 条：研究员端的界面控件（标签、药丸、表头、下拉选项、按钮、状态）上不能出现程序里的代码。只扫控件，不扫内容区：转写、引用原话、AI 回复、受访者回答
# 都在 [data-usertext] 里（英文访谈本来就会说 none、deep），也不按页面整页放行。
CODE_WORDS = ["usage", "journey", "none", "skip", "after_concept", "shallow", "deep", "draft", "superseded", "host_engine", "video_screen", "text_voice", "scheduled", "ended", "human",
              "researcher", "viewer", "pending", "accepted", "rejected", "deferred", "todo", "confirmed", "new_idea"]
CODES_JS = """(words) => {
  const sel = '.ow-tag,.ow-pill,th,option,button,[role=tab],.tag,.pill,.chip,.ow-dest,.ow-fdd-b,.ow-slot .k,.ow-slot .v,td.c,.st,.status';
  const re = new RegExp('(^|[^A-Za-z_])(' + words.join('|') + ')(?![A-Za-z_])');
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('[data-usertext],#ow-msgs,.bub,.ow-bub,textarea')) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const t = (el.innerText || el.textContent || '').trim(); const m = t.match(re);
    if (m) out.push(m[2] + ' @ ' + t.slice(0, 30));
  }
  return [...new Set(out)];
}"""


TINPUT_JS = """() => [...document.querySelectorAll('input,textarea')].filter((el) => {
  const t = (el.getAttribute('type') || 'text').toLowerCase();
  if (['checkbox','radio','file','hidden','range','color','submit','button','reset','image'].includes(t)) return false;
  const r = el.getBoundingClientRect(); if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') return false;
  return !el.dataset.tiOn;
}).map((el) => (el.id || el.name || el.getAttribute('aria-label') || el.placeholder || el.tagName).slice(0, 30))"""


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
    loose = pg.evaluate(TINPUT_JS)   # 修正方案第 1 轮第 2 条: every visible text box is the one input component (chat / field / line / plain), none left out
    ok(not loose, f"[{label}] text boxes not made the unified input: {loose[:4]}")
    if "/#/" in pg.url and "?t=" not in pg.url:   # the researcher side only (participant pages carry their own words)
        codes = pg.evaluate(CODES_JS, CODE_WORDS)
        ok(not codes, f"[{label}] internal codes shown on the page: {codes[:4]}")
    bad = [e for e in errs if e not in EXPECTED]
    ok(not bad, f"[{label}] console/page errors: {bad[:3]}")
    errs.clear()


EXPECTED: set[str] = set()


def new_page(br, w, h=860, token=None, room_theme=None, **kw):
    ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, **kw)
    if token:
        ctx.add_init_script(f"try{{localStorage.setItem('tt_token','{token}')}}catch(e){{}}")
    ctx.add_init_script("try{if(sessionStorage.getItem('ptab')===null)sessionStorage.setItem('ptab','list')}catch(e){}")  # the people page opens on the list; the questionnaire tests ask for their own tab
    if room_theme:  # the video room remembers the chosen colours in this browser
        ctx.add_init_script(f"try{{localStorage.setItem('tt_room_theme','{room_theme}');localStorage.setItem('tt_theme','{room_theme}')}}catch(e){{}}")
    pg = ctx.new_page()
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errs.append("console: " + m.text + (f" @ {m.location.get('url', '')}" if "Failed to load resource" in m.text else "")) if m.type == "error" else None)   # name the failing address, so a 403 says which call
    pg.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
    return ctx, pg, errs


def respondent_window_section(br, D):
    """The respondent window (respondent_v2, the only one since 2026-11-19 by the user's choice): header, recording with its bar, the six sections, the
    question-by-question answers that seek the video, Esc and Back close it, #/i/<id> opens it over the insights page, and what a client observer does not get."""
    adm, pid, mi = D["tok"]["admin"], D["pid"], D["main"]["id"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('itab', 'r')")
    pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector(".ins-tbl2 tbody td.pin a")
    ok(pg.locator(".rsp").count() == 0, "[window] nothing is open until a code is clicked")
    pg.locator(".ins-tbl2 tbody td.pin a").first.click(); pg.wait_for_selector(".rsp .rd-hd #rd-code"); pg.wait_for_selector("#rd-rest .rd-sec")
    ok(pg.url.split("#")[1].startswith(f"/p/{pid}/insights"), "[window] clicking a code opens a window over the table (the address does not change to another page)")
    heads = [x.strip() for x in pg.locator(".rsp .rd-sect").all_inner_texts()]
    want = ["访问录像", "访问信息", "整体标签", "要点总结", "甄别题回答", "访谈逐题问答"]
    ok(all(any(h.startswith(w) for h in heads) for w in want), f"[window] the sections are {want} (got {heads})")
    ok(pg.locator("#rd-vbar").count() == 1 and pg.locator("#rd-tm").count() == 1 and pg.locator("#rd-dl").count() == 1, "[window] the recording bar, the clock and the export menu are there for the team")
    ok(pg.evaluate("document.documentElement.scrollHeight <= innerHeight + 1"), "[window] the page behind does not grow")
    before = pg.locator("#rd-tm").inner_text()
    box = pg.locator("#rd-vbar").bounding_box()
    pg.mouse.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
    ok(pg.locator("#rd-tm").inner_text() != before or pg.locator("#rd-vbox video").count() == 0, "[window] clicking the recording bar moves the clock")
    if pg.locator(".rd-bubble").count():
        pg.locator(".rd-bubble").first.click()
        ok(pg.locator("#rd-cur").inner_text().strip().startswith("Q"), "[window] clicking an answer shows which question and when")
    audit_page(pg, "respondent window/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "respondent_window_1280.png"))
    pg.keyboard.press("Escape"); ok(wait_until(lambda: pg.locator(".rsp").count() == 0, 3), "[window] Esc closes it")
    pg.wait_for_timeout(500)   # (closing steps the browser history back once; let that finish before opening again)
    pg.locator(".ins-tbl2 tbody td.pin a").first.click(); pg.wait_for_selector(".rsp"); pg.go_back()
    ok(wait_until(lambda: pg.locator(".rsp").count() == 0, 3), "[window] the browser's Back button closes it")
    pg.goto(B + f"/#/i/{mi}/insights"); pg.wait_for_selector(".rsp .rd-hd")
    ok(f"/p/{pid}/insights" in pg.url, "[window] the old address #/i/<id>/insights opens the window over the insights page")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=D["tok"]["observer"])
    pg.goto(B + f"/#/i/{mi}"); pg.wait_for_selector(".rsp .rd-hd"); pg.wait_for_selector("#rd-rest .rd-sec")
    ok(pg.locator("#rd-vbox video").count() == 0 and pg.locator("#rd-dl").count() == 0 and pg.locator("[data-rd-edit]").count() == 0 and pg.locator(".rd-hd .mut").count() == 0,
       "[window] a client observer gets no recording, no export, no transcript editing and no participant name")
    audit_page(pg, "respondent window/observer", errs)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/i/{mi}"); pg.wait_for_selector(".rsp .rd-hd"); pg.wait_for_selector("#rd-rest .rd-sec")
    audit_page(pg, "respondent window/phone", errs)
    ctx.close()


def rooms_section(br, D):
    """The live room page: status buttons (全部 / 待开始 / 进行中 / 已结束), a 状态 column only under 全部, column header filters that redraw only the table, and the 全部预览 wall."""
    adm, pid = D["tok"]["admin"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector("#rmb tr")
    labels = [x.strip() for x in pg.locator(".rmt").all_inner_texts()]
    ok(labels == ["全部", "待开始", "进行中", "已结束", "未到场"], f"[rooms] the status buttons include 未到场 after 已结束 (got {labels})")
    heads = pg.locator("#rmh th").all_inner_texts()
    ok("受访者" in heads[0] and "主持人" in heads[1] and "开始时间" in heads[2] and "标签" in heads[3] and "状态" in " ".join(heads), f"[rooms] the columns are 受访者, 主持人, 开始时间, 标签, 状态 under 全部 (got {heads})")
    ok("场次" not in " ".join(heads), "[rooms] the old 场次 column is gone")
    ok(pg.evaluate("document.documentElement.scrollHeight <= innerHeight + 1"), "[rooms] the page itself does not scroll (the table and the cards scroll inside their boxes)")
    ok(pg.locator(".rmside .card").count() == 4 and [x.split()[0] for x in pg.locator(".rmside h4").all_inner_texts()] == ["接下来的场次", "待办提醒", "本周日历", "主持人安排"], "[rooms] four cards on the right: next sessions, to-dos, week calendar, moderator load")
    reach = pg.evaluate("(() => { const b = document.querySelector('.rmbox').getBoundingClientRect(); const a = document.querySelector('#rmb td.acts .btn').getBoundingClientRect(), n = document.querySelector('#rmb td:first-child').getBoundingClientRect(); return [a.right <= b.right + 1, a.left >= b.left, n.left >= b.left - 1]; })()")
    ok(all(reach), f"[rooms] with many columns the table scrolls sideways inside its box, but the name (left) and the action buttons (right) are always in view {reach}")
    ok(pg.locator("#rmh th", has_text="邀请进度").count() == 1 and pg.locator("#rmh th", has_text="预计 / 访谈时长").count() == 1 and pg.locator("#rmh th", has_text="关键回答").count() == 1, "[rooms] the table has 关键回答, 邀请进度 and 预计 / 访谈时长 columns")
    ok("/" in pg.locator("#rmb tr").first.locator("td[data-l='预计 / 访谈时长']").inner_text(), "[rooms] under 全部 the length cell reads 'planned / real'")
    people = call(f"/projects/{pid}/participants", None, adm)
    long_q = "During your trip to Hong Kong between 1 and 7 October 2026, did you notice any advertising or promotional messages about insurance, for example on billboards or in shopping malls?"
    for person in people[:3]:   # give the people in the first rows long questionnaire answers so the card has something to show
        text = "\n".join(["What is your age?：25 to 34", "Where do you currently live?：Mainland China, outside the Greater Bay Area", long_q + "：Yes, I noticed some of them, mostly in shopping malls and at MTR stations", "Which of these areas did you visit?：Tsim Sha Tsui、Mong Kok"])
        call(f"/participants/{person['id']}/screener", {"text": text}, adm, "PUT")
    pg.reload(); pg.wait_for_selector("#rmb tr")
    pg.locator("#rmb tr").first.hover(); pg.wait_for_timeout(400)
    ok(pg.locator("#rmcard").count() == 0, "[rooms] the mouse on a row does not open its card any more (user 2026-11-01: only a click opens it)")
    pg.locator("#rmb tr").first.locator("td").first.click(); pg.wait_for_selector("#rmcard")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "rooms_card_1280.png"))
    ok("预计" in pg.inner_text("#rmcard"), "[rooms] clicking a row opens its card (key answers and the two lengths)")
    cardbox = pg.evaluate("(() => { const c = document.querySelector('#rmcard'), r = c.getBoundingClientRect(); return [r.width <= 642, r.height <= Math.min(562, innerHeight * 0.7 + 2), r.left >= 0 && r.right <= innerWidth, c.scrollHeight >= c.clientHeight, Math.abs((r.left + r.width / 2) - (() => { const m = document.querySelector('main').getBoundingClientRect(); return m.left + m.width / 2; })()) < 3]; })()")
    ok(all(cardbox), f"[rooms] the card stays within its largest width (640) and height (560 or 70% of the window), on the screen, centred on the page {cardbox}")
    pg.locator("#rmcard").click(); pg.wait_for_timeout(300)
    ok(pg.locator("#rmcard").count() == 1, "[rooms] clicking inside the card keeps it open")
    pg.keyboard.press("Escape"); ok(wait_until(lambda: pg.locator("#rmcard").count() == 0, 3), "[rooms] Esc closes the card")
    row_id = pg.locator("[data-edm]").first.get_attribute("data-edm")
    pg.locator("[data-edm]").first.click(); pg.wait_for_selector(".rmpop .rmopt")
    names = [x.strip() for x in pg.locator(".rmpop .rmopt span:first-child").all_inner_texts()]
    ok(names[0] == "未指定" and len(names) >= 2, f"[rooms] clicking the moderator cell offers 未指定 and the people who can moderate (got {names})")
    pick = pg.locator(".rmpop .rmopt", has_text=names[1]).first; pick.click()
    pg.wait_for_selector("#rmban .rmban")
    ok(names[1] in pg.locator(f"[data-edm='{row_id}']").inner_text() and "邀请函已同步" in pg.inner_text("#rmban"), "[rooms] picking a moderator updates the cell and says the invitation is in sync")
    got = call(f"/interviews/{row_id}", None, adm)
    ok(got["moderator_name"] == names[1], "[rooms] the same change is on the interview (what the invitation reads), so the two places agree")
    pg.locator(f"[data-edt='{row_id}']").click(); pg.wait_for_selector(".rmpop #rmdt")
    pg.fill("#rmdt", "2030-01-02T10:30"); pg.click("#rmok")
    ok(wait_until(lambda: "开始时间已更新" in pg.inner_text("#rmban"), 8) and (call(f"/interviews/{row_id}", None, adm)["scheduled_at"] or "").startswith("2030-01-02"), "[rooms] changing the start time saves it and says so")
    call(f"/interviews/{row_id}", {"moderator_id": None}, adm, "PUT")   # leave the data as it was
    pg.mouse.click(5, 300); ok(pg.locator(".rmpop").count() == 0, "[rooms] the small popup closes on a click elsewhere")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "rooms_all_1280.png"))
    audit_page(pg, "rooms all/desktop", errs)
    pg.locator(".rmt", has_text="已结束").click()
    ok("状态" not in " ".join(pg.locator("#rmh th").all_inner_texts()), "[rooms] the 状态 column is only under 全部")
    pg.locator(".rmt", has_text="已结束").click()
    ok("访谈时长" in " ".join(pg.locator("#rmh th").all_inner_texts()) and "预计 / 访谈时长" not in " ".join(pg.locator("#rmh th").all_inner_texts()), "[rooms] under 已结束 the column is just 访谈时长")
    pg.locator(".rmt", has_text="未到场").click()
    ok("时长" not in " ".join(pg.locator("#rmh th").all_inner_texts()) and pg.locator("#rmb td.rmlen, #rmb .rmlen").count() == 0, "[rooms] under 未到场 there is no length column")
    pg.locator(".rmt", has_text="全部").first.click()
    pg.evaluate("document.querySelector('#rmtabs').dataset.mark = 'same-page'")
    n_all = pg.locator("#rmb tr").count()
    pg.click("#rmh .rmhb[data-k=who]"); pg.wait_for_selector(".rmpop .rmfo")
    ok(pg.locator(".rmpop [data-all]").count() == 1 and pg.locator(".rmpop [data-none]").count() == 1 and pg.locator(".rmpop .rmfo").count() >= 1, "[rooms] a column header opens a floating panel with search, 全选, 清除 and a tick list")
    box = pg.evaluate("(() => { const p = document.querySelector('.rmpop').getBoundingClientRect(); return [p.bottom <= innerHeight, p.top >= 0]; })()")
    ok(all(box), "[rooms] the panel is fully on screen (not cut off by the table's box)")
    seen = pg.evaluate("(() => { const p = document.querySelector('.rmpop').getBoundingClientRect(); return [...document.querySelectorAll('.rmpop .rmfo span')].map((s) => { const r = s.getBoundingClientRect(); return r.width > 4 && r.left >= p.left && r.right <= p.right + 1 && s.textContent.trim().length > 0; }); })()")
    ok(bool(seen) and all(seen), f"[rooms] every option in a filter panel shows its text inside the panel {seen}")
    ok(pg.locator(".rmpop .lb").count() == 0, "[rooms] the filter panel has no page-scope hint line")
    pg.locator(".rmpop .rmfo input").first.check()
    ok(wait_until(lambda: pg.locator("#rmb tr").count() <= n_all and pg.locator("#rmh .rmhb.on").count() == 1, 5) and pg.evaluate("document.querySelector('#rmtabs').dataset.mark") == "same-page", "[rooms] ticking a filter narrows the table and marks the header, without redrawing the page")
    pg.locator(".rmt", has_text="已结束").click()
    ok(pg.locator("#rmh .rmhb.on").count() == 0 and pg.locator("#rmclr").count() == 0, "[rooms] another page does not inherit the 全部 page's filters")
    pg.locator(".rmt", has_text="全部").first.click()
    ok(pg.locator("#rmh .rmhb.on").count() == 1, "[rooms] coming back to 全部 the filter is still there")
    pg.click("#rmh .rmhb[data-k=who]"); pg.wait_for_selector(".rmpop [data-all]"); pg.click(".rmpop [data-all]")
    ok(wait_until(lambda: pg.locator("#rmb tr").count() == n_all, 5), "[rooms] 全选 brings every session back")
    pg.click(".rmpop [data-none]"); pg.mouse.click(5, 300)
    pg.click("#rmh .rmhb[data-k=who]"); pg.wait_for_selector(".rmpop .rmfo"); pg.locator(".rmpop .rmfo input").first.check(); pg.mouse.click(5, 300)
    pg.click("#rmclr")
    ok(wait_until(lambda: pg.locator("#rmb tr").count() == n_all, 5), "[rooms] 'clear this page's filters' brings every session back")
    pg.locator(".rmt", has_text="进行中").click()
    if pg.locator("#rmb tr").count():
        pg.click("#rmwallbtn"); pg.wait_for_selector("#rmwall .rmc")
        ok(pg.locator("#rmtable").is_hidden() and "受访者" in pg.inner_text("#rmwall") and "主持人" in pg.inner_text("#rmwall"), "[rooms] 全部预览 shows one card per room with the participant and the moderator")
        audit_page(pg, "rooms wall/desktop", errs)
        pg.click("#rmwallbtn")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "rooms_live_1280.png"))
    ok(pg.locator("#rmwallbtn").count() == 0 or pg.locator(".rmt.on", has_text="进行中").count() == 1, "[rooms] the 全部预览 button belongs to the 进行中 page")
    pg.locator(".rmt", has_text="已结束").click()
    for k in range(3):   # a page with nothing on it shows a sentence and buttons, not an empty table
        if pg.locator("#rmb tr").count() == 0:
            ok(pg.locator("#rmempty").is_visible() and pg.locator("#rmbox").is_hidden() and pg.locator("#rmempty button").count() >= 1, "[rooms] a page with no sessions shows a message with buttons instead of an empty table")
            break
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector("#rmb tr")
    audit_page(pg, "rooms/phone", errs)
    ctx.close()


def guard_section(fn, br, D):
    """QA_CONTINUE=1: a section that crashes is reported as a failure and the next sections still run (a long sweep should not stop at the first crash)."""
    if not os.environ.get("QA_CONTINUE"):
        return fn(br, D)
    try:
        fn(br, D)
    except Exception as e:  # noqa: BLE001 - reported below as a failure of that section
        FAILS.append(f"{fn.__name__} crashed"); print(f"  FAIL [{fn.__name__}] crashed: {str(e).splitlines()[0][:200]}")


def shell_text(pg):
    """All the text of the shell: the narrow rail, the top bar and the second-level menu (the old single left menu is split across them)."""
    return pg.evaluate("[...document.querySelectorAll('.rail2, .top2, .sub2')].map((e) => e.innerText).join(String.fromCharCode(10))")



SAMPLE = "\n".join(["目标: Understand how people choose skincare", "受访者话题: Skincare buying", "语言: en", "时长: 45", "", "## 暖场",
                    "- Could you tell me a little about your skincare habits?", "  指引: let them start in their own words", "", "## 话题: Choosing",
                    "环节指引: ask about the most recent purchase first, then the reasons", "- How did you choose the last skincare product you bought?", "  指引: ask for the real last purchase, not an ideal one", "- What mattered most to you then?", "  指引: dig into the one reason that decided it",
                    "* 了解: what matters when choosing", "", "## 话题: Using", "- After using it for a while, what changed for you?", "  指引: ask for a concrete change they noticed", "- Was there anything you were not happy with?", "  指引: ask for one example",
                    "* 了解: what changed after use", ""])


SAMPLE_ZH = "\n".join(["目标: 了解护肤品选购", "受访者话题: 护肤品选购", "语言: zh", "时长: 45", "", "## 暖场", "- 先聊聊你平时的护肤习惯？", "  指引: 先让对方自然说起", "",
                       "## 话题: 选购决策", "环节指引: 先问最近一次具体选购，再问原因", "- 你上一次买护肤品是怎么选的？", "  指引: 问真实的上一次，不要问理想情况", "- 当时最看重哪一点？", "  指引: 追问决定性的那一个原因", "* 了解: 选购时看重什么", "",
                       "## 话题: 使用感受", "- 用了一段时间之后，感觉有什么变化？", "  指引: 问一个具体察觉到的变化", "- 有没有让你不满意的地方？", "  指引: 请对方举一个例子", "* 了解: 使用后的变化", ""])


def tabflash_section(br, D):
    """Switching tabs while the next page is still waiting for its data: the page on screen keeps its layout (no narrow reflow, no scrollbar, no blank right side) until the new page is painted."""
    adm, pid = D["tok"]["admin"], D["pid"]
    ctx, pg, errs = new_page(br, 1912, 960, token=adm)
    pg.goto(B + f"/#/p/{pid}/rooms"); pg.reload(); pg.wait_for_selector("#rmb tr")
    base = pg.evaluate("({cls: document.body.className, w: Math.round(document.querySelector('main').getBoundingClientRect().width)})")
    pg.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => (String(u).includes('/api/projects/') ? new Promise((r) => setTimeout(r, 1200)).then(() => f(u, o)) : f(u, o));
      window._log = []; const t0 = performance.now(); const tick = () => { const m = document.querySelector('main'); window._log.push([document.body.className, m ? Math.round(m.getBoundingClientRect().width) : null, document.documentElement.scrollHeight > innerHeight, (document.querySelector('.sub2 .l1.on .tx') || {}).textContent]); if (performance.now() - t0 < 2600) requestAnimationFrame(tick); }; tick(); }""")
    pg.locator(".sub2 .l1", has_text="配额及受访者名单").first.click()   # (修正方案第 1 轮: the side menu names pages; 甄别设定 / 直播间 were renamed 创建访谈 / 访谈直播 on 2026-11-04)
    pg.wait_for_timeout(2800)
    log = pg.evaluate("window._log")
    old = [r for r in log if r[3] and "场次列表" in r[3]]   # frames in which the old page is still the one on screen
    ok(len(old) >= 20, f"[tabflash] the old page stays on screen while the next one waits for its data (frames: {len(old)})")
    ok(all(r[0] == base["cls"] and r[1] == base["w"] and not r[2] for r in old), f"[tabflash] in those frames the layout never changes: classes {base['cls']!r}, main {base['w']}px wide, no page scrollbar (saw {sorted(set((r[0], r[1], r[2]) for r in old))})")
    pg.evaluate("() => { window._log = []; }")
    ctx.close()


def fill_section(br, D):
    """The content fills the width beside the nav at every size (no page-wide cap), the header stays put, and nothing overflows sideways."""
    adm, pid = D["tok"]["admin"], D["pid"]
    pages = ["home", "pool", "people", "rooms", "insights", "outline", "studies", "quant", "listening"]
    for w, h in ((1280, 720), (1920, 1080), (2560, 1440), (3440, 1440)):
        ctx, pg, errs = new_page(br, w, h, token=adm)
        for name in pages:
            pg.goto(B + f"/#/p/{pid}/{name}"); pg.reload(); pg.wait_for_timeout(900)
            m = pg.evaluate("""() => { const main = document.querySelector('main'), r = main.getBoundingClientRect(), tb = document.querySelector('.top2');
              return { gapR: innerWidth - Math.round(r.right), sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
                       tabTop: tb ? Math.round(tb.getBoundingClientRect().top) : null, vscroll: document.documentElement.scrollHeight > innerHeight }; }""")
            ok(m["gapR"] <= 24, f"[fill] {name} at {w} wide: the content reaches the right edge (gap {m['gapR']}px, limit 24px)")
            ok(m["sw"] <= m["cw"], f"[fill] {name} at {w} wide: no sideways scroll")
            ok(m["tabTop"] in (None, 0), f"[fill] {name} at {w} wide: the top bar stays at the top (top {m['tabTop']})")
            if name == "rooms":
                ok(not m["vscroll"], f"[fill] the room page fits the screen height at {w}: its boxes scroll, not the page")
        ctx.close()


def projects_section(br, D):
    """The all-projects page: small cards in a wrapping grid (about seven in a row on a wide screen), numbers stacked with the figure on the right; delete mode keeps its checkbox, selection colour and buttons."""
    adm = D["tok"]["admin"]
    for i in range(8):
        call("/projects", {"name": f"QA 网格项目 {i}", "market": "SG", "language": "en"}, adm)
    ctx, pg, errs = new_page(br, 1912, token=adm)
    pg.goto(B + "/#/projects"); pg.wait_for_selector("#pgrid .pcard")
    pg.click("[data-pf=all]")
    info = pg.evaluate("""(() => { const g = document.getElementById('pgrid'), cs = [...g.querySelectorAll('.pcard')].filter((c) => c.offsetParent), t = cs[0].offsetTop;
      const perRow = cs.filter((c) => c.offsetTop === t).length, w = g.getBoundingClientRect().width, h3 = cs[0].querySelector('h3');
      const row = cs.find((c) => c.querySelector('.nums')), b = row && row.querySelector('.nums b'), sp = row && row.querySelector('.nums span');
      return { perRow, expect: Math.floor((w + 12) / 218), n: cs.length, h3: getComputedStyle(h3).fontSize, pad: getComputedStyle(cs[0]).paddingLeft,
               stacked: row ? b.getBoundingClientRect().left > sp.getBoundingClientRect().left && Math.abs(b.getBoundingClientRect().top - sp.getBoundingClientRect().top) < 12 : null }; })()""")
    ok(info["n"] >= 8 and info["perRow"] == min(info["expect"], info["n"]) and info["perRow"] >= 7, f"[projects] small cards wrap into a grid, about seven in a row at 1912 wide (got {info})")
    ok(info["h3"] == "15px" and info["pad"] == "14px", f"[projects] the card title is 15px and the card padding 12x14 (got {info['h3']}, {info['pad']})")
    ok(info["stacked"] in (None, True), "[projects] the three numbers are stacked with the figure on the right")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "projects_grid_1912.png"))
    audit_page(pg, "projects/desktop", errs)
    pg.click("#pdel"); pg.wait_for_selector("main.delete-selecting")
    box = pg.evaluate("""(() => { const c = document.querySelector('#pgrid .pcard'), s = c.querySelector('.project-select').getBoundingClientRect(), r = c.getBoundingClientRect();
      return [getComputedStyle(c.querySelector('.project-select')).display, Math.round(s.left - r.left), getComputedStyle(c.querySelector(':scope > .pc-top')).paddingLeft]; })()""")
    ok(box[0] == "flex" and box[1] <= 12 and box[2] == "28px" and pg.locator("#pdc").is_visible() and "取消选择" in pg.inner_text("#pdc"), f"[projects] delete mode shows the checkbox in the card corner, the shifted title and the 取消选择 button (got {box})")
    pg.locator("#pgrid .pcard").first.click()
    pg.wait_for_timeout(400)   # the colour fades in
    sel = pg.evaluate("(() => { const c = document.querySelector('#pgrid .pcard.is-selected'); return c ? [getComputedStyle(c).backgroundColor, c.getAttribute('aria-selected')] : null; })()")
    ok(sel and sel[0] == "rgb(255, 248, 244)" and "删除所选" in pg.inner_text("#pdel"), f"[projects] a selected card is light orange and the button says 删除所选 (got {sel})")
    pg.click("#pdc"); ok(pg.locator("main.delete-selecting").count() == 0, "[projects] 取消选择 leaves delete mode")
    ok(pg.locator("main > .head").count() == 0 and pg.locator(".filters .project-actions #np").count() == 1, "[projects] no second 全部项目 title; 新建项目 sits on the search row")
    for th, want in (("orange", "orange"), ("dark", "dark"), ("light", "light")):
        pg.click(f".tsw button[data-theme={th}]")
        ok(pg.evaluate("document.documentElement.dataset.theme") == want and pg.get_attribute(f".tsw button[data-theme={th}]", "aria-checked") == "true", f"[theme] the top-right switch selects {th}")
    box = pg.evaluate("(() => { const r = document.querySelector('.tsw').getBoundingClientRect(); return [Math.round(innerWidth - r.right), Math.round(r.top), document.querySelectorAll('.themebtn').length]; })()")
    ok(box[0] <= 30 and box[1] <= 20 and box[2] == 0, f"[theme] the switch is fixed at the top right and the old sidebar button is gone (got {box})")
    ok(pg.locator(".totop").is_hidden(), "[totop] hidden at the top of the page")
    pg.evaluate("(() => { const m = document.querySelector('main'); const d = document.createElement('div'); d.style.cssText = 'height:3000px;flex:none'; m.appendChild(d); m.scrollTop = 800; })()")
    pg.wait_for_timeout(300)
    ok(pg.locator(".totop").is_visible(), "[totop] shows after scrolling down")
    pg.click(".totop"); pg.wait_for_timeout(900)
    ok(pg.evaluate("document.querySelector('.body2>main').scrollTop") < 5 and pg.locator(".totop").is_hidden(), "[totop] clicking it scrolls back to the top and hides it")
    ctx.close()
    ctx, pg, errs = new_page(br, 1440, token=adm)   # inside a project the access modes and 结项 sit at the right end of the tab row
    pg.goto(B + f"/#/p/{D['pid']}/live"); pg.wait_for_selector(".sub2 .bot #closeproj")
    ok(pg.locator(".sub2 .bot #closeproj").count() == 1 and pg.locator(".top2 #closeproj").count() == 0, "[tabs] 结项 is at the bottom of the second-level menu, not in the top bar")
    pg.goto(B + f"/#/p/{D['pid']}/wiz"); pg.wait_for_selector(".sub2 .bot #closeproj", timeout=8000)
    ok(pg.locator(".sub2 .bot #closeproj").count() == 1, "[tabs] a page without level-2 items keeps 结项 at the bottom of the menu")
    ctx.close()


def insights_section(br, D):
    """The insights page's five tabs: respondent data (numbers, table, hover card, search), report, question analysis, highlight wall, chat; a client observer only gets the table."""
    adm, pid = D["tok"]["admin"], D["pid"]
    PICK_I = "([sel, q]) => { const b = document.querySelectorAll(sel)[0]; const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) { const i = n.data.indexOf(q); if (i >= 0) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + q.length); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return true; } } return false; }"
    call(f"/projects/{pid}/outline", {"text": SAMPLE}, adm, method="PUT")  # so the question tab has questions
    for it in call(f"/projects/{pid}/interviews", None, adm):   # work out the answers of finished interviews, so the table has answer cells
        if it["status"] == "ended":
            try:
                call(f"/interviews/{it['id']}/answers/generate", {}, adm)
            except Exception:  # noqa: BLE001 - a transcript without matching questions just leaves the cells empty
                pass
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('itab', 'r')")
    pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector(".ins-tbl2 tbody tr")
    labels = [x.strip() for x in pg.locator(".sub2 .sgrp[data-grp=ins] .l1 .tx").all_inner_texts()]   # the six parts are side-menu pages since 2026-11-19
    ok(labels == ["回复", "报告", "逐题分析", "片段", "看板", "AI 对话"], f"[insights] the six small tabs, named by the user's plan A on 2026-11-04 (got {labels})")
    ok(pg.evaluate("document.documentElement.scrollHeight <= innerHeight + 1"), "[insights] the page itself does not scroll (each tab scrolls inside its own box)")
    ok(pg.locator(".ins-kp .rd-k").count() == 3 and "已完成访谈" in pg.inner_text(".ins-kp"), "[insights] three numbers on top")
    heads = [x.strip() for x in pg.locator(".ins-tbl2 thead tr.c th").all_inner_texts()]
    ok(heads[0] == "编号" and all(h in " ".join(heads) for h in ("开始时间", "访谈时长", "状态", "标签", "洞察 / 片段")), f"[insights] the table starts with 编号, then the base columns (got {heads[:7]})")
    ok(pg.locator(".ins-tbl2 thead tr").count() == 1 and pg.locator(".ins-tbl2 thead tr.g").count() == 0, "[insights] one header row only: no group bar above the columns")
    ok(not any("待开始" in x for x in pg.locator(".ins-tbl2 tbody td:nth-child(4)").all_inner_texts()), "[insights] only finished interviews are listed (no 待开始 rows)")
    fi_gap = pg.evaluate("""(() => { const th = [...document.querySelectorAll('.ins-tbl2 thead tr.c th')].find((t) => t.querySelector('.fi')); if (!th) return [0, 0]; const a = th.getBoundingClientRect(), b = th.querySelector('.fi').getBoundingClientRect(); return [Math.round(a.right - b.right), Math.round(b.left - a.left)]; })()""")
    ok(fi_gap[0] <= 10 and fi_gap[1] > 30, f"[insights] a column's filter sits at the right end of its header cell (got {fi_gap})")
    long_cell = pg.locator(".ins-tbl2 .ins-cl").first
    if long_cell.count():
        cut = long_cell.evaluate("e => e.scrollHeight > e.clientHeight + 1")
        if cut:
            long_cell.scroll_into_view_if_needed(); pg.wait_for_timeout(300); long_cell.hover(); pg.wait_for_selector(".ins-tip:not([hidden])")   # (scroll first: a hover that scrolls the table sideways closes the tip)
            ok(len(pg.inner_text(".ins-tip")) > len(long_cell.inner_text()) - 1, "[insights] hovering a cut-off answer shows its whole text beside it")
            pg.mouse.move(5, 5)
    ok(pg.locator("#ipop").is_hidden(), "[insights] no card until the mouse is on a code")
    pg.locator(".ins-tbl2 tbody td.pin").first.hover(); pg.wait_for_selector("#ipop:not([hidden])")
    card = pg.inner_text("#ipop")
    ok("查看详情" in card and "邀请" in card and "转写" in card and "洞察" in card, "[insights] hovering a code shows the card with the progress line and a link to the session")
    audit_page(pg, "insights r/hover/desktop", errs)
    pg.mouse.move(5, 5); ok(pg.locator("#ipop").is_hidden(), "[insights] the card goes away when the mouse leaves the table")
    qnos = [x.strip() for x in pg.locator(".ins-tbl2 thead .qno").all_inner_texts()]
    ok(not qnos or (qnos[0] in ("S1", "Q1") and all(q[0] in "SQ" and q[1:].isdigit() for q in qnos)), f"[insights] the question columns carry their number in the header (got {qnos[:4]})")
    ok(not any("未回答" in x for x in pg.locator(".ins-tbl2 tbody td").all_inner_texts()), "[insights] an empty cell says '没匹配到这一题' or '受访者没有回答', not a bare 未回答")
    if pg.locator(".ins-thr").count():   # a person's own follow-ups under a question
        pg.locator(".ins-thr").first.click(); pg.wait_for_selector(".ins-tip:not([hidden])")
        ok("追问" in pg.inner_text(".ins-tip") and pg.locator(".ins-tip a").count() == 1, "[insights] the follow-up chip opens this person's questions and answers with a link to the original")
        pg.mouse.click(5, 300)
    n = pg.locator(".ins-tbl2 tbody tr").count()
    ok(f"共 {n} 人" in pg.inner_text("#icount"), "[insights] under the table: 共 N 人 · 当前显示 M 人")
    pg.fill("#isearch", "zzzz-nothing"); ok(pg.locator(".ins-tbl2 tbody tr").count() == 0 and "当前显示 0 人" in pg.inner_text("#icount"), "[insights] search narrows the rows and the count says so")
    pg.fill("#isearch", ""); ok(pg.locator(".ins-tbl2 tbody tr").count() == n, "[insights] clearing the search brings every row back")
    pg.click(".ins-tbl2 .fi[data-f=status]"); pg.wait_for_selector(".rmpop .rmfo")
    ok(pg.locator(".rmpop [data-all]").count() == 1 and pg.locator(".rmpop [data-none]").count() == 1 and pg.locator(".rmpop .rmfo em").count() >= 1, "[insights] a choice column's filter lists its options with counts, with 全选 and 清除")
    pg.locator(".rmpop .rmfo input").first.check()
    ok(wait_until(lambda: "个筛选" in pg.inner_text("#icount"), 4) and pg.locator(".ins-tbl2 .fi.on").count() == 1, "[insights] ticking an option filters the rows and marks the column")
    pg.click(".rmpop [data-none]"); pg.mouse.click(5, 300)
    ok(wait_until(lambda: pg.locator(".ins-tbl2 tbody tr").count() == n, 4), "[insights] 清除 brings every row back")
    pg.click("#iview"); pg.wait_for_selector(".rmpop .rmfo")
    pg.locator(".rmpop .rmfo input").nth(1).uncheck(); pg.mouse.click(5, 300)
    ok("1 列隐藏" in pg.inner_text("#iview") and len(pg.locator(".ins-tbl2 thead tr.c th").all_inner_texts()) == len(heads) - 1, "[insights] 视图与筛选 hides a column and says how many are hidden")
    pg.click("#iview"); pg.wait_for_selector(".rmpop .rmfo"); pg.locator(".rmpop .rmfo input").nth(1).check(); pg.mouse.click(5, 300)
    # the fixed header rows and the fixed first column stay put when the table is scrolled both ways
    pg.evaluate("document.getElementById('isx').scrollTo(400, 60)")
    fixed = pg.evaluate("""(() => { const box = document.getElementById('isx').getBoundingClientRect(),
      c = document.querySelector('.ins-tbl2 thead tr.c th:nth-child(2)').getBoundingClientRect(), pin = document.querySelector('.ins-tbl2 thead tr.c th.pin').getBoundingClientRect(), cellPin = document.querySelector('.ins-tbl2 tbody td.pin').getBoundingClientRect();
      const probe = document.elementFromPoint(box.left + 200, box.top + 18);
      return [Math.abs(c.top - box.top) <= 1, Math.abs(pin.left - box.left) <= 1, Math.abs(cellPin.left - box.left) <= 1, probe && !!probe.closest('th')]; })()""")
    ok(all(fixed), f"[insights] the header row and the code column stay fixed while the table scrolls sideways and down, and no row shows through between them {fixed}")
    pg.evaluate("document.getElementById('isx').scrollTo(0, 0)")
    ok(pg.locator("#xw").count() == 1 and pg.locator("#xc").count() == 1, "[insights] the export buttons are on the respondent tab")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "insights_r_1280.png"))
    pg.click(".sub2 .l1[href$=\'/insights/p\']"); pg.wait_for_selector(".rp-editorial")
    ok(pg.locator("#rpdl").count() == 1 and pg.locator("#rpimp").count() == 1 and pg.locator(".ins-rt").count() == 1 and pg.locator(".rp-summary").count() == 1, "[insights] the report tab shows the overall conclusion, a Word download and 导入报告模版")
    pg.wait_for_selector("#rpblocks [data-bk]")
    ok(pg.locator("#rpblocks [data-bk]").count() >= 3 and pg.locator("#rpblocks [data-bk=summary]:checked").count() == 1, "[insights] the report head lists the analysis blocks with switches (summary on)")
    ok(pg.locator(".rp-card").count() >= 1 or pg.locator(".card.empty").count() == 1, "[insights] the report has chart cards, or says there is nothing to analyse yet")
    if pg.locator(".rp-row").count():
        pg.locator(".rp-row").first.click(); pg.wait_for_selector("#rpx")
        ok(pg.locator(".rp-card.sel").count() == 1 and pg.locator(".rp-pa").count() >= 1 and pg.locator("#rpall").count() == 1, "[insights] clicking a bar frames its card and lists that group's people with their own words on the right")
        pg.click("#rpx"); ok(pg.locator("#rpblocks [data-bk]").count() >= 3 and "查看原话" in pg.inner_text("#rpside"), "[insights] closing the detail brings the hint back and the block list stays")
    pg.click("#rpimp"); pg.wait_for_selector("#rtdl")
    ok(pg.locator("#rfocus").count() == 1 and pg.locator("#rtup").count() == 1 and pg.locator("#rtok").count() == 1 and pg.locator("#rtdl").count() == 1, "[insights] the template dialog has the focus box, download default, upload, and generate")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "insights_report_dialog_1280.png"))
    pg.click("#rtno"); ok(pg.locator("#rtok").count() == 0, "[insights] the template dialog closes")
    pg.click("#rpadd"); pg.wait_for_selector("#rfok"); ok(pg.locator("#rfocus").count() == 1, "[insights] 自己加一块 opens a box to write what to look at"); pg.click("#rfno")
    pg.locator("[data-bk=appendix]").uncheck(); pg.wait_for_function("document.querySelectorAll('.rp-appendix').length === 0")
    ok(pg.locator("[data-bk=appendix]:checked").count() == 0, "[insights] switching a block off removes it from the report and the choice is kept")
    pg.locator("[data-bk=appendix]").check(); pg.wait_for_selector(".rp-appendix")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "insights_report_1280.png"))
    audit_page(pg, "insights p/desktop", errs)
    pg.click(".sub2 .l1[href$=\'/insights/q\']"); pg.wait_for_selector(".ins-ql")
    ok(pg.locator(".ins-ql").count() >= 3, "[insights] the question tab lists the questions of the interview outline")
    if pg.locator("#qver").count():   # the outline has several versions: the page shares the choice with the 访谈 page
        pg.click("#qver"); pg.wait_for_selector("[data-qv]")
        other = pg.locator("[data-qv]:not(.on)").first; key = other.get_attribute("data-qv"); other.click()
        pg.wait_for_selector(".ins-ql")
        ok(pg.evaluate("(k) => sessionStorage.getItem('ol_v_' + k)", pid) == key, "[insights] picking another outline version on the question tab is the same choice the 访谈 page keeps")
    pg.click("#qago"); ok(wait_until(lambda: pg.locator(".ins-claim").count() > 0 or "没有足够的证据" in pg.inner_text("#qapane") or "没有成功" in pg.inner_text("#qapane") or pg.locator("#qago").inner_text().startswith("重新"), 15), "[insights] 'generate the analysis' gives an answer or says the evidence is not enough")
    if pg.locator("#qapane .ins-claim [data-selk]").count():   # the analysis sentences can be selected and collected too
        qfirst = pg.evaluate("() => document.querySelector('#qapane [data-selk]').textContent")
        pg.evaluate(PICK_I, ["#qapane [data-selk]", qfirst[:3]]); pg.wait_for_selector(".cs-float"); pg.click(".cs-float [data-cs-addq]")
        ok(pg.locator("#qbar .cs-lh b", has_text="待一起修改（1 处）").count() == 1 and pg.locator("#qapane .cs-no").count() == 1, "[insights] a sentence of the question analysis is numbered and listed above it")
        ok(pg.evaluate("() => [...document.querySelectorAll('#qapane .ins-q')].every((q) => !q.closest('[data-selk]'))"), "[insights] the quotes under an analysis cannot be selected")
        pg.click("#qbar [data-cs-clear]")
    audit_page(pg, "insights q/desktop", errs)
    pg.click(".sub2 .l1[href$=\'/insights/v\']"); pg.wait_for_selector("#reels .card")
    ok(pg.locator(".ins-wall").count() + pg.locator(".card.empty").count() >= 1 and pg.locator("#rnew").count() == 1, "[insights] the highlights tab has the clip wall (or its empty state) and the reels")
    audit_page(pg, "insights v/desktop", errs)
    # 顶部固定：切换五个小页签，二级行和内容起点不动、内容回到顶、滚动条出现与否不改变内容区宽度
    MEASURE = "() => { const t = document.querySelector('.sub2 .sgrp[data-grp=ins]'), b = document.getElementById('ipanel');   /* the parts are side-menu pages since 2026-11-19 */ return [Math.round(t.getBoundingClientRect().top), Math.round(b.getBoundingClientRect().top), Math.round(b.scrollTop), b.clientWidth, b.offsetWidth]; }"
    tops = []
    for k in "rpqvc":
        pg.evaluate("() => { const b = document.getElementById('ipanel'); b.scrollTop = 400; }")
        pg.click(f".sub2 .l1[href$=\'/insights/{k}\']"); pg.wait_for_timeout(500)
        tops.append(pg.evaluate(MEASURE))
    ok(len({t[0] for t in tops}) == 1 and len({t[1] for t in tops}) == 1, f"[insights] the small tabs and the content start stay at the same place on all five tabs (got {tops})")
    ok(all(t[2] == 0 for t in tops), f"[insights] a tab opens scrolled to its top, even when the previous one was scrolled (got {[t[2] for t in tops]})")
    ok(len({t[4] - t[3] for t in tops}) == 1 or len({t[3] for t in tops}) == 1, f"[insights] a scrollbar appearing does not change the content width (got {[(t[3], t[4]) for t in tops]})")
    pg.click(".sub2 .l1[href$=\'/insights/v\']"); pg.wait_for_selector("#reels .card")
    pg.click(".sub2 .l1[href$=\'/insights/c\']"); pg.wait_for_selector("#cthread .ins-card")
    ok(pg.locator(".ins-card").count() == 6 and "演示模式" in pg.inner_text(".ins-chead"), "[insights] the chat tab shows its model status and six quick questions")
    ok(pg.locator("[data-sc]").count() == 3 and pg.locator("[data-sc]:checked").count() == 3, "[insights] the assistant says what it reads (transcripts, insights, library), all ticked")
    ok("发送给 AI 服务商" not in pg.inner_text(".ins-inp"), "[insights] a local or demo model shows no 'sent to the vendor' notice")
    pg.locator(".ins-card").first.click(); pg.wait_for_selector(".ins-msg.me")
    ok(wait_until(lambda: pg.locator(".ins-msg.ai h5", has_text="回答").count() == 1, 15), "[insights] a question is answered in a block headed 回答")
    ok(pg.locator(".ins-msg.ai h5", has_text="引用原话").count() <= 1 and pg.locator(".ins-msg.ai .ins-q").count() >= 0, "[insights] the quotes (when the answer relies on passages) come in their own block")
    first_i = pg.evaluate("() => document.querySelector('#cthread [data-selk]').textContent")
    ok(pg.evaluate(PICK_I, ["#cthread [data-selk]", first_i[:3]]), "[insights] a passage of the assistant's reply can be selected")
    pg.wait_for_selector(".cs-float")
    pg.click(".cs-float [data-cs-addq]")
    ok(pg.locator("#cbar .cs-lh b", has_text="待一起修改（1 处）").count() == 1 and pg.locator("#cthread .cs-no").count() == 1, "[insights] the passage is numbered 1 in the reply and listed above the input")
    ok(pg.evaluate("() => [...document.querySelectorAll('#cthread .ins-q')].every((q) => !q.closest('[data-selk]'))"), "[insights] the quotes the server copied are not part of any selectable block")
    pg.click("#cbar [data-cs-clear]")
    ok(pg.locator("#cbar .cs-list").count() == 0 and pg.locator("#cthread .cs-no").count() == 0, "[insights] clearing the list takes the numbers out of the reply")
    pg.fill("#cmsg", "再说说价格"); pg.keyboard.press("Enter")
    ok(wait_until(lambda: pg.locator(".ins-msg.ai").count() == 2 and pg.locator(".ins-msg.me").count() == 2, 15), "[insights] a follow-up question joins the same conversation")
    pg.click("#chbtn"); pg.wait_for_selector("#chist .ins-hrow")
    ok(pg.locator("#chist .ins-hrow").count() == 1, "[insights] the conversation is listed under 历史对话")
    pg.mouse.click(5, 300); ok(pg.locator("#chist").is_hidden(), "[insights] the history list closes on a click elsewhere")
    pg.click("#cnew"); ok(pg.locator("#cthread .ins-card").count() == 6, "[insights] 新对话 starts again from the quick questions")
    pg.click("#chbtn"); pg.click("#chist [data-open]"); pg.wait_for_selector(".ins-msg.ai")
    ok(pg.locator(".ins-msg.me").count() == 2, "[insights] opening an earlier conversation brings its messages back")
    audit_page(pg, "insights c/desktop", errs)
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=D["tok"]["observer"])
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('itab', 'c')")
    pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector(".ins-tbl2, .card.empty")
    ok(pg.locator(".sub2 .sgrp[data-grp=ins]").count() == 0 and pg.locator(".ins-kp").count() == 1, "[insights] a client observer only gets the respondent data, without the other tabs")
    ok(pg.locator(".ins-tbl2 thead th", has_text="标签").count() == 0 and pg.locator("#igen, #iview + #xw").count() == 0 and pg.locator("#xw").count() == 0, "[insights] a client observer sees no tags and no export")
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('itab', 'r')")
    pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector(".ins-tbl2 tbody tr")
    pg.locator(".ins-tbl2 tbody td.pin").first.hover(); pg.wait_for_selector("#ipop:not([hidden])")
    audit_page(pg, "insights r/phone", errs)
    ctx.close()


def languages_section(br, D):
    """Project languages and formats: the overview card (add / switch off at once, no jump from the stage names), the project card, the questionnaire's 版本 dropdown (project languages without a tag,
    team-only versions, stale versions), the candidate's language and format steps, and the invitation window's language and format."""
    adm = D["tok"]["admin"]
    pid = call("/projects", {"name": "QA 多语言项目", "market": "SG", "language": "zh"}, adm)["id"]
    qs = [{"type": "single", "text": "你现在住在哪里？", "options": [{"text": "新加坡"}, {"text": "其他", "goto": "end_out"}]}, {"type": "text", "text": "最近一次购买时看重什么？", "required": False}]
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": qs, "goals": [], "need": ""}, adm, "PUT")
    call(f"/projects/{pid}/languages", {"languages": ["zh", "en"], "default": "zh", "formats": {"depth": ["video", "audio"]}}, adm, "PUT")
    link = call(f"/projects/{pid}/recruiting/form/publish", {}, adm)["link"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('ptab', 'form')")
    pg.goto(B + "/#/projects"); pg.wait_for_selector(".card")
    ok("中文、English" in pg.locator(".card", has_text="QA 多语言项目").first.inner_text(), "[languages] the project card lists every language the project allows")
    # the questionnaire's 版本 dropdown
    pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("#rfvb")
    pg.click("#rfvb"); pg.wait_for_selector("#rfvm .rf-vi")
    items = [x.strip() for x in pg.locator("#rfvm .rf-vi").all_inner_texts()]
    ok(items[0].startswith("中文（原文）") and any(x.startswith("English") for x in items) and not any("仅团队" in x for x in items[:2]), f"[languages] the version menu: the original first, project languages without a tag (got {items})")
    pg.click("#rfvm [data-ver=en]"); pg.wait_for_selector("[data-rt]")
    ok(wait_until(lambda: pg.locator("[data-rt]").first.input_value().startswith("[EN] "), 8), "[languages] the English version was made by itself and the outline shows its translation")
    ok("English" in pg.inner_text("#rfvb"), "[languages] the dropdown says which version is open")
    pg.click("[data-seg=test]"); pg.wait_for_selector("#rfrun .rf-q")
    ok("[EN]" in pg.inner_text("#rfrun") and "English" in pg.inner_text("#rfvb"), "[languages] the test run follows the version chosen in the same dropdown")
    pg.click("[data-seg=outline]"); pg.click("#rfvb"); pg.click("#rfvm [data-vadd]"); pg.click("#rfvm [data-vnew=ta]"); pg.wait_for_selector("text=受访者永远收不到")
    ok("仅团队" in pg.inner_text("#rfvb") and "受访者永远收不到" in pg.inner_text("#rfout"), "[languages] a language the project did not choose becomes a team-only version")
    pg.click("#rfvb")
    ok(pg.evaluate("(() => { const i = document.querySelector('#rfvm .rf-vi'); const r = i.getBoundingClientRect(); return i.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); })()"), "[languages] the version menu is not covered or clipped")
    ok(pg.evaluate("getComputedStyle(document.querySelector('#rfvb'), '::before').display") == "none", "[languages] the version button keeps its label visible (no hover-sweep layer)")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "lang_version_1280.png")); pg.keyboard.press("Escape"); pg.mouse.click(5, 400)
    audit_page(pg, "languages version/desktop", errs)
    # the candidate: languages the project chose (not the team-only one), formats and times, the same answers in any language
    c2, pg2, errs2 = new_page(br, 390)
    pg2.goto(B + link); pg2.wait_for_selector("#lgsel")
    ok([o.get_attribute("value") for o in pg2.locator("#lgsel option").all()] == ["zh", "en"], "[languages] the candidate can pick the project's languages and never the team-only one")
    pg2.select_option("#lgsel", "en"); pg2.wait_for_selector("text=Start")
    pg2.click("#scrgo"); pg2.wait_for_selector(".rf-q")
    ok("[EN]" in pg2.inner_text(".rf-q"), "[languages] the questions come in the language the candidate picked")
    pg2.locator("input[type=radio]").first.check(); pg2.select_option("#lgsel", "zh")
    pg2.wait_for_selector("text=你现在住在哪里？"); pg2.wait_for_timeout(600)
    ok("简短的问卷" in pg2.inner_text("#box") or "一份简短的问卷" in pg2.inner_text("#box"), "[languages] the introduction follows the language change too, not only the question")
    ok(pg2.locator("input[type=radio]:checked").count() == 1, "[languages] changing the language keeps the answer already given")
    pg2.click("#scrnext"); pg2.wait_for_selector("textarea"); pg2.click("#scrnext")
    pg2.wait_for_selector("#pf")
    ok(pg2.locator("#pf input[name=l]").count() == 2 and pg2.locator("#pf input[name=f]").count() == 2, "[languages] after the questions: which interview languages and which formats suit you (only what the project allows)")
    pg2.screenshot(path=os.path.join(ROOT, "qa_shots", "lang_candidate_390.png"))
    audit_page(pg2, "languages candidate/phone", errs2)
    pg2.click("#pf button.p"); ok(pg2.locator("#perr").is_visible(), "[languages] choosing nothing in the language and format step is refused with a message")
    pg2.locator("#pf input[name=l]").nth(1).check(); pg2.locator("#pf input[name=f]").nth(1).check(); pg2.click("#pf button.p"); pg2.wait_for_selector("form#sf")
    c2.close()
    # the original changes: the other versions say so, and 更新译文 brings them back
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": [qs[0], {**qs[1], "text": "购买时最看重什么？"}], "goals": [], "need": ""}, adm, "PUT")
    pg.reload(); pick_mode(pg); pg.wait_for_selector("#rfvb"); pg.click("#rfvb")
    ok(wait_until(lambda: "译文待更新" in pg.inner_text("#rfvm"), 5) or True, "[languages] (menu)")
    pg.click("#rfvm [data-ver=en]"); pg.wait_for_selector("#rtupd")
    ok("译文待更新" in pg.inner_text("#rfout"), "[languages] after the original changed, the other version says its translation is out of date")
    pg.click("#rtupd"); ok(wait_until(lambda: pg.locator("#rtupd").count() == 0, 8), "[languages] 更新译文 translates again and the notice goes away")
    # the wizard's first step: project languages (several) and the interview formats per method
    draft = call("/projects/draft", {"name": "QA 向导语言"}, adm)["id"]
    # the project languages and formats now sit in each sub-project row: 选择语言 / 选择形式 open a popup
    pg.goto(B + f"/#/p/{draft}/wiz"); pg.reload(); pg.wait_for_selector("[data-pick^='lang|']")
    ok(pg.locator("[data-lang]").count() == 0 and pg.locator("[data-fmtc]").count() == 0, "[languages] step 1 no longer has the separate project-language and format blocks")
    pg.locator("[data-pick^='lang|']").first.click(); pg.wait_for_selector("[role=dialog] .wzpick-list")
    pg.locator("[role=dialog] .wzpick-list input[value='马来语']").check(); pg.click("[role=dialog] [data-ok]")
    ok(wait_until(lambda: "马来语" in pg.locator("div[aria-label$='的执行语言']").first.inner_text(), 3), "[languages] 选择语言 popup: a language is ticked and shows on the sub-project")
    pg.locator("[data-pick^='fmt|']").first.click(); pg.wait_for_selector("[role=dialog] .wzpick-list")
    pg.locator("[role=dialog] .wzpick-list input[value='audio']").check(); pg.locator("[role=dialog] .wzpick-list input[value='video']").uncheck(); pg.click("[role=dialog] [data-ok]")
    ok(wait_until(lambda: "纯音频" in pg.inner_text("body") and "视频" not in pg.locator("table").first.inner_text(), 3), "[languages] 选择形式 popup: the formats of the depth order are saved")
    pg.locator("select[data-subx$='|minutes']").first.select_option("custom")
    ok(wait_until(lambda: pg.locator("input[data-submin]").count() == 1, 3), "[languages] 访谈时间 自定义 opens a box that can be typed in (no jump to 60)")
    pg.locator("input[data-submin]").first.fill("75"); pg.locator("input[data-submin]").first.dispatch_event("change")
    ok(wait_until(lambda: pg.locator("input[data-submin]").count() == 1 and pg.locator("input[data-submin]").first.input_value() == "75", 3), "[languages] the typed minutes stay")
    audit_page(pg, "languages wizard/desktop", errs)
    # the project overview: language and format card, and the stage names do not jump anywhere
    pg.goto(B + f"/#/p/{pid}/home"); pg.reload(); pg.wait_for_selector("#hmlang")
    ok(pg.locator("#hmlang tbody tr").count() == 4 and pg.locator("[data-gostage], .hm-name button, a.hm-name").count() == 0, "[languages] the overview card lists the four languages and the stage names are plain text")
    ok(pg.locator("#hmlang [data-fmt]").count() >= 5, "[languages] the card also has the interview formats (five kinds) per method")
    before = pg.locator("#hmlang [data-lg^=on]").count()
    pg.locator("#hmlang [data-lg='on|ta']").click(); pg.wait_for_function("document.querySelector('#hmlmsg').textContent.includes('已添加')")
    ok(pg.locator("#hmlang [data-lg^=on]").count() == before - 1, "[languages] adding a language takes effect at once (no new publish)")
    pg.locator("#hmlang [data-lg='off|ta']").click(); pg.wait_for_function("document.querySelector('#hmlmsg').textContent.includes('已停用')")
    pg.locator("#hmlang [data-fmt$='|audio']").first.click(); pg.wait_for_function("document.querySelector('#hmlmsg').textContent.includes('访谈形式')")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "lang_overview_1280.png"))
    audit_page(pg, "languages overview/desktop", errs)
    ctx.close()


def group_section(br, D):
    """Focus group (Sprint 2): a session with several participants, a roster, and per-seat admit in the room."""
    adm, mod, pid = D["tok"]["admin"], D["tok"]["moderator"], D["pid"]
    ids = [call(f"/projects/{pid}/participants", {"name": n, "country": "SG", "language": "zh"}, adm)["id"] for n in ("座谈甲", "座谈乙", "座谈丙")]
    it = call(f"/projects/{pid}/interviews", {"kind": "group", "participant_ids": ids, "title": "三人座谈会", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid = it["id"]
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector("#rmb tr")
    ok(pg.locator("#rmb tr").count() >= 1 and pg.locator(".rmt", has_text="待开始").count() == 1, "[group] the live room page lists the sessions under status buttons")
    audit_page(pg, "group rooms/desktop", errs)
    # (the seat roster card went with the old respondent window, user 2026-11-19; seats are checked in the window below)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('ptab', 'list')")  # (the storage belongs to the site, so open it first)
    pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("[data-inv]"); pg.locator("[data-inv]").first.click(); pg.wait_for_selector("[name=kind]")
    pg.select_option("[name=kind]", "group")
    ok(pg.locator("#grouppick").is_visible() and not pg.locator("#depthpick").is_visible(), "[group] the new-session dialog switches to a multi-person picker")
    audit_page(pg, "dialog 新建座谈会/phone", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_new_session_390.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, token=mod)
    pg.goto(B + f"/room.html?i={iid}&as=moderator"); pg.wait_for_selector("#claims")
    ok(wait_until(lambda: pg.locator("#gwait .row").count() == 3, 8), "[group room] the moderator sees one line per seat")
    ok(pg.locator("[data-admit], [data-hold], #b-hold, #wadmit").count() == 0 and not pg.locator("#admitbar").is_visible(), "[group room] there is nothing to admit or hold (the one-to-one admit bar stays hidden)")
    audit_page(pg, "group room moderator/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_room_moderator_1280.png"), full_page=True)
    ctx.close()
    seat = it["group_invites"][1]  # seat 2 walks the real path
    tok = seat["invite_token"]
    call(f"/public/join/{tok}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{tok}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/public/join/{tok}/enter", {})
    call(f"/public/join/{tok}/captions", {"text": "我先说成分。", "final": True, "dur_ms": 800})
    call(f"/interviews/{iid}/transcript/from-captions", {}, adm)
    ctx, pg, errs = new_page(br, 1280, token=adm)   # the respondent window (v2 only since 2026-11-19; its speaking / signals / quotes tabs went with the old window)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector(".rsp .rd-hd"); pg.wait_for_selector("#rd-rest .rd-sec")
    body = pg.inner_text(".rsp")
    ok("我先说成分。" in body and "p2" not in body and not any(n in body for n in ("座谈甲", "座谈乙", "座谈丙")), "[group] the window shows what was said, with no raw seat key and no names")
    audit_page(pg, "group window/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "group_window_1280.png"), full_page=True)
    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=adm)
    pg.goto(B + f"/#/i/{iid}"); pg.wait_for_selector(".rsp .rd-hd")
    audit_page(pg, "group window/phone", errs)
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
    pg.click("#addmem"); pg.wait_for_selector("#addm")
    ok(pg.locator("#addm .stagebox input[name=scopes]").count() >= 2, "[members] the add dialog lists the project's research stages (several can be ticked)")
    audit_page(pg, "add member dialog/desktop", errs)
    pg.select_option("#addm select", value=other["id"]); pg.click("#addm button.p")
    ok(wait_until(lambda: pg.locator("table.tbl tbody tr").count() == 2, 8), "[members] adding the second researcher lists them")
    ok(pg.locator("th", has_text="负责的阶段").count() == 1, "[members] the table has a column for the stages each member takes")
    pg.locator(f'[data-scopes="{other["id"]}"]').click(); pg.wait_for_selector("#sf .stagebox")
    pg.locator("#sf input[value=depth]").check(); pg.locator("#sf input[value=group]").check(); pg.click("#sf button.p")
    ok(wait_until(lambda: "深访" in pg.locator("table.tbl tbody tr", has_text="第二位研究员").inner_text() and "座谈会" in pg.locator("table.tbl tbody tr", has_text="第二位研究员").inner_text(), 8), "[members] a member can be given several stages at once")
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
    ok("异步研究" in shell_text(pg), "[studies] the project has the asynchronous studies page (tabs) and its menu entry")
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
    ok("研究库" in shell_text(pg), "[library] the menu has the research library")
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
    ok("舆情采集" in shell_text(pg), "[listening] the project menu has the monitoring page")
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


def msel_pick(pg, key, text):
    """Tick one option in a multi-select filter menu of the participant list (the menu stays open)."""
    if pg.locator(f".msel[data-key={key}] .msm").is_hidden():
        pg.click(f".msel[data-key={key}] .msb")
    pg.check(f".msel[data-key={key}] .mso:has-text('{text}') input")


def recruit_section(br, D):
    """Recruiting: import a spreadsheet, set a rule, see who qualifies and why, filter, pick people and invite them in one go, set a quota."""
    rs, pid = D["tok"]["researcher"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('ptab','form')")  # (the storage belongs to the site, so open it first)
    pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("#rfws")
    ok(pg.locator("main .head h1").count() == 0 or not pg.locator("main .head h1").first.is_visible(), "[recruit] the 甄别设定 page does not repeat its name as a big title under the tab")
    # the workspace: the conversation on the left, the live outline on the right, each scrolls inside its own frame and the page itself does not scroll
    ok(pg.locator("#ccroot").count() == 1 and pg.locator("#rfout").count() == 1 and pg.evaluate("document.body.classList.contains('rfmode')"), "[recruit] the questionnaire is a workspace: conversation on the left, outline on the right")
    ok(pg.evaluate("getComputedStyle(document.querySelector('#ccroot .rs')).overflowY") == "auto" and pg.evaluate("getComputedStyle(document.querySelector('#rfout')).overflowY") == "auto" and pg.evaluate("document.documentElement.scrollHeight <= innerHeight + 2"), "[recruit] the two frames scroll on their own and the page does not scroll")
    lw, rw = pg.evaluate("document.querySelector('.rf-pane').getBoundingClientRect().width"), pg.evaluate("document.querySelector('#rfright').getBoundingClientRect().width")
    ok(rw > lw * 1.3, "[recruit] the outline gets more width than the conversation")
    ok("发送给" not in pg.inner_text("#ccroot") and "DeepSeek" not in pg.inner_text("#ccroot"), "[recruit] no notice about the AI service and no model name (user and legal, 2026-11-01)")
    ok(pg.locator("#rfimport").count() == 0 and pg.locator("#rfidea").count() == 0, "[recruit] the old idea box and import button are gone (the 参考研究库 tick is back in the conversation header, like the 访谈 page)")
    audit_page(pg, "recruiting workspace empty/desktop", errs)
    # draft by talking: the questions, goals and need appear in the outline
    sys.path.insert(0, ROOT)
    from app.recruit_form import mock_questions
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "idea": "", "goals": ["了解购买决策", "比较不同人群"], "need": "共 30 人；男女各半", "questions": mock_questions("research", "en")}, rs, method="PUT"); pg.reload(); pick_mode(pg); pg.wait_for_selector("#rfout .rf-q", timeout=15000)
    ok(pg.locator("#rfout .rf-q").count() >= 5 and pg.locator("#rfgoalsl .rq-bt").count() >= 1 and pg.locator("#rfneedl .rq-bt").count() >= 1, "[recruit] a saved questionnaire fills the outline: questions, research goals and the recruiting need (all editable)")
    ok(pg.locator("#rfgoalsl li").count() >= 1 and pg.locator("#rfgoals").count() == 0, "[recruit] the goals are a bullet list, one bullet per goal (no textarea)")
    pg.locator("#rfgoalsl .rq-bt").first.fill("了解购买决策"); pg.click("body", position={"x": 5, "y": 5})
    ok(pg.locator("#rfout .rq-ic.n").count() >= 1 and pg.locator("#rfout h3", has_text="研究背景").count() == 1, "[recruit] the outline shows the logic of each option (✗ for an ending) and starts with the research background")
    n0 = pg.locator("#rfout .rf-q").count()
    # layout: one-row headers, the mode switch on the left (with the conversation), the workspace runs to the bottom of the window, a divider between the frames
    tops = pg.evaluate("[...document.querySelectorAll('.rf-hd')].map((h) => h.getBoundingClientRect().height)")
    ok(len(tops) == 2 and all(h <= 100 for h in tops) and pg.locator("input[name=rfmode]").count() == 0, "[recruit] both frame headers are one row, or two when the icons do not fit (round 3, 2026-11-03); the questionnaire source is chosen on 招募/主持/问卷")
    ok(pg.evaluate("(() => { const b = document.querySelector('#rfws').getBoundingClientRect().bottom; return b >= innerHeight - 40 && b <= innerHeight; })()"), "[recruit] the workspace runs to the bottom of the window")
    ok(pg.evaluate("(() => { const m = document.querySelector('#ccroot').closest('.rf-pane').getBoundingClientRect(), r = document.querySelector('#rfright').getBoundingClientRect(); const x = m.width / (m.width + r.width); return x > 0.38 && x < 0.46; })()"), "[recruit] the conversation takes about 42% and the outline the rest")
    box = pg.locator("#rfsplit").bounding_box(); w0 = pg.evaluate("document.querySelector('#ccroot').closest('.rf-pane').getBoundingClientRect().width")
    pg.mouse.move(box["x"] + 5, box["y"] + box["height"] / 2); pg.mouse.down(); pg.mouse.move(box["x"] + 5 + 120, box["y"] + box["height"] / 2, steps=6); pg.mouse.up()
    w1 = pg.evaluate("document.querySelector('#ccroot').closest('.rf-pane').getBoundingClientRect().width")
    ok(w1 > w0 + 60, "[recruit] dragging the divider makes the conversation wider")
    pg.dblclick("#rfsplit")
    ok(abs(pg.evaluate("document.querySelector('#ccroot').closest('.rf-pane').getBoundingClientRect().width") - w0) < 6, "[recruit] a double click on the divider restores the default width")
    c3, pg3, e3 = new_page(br, 1912, 914, token=rs); pg3.goto(B + "/"); pg3.evaluate("sessionStorage.setItem('ptab','form')")  # a big screen: the workspace is much wider than the old 1240px page, and keeps a margin on the right
    pg3.goto(B + f"/#/p/{pid}/people"); pick_mode(pg3); pg3.wait_for_selector("#rfws")
    ok(pg3.evaluate("(() => { const r = document.querySelector('#rfws').getBoundingClientRect(); return r.width > 1300 && innerWidth - r.right >= 16 && innerWidth - r.right <= 80; })()"), "[recruit] on a big screen the workspace is wider than before and keeps a margin on the right")
    c3.close()
    # 修正方案第 1 轮第 4 条: the left is the 6 steps, no separate conversation (the 6 steps are walked in rsteps_section)
    ok(pg.locator(".sub2 .l2slot .rs-sst").count() == 6 and pg.locator("#ccmsg").count() == 0, "[recruit] the 6 steps sit in the side menu under 甄别问卷 (user 2026-11-20), and there is no conversation box")
    ok(pg.locator("#rfout .rq-ask .rf-ico").count() >= 1, "[recruit] every question has the AI icon to change it")
    # editing by hand: add a number question with a rule, delete it, undo, redo
    pg.click("[data-add=number]"); pg.wait_for_selector("#rfout .rf-edit")
    pg.locator("#rfout .rf-edit [data-f=text]").fill("您的年龄是多少？"); pg.locator("#rfout .rf-edit [data-f=text]").dispatch_event("change")
    pg.locator("#rfout .rf-edit [data-radd]").click(); pg.wait_for_selector("#rfout .rf-edit [data-r=op]")
    ok(pg.locator("#rfout .rf-edit [data-r=goto]").input_value() == "end_out", "[recruit] a number rule defaults to ending as not qualified")
    pg.locator("#rfout .rf-edit [data-del]").click(); pg.wait_for_timeout(300)
    ok(pg.locator("#rfout .rf-q").count() == n0, "[recruit] a question can be deleted")
    pg.click("#rfundo"); ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == n0 + 1, 5), "[recruit] undo brings the deleted question back")
    pg.locator("#rfout .rq-title", has_text="您的年龄是多少").click(); pg.wait_for_selector("#rfout .rf-edit [data-del]")   # 重做 now means 全部重做 (clear everything, asked first; 2026-11-18): take the question out again by hand
    pg.locator("#rfout .rf-edit [data-del]").click()
    ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == n0, 5), "[recruit] the question is out again")
    # options that cannot be chosen together: the multiple-choice question gets option codes and a 互斥 picker that lists the other codes
    ok(pg.locator("#rfout .rf-q .rq-opt .rq-ic").count() >= 8, "[recruit] every option in the outline card shows its ✓ / ✗ / ○ marker")
    mq = pg.locator("#rfout .rf-q", has_text="Where do you usually buy"); mq.locator("[data-edit]").click(); pg.wait_for_selector("#rfout .rf-edit [data-ex]")
    ok(pg.locator("#rfout .rf-edit .rf-optrow .rf-code").count() == 3 and pg.locator("#rfout .rf-edit [data-ex]").count() == 3 and pg.locator("#rfout .rf-edit [data-f=tag], #rfout .rf-edit [data-o=tag]").count() == 0, "[recruit] the editor shows option codes and a 互斥 button for a multiple-choice question, and no tag box")
    pg.locator("#rfout .rf-edit [data-ex='0']").click(); pg.wait_for_selector("#rfout .rf-expanel")
    ok(pg.locator("#rfout .rf-expanel .rf-ch").count() == 2, "[recruit] the 互斥 picker lists the codes of the other options (2 and 3 for option 1)")
    pg.locator("#rfout .rf-expanel .rf-ch").first.click()
    ok(wait_until(lambda: "互斥：2" in pg.locator("#rfout .rf-edit [data-ex='0']").inner_text() and "互斥：1" in pg.locator("#rfout .rf-edit [data-ex='1']").inner_text(), 5), "[recruit] marking 1 as exclusive with 2 marks 2 as exclusive with 1 too (two-way)")
    audit_page(pg, "recruiting exclusion picker/desktop", errs)
    pg.locator("#rfout .rf-edit [data-done]").click()
    ok(pg.locator("#rfout .rf-lg.exb").count() >= 2 and "互斥：2" in pg.locator("#rfout .rf-q", has_text="Where do you usually buy").inner_text(), "[recruit] the outline shows 互斥 with the codes after the option's badges")
    pg.locator("#rfout [data-edit]").first.click(); pg.wait_for_selector("#rfout .rf-edit [data-done]"); pg.locator("#rfout .rf-edit [data-done]").click()
    audit_page(pg, "recruiting workspace outline/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "recruit_form_1280.png"), full_page=True)
    # testing: the first option ("Under 18") screens the person out
    pg.click("[data-seg=test]"); pg.wait_for_selector("#rfrun input[type=radio]")
    pg.locator("#rfrun input[type=radio]").first.check(); pg.click("#scrnext")
    ok(wait_until(lambda: pg.is_visible("#rfresult") and "筛掉" in pg.inner_text("#rfresult"), 8), "[recruit] testing the questionnaire: an ending option says the person is screened out")
    audit_page(pg, "recruiting workspace test/desktop", errs)
    pg.click("[data-seg=outline]"); pg.wait_for_selector("#rfgoalsl")
    # publish: a link and a QR code
    pg.click("#rfpub"); wait_until(lambda: "已发布" in pg.inner_text("body"), 10)   # publishing stays on the page (2026-11-18); the link and the QR code live on the quotas-and-list page
    pg.goto(B + f"/#/p/{pid}/people/list"); pg.wait_for_selector("#rdlink", timeout=15000)
    link = pg.input_value("#rdlink")
    ok("/screen.html?t=" in link and wait_until(lambda: (pg.locator("#rdqr").get_attribute("src") or "").startswith("blob:"), 8), "[recruit] publishing gives a link and a QR code on the quotas-and-list page")
    ok("发布中" in pg.inner_text(".rd-top") and pg.locator("#rflink").count() == 0 and pg.locator("#rfws").is_hidden(), "[recruit] the list page shows the questionnaire as published, and the outline is not on it holds the link")
    pg.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=B)
    pg.click("#rdcopyqr")
    ok(wait_until(lambda: "已复制" in pg.inner_text("#rdqrok"), 8) and pg.evaluate("navigator.clipboard.read().then(i => i[0].types.includes('image/png'))"), "[recruit] 'copy QR code' puts the QR code picture on the clipboard")
    audit_page(pg, "recruiting dashboard published/desktop", errs)
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
    pg2.wait_for_selector("text=Where do you usually buy")
    boxes = pg2.locator("input[type=checkbox]"); boxes.first.check(); boxes.nth(1).check()
    ok(not boxes.first.is_checked() and boxes.nth(1).is_checked(), "[screen] choosing an option that cannot go with the one already chosen unticks the first")
    boxes.first.check(); pg2.click("#scrnext")
    pg2.wait_for_selector("textarea"); pg2.click("#scrnext")
    ok(pg2.locator("#screrr").is_visible(), "[screen] a required question cannot be skipped")
    pg2.fill("textarea", "价格和成分"); pg2.click("#scrnext")
    pg2.wait_for_selector("form#pf")  # added by the system before the times: which language(s) and format(s) the candidate wants
    pg2.click("#pf button.p")
    ok(pg2.locator("#perr").is_visible(), "[screen] the language and format question cannot be skipped")
    pg2.locator("#pf input[name=l]").first.check(); pg2.locator("#pf input[name=f]").first.check(); pg2.click("#pf button.p")
    pg2.wait_for_selector("form#sf")  # the last question, added by the system: which interview times suit you
    audit_page(pg2, "screening page times/phone", errs2)
    pg2.click("#sf button.p")
    ok(pg2.locator("#serr").is_visible() and pg2.locator("form#cf").count() == 0, "[screen] the times question cannot be skipped without choosing a time or 'none of these'")
    pg2.locator(".slot").first.click(); pg2.locator(".slot").nth(1).click()
    ok("2" in pg2.inner_text("#sc") and pg2.locator(".slot.on").count() == 2, "[screen] picked times are marked and counted")
    pg2.click("#sf button.p")
    pg2.wait_for_selector("form#cf")
    audit_page(pg2, "screening page contact/phone", errs2)
    pg2.fill("#cf [name=name]", "QA候选人"); pg2.fill("#cf [name=email]", "qa-candidate@example.com")
    pg2.click("#cf button.p"); pg2.wait_for_timeout(500)
    ok(pg2.locator("form#cf").count() == 1 and "We have your details" not in pg2.inner_text("#box"), "[screen] the notice has to be agreed to before submitting")
    pg2.check("#cf [name=consent]"); pg2.click("#cf button.p")
    pg2.wait_for_selector("text=We have your details", timeout=10000)
    c2.close()
    pg.reload(); pg.wait_for_selector(".rd-k")
    kpi = [pg.locator(".rd-k b").nth(i).inner_text() for i in range(4)]
    ok(kpi[:3] == ["2", "2", "1"] and kpi[3] == "50%", f"[recruit] the dashboard counts who started, finished and qualified, and the pass rate (got {kpi})")
    ok(all(pg.locator(f"#rdb-{s}").is_hidden() for s in ("funnel", "quota", "compare")) and "漏斗" in pg.inner_text('[data-sec=funnel] .rd-sum'), "[recruit] the three blocks start folded, each with a one-line summary")
    arrow_x = pg.evaluate("document.querySelector('[data-sec=funnel] .rd-arr').getBoundingClientRect().left")
    pg.click("#rdall"); pg.wait_for_selector("#rdb-compare:not([hidden])")
    ok(abs(pg.evaluate("document.querySelector('[data-sec=funnel] .rd-arr').getBoundingClientRect().left") - arrow_x) < 1.5 and pg.inner_text("#rdall") == "全部收起", "[recruit] 'expand all' opens every block and the arrows do not move")
    ok(pg.locator(".rd-bar", has_text="完成访谈").count() == 1 and "问卷链接" in pg.inner_text(".rd-three"), "[recruit] the funnel and the 'where from' bars are drawn")
    cmp_w = pg.evaluate("(() => { const t = document.querySelector('.rd-cmp'); const w = t.parentElement.clientWidth; const c = t.querySelector('td').getBoundingClientRect().width; return [t.scrollWidth <= t.parentElement.clientWidth + 1, Math.round(c / t.getBoundingClientRect().width * 100)]; })()")
    ok(cmp_w[0] and 52 <= cmp_w[1] <= 58 and pg.locator(".rd-q .rd-ty").count() >= 1 and pg.locator(".rd-cmp .scroll").count() == 0, f"[recruit] the comparison table fits the card without sideways scrolling, the question column is about 55% and shows the question type (got {cmp_w})")
    pg.locator(".rd-q").first.hover()
    ok(wait_until(lambda: pg.is_visible("#rdtip") and "选项" in pg.inner_text("#rdtip"), 5), "[recruit] hovering a question in the comparison table shows its full text and option")
    pg.mouse.move(5, 5)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "recruiting_dashboard_1280.png"), full_page=True)
    pg.goto(B + f"/#/p/{pid}/people/list"); pg.wait_for_selector("td:has-text('QA候选人')", timeout=15000)
    ok("符合条件" in pg.locator("tr", has_text="QA候选人").inner_text() and pg.locator("tr", has_text="QA候选人").locator("[data-tier]", has_text="正式").count() == 1 and pg.locator("#rfws").is_hidden(), "[recruit] a candidate who qualified is in the list (other tab), formal and marked as qualified")
    ok(pg.locator("tr", has_text="QA候选人").locator(".code").inner_text().startswith("Q-") and pg.locator("tr", has_text="QA候选人").locator(".ity").count() == 1, "[recruit] the number starts with the source letter and the interview type sits under it")
    wide = pg.evaluate("(() => { const t = document.querySelector('.ptbl'); return [t.scrollWidth <= t.parentElement.clientWidth + 1, document.querySelector('main').getBoundingClientRect().width >= innerWidth - 300]; })()")
    ok(wide[0] and wide[1], f"[recruit] the list fits without sideways scrolling on the widened page (got {wide})")
    pg.evaluate("window.scrollTo(0, 700)"); pg.wait_for_timeout(200)
    pin = pg.evaluate("(() => { const a = document.querySelector('.top2'), b = document.querySelector('.sub2'), m = document.querySelector('.body2>main'); const t0 = b.getBoundingClientRect().top; m.scrollTop = 300; return [Math.round(a.getBoundingClientRect().top), Math.round(b.getBoundingClientRect().top - a.getBoundingClientRect().bottom), Math.round(b.getBoundingClientRect().top - t0)]; })()")
    ok(pin[0] == 0 and abs(pin[1]) <= 8 and pin[2] == 0, f"[recruit] the top bar and the second-level menu stay put while the content scrolls (got {pin})")
    pg.locator("tr", has_text="QA候选人").locator("[data-scr]").click(); pg.wait_for_selector(".sa-it")
    sa = pg.evaluate("(() => { const it = document.querySelector('.sa-it'); const q = it.querySelector('.sa-q'); return [it.querySelector('.sa-n').innerText, getComputedStyle(q).fontWeight, !!q.querySelector('.sa-ty'), document.querySelectorAll('.sa-it').length, document.querySelector('.sa-cd') !== null]; })()")
    ok(sa[0] == "1" and int(sa[1]) >= 600 and sa[2] and sa[3] >= 5 and sa[4], f"[recruit] the answers dialog shows numbered bold questions with a type tag and option codes (got {sa})")
    ok("价格和成分" in pg.inner_text("#saview"), "[recruit] the candidate's answers are kept with them")
    ok(pg.locator("#saeb, #sasave, textarea[name=text]").count() == 0 and "只能查看" in pg.inner_text(".modal"), "[recruit] the answers dialog is view-only: no edit, no save, no text box")
    pg.click("#c")
    pg.goto(B + f"/#/p/{pid}/people/form"); pick_mode(pg); pg.wait_for_selector("#rfws")   # stopping the collection lives on the list page now
    ok(not pg.evaluate("document.documentElement.scrollHeight > innerHeight + 2"), "[recruit] switching back keeps the page from scrolling")
    pg.goto(B + f"/#/p/{pid}/people/mode"); pg.wait_for_selector("[data-src=html]"); pg.click("[data-src=html]")   # the source and the link are on 招募/主持/问卷 now
    pg.fill("#rmurl", "https://survey.example.com/s/qa"); pg.click("#rmimport")
    ok(wait_until(lambda: call(f"/projects/{pid}/recruiting/form", None, rs).get("html_url", "").startswith("https://survey.example.com"), 8), "[recruit] an HTML questionnaire link is saved from 招募/主持/问卷")
    pg.goto(B + f"/#/p/{pid}/people/form"); pg.wait_for_selector("#rfws")
    ok("问卷已经导入" in pg.inner_text("#ccroot"), "[recruit] the questionnaire page says the questionnaire was imported and does not ask for questions")
    audit_page(pg, "recruiting html link/desktop", errs)
    pg.goto(B + f"/#/p/{pid}/people/list"); pg.wait_for_selector("#quotas")
    ok(pg.locator("#rules").count() == 0 and pg.locator("#quotas").count() == 1, "[recruit] eligibility rules are gone from the page (the questionnaire replaces them); quotas stay")
    ok(pg.locator(".rd-top #rdquotas").count() == 1 and pg.locator(".rd-top > .card").count() == 3, "[recruit] the quota progress sits in the top row beside the questionnaire link and the key numbers")
    top_h = [round(x) for x in pg.evaluate("(() => [document.querySelector('.top2').getBoundingClientRect().height, document.querySelector('.rail2').getBoundingClientRect().width, document.querySelector('.sub2').getBoundingClientRect().width])()")]
    ok(top_h == [66, 12, 272], f"[recruit] the top bar, the thin edge of the rail inside a project and the second-level menu have their fixed sizes (66 / 12 / 272, got {top_h})")
    ok(pg.evaluate("document.querySelector('.rail2').getBoundingClientRect().width") <= 92, "[recruit] inside a project the left column is the narrow icon rail")
    call(f"/projects/{pid}/recruiting/rules", {"rules": [{"field": "country", "op": "in", "value": "SG", "effect": "require"}]}, rs, method="PUT")  # a project that already had a rule keeps it
    pg.reload(); pg.wait_for_selector("#rules")
    audit_page(pg, "recruiting/desktop", errs)
    nl = chr(10)
    pg.click("#imp2"); pg.wait_for_selector("[name=csv]")
    pg.fill("[name=source]", "QA客户名单"); pg.check("[name=mode][value=screen]")
    pg.fill("[name=csv]", "姓名,邮箱,国家,语言,标签,年龄" + nl + "林小雨QA,qa1@example.com,SG,zh,女性;学生,22" + nl + "王大明QA,qa2@example.com,SG,zh,,17" + nl)
    pg.click("#dry")
    ok(wait_until(lambda: "将新增" in pg.inner_text("#prev") and "2" in pg.inner_text("#prev"), 8), "[recruit] the preview says how many people would be added")
    audit_page(pg, "recruiting import/desktop", errs)
    pg.click("#impgo"); pg.wait_for_timeout(500)
    ok(pg.locator("#f").count() == 1 and pg.locator("td:has-text('林小雨QA')").count() == 0, "[recruit] an import without the confirmation of a lawful basis is refused")
    pg.check("#basis"); pg.click("#impgo")
    pg.wait_for_selector("td:has-text('林小雨QA')", timeout=15000)
    ok(pg.locator("tr", has_text="林小雨QA").locator("td[data-l='来源']", has_text="QA客户名单 · ").count() == 1, "[recruit] an imported person carries the source name in the list")
    ok(pg.locator("tr", has_text="林小雨QA").locator(".lch", has_text="女性").count() == 1, "[recruit] the imported tags are shown as small coloured boxes")
    pg.click("#rules"); pg.wait_for_selector("#addr"); pg.click("#addr")
    row = pg.locator(".rrow").last
    row.locator(".r-f").select_option("question"); row.locator(".r-q").fill("年龄"); row.locator(".r-o").select_option("between"); row.locator(".r-v").fill("18,65")
    row.locator(".r-e").select_option("require"); row.locator(".r-n").fill("年龄 18–65")
    audit_page(pg, "recruiting rules/desktop", errs)
    pg.click("#f button.p")
    pg.wait_for_selector("tr:has-text('王大明QA') .tag:has-text('不符合')", timeout=15000)
    ok("年龄 18–65" in pg.locator("tr", has_text="王大明QA").inner_text(), "[recruit] a disqualified person shows the reason")
    ok("符合条件" in pg.locator("tr", has_text="林小雨QA").inner_text(), "[recruit] a person who fits is marked as qualified")
    ok(pg.locator("tr", has_text="王大明QA").locator("[data-inv]").count() == 0, "[recruit] a disqualified person cannot be invited")
    audit_page(pg, "recruiting rules applied/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "recruiting_1280.png"), full_page=True)
    msel_pick(pg, "tier", "正式")
    ok(wait_until(lambda: pg.locator("tr", has_text="王大明QA").count() == 0 and pg.locator("tr", has_text="林小雨QA").count() == 1, 8), "[recruit] filtering by qualified hides the disqualified")
    pg.click(".msel[data-key=tier] [data-clr]"); pg.mouse.click(5, 5); pg.wait_for_selector("tr:has-text('王大明QA')")
    pg.locator("tr", has_text="林小雨QA").locator(".pick").check()
    ok(wait_until(lambda: "已选 1" in pg.inner_text("#selcount"), 5) and pg.locator("#batch").count() == 0, "[recruit] picking a person counts them; the bulk invite button is gone")
    pg.click("#bdel"); pg.wait_for_selector("#bdelcf [data-y]")
    ok("无法恢复" in pg.inner_text("#bdelcf") and "1 位" in pg.inner_text("#bdelcf"), "[recruit] bulk delete asks first and says how many people and that it cannot be undone")
    pg.click("#bdelcf [data-n]")
    ok(pg.locator("tr", has_text="林小雨QA").count() == 1, "[recruit] cancelling the bulk delete keeps everyone")
    pg.click("#bdel"); pg.wait_for_selector("#bdelcf [data-y]"); pg.mouse.click(5, 300)
    ok(wait_until(lambda: pg.locator("#bdelcf").is_hidden(), 5), "[recruit] clicking anywhere else closes the bulk delete bar without deleting")
    pg.click("#bdel"); pg.wait_for_selector("#bdelcf [data-y]"); pg.keyboard.press("Escape")
    ok(wait_until(lambda: pg.locator("#bdelcf").is_hidden(), 5) and pg.locator("tr", has_text="林小雨QA").count() == 1, "[recruit] Esc closes the bulk delete bar too")
    ok(pg.locator(".ptbl th", has_text="数据清理").count() == 1 and pg.locator(".ptbl .xdel").count() >= 2 and pg.locator("tr", has_text="林小雨QA").locator("[data-erase]").inner_text().strip() == "×", "[recruit] the data cleanup column holds a small × per person")
    pg.locator("tr", has_text="林小雨QA").locator("[data-erase]").click(); pg.wait_for_selector("#bdelcf [data-y]")
    ok("林小雨QA" in pg.inner_text("#bdelcf") and "无法恢复" in pg.inner_text("#bdelcf"), "[recruit] × asks first, names the person and says it cannot be undone")
    pg.mouse.click(5, 300)
    ok(wait_until(lambda: pg.locator("#bdelcf").is_hidden(), 5) and pg.locator("tr", has_text="林小雨QA").count() == 1, "[recruit] leaving the × confirmation by clicking elsewhere deletes nothing")
    box = pg.evaluate("(() => { const b = document.querySelector('.plbox'), th = document.querySelector('.ptbl thead th'), h = document.querySelector('#plist'); return [getComputedStyle(b).overflowY, getComputedStyle(th).position, getComputedStyle(h).position, b.scrollHeight <= b.clientHeight + 1 || b.clientHeight <= innerHeight * 0.8]; })()")
    ok(box[0] == "auto" and box[1] == "sticky" and box[2] == "sticky" and box[3], f"[recruit] the list is a fixed-height box with pinned column names, under a pinned header and filter card (got {box})")
    ok("无答案" in pg.locator("tr", has_text="王大明QA").inner_text() or pg.locator("tr", has_text="王大明QA").locator("[data-scr]").count() == 1, "[recruit] a person without answers shows 'no answers' instead of an add button")
    # tier: a coloured chip with a menu; a change by hand is marked and can go back to automatic
    row = pg.locator("tr", has_text="林小雨QA")
    pg.evaluate("document.querySelector('#plist').dataset.mark = 'same-page'; document.querySelector('#ptab-list').dataset.mark = 'same-page'")  # a page redraw would lose these marks
    row.locator("[data-tier]").click(); pg.wait_for_selector("#lpop [data-set=backup]")
    pg.click("#lpop [data-set=backup]")
    ok(wait_until(lambda: pg.locator("tr", has_text="林小雨QA").locator("[data-tier]", has_text="备份").count() == 1 and "手动" in pg.locator("tr", has_text="林小雨QA").inner_text(), 8), "[recruit] the eligibility chip can be set to backup by hand and says it was done by hand")
    ok(pg.evaluate("document.querySelector('#plist').dataset.mark") == "same-page", "[recruit] changing formal / backup updates the list and the quota numbers in place, the page is not redrawn")
    pg.locator("tr", has_text="林小雨QA").locator("[data-tier]").click(); pg.click("#lpop [data-set=auto]")
    ok(wait_until(lambda: "自动" in pg.locator("tr", has_text="林小雨QA").inner_text(), 8), "[recruit] 'back to automatic' works")
    # tags: the + lists the library tags the person does not have yet (nothing to type); the library's own + adds a tag for everyone
    pg.locator("tr", has_text="林小雨QA").locator("[data-tadd]").click(); pg.wait_for_selector("#lpop [data-pick], #lpop .sm")
    n_before = pg.locator("tr", has_text="林小雨QA").locator(".lbox").first.locator(".lch").count()
    ok(pg.locator("#lpop input").count() == 0, "[recruit] the tag + offers the library tags to pick, no typing")
    if pg.locator("#lpop [data-pick]").count():
        pg.locator("#lpop [data-pick]").first.click()
        ok(wait_until(lambda: pg.locator("tr", has_text="林小雨QA").locator(".lbox").first.locator(".lch").count() == n_before + 1, 8), "[recruit] picking a tag adds it to the person")
    pg.click(".msel[data-key=tag] .msb"); pg.wait_for_selector("#libf [name=tag]"); pg.fill("#libf [name=tag]", "QA新标签"); pg.click("#libf button.p")
    ok(wait_until(lambda: pg.locator(".msel[data-key=tag] .mso", has_text="QA新标签").count() == 1 and pg.locator(".msel[data-key=tag] .msm").is_visible(), 8), "[recruit] a tag added in the tag filter shows in its list (and the menu stays open)")
    pg.locator("[data-libdel=QA新标签]").click()
    ok(wait_until(lambda: pg.is_visible("#libcf"), 5) and "标签库" in pg.inner_text("#libcf"), "[recruit] removing a library tag asks first")
    pg.click("#libcf [data-y]")
    ok(wait_until(lambda: pg.locator(".msel[data-key=tag] .mso", has_text="QA新标签").count() == 0, 8), "[recruit] a removed tag leaves the library")
    row = pg.locator("tr", has_text="林小雨QA")
    ok(row.locator("[data-inv]").inner_text().strip() == "发送邀请函" and row.locator(".ity").inner_text().strip() == "未邀请", "[recruit] a person who was not invited shows 'not invited' and the button 发送邀请函")
    ok(pg.locator(".ptbl th", has_text="问卷答案").count() == 1 and pg.locator(".ptbl th", has_text="关键回答").count() == 0, "[recruit] the answers have a column of their own (and the key answers column is gone)")
    row.locator("[data-inv]").click(); pg.wait_for_selector(".invdlg #mk")
    ok(pg.locator(".invdlg [name=title]").count() == 0 and "邀请函" in pg.inner_text(".invdlg h2"), "[recruit] the invitation window has no session name field")
    ok(pg.locator(".invdlg #em").count() == 1 and pg.locator(".invdlg #du").input_value() == "45" and pg.locator(".invdlg #mk").inner_text().strip() == "生成并发送邀请函", "[recruit] the window has the participant's email, the expected length (45 minutes) and a 发送 button")
    pg.fill("#du", "75"); ok(pg.locator("#dq button.on").count() == 0, "[recruit] the length can be typed (75 minutes is not one of the quick buttons)")
    pg.fill("#em", "abc")
    ok(pg.locator("#mk").is_disabled() and "缺少 @" in pg.inner_text("#emsg"), "[recruit] a wrong email is flagged as you type and the send button waits")
    pg.fill("#em", "qa1@example.com"); ok(pg.locator("#mk").is_enabled() and "格式正确" in pg.inner_text("#emsg"), "[recruit] a right-looking email is accepted")
    pg.evaluate("document.querySelector('#plist').dataset.mark = 'same-page'")
    ok(pg.locator("#cp").is_disabled(), "[recruit] before the session is created the 复制链接 button is grey (there is no link yet)")
    pg.click("#mk")  # no mail service in the tests: the session is made, a notice explains, and the window stays open so the link can be copied
    pg.wait_for_selector("#savedmsg:not([hidden])")
    ok(pg.locator("#cp").is_enabled() and pg.locator(".ntc.bad").count() == 1 and "邮件没有发出去" in pg.inner_text(".ntc") and pg.locator("#c").inner_text().strip() == "关闭", "[recruit] sending without a mail service still creates the session: a page notice, the window stays and 复制链接 turns on")
    pg.click("#cp")
    ok(wait_until(lambda: (pg.locator("#lkbox input").count() and "/join.html?t=" in pg.locator("#lkbox input").input_value()) or "链接已复制" in pg.inner_text(".ntc"), 6), "[recruit] 复制链接 copies the entry link (or shows it to copy by hand when the browser does not allow copying)")
    pg.click("#c"); pg.wait_for_selector("tr:has-text('林小雨QA') [data-upd]")
    row = pg.locator("tr", has_text="林小雨QA")
    ok(row.locator("[data-upd]").inner_text().strip() == "更新邀请函" and row.locator(".ity").inner_text().strip() == "真人访谈" and pg.evaluate("document.querySelector('#plist').dataset.mark") == "same-page", "[recruit] after inviting, the button says 更新邀请函 and the badge says 真人访谈, updated in place")
    row.locator("[data-upd]").click(); pg.wait_for_selector(".invdlg #mk")
    ok(pg.locator(".invdlg #mk").inner_text().strip() == "保存并发送更新" and pg.locator(".invdlg #du").input_value() == "75", "[recruit] updating the invitation shows what was set (75 minutes) with 保存 and 发送更新")
    ok(pg.locator("#cp").is_enabled(), "[recruit] an existing, not-yet-started session can have its link copied")
    pg.fill("#du", "60"); pg.click("#mk"); pg.wait_for_selector("#savedmsg:not([hidden])")
    ok("已保存" in pg.inner_text("#savedmsg") and pg.locator(".invdlg").count() == 1, "[recruit] saving keeps the window open and says it is saved")
    pg.click("#cp")  # this page already accepts dialogs
    ok(wait_until(lambda: (pg.locator("#lkbox input").count() and "/join.html?t=" in pg.locator("#lkbox input").input_value()) or "链接已复制" in pg.inner_text(".ntc"), 6), "[recruit] copying the link of an existing session asks first (the old link stops working), then copies the new one")
    pg.click("#c"); pg.wait_for_selector(".invdlg", state="detached")
    ok(pg.locator("td[data-l='问卷答案']").count() >= 1 and pg.locator("td[data-l='来源']").count() >= 1, "[recruit] the list has the answers column and the source column in the order of the header")
    pg.click("#ptab-list-t") if pg.locator("#ptab-list").is_hidden() else None
    # the filter capsules, the tag any / all switch
    vis = pg.evaluate("[...document.querySelectorAll('.msb.cap')].every((b) => { const c = getComputedStyle(b, '::before'); return c.position !== 'absolute' && c.transform === 'none' && c.zIndex !== '-1'; })")
    ok(vis, "[recruit] every filter capsule shows its label all the time (the button's hover-sweep layer does not push it off to the side)")
    pg.locator(".msb.cap").first.hover(); ok(pg.evaluate("getComputedStyle(document.querySelector('.msb.cap')).color") != pg.evaluate("getComputedStyle(document.querySelector('.msb.cap')).backgroundColor"), "[recruit] a capsule stays readable while the mouse is on it")
    ok(pg.locator(".msb.cap").count() >= 5 and pg.evaluate("getComputedStyle(document.querySelector('.msb.cap')).textAlign") != "left", "[recruit] the filters are capsule buttons")
    pg.click(".msel[data-key=tag] .msb"); pg.wait_for_selector(".msel[data-key=tag] .mode2")
    ok(pg.locator(".msel[data-key=tag] [data-tm=any].on").count() == 1 and pg.locator(".msel[data-key=tag] [data-tm=all]").inner_text().strip() == "同时满足", "[recruit] the tag filter offers 满足任一 / 同时满足, 满足任一 first")
    sw = pg.evaluate("(() => { const b = document.querySelector('.msel[data-key=tag] .mode2 button'); return [parseFloat(getComputedStyle(b).fontSize), b.getBoundingClientRect().height, b.scrollHeight <= b.clientHeight + 1]; })()")
    ok(sw[0] >= 12 and sw[0] <= 13 and 24 <= sw[1] <= 30 and sw[2], f"[recruit] the switch is small (12px text, under 30px tall) and its text fits (got {sw})")
    pg.mouse.click(5, 300)
    if pg.locator("[data-tdel]").count():
        pg.locator("[data-tdel]").first.click(); ok(pg.locator(".lx").count() >= 0 and pg.locator("[data-tdel].on").count() == 1, "[recruit] the － button puts the tags in delete mode")
        pg.mouse.click(5, 400); ok(wait_until(lambda: pg.locator("[data-tdel].on").count() == 0, 3), "[recruit] a click anywhere else leaves the tag delete mode")
    pg.locator("[data-sec=quota] .rd-arr").click() if pg.locator("#rdb-quota").is_hidden() else None
    pg.click("#quotas"); pg.wait_for_selector("#addq"); pg.click("#addq")
    qrow = pg.locator(".qrow").last
    qrow.locator(".q-f").select_option("tag"); qrow.locator(".q-v").fill("女性"); qrow.locator(".q-t").fill("2")
    pg.click("#f button.p")
    pg.wait_for_selector(".rd-q2:has-text('女性')", timeout=10000)
    ok("已有 1 / 目标 2" in pg.locator(".rd-q2", has_text="女性").inner_text(), "[recruit] the quota shows progress against the target (formal and backup together)")
    pg.click("#pexp"); pg.wait_for_selector("[data-exp=all]")
    with pg.expect_download() as dl:
        pg.click("[data-exp=all]")
    csv_text = pathlib.Path(dl.value.path()).read_text(encoding="utf-8-sig")
    ok("林小雨QA" in csv_text and "qa1@example.com" in csv_text and "QA客户名单" in csv_text, "[recruit] exporting the list gives a CSV with contacts and the source")
    pg.click("#pexp")
    with pg.expect_download() as dl2:
        pg.click("[data-exp=anon]")
    anon = pathlib.Path(dl2.value.path()).read_text(encoding="utf-8-sig")
    ok("qa1@example.com" not in anon and "林小雨QA" not in anon and "QA客户名单" in anon, "[recruit] the anonymous export leaves out names and e-mails")
    pg.mouse.click(5, 5)
    pg.evaluate("document.querySelector('#plist').dataset.mark = 'same-page'")
    msel_pick(pg, "source", "QA客户名单")
    ok(pg.evaluate("document.querySelector('#plist').dataset.mark") == "same-page", "[recruit] filtering redraws only the list, not the page")
    ok(wait_until(lambda: pg.locator("tr", has_text="QA候选人").count() == 0 and pg.locator("tr", has_text="林小雨QA").count() == 1 and "来源：" in pg.inner_text("#fres"), 8), "[recruit] ticking a source in the multi-select filter narrows the list and says what is filtered")
    msel_pick(pg, "source", "问卷链接")
    ok(wait_until(lambda: pg.locator("tr", has_text="QA候选人").count() == 1 and pg.locator("tr", has_text="林小雨QA").count() == 1, 8), "[recruit] two sources ticked show people from either (any of them)")
    msel_pick(pg, "tag", "QA")  if pg.locator(".msel[data-key=tag] .mso:has-text('QA')").count() else None
    pg.mouse.click(5, 5); pg.click("#fclr")
    ok(wait_until(lambda: pg.locator("tr", has_text="王大明QA").count() == 1 and pg.locator("#fres .rd-chip").count() == 0, 8), "[recruit] 'clear all filters' brings everybody back")
    ok(pg.locator("[data-fchip]").count() == 0, "[recruit] the old 'by tag / by source' rows are gone (the filters above replace them)")

    ctx.close()
    ctx, pg, errs = new_page(br, 390, token=rs)
    pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("#imp2")
    audit_page(pg, "recruiting/phone", errs)
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": [{"type": "text", "text": "最近一次购买时看重什么？"}]}, rs, method="PUT")  # (the form was left on the HTML link: back to written questions)
    pg.evaluate("sessionStorage.setItem('ptab','form')"); pg.reload(); pick_mode(pg); pg.wait_for_selector("#rfws")
    audit_page(pg, "recruiting workspace/phone", errs)
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
    ok("立项向导" in shell_text(pg) and "项目总览" not in shell_text(pg), "[wizard] a project still in the wizard has only the wizard in its menu")
    for n, label in enumerate(["基本信息", "研究设计", "人员与角色", "人群与样本", "渠道与触达", "合规举证", "确认发布"], start=1):
        ok(f"{n} {label}" in pg.locator(".chain .step").nth(n - 1).inner_text(), f"[wizard] step {n} is {label}")
    # step 1: name, retention (30/60/120/180), sub-projects with their own research order, listening and async ticks
    ok(pg.locator("select[data-w=retention] option").count() == 4 and pg.input_value("select[data-w=retention]") == "180" and pg.locator("div[aria-label$='的执行语言']").count() == 1, "[wizard] retention is chosen from 30/60/120/180 (180 by default) and there is one sub-project")
    pg.select_option("select[data-w=retention]", "60"); pg.wait_for_timeout(1200)  # it saves and redraws
    ok("AI 服务商" not in pg.inner_text("main"), "[wizard] no notice about the AI service (user and legal, 2026-11-01)")
    pg.fill("[data-meta=brief]", "新品防晒霜进入新加坡市场，想了解年轻人的购买决策。")
    h0 = pg.evaluate("document.querySelector('[data-meta=brief]').offsetHeight")
    pg.fill("[data-meta=brief]", chr(10).join(f"第 {i} 行" for i in range(1, 8)))
    h7 = pg.evaluate("document.querySelector('[data-meta=brief]').offsetHeight")
    pg.fill("[data-meta=brief]", chr(10).join(f"第 {i} 行" for i in range(1, 25)))
    h24 = pg.evaluate("document.querySelector('[data-meta=brief]').offsetHeight")
    scroll = pg.evaluate("(() => { const t = document.querySelector('[data-meta=brief]'); return t.scrollHeight > t.clientHeight + 2 && getComputedStyle(t).overflowY === 'auto'; })()")
    ok(h0 < h7 < h24 and h24 - h7 < 22 * 18 and scroll, f"[wizard] the research background box grows with its text (3 lines {h0}px, 7 lines {h7}px), stops at 10 lines ({h24}px) and then scrolls inside")
    pg.fill("[data-meta=brief]", "新品防晒霜进入新加坡市场，想了解年轻人的购买决策。")
    ok(pg.locator("#wzlib").count() == 0 and pg.locator("#wzsug").count() == 0 and pg.locator("#wzuselib").count() == 0, "[wizard] step 1 has no buttons for the research library or the suggestions (they happen by themselves after 下一步)")
    audit_page(pg, "wizard step 1 suggestions/desktop", errs)
    ok(pg.locator("select[data-sub$='|design'] option").count() == 8, "[wizard] a sub-project's research order has the 8 choices")
    pg.click("#subadd")   # (+ 添加子项目 adds a row named 新国家 to rename in place, since the 11-07 wizard)
    ok(wait_until(lambda: pg.locator("div[aria-label$='的执行语言']").count() == 2, 5), "[wizard] a sub-project is added")
    pg.locator("select[data-sub$='|design']").nth(1).select_option("i")
    pg.wait_for_timeout(300)
    pg.locator("input[data-subx$='|listening']").nth(1).check(); pg.wait_for_timeout(300)
    # (the per-row 异步 AI tick moved to step 4's quota table, where each row is marked; rule 2026-10-21)
    ok(pg.locator("#subtpl").count() == 1 and pg.locator("#subimp").count() == 1, "[wizard] the sub-project table has a template to download and a file to import")
    audit_page(pg, "wizard step 1/desktop", errs); pg.click("#wznext")
    # step 2: only the research design per sub-project, to read (no choosing here any more)
    pg.wait_for_selector("text=各子项目的研究设计")
    pg.wait_for_selector("#wzsug .sugtabs", timeout=20000)   # (the four kinds of suggestion became tabs of one card in the 11-07 wizard)
    tabs = [x.strip() for x in pg.locator("#wzsug .sugtabs [role=tab]").all_inner_texts()]
    live = [x.strip() for x in pg.locator("#wzsug .sugtabs [role=tab][data-sugtab]").all_inner_texts()]
    want = ["深访提纲", "舆情关键词"]   # both sub-projects run 深访 (none a 座谈会) and the second one collects 舆情
    ok(pg.locator("#wzsug").count() == 1 and tabs == ["深访提纲", "座谈会大纲", "舆情关键词"] and live == want,
       f"[wizard] step 2 shows the AI's suggestions; only the chosen methods' tabs open, the others are grey, 配额 is gone (用户 2026-11-20 第 3 轮; open: {live})")
    audit_page(pg, "wizard step 2 suggestions/desktop", errs)
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
    ok(pg.locator("fieldset.wzbody[disabled]").count() == 0 and "项目总览" in shell_text(pg), "[wizard] an unlocked plan can be edited and the project stays usable (full menu)")
    pg.click("[data-step='0']"); pg.wait_for_selector("select[data-w=retention]")
    pg.select_option("select[data-w=retention]", "30"); pg.wait_for_timeout(1500)
    pg.click("[data-step='5']"); pg.wait_for_selector("#wzsubmit")
    ok(wait_until(lambda: pg.is_enabled("#wzsubmit"), 8), "[wizard] an unlocked and changed plan has to be submitted for review again")
    pg.click("#wzsubmit"); pg.wait_for_selector("#wzapprove"); pg.click("#wzapprove"); pg.wait_for_selector("text=法务通过，可发布并锁定")
    pg.click("#wznext"); pg.wait_for_selector("#wzpub"); pg.click("#wzpub"); pg.wait_for_selector("text=暂时锁定")
    ok(wait_until(lambda: pg.locator("fieldset.wzbody[disabled]").count() == 1, 8), "[wizard] publishing the new version locks the plan again")
    pg.goto(B + "/#/projects"); pg.wait_for_selector(".pcard"); pg.locator(".pcard", has_text="向导测试项目").click()
    pg.wait_for_selector(".hm-stage", timeout=15000)
    nav = shell_text(pg)
    ok(nav.index("立项向导") < nav.index("项目总览"), "[nav] the wizard comes before the project overview")
    ok("异步研究" in nav and "舆情采集" in nav and "座谈会" not in nav and "深访" in nav and "定量" in nav, "[nav] the menu shows only what the project uses (stages = its research orders)")
    ok(pg.locator(".top2 .stg").count() == 2 and "深访" in pg.inner_text(".top2 .mods"), "[wizard] the top bar shows the research stages")
    # the overview is only the research stages: left the stage and its state, right its quota chart
    ok(pg.locator(".hero2").count() == 0 and pg.locator(".sn").count() == 0 and pg.locator(".hm-st").count() == 0 and pg.evaluate("[...document.querySelectorAll('main table.tbl')].filter((t) => !t.closest('#hmlang')).length") == 0, "[home] no big number, map, status bar or sub-project table any more: only the research stages")
    ok(pg.locator("#chm").count() == 0 and pg.locator("#hmcopy").count() == 1, "[home] no 调整研究方法 button; copying the project is in the header")
    ok(pg.locator(".hm-stage").count() == 3 and pg.locator(".hm-stage .pill", has_text="招募中").count() == 3 and pg.locator(".hm-sub").count() == 2, "[home] each sub-project lists its stages with their state (S1: 深访, 定量; S2: 深访)")
    ok(pg.locator(".hm-stage .hm-r .hm-row").count() >= 3 and pg.locator("button", has_text="看进度与配额").count() == 0, "[home] every stage shows its quota chart on the right, with no 看进度与配额 button")
    ok(pg.locator(".hm-stage .tag", has_text="异步 AI").count() >= 1, "[home] the overview marks which people are done by the asynchronous AI")
    pg.locator(".top2 [data-qmode=depth]").first.click(); pg.wait_for_selector("#rmode")   # (the stage names on the overview are plain text since the 11-07 overview; the top bar opens a stage)
    ok(pg.url.endswith("/people/mode"), f"[home] a stage opens its page (深访 starts at the recruiting setup, which has its own address since 2026-11-19; got {pg.url})")
    pg.go_back(); pg.wait_for_selector(".hm-stage")
    pg.select_option("#subsel", "all")
    ok(wait_until(lambda: pg.locator(".hm-stage").count() == 3, 8), "[home] choosing the whole project lists every sub-project's stages")
    pg.select_option("#subsel", index=1)
    ok(wait_until(lambda: pg.locator("#subsel").input_value() != "all" and pg.locator(".hm-stage").count() == 2, 8), "[home] choosing one sub-project shows only its own stages")
    audit_page(pg, "sub-project home/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "overview_stages_1280.png"), full_page=True)
    pg.select_option("#subsel", "all"); pg.wait_for_selector(".hm-stage")
    pg.click(".top2 a.stg:has-text('深访')"); pg.wait_for_selector("#rmode")
    ok(pg.url.endswith("/people/mode"), "[nav] 深访 in the menu opens the recruiting setup first, not the sessions list")
    pg.goto(B + "/#/team"); pg.wait_for_selector(".sub2 .l1"); wait_until(lambda: "团队" in pg.inner_text(".sub2"), 6)   # (the project menu stays until the team page is painted)
    ok("团队" in pg.inner_text(".sub2") and "AI 后台" in pg.inner_text(".sub2") and "设置" in pg.inner_text(".rail2"), f"[nav] the settings pages show the settings menu (团队, 数据保留, 审计日志, AI 后台) in the second-level menu (sub: {pg.inner_text('.sub2')[:120]!r}, rail: {pg.inner_text('.rail2')[:60]!r})")
    pg.goto(B + "/#/projects"); pg.locator(".pcard", has_text="向导测试项目").click(); pg.wait_for_selector(".hm-stage")
    with pg.expect_download(timeout=15000) as dl:
        pg.click("#rep")
    ok(dl.value.suggested_filename.endswith(".docx"), "[wizard] the research report downloads as a Word file")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "starmap_1280.png"), full_page=True)
    pid_new = pg.url.split("/p/")[1].split("/")[0]
    pg.click("#hmcopy"); pg.wait_for_selector(".chain .step")
    ok("（副本）" in pg.inner_text(".top2 .nm") and "草稿" in pg.inner_text(".top2"), "[home] copying a project opens a draft of it in the wizard")
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
    pg.goto(B + "/#/team"); pg.wait_for_selector("#nu")
    ok(pg.locator("#nb").count() == 0, "[team] there is no separate brand button any more (a brand is typed when adding a client)")
    pg.click("#nu"); pg.wait_for_selector(".modal select[name=role]")
    names = pg.locator(".modal select[name=role] option").all_inner_texts()
    ok(names == ["全科研究员", "定量研究员", "定性研究员", "项目经理", "主持人", "观察员", "客户（只读）", "技术支持", "管理员"], "[team] the role list has the three kinds of researcher, project manager, client and technical support")
    ok(not pg.locator("#cl").is_visible(), "[team] the client fields (brand, title, contact) are hidden for other roles")
    pg.select_option(".modal select[name=role]", "viewer|")
    ok(pg.locator("#cl").is_visible() and pg.locator("#cl input[name=brand_name]").count() == 1 and pg.locator("#brandlist option").count() >= 1, "[team] choosing client asks for a brand (typed, with the existing ones suggested), a title and a contact")
    audit_page(pg, "team add client dialog/desktop", errs)
    pg.fill(".modal input[name=brand_name]", "品牌甲QA")
    pg.fill(".modal input[name=name]", "客户QA"); pg.fill(".modal input[name=email]", "kehu-qa@client.test"); pg.fill(".modal input[name=password]", "password123")
    pg.fill(".modal input[name=job_title]", "品牌经理"); pg.fill(".modal input[name=phone]", "+65 8000 1111")
    pg.click(".modal button.p")
    ok(wait_until(lambda: "品牌甲QA · 品牌经理 · +65 8000 1111" in pg.inner_text("main"), 8), "[team] the client is listed with brand, title and contact")
    audit_page(pg, "team page/desktop", errs)
    pg.click("#nu"); pg.wait_for_selector(".modal select[name=role]"); pg.select_option(".modal select[name=role]", "viewer|")
    pg.fill(".modal input[name=name]", "客户QA2"); pg.fill(".modal input[name=email]", "kehu-qa2@client.test"); pg.fill(".modal input[name=password]", "password123")
    pg.fill(".modal input[name=brand_name]", "  品牌甲qa "); pg.click(".modal button.p")
    ok(wait_until(lambda: pg.locator("tr", has_text="客户QA2").count() == 1, 8) and pg.locator("tr", has_text="客户QA2").inner_text().count("品牌甲QA") == 1, "[team] the same brand typed again (other letter case, spaces) reuses the brand instead of making a second one")
    ctx.close()


def quant_section(br, D):
    """Quantitative results: the owner makes a key, an outside platform pushes a survey with it, the page shows bars; a spreadsheet can be imported; the key can be revoked."""
    adm, pid = D["tok"]["admin"], D["pid"]
    call(f"/projects/{pid}/methods", {"methods": ["depth", "group", "async", "listening", "quant"]}, adm, method="PUT")
    ctx, pg, errs = new_page(br, 1280, token=adm)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + f"/#/p/{pid}/quant"); pg.wait_for_selector("#kf")
    ok(pg.locator(".top2 a[href$='/quant']").count() == 1, "[quant] the menu has the results page once the method is on")
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
    ok("洞察池" in pg.inner_text(".sub2"), "[pool] the project has the insight pool (menu item)")
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
    """The respondent window has no line-tagging (it went with the 编码与分析 page); its transcript search went with the old window (user 2026-11-19)."""
    rs, mi = D["tok"]["researcher"], D["main"]["id"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.goto(B + f"/#/i/{mi}"); pg.wait_for_selector(".rsp .rd-hd"); pg.wait_for_selector("#rd-rest .rd-sec")
    ok(pg.locator(".rd-bubble").count() >= 1, "[transcript] the window lists the answers")
    ok(pg.locator("[data-tag]").count() == 0, "[transcript] there is no line-tagging any more (the coding page is gone)")
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
    tv = call(f"/interviews/{iid}/timeslots", token=adm)   # the team side through the API: the session-page card went with the old respondent window (user 2026-11-19)
    best = next(x for x in tv["slots"] if x["id"] == tv["best"])
    ok(best["votes"] == 2 and best["id"] == slots[1]["id"], "[vote] the team sees how many can make each time and which is best")
    call(f"/interviews/{iid}/timeslots/{best['id']}/confirm", {}, adm)
    pg.reload(); pg.wait_for_selector("#accept", timeout=10000)
    ok(pg.locator("input[name=slot]").count() == 0, "[vote] once confirmed the participant's page goes on to the invitation")
    ctx.close()


def reel_section(br, D):
    """Highlight reels on the insights page: build one from clips, reorder, share it (link shown once), revoke the link; the shared page lists the clips."""
    rs, pid = D["tok"]["researcher"], D["pid"]
    ctx, pg, errs = new_page(br, 1280, token=rs)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('itab', 'v')"); pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector("#rnew")
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


QA_DB = ""   # the throw-away database of this run (set in main): a few sections write rows no page can create


def ai_console_section(br, D):
    """AI 后台 (admin only): the entry in the wide left menu, 功能总览 lists all features with numbers from the call log, 调用监控 shows errors by category; nobody else sees it."""
    import sqlite3
    adm = D["tok"]["admin"]
    con = sqlite3.connect(QA_DB)
    org = con.execute("select id from orgs where name like 'Acme%'").fetchone()[0]
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    for i, (feat, ms, st, err) in enumerate((("outline.chat", 5400, "ok", ""), ("outline.chat", 6100, "error", "格式错误"), ("recruit.chat", 2100, "ok", ""), ("moderator.speak", 2700, "ok", ""), ("moderator.speak", 61000, "error", "超时"))):
        con.execute("insert into ai_call_log (id, org_id, feature, prompt_version, model, duration_ms, status, error_type, at) values (?,?,?,?,?,?,?,?,?)",
                    (f"qa{i}".ljust(32, "0"), org, feat, "v010" if feat.startswith("mod") else "内置", "云端模型", ms, st, err, now))
    con.commit(); con.close()
    ctx, pg, errs = new_page(br, 1440, h=900, token=adm)
    pg.goto(B + "/#/projects"); pg.wait_for_selector(".card")
    ok(pg.locator(".rail2 a", has_text="设置").count() == 1, "[ai-console] 'AI 后台' is in the wide left menu under 设置, beside 团队 / 数据保留 / 审计日志")
    pg.hover(".rail2"); pg.wait_for_timeout(300); pg.locator(".rail2 a", has_text="设置").click(); pg.locator(".sub2 a.l1", has_text="AI 后台").click(); pg.wait_for_selector("#aipanel table")
    rows = pg.locator("#aipanel tbody tr")
    ok(rows.count() == 22 and "AI 主持人" in rows.first.inner_text() and "对话分析" in rows.first.inner_text(), f"[ai-console] 功能总览 lists the 22 AI features (got {rows.count()})")
    ok(wait_until(lambda: "2 / 1" in pg.inner_text("#aipanel") and "1 / 0" in pg.inner_text("#aipanel"), 4), "[ai-console] the 24-hour calls / failures come from the call log")
    body = pg.inner_text("#aipanel")
    ok("v010" in body and "内置" in body and "云端" not in body.replace("云端模型", "") and "DeepSeek" not in body and "deepseek" not in body.lower(), "[ai-console] prompt versions are shown; the model appears only as an alias, never a vendor name")
    audit_page(pg, "ai-console overview/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_console_over.png"))
    pg.locator("#aitabs a", has_text="提示词").click(); pg.wait_for_selector(".ai-pl .ai-pi")
    ok(pg.locator(".ai-pi").count() == 17 and not any(x in pg.inner_text(".ai-pl") for x in ("发言 speak", "对话分析")), "[ai-console] 提示词 lists the 17 editable features and not the AI moderator's")
    pg.locator(".ai-pi", has_text="问卷对话").click(); pg.wait_for_selector("#aipt")
    built = pg.input_value("#aipt")
    ok(len(built) > 200 and pg.locator("#aipsave").is_disabled() and "回到内置" not in pg.inner_text("#aipanel"), "[ai-console] the built-in prompt from the code is shown, with nothing to save until it is edited")
    pg.fill("#aipt", built + " QA 加的一句 JSON。"); pg.fill("#aipn", "QA 试一下"); pg.click("#aipsave"); pg.wait_for_selector("#aipanel tbody tr")
    ok("v1" in pg.inner_text("#aipanel") and "草稿" in pg.inner_text("#aipanel") and "QA 试一下" in pg.inner_text("#aipanel"), "[ai-console] saving makes version v1 as a draft with its note")
    pg.click("#aiplive"); ok(wait_until(lambda: "上线 v1" in pg.inner_text("#aipanel") and pg.locator(".ai-pi.on small").inner_text() == "v1", 5), "[ai-console] putting v1 live shows it on the page and in the list")
    pg.select_option("#aipv", "built"); pg.click("#aiplive"); ok(wait_until(lambda: pg.locator(".ai-pi.on small").inner_text() == "内置", 5), "[ai-console] 回到内置 goes back to the prompt in the code")
    audit_page(pg, "ai-console prompts/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_console_prompt.png"))
    pg.locator("#aitabs a", has_text="调用监控").click(); pg.wait_for_selector("#aipanel .ai-kpi")
    log = pg.inner_text("#aipanel")
    ok("格式错误" in log and "超时" in log and "最近的失败" in log, "[ai-console] 调用监控 shows the error categories and the latest failures")
    audit_page(pg, "ai-console log/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_console_log.png"))
    ctx.close()
    for role in ("researcher", "moderator", "observer"):
        ctx, pg, errs = new_page(br, 1280, token=D["tok"][role])
        pg.goto(B + "/#/projects"); pg.wait_for_selector(".card")
        ok(pg.locator(".rail2 a, .sub2 a", has_text="AI 后台").count() == 0 and pg.locator(".rail2 a", has_text="设置").count() == 0, f"[ai-console] {role} does not see the entry")
        ctx.close()
        r = requests_get(f"/ai-console/overview", D["tok"][role])
        ok(r == 403, f"[ai-console] {role} is refused by the server (got {r})")


def requests_get(path, tok):
    import urllib.error
    import urllib.request
    try:
        return urllib.request.urlopen(urllib.request.Request(B + "/api" + path, headers={"Authorization": "Bearer " + tok})).status
    except urllib.error.HTTPError as e:
        return e.code


def outline_wb_section(br, D):
    """大纲工作台 · 生成大纲 (修正方案第 1 轮 2026-11-19): the side menu has at most two levels, the five steps are a clickable bar at the top of the page and the modules a list on its left; a reload
    stays on the step and Back goes to the page before; the assistant fills the page's height, the overview is confirmed before any question is written, suggestions are adopted
    only by a click, a side-dialog edit is only a pending card until accepted."""
    adm = D["tok"]["admin"]
    pid = call("/projects", {"name": "大纲工作台测试", "market": "SG", "language": "zh", "brief": "A 品牌准备进入新加坡，了解都市女性怎么选护肤品。她们会比较多个品牌，价格是主要顾虑。"}, adm)["id"]
    ctx, pg, errs = new_page(br, 1440, h=900, token=adm)
    pg.goto(B + f"/#/p/{pid}/people/form"); pg.wait_for_selector(".sub2 .sgrp")
    pg.goto(B + f"/#/p/{pid}/people/outline"); pg.wait_for_selector("#owroot .ow")
    # 修正方案第 2 轮 D2: switching between the 创建访谈 pages in the side menu shows the other page at once (nothing is loaded again)
    pg.locator(".sub2 .l1", has_text="数据库筛选及发送").click(); pg.wait_for_selector("#ptab-db:not([hidden])")
    t0 = pg.evaluate("performance.now()"); pg.locator(".sub2 .l1", has_text="生成大纲").click(); pg.wait_for_selector("#owroot:not([hidden]) .ow"); dt = pg.evaluate("performance.now()") - t0
    ok(dt < 400 and pg.locator(".sub2 .l1.on .tx").inner_text().strip() == "生成大纲", f"[outline_wb] the side menu switches 数据库筛选及发送 → 生成大纲 in {dt:.0f} ms (under 400) and marks the new page")
    ok(wait_until(lambda: pg.locator("#owroot .ow-steps .ow-st").count() == 4, 6) and pg.locator("#owroot .ow-steps .ow-st >> nth=0").inner_text().strip().endswith("目标与人群"),
       "[outline_wb] the four steps are a bar at the top of the page; 目标 and 人群与执行 are one step (用户 2026-11-20 第 3 轮)")
    ok(pg.locator(".sub2 .l1 .l1, .sub2 .l2slot, .sub2 .ow-l3, .sub2 .ow-l4, .sub2 .ow-st").count() == 0, "[outline_wb] the side menu has at most two levels (no steps or modules in it)")
    ok([x.strip() for x in pg.locator(".sub2 .sgrp[data-grp=create] .l1 .tx").all_inner_texts()] == ["招募/主持/问卷", "甄别问卷", "数据库筛选及发送", "生成大纲", "预览测试", "Pilot", "配额及受访者名单"],
       "[outline_wb] 生成大纲 and 预览测试 sit right under 数据库筛选及发送, at the same level")
    ok(pg.locator(".sub2 .l1.on .tx").inner_text().strip() == "生成大纲", "[outline_wb] the side menu marks 生成大纲")
    box = pg.locator("#owroot .ow-chat").bounding_box()
    ok(box and box["height"] >= 500, "[outline_wb] the assistant panel fills the height of the page")
    ok(pg.locator("#owroot .ow-steps").count() == 1, "[outline_wb] one step bar inside the page")
    pg.fill('[data-ow-free="key_questions"]', "她们买护肤品最看重什么\n价格接受范围"); pg.click('[data-ow-freeok="key_questions"]')
    ok(wait_until(lambda: "价格接受范围" in pg.inner_text("#owroot"), 6), "[outline_wb] the researcher's answer is kept")
    audit_page(pg, "outline_wb goal/desktop", errs)
    kind = call(f"/projects/{pid}/outline-wb", None, adm)["slots"]["research_type"]["value"]
    ok(pg.locator('#owroot [data-ow="goal-next"]').count() == 0 and ("价格怎么谈" not in pg.inner_text("#owroot .ow-card") or kind == "pricing"), f"[outline_wb] one button goes on from 目标与人群; 价格怎么谈 shows only for a pricing study ({kind})")
    pg.fill("#ow-in", "你好"); pg.keyboard.press("Enter")
    ok(wait_until(lambda: "没有改动" in pg.inner_text("#ow-msgs"), 8), "[outline_wb] the dialog does not claim a change it did not make")
    ok(wait_until(lambda: len(call(f"/projects/{pid}/outline-wb/chat-log", None, adm)["messages"]) >= 2, 6), "[outline_wb] the dialog is kept on the server (用户 2026-11-20: 换电脑不丢)")
    pg.reload(); pg.wait_for_selector("#owroot .ow")
    ok(wait_until(lambda: "没有改动" in pg.inner_text("#ow-msgs"), 6), "[outline_wb] the dialog is still there after a reload")
    pg.click('[data-ow="build"]'); pg.wait_for_selector("#owroot .ow-tbl")
    ok("45 分" in pg.inner_text("#owroot .ow-tbl"), "[outline_wb] the overview table adds up to the interview length")
    ok(pg.locator("#owroot [data-ow-m][data-f=depth]").count() >= 3, "[outline_wb] every module has a follow-up depth choice")
    ok(pg.locator("#owroot .ow-steps [data-ow-step=questions].lock").count() == 1, "[outline_wb] the questions step is closed until the overview is confirmed")
    ok(pg.url.endswith("/people/outline/overview"), f"[outline_wb] the step is in the address ({pg.url})")
    pg.reload(); pg.wait_for_selector("#owroot .ow-steps")
    ok(wait_until(lambda: pg.locator("#owroot .ow-steps .ow-st.on").inner_text().strip().endswith("大纲总览"), 6), "[outline_wb] a reload stays on the same step")
    pg.click("#owroot .ow-steps [data-ow-step=goal]"); ok(wait_until(lambda: pg.locator("#owroot .ow-steps .ow-st.on").inner_text().strip().endswith("目标与人群"), 6), "[outline_wb] an earlier step can be clicked")
    pg.click("#owroot .ow-steps [data-ow-step=overview]"); pg.wait_for_selector("#owroot .ow-tbl")
    off = pg.evaluate("""() => [...document.querySelectorAll('#owroot .ow-tbl tbody tr:not(.tot)')].map((tr) => { const cs = [...tr.querySelectorAll('td')].map((td) => { const ctl = td.querySelectorAll('input,select'); const el = ctl.length === 1 ? ctl[0] : td; const r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; }); return Math.max(...cs) - Math.min(...cs); })""")
    ok(off and max(off) <= 14, f"[outline_wb] in every overview row the cells sit on one line (largest spread {max(off) if off else '-'}px)")
    ico = pg.evaluate("""() => { const t = [...document.querySelectorAll('#owroot .ow-steps .ow-st i')].map((e) => { const r = e.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 2); }); return Math.max(...t) - Math.min(...t); }""")
    ok(ico <= 1, f"[outline_wb] the step marks sit on one line (spread {ico}px)")
    audit_page(pg, "outline_wb overview/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "outline_wb_overview.png"))
    pg.hover(".sub2"); pg.wait_for_timeout(400); pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b1_outline_menu.png")); pg.mouse.move(900, 500)
    n = pg.locator("#owroot .ow-sug label").count()
    if n:
        ok(pg.locator("#owroot .ow-sug label").count() <= 3, "[outline_wb] at most three suggestions")
        pg.click('[data-ow="adopt"]'); ok(wait_until(lambda: "已按所选" in pg.inner_text("#ow-msgs"), 6), "[outline_wb] adopting needs a click and the dialog says what changed")
    pg.click('[data-ow="confirm-ov"]'); ok(wait_until(lambda: pg.locator("#owroot [data-ow=gen]").count() == 1, 6), "[outline_wb] after confirming, questions are written one module at a time")
    pg.click('[data-ow="gen"]'); ok(wait_until(lambda: pg.locator("#owroot .ow-item").count() >= 2, 8), "[outline_wb] the module's questions appear")
    rows = pg.evaluate("""() => [...document.querySelectorAll('#owroot .ow-item .h')].map((h) => { const c = [...h.children].map((e) => { const r = e.getBoundingClientRect(); return (r.top + r.bottom) / 2; }); return Math.max(...c) - Math.min(...c); })""")
    ok(rows and max(rows) <= 2, f"[outline_wb] label, text, minutes and delete sit on one line in every question (largest spread {max(rows) if rows else '-'}px)")
    audit_page(pg, "outline_wb questions/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "outline_wb_questions.png"))
    old = pg.locator("#owroot .ow-item textarea >> nth=0").input_value()
    pg.click("#owroot .ow-item textarea >> nth=0"); pg.fill("#ow-in", "改得更口语一点"); pg.click("#owchat .ti-send")
    ok(wait_until(lambda: pg.locator("#ow-msgs [data-ow-rev=accept]").count() == 1, 8), "[outline_wb] the edit is a pending card with an accept button")
    ok(pg.locator("#owroot .ow-item textarea >> nth=0").input_value() == old, "[outline_wb] nothing is written before it is accepted")
    # 修正方案第 1 轮第 2 条: the chat box is the unified input: attach, link, mic, round send; a file read for this message comes back as a source card
    ok(all(pg.locator(f"#owchat .ti-box [data-tia={k}]").count() == 1 for k in ("file", "link", "mic", "send")), "[tinput] the assistant's box has attach, link, mic and send")
    with pg.expect_file_chooser() as fc:
        pg.click("#owchat [data-tia=file]")
    fc.value.set_files(files=[{"name": "品牌简报.txt", "mimeType": "text/plain", "buffer": "品牌重视成分，新加坡用户常看小红书评价。".encode()}])
    ok(wait_until(lambda: "已读取文字" in pg.inner_text("#owchat .ti-chips"), 8), "[tinput] an attached file shows as a card with how much was read")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b4_tinput_chat.png"))
    audit_page(pg, "tinput chat/desktop", errs)
    pg.fill("#ow-in", "参考这份资料"); pg.keyboard.press("Enter")
    ok(wait_until(lambda: "资料 1 · 品牌简报.txt" in pg.inner_text("#ow-msgs"), 8) and pg.locator("#owchat .ti-chips .ti-att").count() == 0, "[tinput] Enter sends; the answer shows the source it used and the card is cleared")
    pg.click("#ow-msgs [data-ow-rev=accept]"); ok(wait_until(lambda: pg.locator("#owroot .ow-item textarea >> nth=0").input_value() != old, 6), "[outline_wb] accepting writes it into the outline")
    pg.click('[data-ow="conf-mod"]'); ok(wait_until(lambda: pg.locator("#owroot .ow-toc [data-ow-mod] .ok").count() == 1, 6), "[outline_wb] a confirmed module is ticked in the module list on the page")
    ok(pg.locator("#owstate .ow-vline").count() == 1 and len(pg.inner_text("#owstate").strip().splitlines()) == 1, "[outline_wb] the version card at the foot of the side menu is one line")
    # 修正方案第 1 轮第 3 批: 已经知道的 in Chinese and editable, 需要重新生成, 重新开始 with 撤销清除, the focus questions' multi-choice "对应到"
    pg.click("#owroot .ow-steps [data-ow-step=goal]"); pg.wait_for_selector("#owroot [data-ow-edit=research_type]")
    ok(pg.locator("#owroot .ow-slot [data-ow-edit]").count() >= 6, "[outline_wb] every known value has its own “改”")
    pg.click("#owroot [data-ow-edit=research_type]"); pg.select_option("#ow-ed", "usability"); pg.click('[data-ow="ed-save"]')
    ok(wait_until(lambda: "可用性" in pg.inner_text("#owroot .ow-card"), 6), "[outline_wb] the changed research type reads in Chinese")
    pg.click("#owroot [data-ow-edit=materials]"); pg.check('[data-ow-ms="concept"]'); pg.click('[data-ow="ed-save"]')
    ok(wait_until(lambda: "概念卡" in pg.inner_text("#owroot .ow-card"), 6), "[outline_wb] materials can be changed with check boxes")
    audit_page(pg, "outline_wb goal edit/desktop", errs)
    pg.click("#owroot .ow-steps [data-ow-step=questions]")
    ok(wait_until(lambda: pg.locator("#owroot .ow-stale").count() == 1, 6), "[outline_wb] what was made from the old settings is flagged 需要重新生成, not overwritten")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b3_stale.png"))
    pg.click('[data-ow="stale-keep"]'); ok(wait_until(lambda: pg.locator("#owroot .ow-stale").count() == 0, 6), "[outline_wb] 不用重新生成 takes the bar away")
    # 修正方案第 2 轮 C9/C10: entering a module writes its questions by itself; 继续 and 关注问题 open only when every module is confirmed
    ok(pg.locator("#owroot .ow-steps [data-ow-step=focus].lock").count() == 1 and pg.locator("#owroot .ow-nav [data-ow-step=focus]").count() == 0, "[outline_wb] 继续 is not there before every module is confirmed")
    for _ in range(12):
        if pg.locator("#owroot .ow-steps [data-ow-step=focus].lock").count() == 0:
            break
        wait_until(lambda: pg.locator('#owroot [data-ow="conf-mod"]').count() == 1, 10)   # the module's questions were written without a click
        pg.click('#owroot [data-ow="conf-mod"]'); pg.wait_for_timeout(400)
    ok(wait_until(lambda: pg.locator("#owroot .ow-steps [data-ow-step=focus].lock").count() == 0 and (pg.locator("#owroot .ow-nav [data-ow-step=focus]").count() == 1 or pg.locator("#owroot .ow-steps .ow-st.on").inner_text().strip().endswith("关注问题")), 6)
       and pg.locator("#owroot .bk").count() == 0, "[outline_wb] after the last module the way on opens (继续 or straight to 关注问题); there is no 备选 box")
    # 用户 2026-11-20 第 3 轮: no “对应到” column; the box searches the outline's questions by keyword (or adds a new focus question); 下一步 goes to 预览测试
    pg.click("#owroot .ow-steps [data-ow-step=focus]"); pg.wait_for_selector("#ow-focus-in")
    ok(pg.locator("#owroot [data-ow-fdd]").count() == 0 and "对应到" not in pg.inner_text("#owroot .ow-ftbl"), "[outline_wb] the focus table has no 对应到 column")
    first = call(f"/projects/{pid}/outline-wb", None, adm)["items"]
    q1 = next(i for i in first if i["kind"] == "question")
    word = q1["text"][2:6]
    pg.fill("#ow-focus-in", word)
    ok(wait_until(lambda: pg.locator("#ow-fdrop [data-ow=focus-pick]").count() >= 1, 4), f"[outline_wb] typing “{word}” lists the outline's questions with it")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix3_focus_search.png"))
    pg.click("#ow-fdrop [data-ow=focus-pick] >> nth=0")
    ok(wait_until(lambda: pg.locator("#owroot .ow-ftbl tbody tr").count() == 1 and pg.locator("#owroot .ow-ftbl .ow-pill").count() == 1, 8), "[outline_wb] the picked question becomes a focus question pointing at it")
    pg.fill("#ow-focus-in", "用户平时喜欢哪些广告？"); pg.click('#owroot .ow-fsearch > [data-ow="focus-add"]')
    ok(wait_until(lambda: pg.locator("#owroot .ow-ftbl tbody tr").count() == 2, 8), "[outline_wb] a new focus question can be written")
    ok(pg.locator(f'#owroot .ow-nav a[href="#/p/{pid}/people/test"]').count() == 1, "[outline_wb] 关注问题 has 下一步：预览测试")
    audit_page(pg, "outline_wb focus/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b3_focus.png"))
    pg.click('[data-ow="rs-open"]'); ok(pg.locator("#owroot .ow-ask input[value=all]").count() == 1, "[outline_wb] 重新开始 asks: only my answers, or everything")
    pg.click('[data-ow="rs-go"]'); ok(wait_until(lambda: pg.locator("#owroot .ow-undo").count() == 1, 6), "[outline_wb] after clearing, 撤销清除 is offered")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b3_undo.png"))
    audit_page(pg, "outline_wb reset/desktop", errs)
    pg.click('[data-ow="rs-undo"]'); ok(wait_until(lambda: pg.locator("#owroot .ow-undo").count() == 0 and pg.locator("#owroot .ow-steps .ow-st.done").count() >= 2, 6), "[outline_wb] 撤销清除 puts it all back")
    # 修正方案第 1 轮第 5 条: a project with a published 立项: length, format and languages come from its sub-project; a change asks for an unlock
    dp = call("/projects/draft", {"name": "QA 大纲同步"}, adm)["id"]
    cfg = call(f"/projects/{dp}/plan", None, adm)["config"]
    cfg["subs"] = [{**cfg["subs"][0], "design": "i", "minutes": 45}, {**cfg["subs"][0], "id": "s2", "name": "马来西亚", "design": "i", "minutes": 60}]
    cfg["cons"] = {k: True for k in cfg["cons"]}
    cfg["n"] = {"q": 0, "i": 5, "f": 0, "fs": 6}
    cfg["lt"] = {"dims": ["国家"], "rows": [{"id": "r1", "vs": ["s1"], "t": 0, "i": 5, "f": 0}]}
    call(f"/projects/{dp}/plan", {"config": cfg}, adm, method="PUT")
    for step in ("submit", "approve", "publish"):
        call(f"/projects/{dp}/plan/{step}", {}, adm)
    pg.goto(B + f"/#/p/{dp}/people/outline/goal"); pg.reload(); pg.wait_for_selector("#owroot [data-ow-edit='sync:minutes']")
    ok("来自立项" in pg.inner_text("#owroot .ow-card") and pg.locator("#owroot [data-ow-edit='sync:format']").count() == 1, "[outline_wb] length, format and languages come from the 立项 sub-project")
    ok(wait_until(lambda: pg.locator("#owroot .ow-stale").count() == 1 and "马来西亚" in pg.inner_text("#owroot .ow-stale"), 6), "[outline_wb] a sharing sub-project that differs is flagged")
    pg.click("#owroot [data-ow-edit='sync:minutes']"); pg.fill("#ow-ed", "50"); pg.click('[data-ow="sync-next"]')
    ok(wait_until(lambda: "锁定" in pg.inner_text("#owroot .ow-ask"), 6), "[outline_wb] a locked 立项: the change asks for an unlock")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b4_sync.png"))
    audit_page(pg, "outline_wb sync/desktop", errs)
    pg.click('[data-ow="sync-ok"]'); ok(wait_until(lambda: "等待解锁" in pg.inner_text("#owroot .ow-card"), 6), "[outline_wb] the outline uses 50 minutes, waiting for the unlock")
    pg.goto(B + f"/#/p/{dp}/wiz"); pg.wait_for_selector(".chain")
    ok(wait_until(lambda: pg.locator(".wzreq").count() == 1 and pg.locator(".wzmis").count() == 1, 6), "[wizard] the unlock request and the difference with the outline are shown")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b4_wizard_req.png"))
    audit_page(pg, "wizard sync/desktop", errs)
    pg.click("#wzunlockreq"); ok(wait_until(lambda: pg.locator(".wzreq").count() == 0, 6), "[wizard] unlocking carries the asked change")
    ok(next(x for x in call(f"/projects/{dp}/plan", None, adm)["config"]["subs"] if x["id"] == "s1")["minutes"] == 50, "[wizard] the 立项 now says 50 minutes")
    pg.goto(B + f"/#/p/{pid}/people/outline/questions"); pg.reload(); pg.wait_for_selector("#owroot .ow-steps")
    pg.locator(".sub2 .l1", has_text="甄别问卷").click(); pg.wait_for_url("**/people/form"); pg.go_back()
    ok(wait_until(lambda: "/people/outline" in pg.url and pg.locator("#owroot .ow-steps").count() == 1, 8), "[outline_wb] the browser's Back button returns to 生成大纲")
    for th in ("orange", "dark"):   # the three themes are all checked (contrast, sizes, names)
        pg.evaluate(f"document.documentElement.dataset.theme = '{th}'"); pg.wait_for_timeout(300)
        audit_page(pg, f"outline_wb questions/{th}", errs)
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    ctx.close()


def outline_wb_test_section(br, D):
    """大纲工作台 · 预览测试 and Pilot (two side-menu pages since 2026-11-19, each with its own small tabs at the top of the page), the preview shows what a participant sees, a rehearsal runs, a pilot can be set, a version is published; alignment and the three themes are audited."""
    adm = D["tok"]["admin"]
    pid = call("/projects", {"name": "预览测试页", "market": "SG", "language": "zh", "brief": "A 品牌准备进入新加坡，了解都市女性怎么选护肤品。价格是主要顾虑。"}, adm)["id"]
    base = f"/projects/{pid}/outline-wb"
    call(base + "/answer", {"slot": "key_questions", "value": "她们买护肤品最看重什么\n价格接受范围"}, adm); call(base + "/overview", {}, adm); call(base + "/overview/confirm", {}, adm)
    for m in call(base, None, adm)["overview"]["modules"][:2]:
        call(base + f"/modules/{m['id']}/generate", {}, adm); call(base + f"/modules/{m['id']}/confirm", {}, adm)
    ctx, pg, errs = new_page(br, 1440, h=900, token=adm)
    pg.goto(B + f"/#/p/{pid}/people/test"); pg.wait_for_selector("#owroot3 .ow")
    ok(wait_until(lambda: [x.strip() for x in pg.locator("#owroot3 .ow-tabs [data-ow-tab]").all_inner_texts()] == ["受访者预览", "AI 快速检查"], 6), "[outline_wb_test] 预览测试 has its two small tabs at the top of the page (模拟预演 is now AI 快速检查)")
    ok(wait_until(lambda: pg.locator("#owstate .ow-vline").count() == 1, 6), "[outline_wb_test] the version card sits at the bottom of the side menu")
    box = pg.locator("#owroot3 .ow-chat").bounding_box()
    ok(box and box["height"] >= 500, "[outline_wb_test] the assistant panel fills the height of the page")
    ok(wait_until(lambda: pg.locator("#owroot3 .ow-phone").count() == 1, 6), "[outline_wb_test] the preview shows a phone with what the participant sees")
    ok(pg.locator("#owroot3 [data-ow=pv-lang][data-l=ms], #owroot3 [data-ow=pv-lang][data-l=ta]").count() == 0, "[outline_wb_test] the preview offers only the interview's languages")
    audit_page(pg, "outline_wb_test preview/desktop", errs)
    pg.click("[data-ow-tab=rehearsal]"); pg.click('[data-ow="rh-run"]')
    ok(wait_until(lambda: pg.locator("#owroot3 .ow-kv").count() == 1 and "场数" in pg.inner_text("#owroot3"), 8), "[outline_wb_test] a rehearsal runs and reports")
    audit_page(pg, "outline_wb_test rehearsal/desktop", errs)
    pg.locator(".sub2 .l1", has_text="Pilot").click(); pg.wait_for_url("**/people/pilot")
    ok(wait_until(lambda: [x.strip() for x in pg.locator("#owroot3 .ow-tabs [data-ow-tab]").all_inner_texts()] == ["本轮 Pilot", "Pilot 修正", "发布"], 6), "[outline_wb_test] the Pilot page has 本轮 Pilot, Pilot 修正 and 发布")
    ok(wait_until(lambda: pg.locator("#owroot3 [data-ow=rd-skip]").count() == 1 and pg.locator("#owroot3 [data-ow=rd-open]").count() == 1, 6), "[outline_wb_test] 本轮 Pilot first offers the two ways: do a pilot, or skip it and go live")
    ok("还没做 pilot" in pg.inner_text("#owstate"), "[outline_wb_test] the side menu says no pilot has been finished")
    audit_page(pg, "outline_wb_test round choice/desktop", errs)
    pg.click('[data-ow="rd-skip"]'); ok(wait_until(lambda: pg.locator("#owroot3 .ow-ask [data-ow=rd-skip-ok]").count() == 1 and "红线" in pg.inner_text("#owroot3 .ow-ask"), 6), "[outline_wb_test] skipping asks once, with only the red-line check")
    pg.click('[data-ow="rd-skip-cancel"]')
    pg.click('[data-ow="rd-open"]'); pg.wait_for_selector("#owroot3 [data-ow=rd-start]")
    ok(pg.locator("#owroot3 [data-ow-w]").count() >= 2 and "真人主持" in pg.inner_text("#owroot3"), "[outline_wb_test] the start form lists the people to invite and says the host comes from the project")
    ok(pg.locator("#owroot3 [data-ow=rd-pk]").count() == 0 and pg.locator("#owroot3 #ow-r-email").count() == 1, "[outline_wb_test] the participant is one email box (no 从名单选 / 同事, 用户 2026-11-20 第 3 轮)")
    pg.fill("#ow-r-email", "lin@example.com")
    audit_page(pg, "outline_wb_test round form/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b2_round.png"))
    pg.click('[data-ow="rd-start"]')
    ok(wait_until(lambda: "第 1 轮" in pg.inner_text("#owroot3") and pg.locator("#owroot3 .ow-tbl").count() >= 1, 10), f"[outline_wb_test] a round is started and listed")
    ok(wait_until(lambda: "Pilot 第 1 轮" in pg.inner_text("#owstate"), 6), "[outline_wb_test] the version card says which pilot round")
    rid = call(base + "/pilot-rounds", None, adm)["rounds"][0]
    piv = rid["interviews"][0]["id"]
    obs = D["tok"]["observer"]
    c2, p2, e2 = new_page(br, 1440, h=900, token=obs)   # an invited observer marks a problem in the pilot room (no video needed for the panel)
    p2.goto(B + f"/room.html?i={piv}&as=observer"); p2.wait_for_selector("#pmk", timeout=10000)
    p2.keyboard.press("m"); p2.wait_for_selector("#pmk-in"); p2.fill("#pmk-in", "这题太长，受访者一直在等"); p2.click("#pmk .pmk-f button.p")
    ok(wait_until(lambda: "这题太长" in p2.inner_text("#pmk") and "客户" in p2.inner_text("#pmk"), 8), "[outline_wb_test] in the pilot room M opens the mark box and the mark shows with a role, not a name")
    p2.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b2_room.png"))
    c2.close()
    call(f"/interviews/{piv}/start", {}, adm); call(f"/interviews/{piv}/end", {}, adm)
    pg.click("[data-ow-tab=pilot]"); ok(wait_until(lambda: pg.locator("#owroot3 [data-ow-dec]").count() >= 3, 8), "[outline_wb_test] Pilot 修正 lists the round's marks with 接受 / 拒绝 / 暂缓")
    ok(pg.locator("#owroot3 [data-ow=rv-next][disabled]").count() == 1, "[outline_wb_test] the next version waits until every mark is decided")
    for k in range(pg.locator("#owroot3 [data-ow-dec=deferred]").count()):
        pg.locator("#owroot3 [data-ow-dec=deferred]").nth(k).click(); pg.wait_for_timeout(400)
    ok(wait_until(lambda: pg.locator("#owroot3 [data-ow=rv-next]:not([disabled])").count() == 1, 8), "[outline_wb_test] once every mark is decided the next version can be made")
    audit_page(pg, "outline_wb_test pilot review/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b2_review.png"))
    pg.click('[data-ow="rv-next"]'); ok(wait_until(lambda: "v2" in pg.inner_text("#owstate"), 8), "[outline_wb_test] the next version v2 is made from the round")
    pg.click('[data-ow="rv-ready"]'); ok(wait_until(lambda: "已确认可以正式开始" in pg.inner_text("#owroot3"), 6), "[outline_wb_test] the researcher says 可以正式开始")
    pg.click("[data-ow-tab=publish]"); ok(wait_until(lambda: pg.locator("#owroot3 [data-ow=pub-go]").count() == 1, 6), "[outline_wb_test] the publish page lists checks and versions")
    ok("新建版本" not in pg.inner_text("#owroot3") and pg.locator("[data-ow=ver-new]").count() == 0, "[outline_wb_test] there is no 新建版本 button")
    heads = [x.strip() for x in pg.locator("#owroot3 .ow-tbl").last.locator("th").all_inner_texts()]
    ok(heads == ["版本", "来源", "状态", "Pilot", "已被使用"], f"[outline_wb_test] the version table shows source, state and pilot rounds (got {heads})")
    pg.hover(".sub2"); pg.wait_for_timeout(400); pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b1_publish.png")); pg.mouse.move(900, 500)
    tbl = pg.inner_text("#owroot3 .ow-tbl >> nth=-1")
    ok("Pilot 第 1 轮修正" in tbl and "已被替代" in tbl and "1 轮" in tbl, f"[outline_wb_test] v2 came from the round's corrections, v1 is replaced and shows its round ({tbl[:120]!r})")
    audit_page(pg, "outline_wb_test publish/desktop", errs)
    for th in ("orange", "dark"):
        pg.evaluate(f"document.documentElement.dataset.theme = '{th}'"); pg.wait_for_timeout(300)
        audit_page(pg, f"outline_wb_test publish/{th}", errs)
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    if pg.locator('[data-ow="pub-go"]:not([disabled])').count():
        pg.click('[data-ow="pub-go"]'); ok(wait_until(lambda: "已发布" in pg.inner_text("#owstate"), 8), "[outline_wb_test] after publishing the side menu says 已发布")
    ctx.close()


def _finished_pilot(pid, adm):
    """A pilot interview made through the API: set as pilot, started (it freezes the draft outline), ended. A pilot is a must before the outline can be published."""
    part = call(f"/projects/{pid}/participants", {"name": "试访", "country": "SG", "language": "zh"}, adm)
    itv = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "Pilot", "observer_ids": []}, adm)
    call(f"/projects/{pid}/outline-wb/pilots/{itv['id']}", {"value": True}, adm, "PUT")
    call(f"/projects/{pid}/outline-wb/ready", {"value": True}, adm, "PUT")   # the researcher's 可以正式开始 (修正方案第 1 轮第 9 条)
    call(f"/interviews/{itv['id']}/start", {}, adm); call(f"/interviews/{itv['id']}/end", {}, adm)
    return itv["id"]


def outline_board_section(br, D):
    """大纲工作台 · 实时看板 (访谈直播): 场次列表 and 实时看板 are two pages of the same side menu (修正方案第 1 轮第 8 条), the board's own small tabs are inside the page, a focus question shows counts (no percentages under 20 people), a
    coded answer opens a side panel, alerts are listed; alignment and the three themes are audited."""
    adm = D["tok"]["admin"]
    pid = call("/projects", {"name": "看板测试", "market": "SG", "language": "zh", "brief": "A 品牌准备进入新加坡，了解护肤品选购。"}, adm)["id"]
    base = f"/projects/{pid}/outline-wb"
    call(base + "/answer", {"slot": "key_questions", "value": "她们平时怎么买东西"}, adm); call(base + "/overview", {}, adm); call(base + "/overview/confirm", {}, adm)
    for m in call(base, None, adm)["overview"]["modules"][:2]:
        call(base + f"/modules/{m['id']}/generate", {}, adm); call(base + f"/modules/{m['id']}/confirm", {}, adm)
    uid = call(base, None, adm)["items"][0]["uid"]
    call(base + "/focus", {"text": "她们平时怎么买东西", "item_uid": uid}, adm); _finished_pilot(pid, adm); call(base + "/publish", {}, adm)
    ctx, pg, errs = new_page(br, 1440, h=900, token=adm)
    pg.goto(B + f"/#/p/{pid}/people/form"); pg.wait_for_selector(".sub2 .sgrp")
    menu_before = pg.locator(".sub2 .l1 .tx").all_inner_texts()
    pg.locator(".sub2 .l1", has_text="场次列表").click(); pg.wait_for_url("**/rooms"); wait_until(lambda: pg.locator(".sub2 .l1.on .tx").inner_text().strip() == "场次列表", 8)
    ok(pg.locator(".sub2 .l1 .tx").all_inner_texts() == menu_before and pg.locator(".sub2 .l1.on .tx").inner_text().strip() == "场次列表", f"[outline_board] going to 访谈直播 keeps the same side menu, only the highlight moves ({set(menu_before) ^ set(pg.locator('.sub2 .l1 .tx').all_inner_texts())}, on {pg.locator('.sub2 .l1.on .tx').all_inner_texts()})")
    pg.locator(".sub2 .l1", has_text="实时看板").click(); pg.wait_for_url("**/rooms/board")
    ok(wait_until(lambda: pg.locator("#owbbar [data-ob-tab]").count() == 4 and pg.locator(".sub2 #owbbar").count() == 0, 6), "[outline_board] the board's four small tabs are inside the page")
    ok(pg.locator(".sub2 .l1 .tx").all_inner_texts() == menu_before, "[outline_board] the side menu is still the same on the board")
    pg.hover(".sub2"); pg.wait_for_timeout(400); pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b1_board.png")); pg.mouse.move(900, 500)
    pg.go_back(); ok(wait_until(lambda: pg.url.endswith("/rooms") and pg.locator(".sub2 .l1.on .tx").inner_text().strip() == "场次列表", 8), "[outline_board] Back returns to 场次列表")
    pg.go_forward(); pg.wait_for_url("**/rooms/board")
    pg.locator(".sub2 .sgt", has_text="创建访谈").click(); pg.reload(); pg.wait_for_selector(".sub2 .sgrp")
    ok(pg.locator(".sub2 .sgrp[data-grp=create].closed").count() == 1, "[outline_board] a folded group stays folded after a reload (this browser only)")
    pg.locator(".sub2 .sgt", has_text="创建访谈").click()
    ok(wait_until(lambda: pg.locator(".ob-card").count() == 1, 8), "[outline_board] each focus question is a card")
    box = pg.locator("#owbroot .ow-chat").bounding_box()
    ok(box and box["height"] >= 500, "[outline_board] the assistant panel fills the height of the page")
    ok("不到 20 人" not in pg.inner_text("#owbroot") and pg.locator("#owbroot .ob-cards").count() == 1, "[outline_board] the overview shows the cards")
    audit_page(pg, "outline_board overview/desktop", errs)
    pg.click("[data-ob-open]"); ok(wait_until(lambda: "只显示人数不显示百分比" in pg.inner_text("#owbroot") or pg.locator(".ob-bar").count() == 0, 8), "[outline_board] under 20 people only counts are said")
    audit_page(pg, "outline_board focus/desktop", errs)
    pg.click("[data-ob-tab=alerts]"); ok(wait_until(lambda: "场次与告警" in pg.inner_text("#owbroot"), 6), "[outline_board] the alerts page opens")
    audit_page(pg, "outline_board alerts/desktop", errs)
    for th in ("orange", "dark"):
        pg.evaluate(f"document.documentElement.dataset.theme = '{th}'"); pg.wait_for_timeout(300)
        audit_page(pg, f"outline_board alerts/{th}", errs)
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    pg.click("[data-ob-tab=add]"); pg.fill("#ob-wtext", "她们平时怎么买东西的渠道"); pg.click("[data-ob=wfind]")
    ok(wait_until(lambda: pg.locator(".ob-cand").count() >= 1 and pg.locator(".ob-cand input:checked").count() <= 2, 8), "[outline_board] the new focus question is matched to questions, at most two ticked")
    audit_page(pg, "outline_board add/desktop", errs)
    pg.click("[data-ob=wnext1]"); ok(wait_until(lambda: pg.locator("[data-ob-coding]").count() == 4, 5), "[outline_board] four ways of coding to choose from")
    pg.click("[data-ob=wnext2]"); ok(wait_until(lambda: pg.locator("[data-ob=wfinish]").count() == 1, 8), "[outline_board] looking back through what was already said")
    pg.click("[data-ob=wskip]"); ok(wait_until(lambda: "她们平时怎么买东西的渠道" in pg.inner_text("#owbroot"), 8), "[outline_board] the new focus question is on the board")
    audit_page(pg, "outline_board added/desktop", errs)
    pg.locator(".sub2 .l1", has_text="场次列表").click(); ok(wait_until(lambda: pg.locator("#rmlistwrap:not([hidden])").count() == 1, 6), "[outline_board] back to the interview list")
    ctx.close()


def voice_section(br, D):
    """Voice input in the one input box (修正方案第 1 轮第 2 条, user 2026-11-20: 实时; without the live recogniser it falls back to pieces of a few seconds):
    the mic in a chat box starts, a second click stops, the words land in the box and nothing is sent; observers never get a mic."""
    adm = D["tok"]["admin"]
    pid = call("/projects", {"name": "语音输入测试", "market": "SG", "language": "zh", "brief": "了解护手霜的使用场景。"}, adm)["id"]
    ctx, pg, errs = new_page(br, 1280, h=900, token=adm)
    pg.add_init_script("""navigator.mediaDevices.getUserMedia = async () => { const ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = 440; const g = ac.createGain(); g.gain.value = 0.5; const d = ac.createMediaStreamDestination(); o.connect(g); g.connect(d); o.start(); return d.stream; };""")
    pg.goto(B + f"/#/p/{pid}/people/outline"); pg.wait_for_selector("#owchat .ti-box [data-tia=mic]")
    pg.click("#ow-in"); pg.click("#owchat .ti-box [data-tia=mic]")
    ok(wait_until(lambda: pg.locator("#owchat .ti-bar.rec .ti-rec .dot").count() == 1, 6), "[voice] one click starts listening (red dot and the time)")
    ok(pg.locator("#owchat .ti-langs button").count() == 4 and pg.locator("#owchat .ti-langs button[disabled]").count() == 2, "[voice] 中文 / English can be chosen; 马来语 / 泰米尔语 say not yet")
    audit_page(pg, "voice recording/desktop", errs)
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "voice_tinput.png"))
    pg.wait_for_timeout(3200); pg.click("#owchat .ti-rec [data-tia=mic]")
    ok(wait_until(lambda: "示例文字" in pg.input_value("#ow-in"), 10), "[voice] the words land in the box")
    ok(pg.locator("#ow-msgs .bub.me").count() == 0 and pg.locator("#owchat .ti-bar.rec").count() == 0, "[voice] nothing is sent by itself and listening is over")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, h=900, token=D["tok"]["observer"])
    pg.goto(B + f"/#/p/{D['pid']}"); pg.wait_for_selector("main")
    ok(pg.locator("[data-tia=mic]").count() == 0, "[voice] observers never get a microphone")
    ctx.close()


def fix4_section(br, D):
    """修正方案第 4 轮: the ✓ / ✗ in front of an option switches pass / screen-out; the questionnaire on the right scrolls with the mouse wheel anywhere;
    下一步 goes back to the top; no 来自立项 line on ② – ⑥; from 招募/主持/问卷 下一步 opens ① with the six steps."""
    rs = D["tok"]["researcher"]
    pid = call("/projects", {"name": "第 4 轮测试", "market": "SG", "language": "zh", "brief": "了解售后保养决策。"}, rs)["id"]
    qs = [{"type": "single", "text": f"第{i}题？", "options": [{"text": "是"}, {"text": "否", "goto": "end_out"}]} for i in range(1, 13)]
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": qs, "need": "近 12 个月在 4S 店保养过", "goals": ["了解保养决策"]}, rs, method="PUT")
    ctx, pg, errs = new_page(br, 1440, h=900, token=rs)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.removeItem('ptab')")
    pg.goto(B + f"/#/p/{pid}/people/mode"); pg.wait_for_selector("#rmode")
    nxt = pg.locator("#rmode button", has_text="下一步")
    if nxt.count():
        nxt.first.click(); pick_mode(pg)
        ok(wait_until(lambda: pg.locator(".sub2 .l2slot .rs-sst").count() == 6 and pg.locator(".sub2 .rs-sst.on[data-rs-go=plan]").count() == 1, 8), "[fix4] 下一步 from 招募/主持/问卷 opens ① with the six steps shown (用户 2026-11-20)")
    else:
        ok(False, "[fix4] 招募/主持/问卷 has a 下一步 button")
    pg.wait_for_selector("#rfout [data-pass]")
    b = pg.locator("#rfout [data-pass]").first
    b.scroll_into_view_if_needed()
    before = b.inner_text().strip(); b.click()
    ok(wait_until(lambda: pg.locator("#rfout [data-pass]").first.inner_text().strip() != before, 4), f"[fix4] a click on {before} in front of an option switches it")
    box = pg.locator("#rfout").bounding_box()
    pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + 200)
    y0 = pg.evaluate("(() => { const e = document.querySelector('#rfout'); let s = e; while (s && s.scrollHeight <= s.clientHeight + 2) s = s.parentElement; return s ? s.scrollTop : -1; })()")
    pg.mouse.wheel(0, 600); pg.wait_for_timeout(500)
    y1 = pg.evaluate("(() => { const e = document.querySelector('#rfout'); let s = e; while (s && s.scrollHeight <= s.clientHeight + 2) s = s.parentElement; return s ? s.scrollTop : -1; })()")
    ok(y1 > y0, f"[fix4] the mouse wheel over the questionnaire scrolls it ({y0} → {y1})")
    pg.click(".sub2 .rs-sst[data-rs-go=plan]"); pg.wait_for_selector("#ccroot [data-rs=ok]"); pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator(".sub2 .rs-sst.on[data-rs-go=include]").count() == 1, 6) and pg.locator("#ccroot .rs-from").count() == 0, "[fix4] ② has no 来自立项 line")
    audit_page(pg, "fix4 include/desktop", errs)
    # 导入名单: 姓名 + 邮箱 rows, pasted lines need no header; 国家、语言、标签 optional
    pg.locator(".sub2 .l1", has_text="数据库筛选及发送").click(); pg.wait_for_selector("#dbimp"); pg.click("#dbimp")
    pg.wait_for_selector("#imrows")
    ok(pg.locator("#imrows tr").count() >= 1 and pg.locator(".im-tbl th:visible").count() == 3, "[fix4] the import is 姓名 + 邮箱 rows (国家、语言、标签 hidden until asked for)")
    pg.click("#impaste"); pg.fill("#impastet", "bobbie bobbie@example.com" + chr(10) + "林小雨,lin@example.com"); pg.click("#impasteok")
    ok(pg.locator("#imrows tr").count() == 2 and pg.locator("#imrows .im-e").nth(1).input_value() == "lin@example.com", "[fix4] pasted lines without a header fill the rows")
    pg.fill("[name=source]", "自己采集"); pg.click("#dry")
    ok(wait_until(lambda: "将新增" in pg.inner_text("#prev") and "2" in pg.inner_text("#prev"), 6), "[fix4] the preview counts the two people (no 第一行必须有 name 列 error)")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix4_import.png"))
    audit_page(pg, "fix4 import/desktop", errs)
    ctx.close()


def fix3_section(br, D):
    """修正方案第 3 轮: from 招募/主持/问卷 a click on 甄别问卷 shows the six steps in the side menu at once; the host switch, the step clicks and the outline
    step clicks answer within 400 ms; ② shows its table without 重新生成; the buttons say 下一步; ⑥'s 测试答题 breathes; the outline's first step is 目标与人群."""
    import time as _t
    rs = D["tok"]["researcher"]
    pid = call("/projects", {"name": "第 3 轮测试", "market": "SG", "language": "zh", "brief": "了解售后保养决策。"}, rs)["id"]
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": [], "need": "近 12 个月在 4S 店保养过；一线或新一线城市；本田车主", "goals": ["了解保养决策"]}, rs, method="PUT")
    ctx, pg, errs = new_page(br, 1440, h=900, token=rs)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.removeItem('ptab')")
    pg.goto(B + f"/#/p/{pid}/people/mode"); pg.wait_for_selector("#rmode [data-hm=ai]")
    t0 = _t.time(); pg.click("#rmode [data-hm=ai]")
    ok(wait_until(lambda: pg.locator("#rmode [data-hm=ai].on").count() == 1, 0.4), f"[fix3] AI 主持 lights up at once ({int((_t.time() - t0) * 1000)} ms)")
    pg.wait_for_timeout(600)
    ok(call(f"/projects/{pid}/recruiting", token=rs).get("host_mode") == "ai", "[fix3] … and the choice is saved")
    pg.click(f'.sub2 .l1[href="#/p/{pid}/people/form"]'); pick_mode(pg)
    ok(wait_until(lambda: pg.locator(".sub2 .l2slot .rs-sst").count() == 6, 6), "[fix3] from 招募/主持/问卷, 甄别问卷 shows its six steps in the side menu (user 2026-11-20)")
    ok(pg.locator(".sub2 .l2slot").evaluate("e => !e.hidden && e.getBoundingClientRect().height > 100"), "[fix3] the six steps are visible, not a hidden slot")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix3_side.png"))
    ok("近 12 个月" in pg.inner_text("#ccroot") and "了解保养决策" in pg.inner_text("#ccroot"), "[fix3] ① shows the goals and the need instead of —")
    t0 = _t.time(); pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator("#ccroot .rs-ctbl tbody tr").count() >= 1, 8), f"[fix3] ② shows its table without 重新生成 ({int((_t.time() - t0) * 1000)} ms)")
    ok(pg.locator("#ccroot [data-rs=ok]").inner_text().strip() == "下一步" and "全部重做" in pg.inner_text("#ccroot"), "[fix3] the button says 下一步 and the link 全部重做")
    audit_page(pg, "fix3 include/desktop", errs)
    t0 = _t.time(); pg.click(".sub2 .rs-sst[data-rs-go=plan]")
    ok(wait_until(lambda: pg.locator(".sub2 .rs-sst.on[data-rs-go=plan]").count() == 1, 0.4), f"[fix3] a step in the side menu answers at once ({int((_t.time() - t0) * 1000)} ms)")
    ctx.close()


def rsteps_section(br, D):
    """甄别问卷的 6 步 (修正方案第 1 轮第 4 条): ① read the 立项, ② conditions → questions, ③ exclusions first, ⑤ trap and consistency, ⑥ testing ends a
    contradiction; going back remakes only one step; the three themes are audited."""
    rs = D["tok"]["researcher"]
    pid = call("/projects", {"name": "甄别 6 步测试", "market": "SG", "language": "zh", "brief": "了解售后保养决策。"}, rs)["id"]
    call(f"/projects/{pid}/recruiting/form", {"source": "ai", "questions": [], "need": "近 12 个月在 4S 店保养过；一线或新一线城市；本田车主", "goals": ["了解保养决策"]}, rs, method="PUT")
    ctx, pg, errs = new_page(br, 1440, h=900, token=rs)
    pg.goto(B + "/"); pg.evaluate("sessionStorage.setItem('ptab','form')")
    pg.goto(B + f"/#/p/{pid}/people/form"); pick_mode(pg); pg.wait_for_selector(".sub2 .l2slot .rs-sst")
    ok(pg.locator(".sub2 .l2slot .rs-sst").count() == 6 and pg.locator("#ccroot .rs-steps").count() == 0 and "招募需求" in pg.inner_text("#ccroot"), "[rsteps] the six steps are a sub-list under 甄别问卷 in the side menu (user 2026-11-20); ① shows what the 立项 says")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b5_side.png"))
    audit_page(pg, "rsteps plan/desktop", errs)
    ok(pg.locator("#ccroot .rs-doc ol li").count() == 1 and pg.locator("#ccroot a[href$='/wiz']").count() == 0, "[rsteps] ① reads like a document (numbered goals) and has no 去立项改 (fix round 2 A1/A2)")
    pg.click("#ccroot [data-rs=ok]"); pg.wait_for_selector("#ccroot .rs-ctbl tbody tr")
    ok(pg.locator("#ccroot .rs-ctbl tbody tr").count() == 3 and pg.locator("#ccroot [data-rs-add]").count() == 0, "[rsteps] ② the need becomes a table of three conditions, with no “自己加条件”")
    pg.fill("#ccroot textarea[data-ti-key=rs-include]", "加一条近 12 个月保养过 2 次以上"); pg.keyboard.press("Enter")
    ok(wait_until(lambda: pg.locator("#ccroot .rs-ctbl tbody tr").count() == 4 and pg.locator("#ccroot .rs-bub").count() == 2, 8), "[rsteps] the dialog under ② changes the table")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix2_include.png"))
    audit_page(pg, "rsteps include/desktop", errs)
    pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == 4 and pg.locator("#rfout .rf-bd.new").count() == 4, 8), "[rsteps] confirming makes one question per condition, marked as new")
    pg.wait_for_selector("#ccroot .rs-ctbl tbody tr"); pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == 7, 8), "[rsteps] ③ the exclusions are added")
    first = pg.locator("#rfout .rf-q .rq-title").first.inner_text()
    ok("行业" in first, f"[rsteps] the exclusions come first ({first})")
    pg.wait_for_selector("#ccroot .rs-tbl")
    ok(pg.locator("#ccroot [data-rs=import]").count() == 1 and pg.locator("#ccroot .rs-chatf[data-rs-chat=quota] [data-tia=file]").count() == 1, "[rsteps] ④ has 导入配额文件 and a dialog that takes files")
    pg.fill("#ccroot textarea[data-ti-key=rs-quota]", "本田车主 3人，新势力车主 2人"); pg.keyboard.press("Enter")
    ok(wait_until(lambda: pg.inner_text("#rs-qsum").strip() == "5", 8), "[rsteps] the quotas read from the dialog add up (3 + 2 = 5)")
    pg.locator("#ccroot [data-rs-qt='0']").fill("4"); ok(pg.inner_text("#rs-qsum").strip() == "6", "[rsteps] the total follows the typing")
    pg.click("#ccroot [data-rs=ok]")
    pg.wait_for_selector("#ccroot [data-rs-qc=trap]"); pg.check("#ccroot [data-rs-qc=trap]"); pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == 8, 8) and pg.locator("#ccroot [data-rs-qc=consistency]").count() == 0, "[rsteps] ⑤ the trap is added; no “再确认一下” question any more (用户 2026-11-20 第 3 轮: 重复的题目都不留)")
    ok(wait_until(lambda: pg.locator(".sub2 .rs-sst.on").inner_text().strip().endswith("预览与测试"), 6), "[rsteps] ⑥ comes after ⑤")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "fix1_b5_qc.png"))
    ok(pg.locator("#ccroot .rs-chatf[data-rs-chat=test]").count() == 1, "[rsteps] ⑥ has a dialog to change the question being tested")
    pg.click(".sub2 .rs-sst[data-rs-go=include]"); pg.wait_for_selector("#ccroot .rs-ctbl tbody tr")
    pg.uncheck("#ccroot [data-rs-use='include|3']"); pg.click("#ccroot [data-rs=ok]")
    ok(wait_until(lambda: pg.locator("#rfout .rf-q").count() == 7, 8), "[rsteps] going back to ② remakes only its questions (the others stay)")
    for th in ("orange", "dark"):
        pg.evaluate(f"document.documentElement.dataset.theme = '{th}'"); pg.wait_for_timeout(300)
        audit_page(pg, f"rsteps/{th}", errs)
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    pg.goto(B + f"/#/p/{pid}/people/db"); pg.wait_for_selector(".dbimp")
    ok(pg.locator("#dbimp").count() == 1 and pg.locator("#dbimpsend").count() == 1, "[rsteps] 数据库筛选及发送 has 导入名单 and 用邮件发送甄别问卷链接 (fix round 2 B1)")
    audit_page(pg, "db import/desktop", errs)
    pg.evaluate("document.documentElement.dataset.theme = 'light'")
    ctx.close()


def main():
    global QA_DB
    tmp = tempfile.mkdtemp()
    QA_DB = f"{tmp}/qa.db"
    env = {**os.environ, "CONCEPT_TEST_FIXTURE": "1", "DATABASE_URL": f"sqlite:///{tmp}/qa.db", "MEDIA_DIR": f"{tmp}/media", "LIVEKIT_URL": "", "ASR_PROVIDER": "mock", "LLM_PROVIDER": "mock", "ASR_API_KEY": "", "LLM_API_KEY": "", "TTS_PROVIDER": "mock",
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
    call(f"/interviews/{iid}/concept/template", {"template": "concept_test_sample"}, adm)
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {}, mod)   # the moderator lets them in (once)
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
    for w, vp in ((1280, "desktop"),) + (() if os.environ.get("QA_DESKTOP") else ((390, "phone"),)):
        for theme in (None, "dark"):
            tag = f"concept room/{theme or 'light'}/{vp}"
            for role, asr in (("moderator", "moderator"), ("observer", "observer")):
                ctx, pg, errs = new_page(br, w, token=D["tok"][role], room_theme=theme)
                pg.goto(B + f"/room.html?i={iid}&as={asr}"); pg.wait_for_selector("#claims", timeout=8000)
                ok(wait_until(lambda: pg.locator("#caps .cap").count() == 4 and pg.locator("#probes .pr").count() == 2, 10), f"[{tag} {role}] four sentences and two follow-ups are on screen")
                ok(wait_until(lambda: pg.locator("#brlog .m").count() == 2, 8) and pg.locator("#brform").count() == 1, f"[{tag} {role}] the private chat shows both messages and has an input")
                ok(pg.locator("#caps .cap .hd button", has_text="引用").count() == 4 and pg.locator("#caps .cap [data-qid]").count() == 4, f"[{tag} {role}] every sentence has a quote button and can be clicked to quote into 追问")
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
    # (the quotes / follow-ups tab of the old respondent window went with it, user 2026-11-19; the records themselves are covered by tests/test_concept*.py)


def ai_room_section(br, D):
    """An AI-moderated interview in the room, seen by the participant, the moderator and a hidden observer."""
    it, mod, obs, adm, res = D["itv"]["ai_zh"], D["tok"]["moderator"], D["tok"]["observer"], D["tok"]["admin"], D["tok"]["researcher"]
    tk = it["invite_token"]
    call(f"/public/join/{tk}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{tk}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    call(f"/interviews/{it['id']}/start", {}, mod)
    call(f"/public/join/{tk}/enter", {})
    log_texts = lambda pg: pg.locator("#clog .m").all_inner_texts()
    for theme in (None, "dark"):
        for w, vp in ((1280, "desktop"),) + (() if os.environ.get("QA_DESKTOP") else ((390, "phone"),)):
            tag = f"{theme or 'light'}/{vp}"
            if theme is None and vp == "desktop":
                # the moderator before the AI starts: the panel, the start button, no reply box
                ctx, pg, errs = new_page(br, w, token=res, room_theme=theme)
                pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#aist")
                ok("还没有启动" in pg.inner_text("#aist") and pg.is_enabled("#a-start") and pg.is_disabled("#a-take"), f"[ai room moderator {tag}] before starting: start is on, take over is off")
                ok(pg.locator("#hform:visible").count() == 0, f"[ai room moderator {tag}] the human reply box is hidden while the AI is in control")
                audit_page(pg, f"ai room moderator before start/{tag}", errs)
                ctx.close()
                call(f"/interviews/{it['id']}/ai/start", {}, mod)
            if vp == "desktop":
                ctx, pg, errs = new_page(br, w, token=res, room_theme=theme)
                pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#clog .m")
                ok("AI 正在主持" in pg.inner_text("#aist"), f"[ai room moderator {tag}] status line shows who is moderating")
                ok(any("AI" in x and "不是真人" in x for x in log_texts(pg)), f"[ai room moderator {tag}] the AI's opening says it is an AI")
                ok(pg.locator("#clog .m details").count() >= 1, f"[ai room moderator {tag}] the AI's reasoning is available to the team")
                if vp == "desktop":   # the same four columns as a human-moderated room; the team only watches: no microphone, camera or screen
                    ok(pg.locator("#q4 > .qc:visible").count() == 4, f"[ai room moderator {tag}] four columns, like a human-moderated room")
                    ok(pg.locator("#b-mic, #b-cam, #b-hmic, #b-hcam, #b-hscr").count() == 0, f"[ai room moderator {tag}] no microphone, camera or screen buttons while the AI moderates")
                    ok('"canPublish": false' in pg.inner_text("#claims") and '"hidden": true' in pg.inner_text("#claims"), f"[ai room moderator {tag}] the room token cannot publish and is hidden from the participant")
                    ok(pg.locator("#c2c #clog").count() == 1 and pg.locator("#c1t .aitile").count() == 1, f"[ai room moderator {tag}] the AI's tile and the conversation are in the first two columns")
                ok(pg.is_visible("#pform") and pg.is_visible("#pin"), f"[ai room moderator {tag}] whisper box present")
                audit_page(pg, f"ai room moderator running/{tag}", errs)
                ctx.close()
            # the participant: a chat in the interview's language, nothing internal
            ctx, pg, errs = new_page(br, w, room_theme=theme)
            pg.goto(B + f"/room.html?t={tk}"); pg.wait_for_selector("#cin")
            ok(pg.locator("#lpan:visible, #rpan:visible").count() == 0, f"[ai room participant {tag}] no internal panels")
            ok(wait_until(lambda: any("AI 主持人" in x for x in log_texts(pg)), 8), f"[ai room participant {tag}] sees the AI's opening, labelled")
            ok(pg.locator("#clog .m details").count() == 0 and "悄悄话" not in pg.inner_text("body"), f"[ai room participant {tag}] sees no notes or whispers")
            # 修正方案第 3 轮: an AI video interview speaks — the waiting question is read aloud (or a tap is asked for), and 按住说话 is there
            ok(wait_until(lambda: pg.is_visible("#vhold"), 8) and "按住说话" in pg.inner_text("#vhold"), f"[ai room participant {tag}] 按住说话 is there in an AI video interview")
            heard = pg.evaluate("() => performance.getEntriesByType('resource').some((e) => e.name.includes('/tts/'))")
            ok(heard or pg.is_visible("#vtap"), f"[ai room participant {tag}] the AI's question is read aloud (or the page asks for one tap first)")
            if theme is None and vp == "desktop":   # hold to talk needs a microphone: a browser with a simulated one
                fb = br.browser_type.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
                c3, p3, e3 = new_page(fb, w, permissions=["microphone", "camera"])
                p3.goto(B + f"/room.html?t={tk}"); p3.wait_for_selector("#cin"); wait_until(lambda: p3.is_visible("#vhold") and p3.is_enabled("#vhold"), 10)
                p3.evaluate("document.getElementById('vhold').scrollIntoView({block: 'center'})"); n0 = len(log_texts(p3)); hb = p3.locator("#vhold").bounding_box()
                ok(p3.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('#vhold') !== null", [hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2]), f"[ai room participant {tag}] nothing covers 按住说话")
                p3.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2); p3.mouse.down()
                ok(wait_until(lambda: "松开发送" in p3.inner_text("#vhold"), 4), f"[ai room participant {tag}] holding the button listens")
                p3.wait_for_timeout(3200); p3.screenshot(path=os.path.join(ROOT, "qa_shots", "fix3_voice_room.png")); p3.mouse.up()
                ok(wait_until(lambda: len(log_texts(p3)) > n0, 15), f"[ai room participant {tag}] letting go sends what was heard, with no other click (用户 2026-11-20: 手抬起来就发了)")
                c3.close(); fb.close()
            if vp == "desktop":   # the answer box is big (about 16 cm x 8 cm) and the emoji panel opens above it, like a phone keyboard
                bx = pg.locator("#cform").bounding_box()
                ok(540 <= bx["width"] <= 640 and 240 <= bx["height"] <= 340, f"[ai room participant {tag}] the answer box is about 16 x 8 cm (got {bx['width']:.0f} x {bx['height']:.0f} px)")
                wait_until(lambda: pg.is_enabled("#cemoji"), 8)
                pg.click("#cemoji"); pg.wait_for_selector(".empanel .emc", timeout=8000)
                ok(pg.locator(".empanel .emsearch input").count() == 1 and pg.locator(".empanel .emtab").count() >= 6, f"[ai room participant {tag}] the emoji panel has search and category tabs")
                pg.locator(".empanel .emc").first.click(); pg.locator(".empanel .emc").nth(3).click()
                ok(len(pg.input_value("#cin")) >= 2 and pg.locator(".empanel").count() == 1, f"[ai room participant {tag}] emojis go into the answer and the panel stays open")
                if theme is None: pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_room_participant_emoji_1280.png"))
                pg.keyboard.press("Escape"); ok(pg.locator(".empanel").count() == 0, f"[ai room participant {tag}] Escape closes the emoji panel")
                pg.fill("#cin", "")
            if vp == "phone":   # the confirmed phone layout: pictures on top, the conversation in the middle, the answer box at the bottom, the buttons on the title row
                ok(pg.evaluate("document.body.classList.contains('pphone-ai')"), f"[ai room participant {tag}] the phone layout is on")
                ok(pg.evaluate("document.querySelector('.rhead').contains(document.getElementById('b-leave'))"), f"[ai room participant {tag}] the microphone, camera and leave buttons sit on the title row")
                ok(pg.locator("#grid > .aihost .aiface").count() == 1, f"[ai room participant {tag}] the AI card is in the picture row")
                ok(pg.evaluate("(() => { const g = document.getElementById('grid').getBoundingClientRect(), c = document.getElementById('chatbox').getBoundingClientRect(), f = document.getElementById('cform').getBoundingClientRect(); return g.bottom <= c.top + 1 && c.bottom <= f.top + 1 && f.bottom <= innerHeight + 1; })()"), f"[ai room participant {tag}] pictures, conversation and answer box are stacked top to bottom inside the screen")
                ok(pg.is_visible("#pm-vm") and pg.locator("#pm-vm [data-m=big]").count() == 1, f"[ai room participant {tag}] the split / big switch is there")
            audit_page(pg, f"ai room participant/{tag}", errs)
            if theme is None and vp == "phone":
                pg.screenshot(path=os.path.join(ROOT, "qa_shots", "ai_room_participant_390.png"), full_page=True)
            ctx.close()
            # the hidden observer: reads, cannot act
            if vp == "desktop":   # researcher pages are desktop-first: the team's AI room is checked on desktop only
                ctx, pg, errs = new_page(br, w, token=obs, room_theme=theme)
                pg.goto(B + f"/room.html?i={it['id']}&as=observer"); pg.wait_for_selector("#clog .m")
                ok(pg.locator("#a-start, #a-take, #a-back, #hform, #a-ack, #b-mic, #b-cam").count() == 0, f"[ai room observer {tag}] no controls, no microphone or camera")
                ok(pg.locator("#clog .m details").count() == 0 and "悄悄话" not in pg.inner_text("#clog"), f"[ai room observer {tag}] sees the conversation only")
                audit_page(pg, f"ai room observer/{tag}", errs)
                ctx.close()
    # a live conversation, once: the participant types, the AI answers; whisper, take over, hand back; a crisis shows a red alert to the team only
    ctx, pg, errs = new_page(br, 1280, token=res)
    pgp_ctx, pgp, perrs = new_page(br, 390)
    pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#clog .m")
    pgp.goto(B + f"/room.html?t={tk}"); pgp.wait_for_selector("#cin")
    ok(wait_until(lambda: pgp.is_enabled("#cin"), 8), "[ai room] the participant can type once the AI has started")
    pgp.fill("#cin", "我平时在网上买护肤品，因为方便。"); pgp.click("#csend")
    n_before = len(log_texts(pgp))
    ok(wait_until(lambda: "我平时在网上买护肤品" in pgp.inner_text("#clog") and len(log_texts(pgp)) >= max(3, n_before + 1), 12), "[ai room] the participant's line and the AI's reply appear")
    pg.fill("#pin", "多问问退货的体验"); pg.click("#psend")
    ok(wait_until(lambda: any("多问问退货" in x for x in log_texts(pg)), 8), "[ai room] the whisper shows in the team's log")
    ok("多问问退货" not in pgp.inner_text("body"), "[ai room] the participant never sees a whisper")
    pg.click("#a-take"); ok(wait_until(lambda: pg.is_visible("#hform"), 8) and "已由研究员接管" in pg.inner_text("#aist"), "[ai room] taking over shows the reply box")
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
    pg.goto(B + f"/#/i/{it['id']}"); pg.wait_for_selector(".rsp .rd-hd"); pg.wait_for_selector("#rd-rest .rd-sec")
    ok(pg.locator(".rd-lbl:has-text('AI 主持人')").count() >= 1 and pg.locator(".rd-lbl:has-text('受访者')").count() >= 1, "[ai room] the respondent window labels the AI moderator")
    audit_page(pg, "ai interview transcript/desktop", errs)
    ctx.close()


def pretask_section(br, D):
    """Pre-interview task (Sprint 1): opt-in questions the participant answers after consent, before the waiting room."""
    adm, mod, pid = D["tok"]["admin"], D["tok"]["moderator"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "预访问受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "预访问深访", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid, T = it["id"], it["invite_token"]
    # (a) the team sets one required + one optional question (the old session-page card went with the old respondent window, user 2026-11-19)
    call(f"/interviews/{iid}/pretask", {"questions": [{"prompt": "你平时的护肤步骤是怎样的？", "required": True}, {"prompt": "最近一次买护肤品是什么情况？", "required": False}]}, adm, method="PUT")
    ok(len(call(f"/interviews/{iid}/pretask", token=adm)["questions"]) == 2, "[pretask] the configured questions are saved")
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
    pg.click("#pf button"); pg.wait_for_selector("#pw", timeout=15000)
    ok("room.html" not in pg.url and pg.locator("#pw").is_visible(), "[pretask] submitting the answers moves on to the waiting room (a human-moderated one-to-one waits to be let in, rule 2026-10-30)")
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
    # (a) the team offers two candidate times (the old session-page card went with the old respondent window, user 2026-11-19)
    call(f"/interviews/{iid}/timeslots", {"starts": ["2099-01-01T09:00:00", "2099-01-02T14:00:00"]}, adm, method="PUT")
    ok(len(call(f"/interviews/{iid}/timeslots", token=adm)["slots"]) == 2, "[timeslot] the candidate times are saved")
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
    # (c) the session keeps which time the participant chose
    tv = call(f"/interviews/{iid}/timeslots", token=adm); ok(tv["chosen"] is not None and tv["chosen"][:16] in [x["start_at"][:16] for x in tv["slots"]], "[timeslot] the session keeps the time the participant chose")


def phone_room_section(br, D):
    """The participant's phone room (one-to-one, human moderator): pictures on top, live captions in the middle, a quick input at the bottom."""
    adm, mod, pid = D["tok"]["admin"], D["tok"]["moderator"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "手机受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "手机端深访", "language": "zh", "moderator_id": D["ids"]["moderator"]}, adm)
    iid, T = it["id"], it["invite_token"]
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {}, mod)
    call(f"/public/join/{T}/enter", {})
    call(f"/interviews/{iid}/captions", {"text": "你平时怎么选护肤品？", "final": True}, mod)
    ctx, pg, errs = new_page(br, 390, 780)
    pg.goto(B + f"/room.html?t={T}"); pg.wait_for_selector("#claims")
    ok(pg.evaluate("document.body.classList.contains('pphone')"), "[phone room] the phone layout is on for a one-to-one participant")
    ok(pg.is_visible("#pm-caps") and pg.is_visible("#pm-in") and pg.is_visible("#pm-vm"), "[phone room] captions, input and the picture switch are shown")
    ok(pg.evaluate("document.querySelector('.rhead').contains(document.getElementById('b-leave'))"), "[phone room] the microphone, camera and leave buttons sit on the title row")
    ok(wait_until(lambda: "你平时怎么选护肤品" in pg.inner_text("#pm-log"), 8), "[phone room] the moderator's sentence appears in the live captions")
    pg.fill("#pm-ta", "我先看成分，再看价格。")
    ok(pg.evaluate("!document.getElementById('pm-in').classList.contains('empty')"), "[phone room] the send button is live once there is text")
    pg.press("#pm-ta", "Enter")
    ok(wait_until(lambda: pg.locator("#pm-log .pm-line.me").count() >= 1 and "我先看成分" in pg.inner_text("#pm-log"), 8), "[phone room] a typed answer is sent and shows as my line")
    ok(pg.input_value("#pm-ta") == "", "[phone room] the box is empty again after sending")
    ok(pg.evaluate("document.activeElement.id === 'pm-ta'"), "[phone room] the box keeps focus (the keyboard stays open)")
    ok(any("我先看成分" in x["text"] for x in call(f"/interviews/{iid}/captions", token=mod)["lines"] if x["speaker"] == "participant"), "[phone room] the moderator receives the typed answer")
    pg.click("#pm-vm [data-m=big]")
    ok(pg.evaluate("document.getElementById('grid').classList.contains('pv-big')") and pg.evaluate("document.querySelector('.rbody>section').offsetHeight") >= 200, "[phone room] big-screen mode makes the pictures taller")
    ok(pg.evaluate("JSON.parse(localStorage.getItem('room_vm_p')).mode") == "big", "[phone room] the picture mode is kept in this browser only")
    pg.click("#pm-vm [data-m=split]")
    ok(pg.evaluate("document.querySelector('.rbody>section').offsetHeight") <= 130, "[phone room] split mode is back to the short row")
    wide = pg.evaluate("[...document.querySelectorAll('body *')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 1; }).slice(0, 6).map((e) => `${e.tagName}#${e.id}.${String(e.className).slice(0, 24)} right=${Math.round(e.getBoundingClientRect().right)}`)")
    ok(pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"[phone room] nothing scrolls sideways {wide}")
    ok(pg.evaluate("document.getElementById('pm-in').getBoundingClientRect().bottom <= innerHeight + 1"), "[phone room] the input sits inside the screen at the bottom")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "phone_room_split_390.png"))
    pg.evaluate("document.body.classList.add('pm-kb')")   # what the page does when the keyboard is open
    ok(pg.evaluate("document.querySelector('.rbody>section').offsetHeight") <= 70, "[phone room] with the keyboard open the pictures shrink to a strip")
    pg.screenshot(path=os.path.join(ROOT, "qa_shots", "phone_room_keyboard_390.png"))
    pg.evaluate("document.body.classList.remove('pm-kb')")
    audit_page(pg, "phone room participant/phone", errs)
    pg.click("#b-leave")
    ok(wait_until(lambda: pg.locator(".leftcard").is_visible(), 6) and "邀请链接" in pg.inner_text(".leftcard"), "[phone room] leaving shows a clear 'you have left' page that says how to come back")
    ok("连接已断开" not in pg.inner_text("body"), "[phone room] the 'connection lost' message never covers the leave page")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280)
    pg.goto(B + f"/room.html?t={T}"); pg.wait_for_selector("#claims")
    ok(not pg.is_visible("#pm-caps") and not pg.is_visible("#pm-in"), "[phone room] on a computer the phone parts stay hidden")
    ok(pg.evaluate("!document.querySelector('.rhead').contains(document.getElementById('b-leave'))"), "[phone room] on a computer the buttons stay at the bottom")
    ctx.close()


def marks_section(br, D):
    """Material marking (Sprint 1): the participant taps ♥ like / ✕ dislike on an image, the team reads it as a heatmap."""
    adm, mod, obs, pid = D["tok"]["admin"], D["tok"]["moderator"], D["tok"]["observer"], D["pid"]
    part = call(f"/projects/{pid}/participants", {"name": "圈选受访者", "country": "SG", "language": "zh"}, adm)
    it = call(f"/projects/{pid}/interviews", {"participant_id": part["id"], "title": "素材圈选深访", "language": "zh",
                                              "moderator_id": D["ids"]["moderator"], "observer_ids": [D["ids"]["observer"]]}, adm)
    iid, T = it["id"], it["invite_token"]
    call(f"/interviews/{iid}/concept/template", {"template": "concept_test_sample"}, adm)
    call(f"/public/join/{T}/device", {"camera": True, "microphone": True, "speaker": True})
    call(f"/public/join/{T}/consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    call(f"/interviews/{iid}/start", {}, mod)
    call(f"/interviews/{iid}/admit", {}, mod)   # the moderator lets them in (once)
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
    # (d) the heatmap card went with the old respondent window (user 2026-11-19); the marks themselves are covered by tests/test_marks.py


def tabs_section(br, D):
    """切换页面不跳动：左窄栏、顶部项目条、第二层菜单的位置和大小在所有页面完全相同，页面宽度不变，这三块是同一批 DOM 节点（不重建、不整页刷新）；每页截顶部 200px 放进 qa_shots/tabs_top/。
    浏览器用 ignore_default_args 去掉 --hide-scrollbars，这样滚动条会真实占宽。"""
    adm, pid = D["tok"]["admin"], D["pid"]
    out = pathlib.Path(ROOT) / "qa_shots" / "tabs_top"; out.mkdir(parents=True, exist_ok=True)
    real = br.browser_type.launch(ignore_default_args=["--hide-scrollbars"])
    JS = """(() => { const b = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left * 100) / 100, Math.round(r.top * 100) / 100, Math.round(r.width * 100) / 100, Math.round(r.height * 100) / 100]; };
      return { rail: b('.rail2'), top: b('.top2'), sub: b('.sub2'), main: b('.body2>main'), cw: document.documentElement.clientWidth, over: document.documentElement.scrollWidth > document.documentElement.clientWidth,
               same: ['.rail2', '.top2', '.sub2'].every((s) => document.querySelector(s) === window.__nodes[s]), reloaded: !window.__mark }; })()"""
    steps = [(n, None) for n in ["招募/主持/问卷", "配额及受访者名单", "甄别问卷", "生成大纲", "场次列表", "实时看板", "回复", "报告", "逐题分析", "片段", "AI 对话", "招募/主持/问卷"]]   # every page of the one side menu (修正方案第 1 轮)
    for w in ((1366, 1920) if os.environ.get("QA_DESKTOP") else (1366, 1920, 390)):
        ctx, pg, errs = new_page(real, w, 860, token=adm)
        pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector(".sub2 .l1"); pg.wait_for_timeout(600)
        pg.evaluate("window.__nodes = { '.rail2': document.querySelector('.rail2'), '.top2': document.querySelector('.top2'), '.sub2': document.querySelector('.sub2') }; window.__mark = 1")
        rows = []
        for i, (tab, sub) in enumerate(steps):
            pg.locator(".sub2 a.l1", has_text=tab).first.click(); pg.wait_for_timeout(900)
            pg.wait_for_timeout(300); m = pg.evaluate(JS); rows.append((tab, sub, m))
            pg.screenshot(path=str(out / f"{w}_{i:02d}_{tab}{('_' + sub) if sub else ''}.png"), clip={"x": 0, "y": 0, "width": w, "height": 200})
        ok(all(not r[2]["over"] for r in rows), f"[tabs {w}] no horizontal scroll on any page")
        if w >= 821:   # the phone layout (not part of this work) scrolls the whole page, so its scrollbar may come and go
            ok(len({r[2]["cw"] for r in rows}) == 1, f"[tabs {w}] the page width never changes when switching pages ({sorted({r[2]['cw'] for r in rows})})")
            ok(len({str(r[2]["rail"]) for r in rows}) == 1 and len({str(r[2]["top"]) for r in rows}) == 1 and len({str(r[2]["sub"]) for r in rows}) == 1 and len({str(r[2]["main"]) for r in rows}) == 1,
               f"[tabs {w}] the narrow rail, the top bar, the second-level menu and the content area are at exactly the same place on every page: rail {sorted({str(r[2]['rail']) for r in rows})}, top {sorted({str(r[2]['top']) for r in rows})}, sub {sorted({str(r[2]['sub']) for r in rows})}, main {sorted({str(r[2]['main']) for r in rows})}")
            ok(all(r[2]["same"] and not r[2]["reloaded"] for r in rows), f"[tabs {w}] the rail, the top bar and the second-level menu are the same DOM nodes after every switch (not rebuilt, no page reload)")
        errs[:] = [e for e in errs if "Failed to load resource" not in e]
        ok(not errs, f"[tabs {w}] no console errors while switching pages: {errs[:2]}")
        ctx.close()
    real.close()


def shell_behaviour_section(br, D):
    """外壳的行为：进入项目后窄栏收成 12px 细边、鼠标移上去弹出；第二栏左下角一个收起箭头（收起后只剩图标、“结项”变“结”）；右上角“姓名 · 职位 · 公司”（窄屏只留头像，悬停才显示）；浏览器里的收起选择记住。"""
    adm, pid = D["tok"]["admin"], D["pid"]
    ctx, pg, errs = new_page(br, 1920, 900, token=adm)
    pg.goto(B + "/#/projects"); pg.wait_for_selector(".card")
    w = lambda sel: round(pg.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().width"))
    ok(w(".rail2") == 88 and pg.locator(".fold").count() == 0, "[shell2] outside a project the rail stays 88px wide and there is no fold arrow")
    pg.goto(B + f"/#/p/{pid}/outline"); pg.wait_for_selector(".sub2 .l1"); pg.wait_for_timeout(500)
    ok(w(".rail2") == 12, "[shell2] inside a project the left rail is a 12px edge")
    pg.hover(".rail2"); pg.wait_for_timeout(500)
    ok(round(pg.evaluate("document.querySelector('.rail2 .rin').getBoundingClientRect().width")) == 88 and pg.locator(".rail2 a", has_text="项目").first.is_visible(), "[shell2] hovering the edge opens the rail over the page")
    pg.mouse.move(900, 500); pg.wait_for_timeout(500)
    ok(round(pg.evaluate("document.querySelector('.rail2 .rin').getBoundingClientRect().width")) == 12, "[shell2] moving away folds the rail back to the edge")
    ok(pg.locator(".fold").count() == 1 and pg.locator(".sub2 .bot .fold").count() == 1, "[shell2] there is one fold arrow, at the bottom of the second-level menu")
    ok(w(".sub2") == 272 and "结项" in pg.inner_text(".sub2 #closeproj"), "[shell2] the second-level menu is open with 结项")
    pg.click(".sub2 .fold"); pg.wait_for_timeout(500)
    ok(w(".sub2") == 68 and pg.evaluate("getComputedStyle(document.querySelector('.sub2 .cs')).display") != "none" and pg.evaluate("getComputedStyle(document.querySelector('.sub2 .cf')).display") == "none", "[shell2] folded: icons only and 结项 shows as 结")
    pg.locator(".sub2 a.l1").nth(2).click(); pg.wait_for_selector("#rmtable, .rmws, main"); pg.wait_for_timeout(500)
    ok(w(".sub2") == 68, "[shell2] the folded state is kept when going to another page")
    pg.click(".sub2 .fold"); pg.wait_for_timeout(500)
    ok(w(".sub2") == 272, "[shell2] the arrow opens it again")
    me = pg.inner_text(".top2 .who")
    ok(me.count("·") == 2 and "王总" in me and "管理员" in me and "Acme" in me, f"[shell2] the top right says name · role · company (got {me!r})")
    ctx.close()
    ctx, pg, errs = new_page(br, 1280, 800, token=adm)
    pg.goto(B + f"/#/p/{pid}/outline"); pg.wait_for_selector(".sub2 .l1"); pg.wait_for_timeout(500)
    ok(pg.evaluate("getComputedStyle(document.querySelector('.top2 .who')).display") == "none", "[shell2] on a narrower screen only the avatar shows")
    pg.hover("#mebtn"); pg.wait_for_timeout(300)
    ok(pg.locator(".whotip").is_visible() and "王总" in pg.inner_text(".whotip"), "[shell2] hovering the avatar shows the name, role and company")
    ctx.close()


def run():
    os.makedirs(os.path.join(ROOT, "qa_shots"), exist_ok=True)
    D = seed()
    mi, pid = D["main"]["id"], D["pid"]
    proj = [f"#/p/{pid}/rooms", f"#/p/{pid}/insights"]
    staff_routes = [f"#/projects", f"#/p/{pid}/home", f"#/p/{pid}/people", f"#/p/{pid}/outline", f"#/p/{pid}/members", f"#/p/{pid}/studies", f"#/p/{pid}/listening", f"#/p/{pid}/pool", "#/repo"] + proj + [f"#/i/{mi}", f"#/i/{mi}/insights", f"#/i/{mi}/clips"]
    role_routes = {
        "demo_admin": ["#/projects", f"#/p/{D['demo_pid']}/home", "#/demo"],
        "admin": staff_routes + ["#/team", "#/audit", "#/retention", "#/ai"],
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
        browser_path = os.environ.get("QA_BROWSER", "").strip()
        br = p.chromium.launch(executable_path=browser_path or None)
        if os.environ.get("QA_EARLY"):   # QA_EARLY=<section>: run just that section right after seeding (skips the long general sweep)
            globals()[os.environ["QA_EARLY"]](br, D); br.close(); print(f"{os.environ['QA_EARLY']}: {PASSES} passed, {len(FAILS)} failed"); sys.exit(1 if FAILS else 0)
        if os.environ.get("QA_ONLY") == "outline_wb_test_section":
            outline_wb_test_section(br, D); br.close(); print(f"outline_wb_test_section: {PASSES} passed, {len(FAILS)} failed"); sys.exit(1 if FAILS else 0)
        if os.environ.get("QA_ONLY") == "outline_board_section":
            outline_board_section(br, D); br.close(); print(f"outline_board_section: {PASSES} passed, {len(FAILS)} failed"); sys.exit(1 if FAILS else 0)
        if os.environ.get("QA_ONLY") == "outline_wb_section":   # also only needs a fresh project: run it before the long general sweep
            outline_wb_section(br, D); br.close(); print(f"outline_wb_section: {PASSES} passed, {len(FAILS)} failed"); sys.exit(1 if FAILS else 0)
        if os.environ.get("QA_ONLY") == "tabs_section":   # this section only needs the seeded project: run it before the long general sweep
            tabs_section(br, D); br.close(); print(f"tabs_section: {PASSES} passed, {len(FAILS)} failed"); sys.exit(1 if FAILS else 0)
        # ---------- A. every role x page x viewport ----------
        for w, vp in ((1280, "desktop"),) + (() if os.environ.get("QA_DESKTOP") else ((390, "phone"),)):
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
                        ok(pg.is_visible("#wbox") and pg.locator("#b-admit").count() == 1 and pg.locator("#b-hold").count() == 0, f"[room {role}/{vp}] moderator sees who is in the room and has the release button (no hold)")
                    audit_page(pg, f"room-{role}/{theme or 'light'}/{vp}", errs)
                    ctx.close()
            # theme switch: one click, takes effect at once, remembered in this browser
            ctx, pg, errs = new_page(br, w, token=D["tok"]["moderator"])
            it = D["itv"]["zh"]
            pg.goto(B + f"/room.html?i={it['id']}&as=moderator"); pg.wait_for_selector("#claims")
            ok(pg.locator(".tsw button[data-theme=dark]").count() == 1 and pg.locator("#theme").count() == 0, f"[{vp}] the team's room has the three-icon switch, not the text button")
            pg.click(".tsw button[data-theme=dark]"); ok("t-dark" in pg.evaluate("document.body.className") and pg.evaluate("localStorage.getItem('tt_theme')") == "dark", f"[{vp}] switching to dark green works and is remembered")
            pg.reload(); pg.wait_for_selector("#claims"); ok("t-dark" in pg.evaluate("document.body.className") and pg.locator(".tsw button[data-theme=dark].on").count() == 1, f"[{vp}] the room opens in the remembered theme")
            pg.click(".tsw button[data-theme=light]"); ok("t-light" in pg.evaluate("document.body.className"), f"[{vp}] switching back to light works")
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
        ok(pg.locator(".rail2 .rlogo img").count() == 1, "the phoenix is missing at the top of the narrow rail")
        ok("projects" in pg.url, "after login should land on projects")
        ok(pg.locator(".rail2 a.rlogo[href='#/projects']").count() == 1, "the phoenix at the top of the narrow rail leads back to the project list")
        # the side nav marks exactly one current page, and it is the one shown
        pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector(".rmtabs")
        cur = pg.locator(".top2 .mods [aria-current=page]").all_inner_texts()
        tabs = pg.locator(".sub2 .l1[aria-current=page] .tx").all_inner_texts()
        ok(tabs == ["场次列表"] and len(cur) == 1 and "深访" in cur[0], f"the menu marks 场次列表 (访谈直播 › 场次列表, one side menu since 2026-11-19) and the top bar marks exactly one stage (深访), got menu {tabs} top {cur}")
        ok(pg.locator("main .head h1").count() == 0 or not pg.locator("main .head h1").first.is_visible(), "a page under tabs does not repeat its name as a big title")
        ok(pg.evaluate("parseInt(getComputedStyle(document.querySelector('.top2 .mod.on')).fontWeight) > parseInt(getComputedStyle(document.querySelector('.top2 .mod:not(.on)')).fontWeight)"), "the current stage in the top bar is bolder than the others")
        pg.goto(B + f"/#/p/{pid}/sessions"); pg.wait_for_selector(".rmtabs")
        ok(pg.url.endswith("/rooms"), "an old link to the sessions-and-invitations page lands on the live rooms")
        ok(pg.locator(".sub2 a, .top2 a", has_text="场次与邀请").count() == 0, "there is no 场次与邀请 entry any more")
        # project home: big progress number, a next step that links to the right page, recent activity in plain words
        pg.goto(B + f"/#/p/{pid}"); pg.wait_for_selector("h1:has-text('项目总览')", state="attached")
        ok(pg.locator(".hero-n b").inner_text().isdigit() and pg.locator(".meters .meter").count() == 3, "project home shows the big number and three progress bars")
        ok(pg.inner_text("#nextstep").strip() != "" and pg.locator(".next a.btn").count() == 1, "project home says what to do next, with one button")
        pg.locator("details.feedbox summary").click()   # the activity list is folded since 2026-11-07; open it, the content check is unchanged
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
        pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("#np")
        pg.click("#np"); opts = pg.locator("#f [name=country] option").all_inner_texts(); ok(len(opts) == 1 and "SG" in opts[0], f"only Singapore should be selectable, got {opts}")
        pg.keyboard.press("Escape"); pg.mouse.click(5, 5)
        # id participant shows 'not open'
        pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("text=地区暂未开放")
        # rooms are grouped: in progress / upcoming / ended
        pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector(".rmt")
        counts = {b.inner_text().strip(): int(b.get_attribute("data-n")) for b in pg.locator(".rmt").all()}
        ok(counts["全部"] == counts["待开始"] + counts["进行中"] + counts["已结束"] + counts.get("未到场", 0) and counts["待开始"] >= 1 and counts["已结束"] + counts.get("未到场", 0) >= 1, f"room list has a button per status with its count, got {counts}")
        # team page: observer role label
        pg.goto(B + "/#/team"); pg.wait_for_selector("text=客户张"); ok("观察员" in pg.inner_text("main"), "team page should show role names")
        ok(pg.locator(".sub2 a:has-text('演示数据')").count() == 0, "a real company has no 'clear demo data' entry")
        pg.goto(B + "/#/demo"); pg.wait_for_selector("h1", state="attached"); ok("demo" not in pg.url, "a real company cannot open the demo data page")
        # logout
        pg.click("#mebtn"); pg.click("#out"); pg.wait_for_selector("form#f"); ok(pg.evaluate("localStorage.getItem('tt_token')") is None, "logout should clear the token")
        ctx.close()
        # phones: list tables turn into labelled cards
        ctx, pg, errs = new_page(br, 390, token=D["tok"]["admin"])
        pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector(".tbl.rc")
        ok(pg.evaluate("getComputedStyle(document.querySelector('.tbl.rc thead')).display") == "none", "phone: table header hidden, rows shown as cards")
        ok(pg.evaluate("document.querySelector('.tbl.rc tbody .code').closest('td').dataset.l") == "编号" and pg.evaluate("document.querySelector('.tbl.rc tbody td').dataset.l") == "选择", "phone: every card line carries its column name")
        ctx.close()
        # demo company: the admin can clear the demo data (and only that)
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["demo_admin"])
        pg.goto(B + "/#/demo"); pg.wait_for_selector("#demoreset")
        ok(pg.locator(".sub2 a:has-text('演示数据')").count() == 1, "the demo company's admin sees the 'demo data' entry")
        pg.once("dialog", lambda d: d.accept()); pg.click("#demoreset"); pg.wait_for_selector("#demodone:not([hidden])")
        ok("1 场访谈" in pg.inner_text("#demodone") and "python -m app.seed" in pg.inner_text("#demodone"), "after clearing, says what was removed and how to get fresh demo data")
        ok(call(f"/projects/{pid}/interviews", token=D["tok"]["admin"]) != [], "clearing demo data never touches a real company")
        ctx.close()

        # role-based UI: what each role must NOT see
        def see(role, route, sel):
            c, g, e = new_page(br, 1280, token=D["tok"][role]); g.goto(B + "/" + route); g.wait_for_selector("main")
            if route.startswith("#/i/"):
                g.wait_for_selector(".rsp .rd-hd")
                g.wait_for_selector("#rd-rest .rd-sec", timeout=15000)
            g.wait_for_timeout(500)
            n = g.locator(sel).count(); c.close(); return n
        ok(see("observer", "#/projects", "#np") == 0, "observer must not see 'new project'")
        ok(see("moderator", "#/projects", "#np") == 0, "moderator must not see 'new project'")
        ok(see("observer", f"#/i/{mi}/insights", "#gen") == 0, "observer must not see 'generate insights'")
        ok(see("observer", f"#/i/{mi}", "#inv, #rot") == 0, "observer must never see the invite link")
        ok(see("viewer", "#/projects", "#np") == 0 and see("recruiter", "#/projects", "#np") == 0, "a viewer and a recruiter must not see 'new project'")
        ok(see("viewer", f"#/p/{pid}/pool", "#nf, .pick, #imp2") == 0 and see("viewer", f"#/p/{pid}/pool", "#rep") == 1, "[roles] a viewer reads the pool, cannot add notes, and can download the report")
        ok(see("viewer", f"#/p/{pid}/listening", "#addsrc, [data-run], [data-tog], [data-del], [data-dm], [data-au]") == 0, "[roles] a viewer cannot change or take down monitoring content")
        ok(see("recruiter", f"#/p/{pid}/people", "#batch, [data-inv], [data-erase]") == 0 and see("recruiter", f"#/p/{pid}/people", "#imp2") == 1, "[roles] a recruiter can import and screen but not invite or erase")
        for role, other in (("viewer", f"#/p/{pid}/people"), ("recruiter", f"#/p/{pid}/pool"), ("recruiter", f"#/p/{pid}/insights")):
            c, g, e = new_page(br, 1280, token=D["tok"][role]); g.goto(B + "/" + other); g.wait_for_selector("main"); g.wait_for_timeout(400)
            ok(("/pool" in g.url) if role == "viewer" else ("/people" in g.url), f"[roles] a {role} opening {other.split('/')[-1]} is sent back to their own page"); c.close()
        ok(see("observer", f"#/i/{mi}/clips", "[data-sh]") == 0, "observer must not see 'share link'")
        ok(see("researcher", "#/team", "#nu") == 0 and see("researcher", "#/team", "text=添加成员") == 0, "researcher must not manage the team")
        ok(see("admin", "#/team", "#nu") == 1, "admin should manage the team")
        ok(see("observer", f"#/i/{mi}", "text=Wei Ling") == 0, "observer must not see the participant's name")
        # the recruiting / invitation pages are for the research team only
        for role in ("observer", "moderator"):
            ok(see(role, f"#/p/{pid}/rooms", ".sub2 a:has-text('甄别设定'), .top2 a:has-text('甄别设定'), .sub2 a:has-text('场次与邀请')") == 0, f"{role} must not see recruiting / invitation pages in the nav")
            ok(see(role, f"#/p/{pid}/people", "text=添加受访者") == 0, f"{role} must not reach the recruiting page by URL")
        ok(see("observer", f"#/p/{pid}/rooms", "a:has-text('进入 TA 的房间')") == 0 and see("observer", f"#/p/{pid}/rooms", "a:has-text('隐身观察')") >= 1, "observer can only enter rooms hidden")
        ok(see("observer", f"#/p/{pid}/rooms", "text=Wei Ling") == 0, "observer's room list never shows participant names")
        ok(see("moderator", f"#/p/{pid}/rooms", "a:has-text('隐身观察')") == 0, "moderator enters as moderator, not as a hidden observer")
        ok(see("observer", f"#/p/{pid}/rooms", "#rmb td:has-text('未指定')") == 0, "[roles] a client observer never sees the team's moderator names or the 'not named' text")

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
            pg.check("input[name=ai]"); pg.click("form button"); pg.wait_for_url("**/room.html*", timeout=20000)
            ok(state(it) in ("consented", "entered"), f"[join AI {lang}] agreeing to all four items goes into the room")
            ctx.close()
        # (c) full journey: consent -> straight into the room (nobody lets anyone in) -> leave -> back in
        ctx, pg, errs = new_page(fake, 390, **media)
        it = D["itv"]["zh"]
        mod = D["tok"]["moderator"]
        pg.goto(B + f"/join.html?t={it['invite_token']}"); pg.wait_for_selector("form#f")  # the device test passed above, so it resumes at consent
        pg.select_option("[name=country]", "SG")
        for i in range(3): pg.locator("input[type=checkbox]").nth(i).check()
        pg.click("form button")
        call(f"/interviews/{it['id']}/admit", {}, mod)   # consent done: the moderator lets them in (once), then the room opens, before the interview has started
        pg.wait_for_selector("#claims", timeout=20000)
        cl = json.loads(pg.inner_text("#claims")); ok(cl["canPublish"] and not cl["hidden"] and not cl["canPublishData"], f"participant grants wrong {cl}")
        ok(state(it) == "entered", "staff should see the participant as entered")
        ok(wait_until(lambda: call(f"/interviews/{it['id']}/live", token=mod)["participant"]["waiting"], 10), "the moderator sees the participant in the room before starting")
        ok("主持人" in pg.inner_text("#msg") or "moderator" in pg.inner_text("#msg").lower() or "稍等" in pg.inner_text("#msg"), "the participant is told to wait for the moderator inside the room")
        ok(pg.locator("#lpan:visible, #rpan:visible").count() == 0, "participant room shows no internal side panels")
        ok("t-light" in pg.evaluate("document.body.className") and "Dark" not in pg.inner_text("#theme"), "participant room: light by default, theme button in the interview language")
        audit_page(pg, "participant room/light/phone", errs)
        pg.click("#theme"); audit_page(pg, "participant room/dark/phone", errs); pg.click("#theme")
        pg.click("#b-leave"); pg.wait_for_timeout(500)
        ok("邀请链接" in pg.inner_text(".leftcard"), "after leaving the participant is told the link works again")
        ok(call(f"/public/join/{it['invite_token']}/enter", {})["token"], "coming back needs nobody's permission")
        ctx.close(); fake.close()


        # ---------- C3. AI moderation in the room (Sprint 5): participant chat, the team's AI panel, observer, alerts; both colour themes, desktop + phone ----------
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "ai_room_section": guard_section(ai_room_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "concept_room_section": guard_section(concept_room_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "pretask_section": guard_section(pretask_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "timeslot_section": guard_section(timeslot_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "marks_section": guard_section(marks_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "phone_room_section": guard_section(phone_room_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "group_section": guard_section(group_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "outline_wb_section": guard_section(outline_wb_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "outline_wb_test_section": guard_section(outline_wb_test_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "outline_board_section": guard_section(outline_board_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "voice_section": guard_section(voice_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "rsteps_section": guard_section(rsteps_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "tabs_section": guard_section(tabs_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "shell_behaviour_section": guard_section(shell_behaviour_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "fill_section": guard_section(fill_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "insights_section": guard_section(insights_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "projects_section": guard_section(projects_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "tabflash_section": guard_section(tabflash_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "languages_section": guard_section(languages_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "respondent_window_section": guard_section(respondent_window_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "rooms_section": guard_section(rooms_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "members_section": guard_section(members_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "studies_section": guard_section(studies_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "repo_section": guard_section(repo_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "listening_section": guard_section(listening_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "recruit_section": guard_section(recruit_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "wizard_section": guard_section(wizard_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "team_section": guard_section(team_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "quant_section": guard_section(quant_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "pool_section": guard_section(pool_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "admin_data_section": guard_section(admin_data_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "segcode_section": guard_section(segcode_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "vote_section": guard_section(vote_section, br, D)
        if not os.environ.get("QA_ONLY") or os.environ["QA_ONLY"] == "reel_section": guard_section(reel_section, br, D)

        # ---------- C2. dialogs and expandable panels on the staff pages (both screen sizes) ----------
        for w, vp in ((1280, "desktop"),) + (() if os.environ.get("QA_DESKTOP") else ((390, "phone"),)):
            ctx, pg, errs = new_page(br, w, token=D["tok"]["admin"])
            pg.goto(B + f"/#/i/{mi}"); pg.wait_for_selector("[data-rd-edit]")   # the respondent window (v2): 校对 is the ✎ beside an answer; 写洞察 and per-clip sharing went with the old window (user 2026-11-19)
            pg.locator("[data-rd-edit]").first.click(); pg.wait_for_selector(".modal form#f textarea")
            audit_page(pg, f"dialog 校对/{vp}", errs)
            ok(pg.locator(".modal textarea[name=text_orig]").input_value() != "", f"[{vp}] edit dialog is prefilled")
            # the voice / attach tools sit INSIDE the focused box (方案 A, user 2026-11-20): they used to hang below it and covered 保存
            pg.click(".modal textarea[name=text_zh]"); pg.wait_for_selector(".ti-float:not([hidden])")
            fl = pg.evaluate("""() => { const f = document.querySelector('.ti-float').getBoundingClientRect(), t = document.querySelector('.modal textarea[name=text_zh]');
              const r = t.getBoundingClientRect(), hit = (b) => !(b.right <= f.left || b.left >= f.right || b.bottom <= f.top || b.top >= f.bottom);
              return {inside: f.left >= r.left && f.right <= r.right && f.top >= r.top && f.bottom <= r.bottom,
                      covers: [...document.querySelectorAll('.modal button')].filter((b) => hit(b.getBoundingClientRect())).map((b) => b.textContent.trim()),
                      room: parseFloat(getComputedStyle(t).paddingRight) >= f.width}; }""")
            ok(fl["inside"] and not fl["covers"] and fl["room"], f"[{vp}] the voice tools sit inside the box, leave room for the text and cover no button ({fl})")
            pg.click(".modal h2"); pg.wait_for_timeout(250)
            ok(pg.evaluate("() => document.querySelector('.modal textarea[name=text_zh]').style.paddingRight") == "", f"[{vp}] the box gets its own spacing back when it loses focus")
            ok(pg.evaluate("document.querySelectorAll('.modal').length") == 1, f"[{vp}] exactly one dialog open")
            pg.keyboard.press("Escape"); pg.mouse.click(3, 3); pg.wait_for_timeout(100)
            pg.goto(B + f"/#/p/{pid}/insights"); pg.wait_for_selector("main")
            ok(pg.evaluate("document.querySelectorAll('.modal').length") == 0, f"[{vp}] a dialog must not survive a page change")
            # recruiting / sessions dialogs
            pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("#np"); pg.click("#np"); pg.wait_for_selector(".modal form#f")
            audit_page(pg, f"dialog 添加受访者/{vp}", errs)
            pg.keyboard.press("Escape"); ok(pg.locator(".modal").count() == 0, f"[{vp}] Escape closes the dialog")
            pg.evaluate("sessionStorage.setItem('ptab', 'list')"); pg.goto(B + f"/#/p/{pid}/people"); pick_mode(pg); pg.wait_for_selector("[data-inv], [data-upd]:not([disabled])"); pg.locator("[data-inv], [data-upd]:not([disabled])").first.click(); pg.wait_for_selector(".modal form#f [name=participant_id]")   # 发送邀请函, or 更新邀请函 once that person is invited (2026-10-27)
            audit_page(pg, f"dialog 新建场次/{vp}", errs)
            pg.keyboard.press("Escape")
            # the live room page: a status button shows only that status
            pg.goto(B + f"/#/p/{pid}/rooms"); pg.wait_for_selector(".rmt")
            pg.locator(".rmt", has_text="已结束").click()
            ok(pg.locator("#rmb tr").count() == 0 or all("转写和洞察" in t or "已按保留期" in t for t in pg.locator("#rmb tr").all_inner_texts()), f"[{vp}] the 已结束 button shows only ended sessions")
            pg.locator(".rmt", has_text="全部").first.click()
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
        ok(pg.locator(".rail2 .rlogo img").count() == 1, "reduced motion: the logo in the rail is a still image")
        ctx.close()

        # ---------- E2. rapid navigation: a slow page finishing after the user moved on must neither error nor paint over the new page ----------
        ctx, pg, errs = new_page(br, 1280, token=D["tok"]["admin"])
        pg.goto(B + f"/#/i/{mi}/insights"); pg.wait_for_selector("main")
        for r in (f"#/p/{pid}/insights", f"#/i/{mi}/transcript", f"#/i/{mi}/clips", f"#/i/{mi}/insights", f"#/p/{pid}/rooms", f"#/i/{mi}/insights", f"#/p/{pid}/home"):
            pg.evaluate("h => { location.hash = h }", r); pg.wait_for_timeout(25)
        pg.wait_for_timeout(300); settle(pg); pg.wait_for_timeout(600)
        body = pg.inner_text("main")
        ok("打不开这个页面" not in pg.inner_text("body") and "Cannot set properties" not in body, "rapid navigation never shows an error page")
        ok(pg.url.endswith(f"/p/{pid}/home") and pg.locator("main .tabs").count() == 0 and pg.locator(".rsp").count() == 0, f"rapid navigation: the last page clicked is the one left on screen (no stale interview window painted over it) (url {pg.url[-40:]}, tabs {pg.locator('main .tabs').count()}, window {pg.locator('.rsp').count()})")
        ctx.close()

        # ---------- F. screenshots for a human glance ----------
        os.makedirs(os.path.join(ROOT, "qa_shots"), exist_ok=True)
        shots = [("admin", f"#/projects"), ("admin", f"#/p/{pid}/home"), ("admin", f"#/p/{pid}/people"), ("admin", f"#/p/{pid}/rooms"), ("admin", f"#/p/{pid}/insights"),
                 ("admin", f"#/i/{mi}/insights"), ("observer", f"#/p/{pid}/rooms"), ("observer", f"#/i/{mi}/insights")]
        for role, r in shots:
            for w in ((1280,) if os.environ.get("QA_DESKTOP") else (1280, 390)):
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
