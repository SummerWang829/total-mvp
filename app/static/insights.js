/* “洞察与片段”页：五个横向小页签——受访者数据、报告、问题分析、精彩片段、对话（研究员/管理员/技术支持；观察员只有受访者数据）。
   受访者数据：三个数字 + 受访者表，鼠标移到某一行出详细卡片（手机上点一下）；报告：整个项目的研究报告（页面里看 + 下载 Word）；
   问题分析：按“访谈”页大纲里的每个问题归纳回答；精彩片段：片段卡片墙 + 集锦；对话：只围绕本项目的 AI 提问（引文由服务器从转写里原样取）。
   依赖 index.html 里的 api / esc / toast / guard / $ / STAFF / ME / STATUS / CONFIG / reelsSection / shell。 */
const INS_TABS = [["r", "回复"], ["p", "报告"], ["q", "逐题分析"], ["v", "片段"], ["b", "看板"], ["c", "AI 对话"]];   // A 案命名（用户 2026-11-04 定）
const insClock = (ms) => { const s = Math.max(0, Math.round((ms || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
const insAiNotice = () => "";   // the notice about where questions go was removed (user and legal, 2026-11-01)
const insDownload = (p, url, name) => guard(async () => {  // a download needs the login header, so fetch it and hand the browser a file
  const r = await fetch(url, { headers: { Authorization: "Bearer " + store.get("tt_token") } });
  if (!r.ok) throw new Error("导出失败，请稍后再试");
  const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name; document.body.appendChild(a); a.click(); a.remove();
});
/** Quotes the server copied out of the transcripts, as clickable lines. */
const insEvidence = (hits) => hits.map((h) => `<div class="ins-q" data-nosel>“${esc(h.text)}”<small> ${esc(h.who || "")} ${h.at_ms ? insClock(h.at_ms) : ""}${h.interview_id ? ` · <a href="#/i/${h.interview_id}">看原文</a>` : ""}</small></div>`).join("");
const insAnswer = (r) => (r.insufficient ? `<div class="mut">${esc(r.message)}</div>` : r.answer.map((c, ci) => `<div class="ins-claim"><div data-selk="c:${ci}">${esc(c.text)}</div>${insEvidence(c.evidence)}</div>`).join(""));

async function insightsHub(p, my) {
  const team = STAFF(), tabs = team ? INS_TABS : [["b", "看板"]];   // a client sees only the focus board (aggregated, de-identified)
  let tab = sessionStorage.getItem("itab") || (team ? "r" : "b"); if (!tabs.some((t) => t[0] === tab)) tab = tabs[0][0];
  let themesBackfilled = false;   // one automatic theme-label backfill per page load, before the table is drawn
  shell(`${team ? `<div class="hubtabs sub" id="itabs" role="tablist" aria-label="洞察">${team ? `<span class="l2grp">洞察 · AI 分析</span>` : ""}${tabs.map(([k, n]) => `<a href="#" role="tab" data-k="${k}">${n}</a>`).join("")}</div>` : ""}<div id="ipanel" class="ins ins-ws"></div>`, { active: "insights", p });
  document.body.classList.add("rfmode", "pgw");   // the page itself does not scroll: each tab's content scrolls inside its own box
  const fit = () => { const b = $("#ipanel"); if (b && b.offsetParent) b.style.height = Math.max(320, innerHeight - b.getBoundingClientRect().top - 18) + "px"; };
  fit(); pageOn("fit", window, "resize", fit);
  const drawTabs = () => document.querySelectorAll("#itabs a").forEach((a) => { const on = a.dataset.k === tab; a.classList.toggle("on", on); a.setAttribute("aria-selected", on); });
  let selOff = null;   // takes the text-selection helper of the open tab down (see chatsel.js)
  const show = async (k) => { if (selOff) { selOff(); selOff = null; } tab = k; sessionStorage.setItem("itab", k); drawTabs(); const box = $("#ipanel"); box.innerHTML = `<div class="mut" style="padding:16px">加载中…</div>`; try { await ({ r: respondents, p: report, q: questions, v: reels, b: board, c: chat })[k](box); } catch (e) { if (!stale(my)) box.innerHTML = `<div class="card empty"><b>${esc(e.message)}</b></div>`; } };
  document.querySelectorAll("#itabs a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.k); }));
  show(tab);

  /* ---------- 受访者数据 ---------- */
  async function respondents(box) {
    // 列名含「· 主题」的列：已有逐题回答但没标签时，先接模型补齐，再画表（只补标签，回答原文一律不动）
    if (team && !themesBackfilled) {
      themesBackfilled = true;
      try {
        const r = await api(`/projects/${p.id}/insights/themes/backfill`, { method: "POST" });
        if (r && r.updated) toast(`已补齐 ${r.labels} 道题的主题`);
      } catch (_) { /* 主题补不上也要正常显示表格 */ }
    }
    const d = await api(`/projects/${p.id}/insights/overview`); if (stale(my)) return;
    const k = d.kpi, rows = d.rows.filter((r) => r.status === "ended"), showTags = d.rows.length ? "tags" in d.rows[0] : team;   // only finished interviews are listed
    const SEP = "、";
    const statusLabel = (r) => (STATUS[r.status] || [r.status])[0];
    const dur = (r) => (r.minutes != null ? `${r.minutes} 分` : "—");
    /* the columns: the fixed ones, then one per questionnaire question and per outline question (the server says which are choices and which are open text) */
    const base = [{ id: "start", group: "base", label: "开始时间", type: "time" }, { id: "dur", group: "base", label: "访谈时长", type: "num" }, { id: "status", group: "base", label: "状态", type: "choice", multi: false, options: [] },
      ...(showTags ? [{ id: "tags", group: "base", label: "标签", type: "choice", multi: true, options: [] }] : []), { id: "ins", group: "base", label: "洞察 / 片段", type: "num" }];
    const cols = [...base, ...(d.columns || [])];
    const GROUPS = { base: "基本信息", screen: "问卷答案", answer: "访谈回答（按“访谈”页大纲的问题）" };
    const valueOf = (r, c) => {
      if (c.id === "start") return r.started_at || "";
      if (c.id === "dur") return r.minutes != null ? String(r.minutes) : "";
      if (c.id === "status") return statusLabel(r);
      if (c.id === "tags") return r.tags || [];
      if (c.id === "ins") return `${r.insights || 0} / ${r.clips || 0}`;
      const v = (r.cells || {})[c.id];
      return v == null ? "" : v;
    };
    const asList = (v, c) => (Array.isArray(v) ? v : v === "" ? [] : c.multi ? String(v).split(SEP) : [String(v)]);
    const optionsOf = (c) => { const seen = new Map(); (c.options || []).forEach((o) => seen.set(o, 0)); rows.forEach((r) => asList(valueOf(r, c), c).forEach((v) => seen.set(v, (seen.get(v) || 0) + 1))); return [...seen.entries()]; };
    const filterable = (c) => c.type === "choice" || (c.type === "text");
    const hideKey = "ins_hide_" + p.id;
    let hidden = new Set(); try { hidden = new Set(JSON.parse(sessionStorage.getItem(hideKey) || "[]")); } catch { /* none */ }
    const F = {};     // column id -> { sel: Set } for choices, { q: "text" } for open text
    let gq = "";
    const visible = () => cols.filter((c) => !hidden.has(c.id));
    const matches = (r) => {
      if (gq && ![r.code, ...Object.values(r.cells || {}).flat(), ...(r.tags || []), ...r.summary].join(" ").toLowerCase().includes(gq)) return false;
      return Object.entries(F).every(([id, f]) => {
        const c = cols.find((x) => x.id === id); if (!c) return true; const v = valueOf(r, c);
        if (f.sel) return !f.sel.size || asList(v, c).some((x) => f.sel.has(x));
        return !f.q || String(Array.isArray(v) ? v.join(" ") : v).toLowerCase().includes(f.q);
      });
    };
    const activeFilters = () => Object.values(F).filter((f) => (f.sel && f.sel.size) || f.q).length;
    const cell = (r, c) => {
      if (c.id === "start") return `${r.started_at ? when(r.started_at) : "—"}${r.scheduled ? ` <span class="mut sm">预约</span>` : ""}`;
      if (c.id === "dur") return dur(r);
      if (c.id === "status") { const [t, cl] = STATUS[r.status] || [r.status, ""], g = r.progress; return g && g.answered < g.asked ? `<span class="tag o">中途结束 · ${g.answered}/${g.asked} 题</span>` : `<span class="tag ${cl}">${t}</span>`; }
      if (c.id === "tags") return (r.tags || []).map((t) => `<span class="lch ${lcolor(t)}">${esc(t)}</span>`).join("") || `<span class="mut">—</span>`;
      if (c.id === "ins") return `${r.insights || "—"} / ${r.clips || "—"}${r.review ? ` <span class="tag bad">${r.review} 待复核</span>` : ""}`;
      const v = valueOf(r, c);
      if (c.group === "answer" && c.type === "text") {
        const q = r.qa && r.qa[c.id];
        if (v) return `<div class="ins-cl" tabindex="0" role="button" data-ttl="${esc(r.code + " · " + (c.no || "") + " · " + c.label)}" data-tip="${esc(v)}">${esc(v)}</div>${q && q.followups.length ? `<button type="button" class="ins-thr" data-thr="${c.id}">+${q.followups.length} 条追问</button>` : ""}`;
        if (r.transcript_state === "waiting") return `<span class="mut">待转写</span>`;
        if (!r.qa) return `<span class="mut">${r.transcript_state === "ready" ? "还没整理" : "—"}</span>`;
        return q && q.state === "empty" ? `<span class="mut">受访者没有回答</span>` : `<span class="tag o" title="有转写，但没认出主持人哪句话问的是这一题（主持人换了说法，或语言和大纲不一样）">没匹配到这一题</span>`;
      }
      if (c.type === "choice") {
        const list = asList(v, c);
        if (list.length) return list.map((x) => `<span class="tag">${esc(x)}</span>`).join("");
        if (/^t\d+$/.test(c.id) && /·\s*主题/.test(c.label || "")) {
          const q = r.qa && r.qa["a" + c.id.slice(1)];
          if (r.transcript_state === "waiting") return `<span class="mut">待转写</span>`;
          if (q && q.state === "empty") return `<span class="mut">受访者没有回答</span>`;
          if (q && q.state === "unmatched") return `<span class="mut">没匹配到这一题</span>`;
          return `<span class="mut">待整理主题</span>`;
        }
        return `<span class="mut">—</span>`;
      }
      return v ? `<div class="ins-cl" tabindex="0" role="button" data-ttl="${esc(r.code + " · " + c.label)}" data-tip="${esc(String(v))}">${esc(String(v))}</div>` : `<span class="mut">—</span>`;
    };
    const tw = (t) => [...String(t)].reduce((a, ch) => a + (ch.charCodeAt(0) > 255 ? 14 : 7.5), 0);
    const FIXED_PX = { start: 130, dur: 96, status: 150, tags: 190, ins: 124 };
    const colPx = (c) => {
      if (FIXED_PX[c.id]) return FIXED_PX[c.id];
      if (c.type === "choice") return Math.min(240, Math.max(130, Math.round(Math.max(0, ...optionsOf(c).map(([v]) => tw(v))) + 40)));
      const vals = rows.map((r) => valueOf(r, c)).flat().map(String).filter(Boolean);
      return Math.min(320, Math.max(160, Math.round((vals.reduce((a, v) => a + tw(v), 0) / (vals.length || 1)) / 2.4)));
    };
    const widthOf = (c) => (c.group === "answer" && c.type === "text" ? "w2" : c.type === "choice" || c.id === "tags" ? "w3" : "w1");
    box.innerHTML = `<div class="ins-kp"><div class="rd-k"><span>已完成访谈</span><b>${k.completed}</b><small class="mut">${k.upcoming ? `另有 ${k.upcoming} 场待开始` : "没有待开始的场次"}${k.live ? ` · ${k.live} 场进行中` : ""}</small></div>
        <div class="rd-k"><span>平均时长</span><b>${k.avg_minutes != null ? k.avg_minutes + " 分钟" : "—"}</b></div><div class="rd-k ok"><span>洞察通过率</span><b>${k.approval_rate != null ? k.approval_rate + "%" : "—"}</b><small class="mut">已通过 ${k.approved} / 共 ${k.insights} 条</small></div></div>
      <div class="row ins-tool"><input id="isearch" type="search" placeholder="搜索（所有列）" aria-label="搜索受访者" style="max-width:260px"><span class="sp"></span><span class="sm mut" id="igen-st" role="status" aria-live="polite"></span>
        ${d.can_generate ? `<button type="button" id="igen">整理访谈回答</button>` : ""}<button type="button" id="iview">视图与筛选</button>${ME.role === "observer" ? "" : `<button type="button" id="xw">导出 Word</button><button type="button" id="xc">导出 CSV</button>`}</div>
      ${d.can_generate ? insAiNotice().replace("将提问和相关转写发送给", "整理时，主持人提问和受访者回答会发送给") : ""}
      ${rows.length ? `<div class="ins-wrap" id="iwrap"><div class="ins-sc"><div class="ins-sx" id="isx"><table class="ins-tbl2" id="itbl"></table></div></div><div class="ins-pop" id="ipop" hidden></div></div><div class="ins-ft"><span id="icount"></span></div>`
        : `<div class="card empty"><b>还没有已结束的访谈。</b><span>访谈结束后，每位受访者的数据会列在这里。</span></div>`}`;
    if (ME.role !== "observer" && $("#xw")) { $("#xw").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=docx`, `${p.name}-洞察.docx`); $("#xc").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=csv`, `${p.name}-洞察.csv`); }
    const gen = $("#igen");
    if (gen) gen.onclick = guard(async () => {   // the answers of every finished interview, one after the other (each call reads one transcript)
      // 转写段已不在库里时不能重算：那会把已存的逐题回答清空。主题标签由上面的 backfill 单独补。
      const todo = rows.filter((r) => r.status === "ended" && !r.purged && r.kind !== "group" && r.transcript_state === "ready"), st = $("#igen-st"); gen.disabled = true; let n = 0, skip = "";
      if (!todo.length) { gen.disabled = false; toast("没有可整理的转写，已保留现有回答；主题标签会自动补齐", true); return; }
      for (const r of todo) { st.textContent = `整理中 ${n}/${todo.length}…`; try { const out = await api(`/interviews/${r.id}/answers/generate`, { method: "POST" }); skip = out.themes_skipped_reason || skip; } catch (e) { toast(e.message, true); break; } n++; }
      st.textContent = `已整理 ${n}/${todo.length}`; if (skip) toast(skip, true); await show("r");
    });
    if (!rows.length) return;
    const wrap = $("#iwrap"), sx = $("#isx"), tbl = $("#itbl"), pop = $("#ipop");
    const fit = () => { const b = $(".ins-sc"), pane = $("#ipanel"); if (!b || !pane || !b.offsetParent) return; b.style.height = Math.max(200, pane.getBoundingClientRect().bottom - b.getBoundingClientRect().top - 34) + "px"; };
    pageOn("ins-tbl-fit", window, "resize", fit);
    let shown = [], open = -1;
    const closeCard = () => { open = -1; pop.hidden = true; tbl.querySelectorAll("tr.hv").forEach((x) => x.classList.remove("hv")); };
    const card = (r) => {
      const c = r.card, steps = c.steps.map((on, i) => `<i class="${on ? "" : "n"}"></i>${i < 5 ? `<u class="${on && c.steps[i + 1] ? "" : "n"}"></u>` : ""}`).join("");
      return `<h5><b>${esc(r.code)}</b><span class="mut">${esc(c.loc)}</span><span class="sp"></span><a class="btn s" href="#/i/${r.id}">查看详情 →</a></h5>
        <div class="ins-g2">${c.source !== undefined ? `<div><span>来源</span> ${esc(c.source || "—")}</div><div><span>主持</span> ${c.moderation === "ai" ? "AI 主持" : "真人" + (c.moderator ? " · " + esc(c.moderator) : "")}</div>` : ""}
          <div><span>${r.scheduled ? "预约" : "开始"}</span> ${r.started_at ? when(r.started_at) : "—"}</div><div><span>时长</span> ${dur(r)}</div><div><span>字数</span> ${c.words ? c.words.toLocaleString() + " 字" : "—"}</div>${c.device !== undefined ? `<div><span>设备</span> ${esc(c.device)}</div>` : ""}</div>
        <div class="ins-tl" aria-hidden="true">${steps}</div><div class="ins-tll">${c.step_names.map((s, i) => `<span class="${c.steps[i] ? "" : "mut"}">${s}</span>`).join("")}</div>
        ${c.talk[1] ? `<div class="sm">说话占比　<span class="mut">主持人 ${c.talk[0]}% · 受访者 ${c.talk[1]}%</span></div><div class="ins-sh" role="img" aria-label="主持人 ${c.talk[0]}%，受访者 ${c.talk[1]}%"><i style="width:${c.talk[0]}%"></i><i class="b" style="width:${c.talk[1]}%"></i></div>` : ""}
        <div class="sm"><span class="mut">洞察</span> ${r.insights ? `${r.insights} 条（通过 ${r.approved}${r.review ? `，待复核 ${r.review}` : ""}）` : "—"}　<span class="mut">片段</span> ${r.clips ? r.clips + " 个" : "—"}</div>
        ${c.quotes.map((x) => `<div class="ins-q">“${esc(x.text)}” <small>${insClock(x.at_ms)}</small></div>`).join("")}`;
    };
    const openCard = (i, anchor) => {   // beside the code cell that was pointed at, kept inside the table's box
      const trEl = tbl.tBodies[0].children[i]; if (!trEl) return; open = i; tbl.querySelectorAll("tr.hv").forEach((x) => x.classList.remove("hv")); trEl.classList.add("hv");
      pop.innerHTML = card(shown[i]); pop.hidden = false;
      const wr = wrap.getBoundingClientRect(), a = (anchor || trEl).getBoundingClientRect();
      pop.style.left = Math.max(8, Math.min(wr.width - pop.offsetWidth - 8, a.right - wr.left + 6)) + "px";
      pop.style.top = Math.max(0, Math.min(a.bottom - wr.top + 4, wrap.offsetHeight - pop.offsetHeight)) + "px";
    };
    const pxStyle = (c) => { const w = colPx(c); return `width:${w}px;min-width:${w}px;max-width:${w}px`; };
    const header = () => {
      const vs = visible();
      const fi = (c) => (filterable(c) || c.id === "status" || c.id === "tags" ? `<button type="button" class="fi${F[c.id] && ((F[c.id].sel && F[c.id].sel.size) || F[c.id].q) ? " on" : ""}" data-f="${c.id}" aria-label="筛选：${esc(c.label)}">☰</button>` : "");
      return `<thead><tr class="c"><th class="pin">编号</th>${vs.map((c) => `<th class="${widthOf(c)}" style="${pxStyle(c)}"><div class="thc"><span class="tt" data-ttl="" data-tip="${esc((c.no ? c.no + " · " : "") + c.label)}">${c.no ? `<b class="qno">${esc(c.no)}</b> ` : ""}${esc(c.label)}</span>${fi(c)}</div></th>`).join("")}</tr></thead>`;
    };
    const draw = () => {
      const vs = visible(); shown = rows.filter(matches); closeCard(); rspSetList(shown.map((r) => r.id));
      tbl.innerHTML = header() + `<tbody>${shown.map((r, i) => `<tr data-i="${i}"><td class="pin"><a href="#/i/${r.id}"><b>${esc(r.code)}</b></a></td>${vs.map((c) => `<td class="${widthOf(c)}" style="${pxStyle(c)}" data-l="${esc(c.label)}">${cell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      tbl.style.width = 92 + vs.reduce((a, c) => a + colPx(c), 0) + "px";
      $("#icount").textContent = `共 ${rows.length} 人 · 当前显示 ${shown.length} 人${activeFilters() ? ` · ${activeFilters()} 个筛选` : ""}`;
      tbl.querySelectorAll("td.pin").forEach((td) => { const i = +td.parentElement.dataset.i; td.onmouseenter = () => { if (matchMedia("(hover:hover)").matches) openCard(i, td); }; td.onclick = (e) => { if (e.target.closest("a") || matchMedia("(hover:hover)").matches) return; open === i ? closeCard() : openCard(i, td); }; });
      tbl.querySelectorAll(".ins-thr").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const r = shown[+b.closest("tr").dataset.i], c = cols.find((x) => x.id === b.dataset.thr); if (r && c) showThread(b, r, c); }));
      tbl.querySelectorAll(".ins-cl,.thc .tt").forEach((c) => {
        const cut = () => c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1;
        c.onmouseenter = () => { if (matchMedia("(hover:hover)").matches && cut()) showTip(c); };
        c.onmouseleave = () => { if (matchMedia("(hover:hover)").matches) hideTip(); };
        c.onfocus = () => { if (cut()) showTip(c); }; c.onblur = hideTip;
        c.onclick = (e) => { if (matchMedia("(hover:hover)").matches) return; e.preventDefault(); tip.hidden && cut() ? showTip(c) : hideTip(); };
      });
      tbl.querySelectorAll(".fi").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openFilter(b, cols.find((x) => x.id === b.dataset.f)); }));
      fit();
    };
    const tip = document.createElement("div"); tip.className = "ins-tip"; tip.hidden = true; tip.setAttribute("role", "tooltip"); wrap.appendChild(tip);
    const hideTip = () => { tip.hidden = true; };
    const showThread = (btn, r, c) => {   // this person's own follow-ups under one question
      const q = r.qa[c.id], first = (r.cells || {})[c.id] || "";
      tip.innerHTML = `<small>${esc(r.code)} · ${esc(c.no || "")} · ${esc(c.label)}</small><div class="th-a">${esc(first)}</div>${q.followups.map((f) => `<div class="th-ask">追问${f.at_ms != null ? " · " + insClock(f.at_ms) : ""}　${esc(f.ask)}</div><div class="th-a">${esc(f.a)}</div>`).join("")}<a href="#/i/${r.id}">看原文</a>`;
      tip.hidden = false;
      const wr = wrap.getBoundingClientRect(), a = btn.getBoundingClientRect();
      tip.style.left = Math.max(4, Math.min(wr.width - tip.offsetWidth - 4, a.left - wr.left)) + "px";
      tip.style.top = (a.bottom - wr.top + 4 + tip.offsetHeight > wrap.offsetHeight ? Math.max(0, a.top - wr.top - tip.offsetHeight - 4) : a.bottom - wr.top + 4) + "px";
    };
    const showTip = (el) => {   // the whole text of a cut-off cell or column title, beside it
      tip.innerHTML = `${el.dataset.ttl ? `<small>${esc(el.dataset.ttl)}</small>` : ""}${esc(el.dataset.tip || "")}`; tip.hidden = false;
      const wr = wrap.getBoundingClientRect(), a = el.getBoundingClientRect();
      tip.style.left = Math.max(4, Math.min(wr.width - tip.offsetWidth - 4, a.left - wr.left)) + "px";
      tip.style.top = (a.bottom - wr.top + 4 + tip.offsetHeight > wrap.offsetHeight ? Math.max(0, a.top - wr.top - tip.offsetHeight - 4) : a.bottom - wr.top + 4) + "px";
    };
    wrap.addEventListener("click", (e) => { if (!e.target.closest(".ins-thr,.ins-tip")) hideTip(); });
    wrap.onmouseleave = () => { if (matchMedia("(hover:hover)").matches) { closeCard(); hideTip(); } };
    sx.onscroll = () => { closeCard(); hideTip(); };
    /* a column's filter: choices (and labels) get a tick list with counts, 全选 / 清除; an open question only gets a search box */
    function openFilter(btn, c) {
      if (!filterable(c) && c.id !== "status" && c.id !== "tags") return;
      if (c.type === "text") {
        const pp = rmPop(btn, `<div class="lb">${esc(c.label)}</div><input class="mss" placeholder="搜索回答里的词" aria-label="搜索${esc(c.label)}" value="${esc((F[c.id] && F[c.id].q) || "")}">`), inp = pp.querySelector("input");
        inp.oninput = () => { F[c.id] = { q: inp.value.trim().toLowerCase() }; draw(); btn.classList.toggle("on", !!F[c.id].q); }; inp.focus(); return;
      }
      const all = optionsOf(c), pp = rmPop(btn, `<div class="lb">${esc(c.label)}${c.multi ? "（多选题：满足任一）" : ""}</div><input class="mss" placeholder="搜索" aria-label="搜索${esc(c.label)}"><div class="rmfa"><button type="button" class="lnk" data-all>全选</button><button type="button" class="lnk" data-none>清除</button></div><div class="rmopts rmfl"></div>`);
      const list = pp.querySelector(".rmfl"), inp = pp.querySelector("input"), cur = () => (F[c.id] = F[c.id] || { sel: new Set() }).sel;
      const fill = () => { const q = inp.value.trim().toLowerCase(), os = all.filter(([v]) => !q || v.toLowerCase().includes(q));
        list.innerHTML = os.length ? os.map(([v, n]) => `<label class="rmfo"><input type="checkbox" value="${esc(v)}"${cur().has(v) ? " checked" : ""}><span>${esc(v)}</span><em>${n}</em></label>`).join("") : `<div class="sm mut" style="padding:6px 8px">${all.length ? "没有匹配的" : "还没有可选的内容"}</div>`;
        list.querySelectorAll("input").forEach((i) => (i.onchange = () => { i.checked ? cur().add(i.value) : cur().delete(i.value); draw(); })); };
      pp.querySelector("[data-all]").onclick = () => { all.filter(([v]) => !inp.value.trim() || v.toLowerCase().includes(inp.value.trim().toLowerCase())).forEach(([v]) => cur().add(v)); fill(); draw(); };
      pp.querySelector("[data-none]").onclick = () => { cur().clear(); fill(); draw(); };
      inp.oninput = fill; fill(); inp.focus();
    }
    $("#iview").onclick = (e) => {   // which columns to show, and one place to clear every filter
      const pp = rmPop(e.currentTarget, `<div class="lb">显示哪些列</div><div class="rmopts rmfl">${cols.map((c) => `<label class="rmfo"><input type="checkbox" value="${c.id}"${hidden.has(c.id) ? "" : " checked"}><span>${esc(c.label)}</span></label>`).join("")}</div><div class="rmfa"><button type="button" class="lnk" data-clr>清除全部筛选</button></div>`);
      pp.querySelectorAll("input").forEach((i) => (i.onchange = () => { i.checked ? hidden.delete(i.value) : hidden.add(i.value); try { sessionStorage.setItem(hideKey, JSON.stringify([...hidden])); } catch { /* none */ } $("#iview").textContent = hidden.size ? `视图与筛选 · ${hidden.size} 列隐藏` : "视图与筛选"; draw(); }));
      pp.querySelector("[data-clr]").onclick = () => { Object.keys(F).forEach((x) => delete F[x]); gq = ""; $("#isearch").value = ""; draw(); };
    };
    if (hidden.size) $("#iview").textContent = `视图与筛选 · ${hidden.size} 列隐藏`;
    $("#isearch").oninput = (e) => { gq = e.target.value.trim().toLowerCase(); draw(); };
    draw();
  }

  /* ---------- 报告：带框的分析卡片 + 文字解读；点图表，右侧看对应受访者的回答；分析块可开关、排序 ---------- */
  async function report(box) {
    const d = await api(`/projects/${p.id}/report/analysis`); if (stale(my)) return;
    const rm = d.meta || {};   // 项目真实设定（国家/语言/研究目标），不再显示 SG / zh 这类原始码
    const team = STAFF(), wantApp = !team || d.blocks.some((b) => b.id === "appendix" && b.on);
    const oldBlocks = wantApp ? (await api(`/projects/${p.id}/report?format=json`).catch(() => ({ blocks: [] }))).blocks : [];
    if (stale(my)) return;
    let sel = null;   // { c: card id, i: bar index }
    const noteHtml = (n) => `<div class="rp-note"><b>解读：</b>${esc(n.t)}${n.ai ? ` <span class="tag">小星星助手的判断</span>` : ""}${n.stale ? ` <span class="tag o">数据已更新</span>` : ""}</div>`;
    const cardHtml = (c) => `<div class="rp-card${sel && sel.c === c.id ? " sel" : ""}" data-c="${esc(c.id)}"><div class="rp-t"><b>${esc(c.title)}</b></div><div class="rp-d">${esc(c.desc)}</div>
      ${c.bars.map((b, i) => `<div role="button" tabindex="0" class="rp-row${sel && sel.c === c.id && sel.i === i ? " on" : ""}" data-c="${esc(c.id)}" data-i="${i}"><span class="rp-h"><span class="rp-label">${esc(b.label)}</span><span class="rp-count">${b.k} / ${b.n}</span></span><span class="rp-tr"><span class="rp-f" style="width:${b.n ? Math.round(b.k * 100 / b.n) : 0}%"></span></span><span class="rp-chevron" aria-hidden="true">›</span></div>`).join("")}</div>${noteHtml(c.note)}`;
    const appendix = () => {
      let html = "", list = false;
      const closeList = () => { if (list) { html += "</ul>"; list = false; } };
      oldBlocks.slice(2).forEach((b) => {
        if (b[0] !== "bullet") closeList();
        if (b[0] === "h1") html += `<h4 class="ins-h1 rp-app-h1">${esc(b[1])}</h4>`;
        else if (b[0] === "h2") html += `<h5 class="ins-h2 rp-app-h2">${esc(b[1])}</h5>`;
        else if (b[0] === "p") html += /^受访者共\s*\d+\s*人/.test(b[1])
          ? `<p class="rp-app-stat">${esc(b[1])}</p>` : `<p class="rp-app-p">${esc(b[1])}</p>`;
        else if (b[0] === "note") html += `<p class="rp-app-note">${esc(b[1])}</p>`;
        else if (b[0] === "bullet") { if (!list) { html += `<ul class="rp-app-list">`; list = true; } html += `<li>${esc(b[1])}</li>`; }
        else if (b[0] === "quote") html += `<div class="rp-app-quote">${esc(b[1])}</div>`;
        else if (b[0] === "table") html += `<div class="rp-app-tbl"><table class="tbl cols${b[1].length}"><thead><tr>${b[1].map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${b[2].map((r) => `<tr>${r.map((x) => `<td>${esc(x)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      });
      return list ? html + "</ul>" : html;
    };
    const themeCard = d.cards.find((c) => c.id === "themes"), topTheme = themeCard && themeCard.bars[0];
    const docHtml = d.cards.length ? d.cards.map((c, i) => `<section class="rp-section"><div class="rp-eyebrow"><i>${String(i + 2).padStart(2, "0")}</i>${c.kind === "themes" ? "主题分布" : "分人群比较"}</div>${cardHtml(c)}</section>`).join("")
      : `<div class="rp-empty"><b>还没有可分析的数据</b><p>访谈结束并整理出回答的主题后，图表会出现在这里。</p></div>`;
    const detail = () => {
      const c = d.cards.find((x) => x.id === sel.c), b = c.bars[sel.i];
      return `<div class="row"><b>${esc(b.label)} · ${b.k} 位的回答</b><span class="sp"></span><button type="button" class="lnk" id="rpx">关闭</button></div>
        ${b.people.length ? b.people.map((x) => `<div class="rp-pa"><div class="rp-ph"><b>${esc(x.code)}</b><small>${esc(x.group || "")} · <a href="#/i/${x.interview_id}">看原文</a></small></div>
          ${x.items.map((it) => `<div class="rp-q"><span class="mut sm">${esc(it.q)}${it.at_ms ? " · " + insClock(it.at_ms) : ""}</span><div>“${esc(it.a)}”</div>${it.themes.map((t) => `<span class="tag">${esc(t)}</span>`).join(" ")}</div>`).join("")}</div>`).join("")
          : `<div class="mut sm">没有可显示的回答</div>`}<button type="button" class="lnk" id="rpall">在受访者数据里查看全部</button>`;
    };
    const blocksPanel = () => `<div class="row"><b>这份报告的分析块</b></div>${d.blocks.map((b, i) => `<div class="rp-bk"><label class="chk"><input type="checkbox" data-bk="${esc(b.id)}" ${b.on ? "checked" : ""}><span><b>${esc(b.title)}</b><small>${esc(b.source)}</small></span></label>
        <span class="rp-mv"><button type="button" class="s" data-up="${i}" aria-label="上移" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" class="s" data-dn="${i}" aria-label="下移" ${i === d.blocks.length - 1 ? "disabled" : ""}>↓</button></span></div>`).join("")}
      <button type="button" class="rp-add" id="rpadd">＋ 自己加一块：写一句想看什么</button>`;
    box.innerHTML = `<article class="rp-editorial" aria-label="研究报告">
      <header class="rp-hero"><div><div class="rp-eyebrow">研究报告 · ${esc(rm.country || p.market || "")} · ${esc(rm.language || "")} · 深访</div><h1 class="ins-rt">${esc(p.name)}</h1>
        <p class="rp-meta">${d.n} 位受访者 · 数据截至已结束且可分析的访谈${rm.topic ? ` · 受访者话题「${esc(rm.topic)}」` : ""}${rm.source ? ` · 信息同步自${esc(rm.source)}` : ""}</p><div class="rp-eyebrow"><i>01</i>总体结论</div>
        <p class="rp-summary">${esc(d.summary.t)}${d.summary.ai ? ` <span class="tag">小星星助手的判断</span>` : ""}</p>
        <div class="rp-kpis"><div><b>${d.n}</b><span>报告样本</span></div><div><b>${d.cards.length}</b><span>已开启分析图表</span></div><div><b>${topTheme ? `${topTheme.k}/${topTheme.n}` : "—"}</b><span>${topTheme ? `最高提及 · ${esc(topTheme.label)}` : "暂无主题统计"}</span></div></div></div>
        <div><div class="rp-actions">${team ? `<button type="button" id="rpimp">导入报告模版</button>` : ""}<button type="button" class="p" id="rpdl">下载 Word</button></div>
        ${team ? `<div class="rp-blocks" id="rpblocks"></div>` : ""}</div></header>
      <div class="rp-body"><div class="rp-sections">${docHtml}</div>${team ? `<aside class="rp-side" id="rpside" aria-live="polite"></aside>` : ""}</div>
      ${oldBlocks.length ? `<details class="rp-appendix"><summary><span class="rp-app-badge">附录</span><span class="rp-app-title">研究背景、目的与目标、方法与样本</span><span class="rp-app-chev" aria-hidden="true">⌄</span></summary><div class="rp-app-body">${appendix()}</div></details>` : ""}</article>`;
    const ids = d.blocks.map((b) => b.id), onIds = () => d.blocks.filter((b) => b.on).map((b) => b.id);
    const save = guard(async (order, on, off) => { await api(`/projects/${p.id}/report/template`, { method: "PUT", body: { order, on, off } }); await report(box); });
    const wire = () => {
      const x = $("#rpx"); if (x) x.onclick = () => { sel = null; box.querySelectorAll(".rp-card.sel,.rp-row.on").forEach((e) => e.classList.remove("sel", "on")); side(); };
      const all = $("#rpall"); if (all) all.onclick = () => show("r");
      box.querySelectorAll("[data-bk]").forEach((c) => (c.onchange = () => { const s = new Set(onIds()); c.checked ? s.add(c.dataset.bk) : s.delete(c.dataset.bk); save(ids, ids.filter((i) => s.has(i)), ids.filter((i) => !s.has(i))); }));
      const move = (i, j) => { const o = ids.slice(); [o[i], o[j]] = [o[j], o[i]]; save(o, onIds(), ids.filter((k) => !onIds().includes(k))); };
      box.querySelectorAll("[data-up]").forEach((b) => (b.onclick = () => move(+b.dataset.up, +b.dataset.up - 1)));
      box.querySelectorAll("[data-dn]").forEach((b) => (b.onclick = () => move(+b.dataset.dn, +b.dataset.dn + 1)));
      const add = $("#rpadd"); if (add) add.onclick = () => reportAsk(box);
    };
    const side = () => { const el = $("#rpside"); if (!el) return; el.innerHTML = sel ? detail() : `<b>查看原话</b><p class="mut sm">点左侧图表中的主题或人群，这里显示对应受访者的原话。</p>`; const blocks = $("#rpblocks"); if (blocks) blocks.innerHTML = blocksPanel(); wire(); };
    box.querySelectorAll(".rp-row").forEach((r) => { const go = () => { if (!team) return; sel = { c: r.dataset.c, i: +r.dataset.i }; box.querySelectorAll(".rp-card").forEach((e) => e.classList.toggle("sel", e.dataset.c === sel.c)); box.querySelectorAll(".rp-row").forEach((e) => e.classList.toggle("on", e === r)); side(); }; r.onclick = go; r.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }; });
    side();
    $("#rpdl").onclick = insDownload(p, `/api/projects/${p.id}/report?format=docx`, `${p.name}-研究报告.docx`);
    const imp = $("#rpimp"); if (imp) imp.onclick = () => reportTemplate(d, box);
  }
  /** One more block written by hand: the words go to the AI service when a model is used. */
  function reportAsk(box) {
    const mod = modal(`<h2>自己加一块</h2><label class="f"><span>想看什么</span><textarea id="rfocus" rows="3" maxlength="600" placeholder="价格敏感度，按家庭月收入比较"></textarea></label>${insAiNotice().replace("你的提问和相关的访谈转写会发送给", "你写的内容会发送给")}
      <div class="row"><button class="p" id="rfok">挑出对应的图表</button><button type="button" id="rfno">取消</button></div>`);
    $("#rfno", mod).onclick = () => mod.remove();
    $("#rfok", mod).onclick = guard(async () => { const t = $("#rfocus", mod).value.trim(); if (!t) throw new Error("先写一句想看什么");
      const r = await api(`/projects/${p.id}/report/focus`, { method: "POST", body: { text: t, add: true } }); mod.remove(); toast(r.picked.length ? `已加入 ${r.picked.length} 张图表` : "没有找到对应的问卷题或主题，换个说法试试", !r.picked.length); await report(box); });
  }
  function reportTemplate(d, box) {
    const mod = modal(`<h2>导入报告模版</h2><label class="f"><span>这个项目最想看清什么</span><textarea id="rfocus" rows="3" maxlength="600">${esc(d.focus || "")}</textarea></label>${insAiNotice().replace("你的提问和相关的访谈转写会发送给", "你写的内容会发送给")}
      <div class="row"><button type="button" id="rtdl">下载系统默认模版修改</button><label class="btn" id="rtup" tabindex="0">上传我改好的 .docx<input type="file" id="rtfile" accept=".docx" hidden></label></div>
      <div class="row"><button class="p" id="rtok">用这个模版生成</button><button type="button" id="rtno">取消</button></div>`);
    $("#rtno", mod).onclick = () => mod.remove();
    $("#rtdl", mod).onclick = insDownload(p, `/api/projects/${p.id}/report/template/file`, `${p.name}-报告模版.docx`);
    $("#rtfile", mod).onchange = guard(async (e) => { const f = e.target.files[0]; if (!f) return;
      const r = await fetch(`/api/projects/${p.id}/report/template/import`, { method: "POST", headers: { Authorization: "Bearer " + store.get("tt_token"), "Content-Type": "application/octet-stream" }, body: f });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || "导入失败");
      mod.remove(); toast(j.unmatched && j.unmatched.length ? `已导入。这几章没找到对应的数据：${j.unmatched.join("、")}` : "已按你的模版排好分析块", !!(j.unmatched && j.unmatched.length)); await report(box); });
    $("#rtok", mod).onclick = guard(async () => { const t = $("#rfocus", mod).value.trim(), b = $("#rtok", mod); b.disabled = true; b.textContent = "正在生成…";
      try { if (t && t !== (d.focus || "")) await api(`/projects/${p.id}/report/focus`, { method: "POST", body: { text: t, add: false } });
        if (d.ai.ready) await api(`/projects/${p.id}/report/generate`, { method: "POST" }); else toast(d.ai.mock ? "演示模式：解读用统计句子" : d.ai.message, true);
      } finally { mod.remove(); await report(box); } });
  }

  /* ---------- 问题分析：问题列表就是“访谈”页大纲的那一版（语言 × 访谈形式），两处共用同一个选择 ---------- */
  const done = {};  // question text -> the answer, kept while the page is open
  async function questions(box) {
    const vkey = "ol_v_" + p.id;   // the same key the 访谈 page keeps its open version in: change it in either place and the other follows
    const fetchOutline = async (k) => { try { return await api(`/projects/${p.id}/outline${k ? `?v=${encodeURIComponent(k)}` : ""}`); } catch (e) { if (k) return fetchOutline(""); throw e; } };
    // 先读项目当前最新的主版本；浏览器里缓存的旧版本 key 只有在仍属于项目设定时才沿用。
    let o = await fetchOutline(""); if (stale(my)) return;
    const saved = sessionStorage.getItem(vkey);
    if (saved && saved !== o.base && (o.versions || []).some((x) => x.key === saved && x.exists !== false)) {
      o = await fetchOutline(saved); if (stale(my)) return;
    }
    const setup = o.setup || {}, scr = o.screener || {};
    const scrLabel = `甄别问卷${scr.version ? ` · v${scr.version}` : ""}${scr.status === "published" ? " · 已发布" : ""}`;
    const qsOf = () => (o.summary ? o.summary.parts.flatMap((x) => x.questions.filter((y) => !y.cover).map((y) => y.text)) : []);
    // 题目跟着「当前最新甄别问卷」+「当前打开的那版访问大纲」，切版本时一起跟着变。
    const itemsOf = () => {
      const vn = ((o.versions || []).find((x) => x.key === o.v) || {}).name || "当前版本";
      return [...(scr.questions || []).map((t) => ({ text: t, group: scrLabel })),
              ...qsOf().map((t) => ({ text: t, group: `访问大纲 · ${vn}` }))];
    };
    let items = itemsOf(), qs = items.map((x) => x.text), cur = 0, menu = false;
    if (!qs.length && !(o.versions || []).length) { box.innerHTML = `<div class="card empty"><b>还没有访谈大纲。</b><span>先到“访谈”页写好大纲，这里会按每个问题归纳回答。</span><a class="btn p" href="#/p/${p.id}/outline">去访谈页</a></div>`; return; }
    const verBar = () => { const vs = (o.versions || []).filter((x) => x.exists !== false); if (vs.length < 2) return "";
      const c = vs.find((x) => x.key === o.v) || vs[0];
      return `<div class="row ins-tool"><span class="sm mut">访谈大纲版本</span><div class="rf-ver"><button type="button" id="qver" aria-haspopup="true" aria-expanded="${menu}">${esc(c.name)} ▾</button>${menu ? `<div class="rf-vm" role="menu">${vs.map((x) => `<button type="button" class="rf-vi${x.key === c.key ? " on" : ""}" role="menuitemradio" aria-checked="${x.key === c.key}" data-qv="${esc(x.key)}"><span>${esc(x.name)}${x.base ? "（原文）" : ""}</span></button>`).join("")}</div>` : ""}</div>${setup.source ? `<span class="sm mut" style="margin-left:auto">${esc(setup.country || "")} · 语言与形式同步自${esc(setup.source)}：${esc(setup.language || "")} · ${esc(setup.format_label || "")}</span>` : ""}</div>`; };
    const right = () => {
      const q = qs[cur], r = done[q];
      if (!q) { $("#qapane").innerHTML = `<div class="card empty"><b>这个版本的大纲里还没有问题</b><a class="btn p" href="#/p/${p.id}/outline">去访谈页</a></div>`; return; }
      $("#qapane").innerHTML = `<div class="row ins-tool"><b>问题 ${cur + 1}</b>${items[cur] ? `<span class="sm mut">${esc(items[cur].group)}</span>` : ""}<span class="sp"></span><button type="button" class="p" id="qago">${r ? "重新生成这题的分析" : "生成这题的分析"}</button></div><div class="card"><div><b>${esc(q)}</b></div>${insAiNotice()}</div>
        ${r ? `<div class="card"><h4 style="margin:0 0 6px">受访者怎么回答的</h4>${insAnswer(r)}</div>` : `<div class="card empty"><b>还没有分析</b><span>点右上角的按钮，按已有的转写归纳这一题。</span></div>`}`;
      $("#qago").onclick = guard(async () => { const b = $("#qago"); b.disabled = true; b.textContent = "正在分析…"; try { done[q] = await api("/repo/ask", { method: "POST", body: { question: `受访者是怎么回答这个问题的：${q}`, project_id: p.id } }); } finally { right(); list(); } });
    };
    const list = () => {
      let last = "", html = "";
      qs.forEach((q, i) => {
        const g = (items[i] && items[i].group) || "";
        if (g !== last) { html += `<div class="sm mut" style="margin:10px 0 4px">${esc(g)}</div>`; last = g; }
        html += `<div role="button" tabindex="0" class="ins-ql${i === cur ? " on" : ""}" data-qi="${i}"><span>${i + 1}</span><div>${esc(q)}</div>${done[q] ? `<em>已分析</em>` : ""}</div>`;
      });
      $("#qalist").innerHTML = html;
      document.querySelectorAll("[data-qi]").forEach((b) => { const go = () => { cur = +b.dataset.qi; list(); right(); }; b.onclick = go; b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }; });
    };
    // 版本菜单是纯浮层：直接出示在最上层，下方题目区保持原位、不产生任何位移。
    const refreshVer = () => {
      const bar = $("#qverbar"); if (!bar) return;
      bar.style.removeProperty("padding-bottom");   // 清掉可能残留的旧版推开样式
      bar.innerHTML = verBar();
      wireVer();
    };
    const draw = () => {
      if (selOff) { selOff(); selOff = null; }
      box.innerHTML = `<div id="qverbar"></div><div class="ins-qa"><div><div class="sm mut" style="margin:0 0 6px">题目同步自当前甄别问卷${scr.version ? ` · v${scr.version}` : ""}${scr.status === "published" ? " · 已发布" : ""} 与当前访问大纲${setup.country ? ` · ${esc(setup.country)} · ${esc(setup.language || "")} · ${esc(setup.format_label || "")}` : ""}</div><div id="qalist" class="ins-qls"></div></div><div><div id="qbar" hidden></div><div id="qapane"></div></div></div>`;
      refreshVer(); list(); right();
      const ask = (body) => api("/repo/ask", { method: "POST", body: { ...body, project_id: p.id } });
      const sel = ChatSel.attach({ roots: [$("#qapane")], bar: $("#qbar"), label: (k) => `第 ${+k.split(":")[1] + 1} 句`,
        ask: async ({ quote, text }) => { const r = await ask({ question: `关于“${quote.slice(0, 120)}”：${text}`.slice(0, 480) }); return r.insufficient ? r.message : r.answer.map((c) => c.text).join("\n"); },
        send: async ({ text }) => { const q = qs[cur]; if (!q) return false; done[q] = await ask({ question: `受访者是怎么回答这个问题的：${q}。请按下面的要求重新归纳：${text}`.slice(0, 480) }); right(); list(); return true; } });
      selOff = () => sel.destroy();
    };
    const wireVer = () => {
      const vb = $("#qver"); if (vb) vb.onclick = (e) => { e.stopPropagation(); menu = !menu; refreshVer(); };
      box.querySelectorAll("[data-qv]").forEach((b) => (b.onclick = guard(async () => {
        const k = b.dataset.qv; menu = false; try { sessionStorage.setItem(vkey, k); } catch { /* no storage: this page only */ }
        o = await fetchOutline(k); items = itemsOf(); qs = items.map((x) => x.text); cur = 0; draw();
      })));
    };
    pageOn("ins-qv", document, "click", () => { if (menu && $("#qverbar")) { menu = false; refreshVer(); } });
    draw();
  }

  /* ---------- 精彩片段 ---------- */
  async function reels(box) {
    const clips = await api(`/projects/${p.id}/clips`); if (stale(my)) return;
    box.innerHTML = `<div class="row ins-tool"><b>片段 ${clips.length} 条</b></div>${clips.length ? `<div class="ins-wall">${clips.map((c) => `<a class="ins-clip" href="#/i/${c.interview_id}/clips"><div class="th"><span>${insClock(c.end_ms - c.start_ms)}</span><span aria-hidden="true">▶</span></div><div class="tx">${esc(c.title)}${c.quote ? `<div class="mut sm">“${esc(c.quote)}”</div>` : ""}<div><span class="tag">${esc(c.who)}</span>${c.theme ? ` <span class="tag ok">${esc(c.theme)}</span>` : ""}</div></div></a>`).join("")}</div>`
      : `<div class="card empty"><b>还没有片段。</b><span>在单场页面把洞察剪成片段，就会出现在这里。</span></div>`}
      <div class="group"><h2>集锦</h2></div><div id="reels"></div>`;
    reelsSection(p, my);
  }

  /* ---------- 综合看板：把大纲里标了 ★ 重点关注 的问题，从所有已完成访谈里聚合到一起看 ---------- */
  async function board(box) {
    const d = await api(`/projects/${p.id}/board`); if (stale(my)) return;
    const k = d.kpi;
    const themeBar = (label, n, total) => `<div class="bd-tr"><span class="bd-label">${esc(label)}</span><span class="bd-f" style="width:${total ? Math.round(n * 100 / total) : 0}%"></span><span class="bd-n">${n}</span></div>`;
    const byLabel = (mb) => mb === "client" ? "客户" : mb === "moderator" ? "主持人" : "";
    const card = (r) => `<div class="card bd-card">
      <div class="bd-head"><span class="bd-star">★</span><span class="bd-q">Q${r.num}　${esc(r.q)}</span><span class="bd-ans">已回答 ${r.answered} / ${r.total}</span></div>
      ${r.reason ? `<div class="bd-reason">关注原因${byLabel(r.mark_by) ? `（${byLabel(r.mark_by)}）` : ""}：${esc(r.reason)}</div>` : ""}
      ${r.themes.length ? `<div class="bd-themes">${r.themes.map((t) => themeBar(t.label, t.n, r.answered)).join("")}</div>` : (r.answered ? `<div class="mut sm">还没有主题标签（访谈答案尚未归纳）。</div>` : "")}
      ${r.quotes.length ? `<div class="bd-quotes"><span class="sm mut">原话（服务器从转写复制）</span>${r.quotes.map((q) => `<div class="ins-q">“${esc(q)}”</div>`).join("")}</div>` : ""}
    </div>`;
    box.innerHTML = `<div class="row ins-tool"><b>综合看板</b><span class="sp"></span><span class="sm mut">按“访谈”页大纲里 ★ 重点关注 的问题汇总</span></div>
      <div class="rp-kpis bd-kpis"><div><b>${k.live}</b><span>进行中</span></div><div><b>${k.done} / ${k.total}</b><span>已完成</span></div><div><b>${k.focus}</b><span>关注点</span></div></div>
      ${d.rows.length ? d.rows.map(card).join("") : `<div class="card empty"><b>还没有关注点。</b><span>到“访谈”页，在题目上点 ★ 标为重点关注（可写一句关注原因），这里就会按它聚合所有受访者的回答。</span><a class="btn p" href="#/p/${p.id}/outline">去访谈页</a></div>`}`;
  }

  /* ---------- 对话：研究助手（接已配置的 AI 模型；回答 / 引用原话 / 建议 分开显示） ---------- */
  const ASK = ["这次研究的关键结论是什么？", "受访者最常提到的困扰有哪些？", "按不同人群比较一下回答的差别", "找出最有说服力的几句原话", "哪些问题受访者答得最犹豫？", "下一轮访谈该怎么改大纲？"];
  const SCOPES = [["transcripts", "本项目访谈转写"], ["insights", "已通过的洞察"], ["library", "研究库资料"]];
  async function chat(box) {
    const st = await api(`/projects/${p.id}/chat/status`); if (stale(my)) return;
    let threads = await api(`/projects/${p.id}/chats`).catch(() => []), cur = null, busy = null;   // cur: the open conversation { id, title, messages }
    let scope = SCOPES.map((s) => s[0]); try { const saved = JSON.parse(sessionStorage.getItem("ichat_scope") || "null"); if (Array.isArray(saved) && saved.length) scope = saved; } catch { /* the default */ }
    const key = `ichat_${p.id}`, status = st.mock ? ["演示模式", "w"] : st.ready ? ["已连接", ""] : ["未连接", "w"];
    const answerHtml = (m) => esc(m.answer).replace(/\[(\d+)\]/g, (_, n) => `<sup class="ins-ref">[${n}]</sup>`).replace(/\n/g, "<br>");
    const aiHtml = (m, i) => `<div class="ins-msg ai"><h5>回答</h5><div data-selk="m:${i}">${answerHtml(m)}</div>
      ${m.quotes.length ? `<h5>引用原话（从转写里原样复制）</h5>${m.quotes.map((q) => `<div class="ins-q"><b>[${q.n}]</b> “${esc(q.text)}”<small> ${esc(q.who || "")} ${q.at_ms ? insClock(q.at_ms) : ""}${q.interview_id ? ` · <a href="#/i/${q.interview_id}">看原文</a>` : ""}</small></div>`).join("")}` : ""}
      ${m.suggestions.length ? `<h5>建议（小星星助手的判断，不是受访者原话）</h5><div class="ins-sg"><ul data-selk="g:${i}">${m.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
      <div class="ins-fu">${m.followups.map((f) => `<button type="button" class="s" data-fu="${esc(f)}">${esc(f)}</button>`).join("")}${m.followups.length || m.suggestions.length ? `<button type="button" class="s" data-addq="${i}">把追问加进访谈大纲</button>` : ""}</div>
      <div class="ins-act"><button type="button" class="lnk" data-copy="${i}">复制</button><button type="button" class="lnk" data-redo="${i}">重新生成</button></div></div>`;
    const draw = () => {
      const msgs = cur ? cur.messages : [];
      $("#cthread").innerHTML = msgs.length || busy ? msgs.map((m, i) => (m.role === "user" ? `<div class="ins-msg me">${esc(m.text)}</div>` : aiHtml(m, i))).join("")
          + (busy ? `<div class="ins-msg me">${esc(busy)}</div><div class="ins-msg ai"><span class="ins-dots"><i></i><i></i><i></i></span>　正在读资料并回答…　<button type="button" class="s" id="cstop">停止</button></div>` : "")
        : `<div class="ins-hello"><b>研究助手</b><div class="mut">可以问这次访谈的任何问题，也可以让它给建议。引用的原话由程序从转写里复制。</div><div class="ins-cg">${ASK.map((a) => `<button type="button" class="ins-card" data-ask="${esc(a)}">${esc(a)}</button>`).join("")}</div></div>`;
      const t = $("#cthread"); t.scrollTop = t.scrollHeight;
      t.querySelectorAll("[data-ask],[data-fu]").forEach((b) => (b.onclick = () => send(b.dataset.ask || b.dataset.fu)));
      t.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => { const m = cur.messages[+b.dataset.copy]; try { await navigator.clipboard.writeText([m.answer, ...m.suggestions.map((s) => "· " + s)].join("\n")); toast("已复制"); } catch { toast("这个浏览器不让复制，请手动选中文字", true); } }));
      t.querySelectorAll("[data-redo]").forEach((b) => (b.onclick = () => { const q = cur.messages[+b.dataset.redo - 1]; if (q) send(q.text); }));
      t.querySelectorAll("[data-addq]").forEach((b) => (b.onclick = () => addToOutline(cur.messages[+b.dataset.addq])));
      const stop = $("#cstop"); if (stop) stop.onclick = () => { if (busy && drawAbort) drawAbort.abort(); };
      const go = $("#csend"); if (go) { go.textContent = busy ? "■" : "↑"; go.classList.toggle("stop", !!busy); go.setAttribute("aria-label", busy ? "停止" : "发送"); }
    };
    let drawAbort = null;
    const drawHist = () => { const h = $("#chist"); h.innerHTML = threads.length ? threads.map((t) => `<div class="ins-hrow"><button type="button" class="ins-hbtn" data-open="${t.id}">${esc(t.title)}<small class="mut"> ${when(t.updated_at)}</small></button><button type="button" class="s" data-del="${t.id}" aria-label="删除这段对话">×</button></div>`).join("") : `<div class="mut sm" style="padding:6px 8px">还没有历史对话。</div>`;
      h.querySelectorAll("[data-open]").forEach((b) => (b.onclick = guard(async () => { await open(b.dataset.open); h.hidden = true; })));
      h.querySelectorAll("[data-del]").forEach((b) => (b.onclick = guard(async () => { if (!confirm("删除这段对话？删除后不能恢复。")) return; await api(`/chats/${b.dataset.del}`, { method: "DELETE" }); threads = threads.filter((t) => t.id !== b.dataset.del); if (cur && cur.id === b.dataset.del) { cur = null; sessionStorage.removeItem(key); draw(); } drawHist(); }))); };
    const open = async (id) => { cur = await api(`/chats/${id}`); sessionStorage.setItem(key, id); draw(); };
    async function send(text) {
      text = (text || "").trim(); if (!text || busy) return false;
      if (!st.ready) { toast(st.message, true); return false; }
      let ok = false;
      busy = text; $("#cmsg").value = ""; draw();
      drawAbort = new AbortController();
      try {
        if (!cur) { cur = await api(`/projects/${p.id}/chats`, { method: "POST" }); sessionStorage.setItem(key, cur.id); }
        const r = await api(`/chats/${cur.id}/ask`, { method: "POST", body: { message: text, scope }, signal: drawAbort.signal });
        cur.messages.push({ role: "user", text }, r.message); cur.title = r.title;
        threads = [{ id: cur.id, title: r.title || "新对话", updated_at: new Date().toISOString().replace("Z", "") }, ...threads.filter((t) => t.id !== cur.id)];
        ok = true;
      } catch (e) { if (e.name !== "AbortError") toast(e.message, true); else { cur = cur ? await api(`/chats/${cur.id}`).catch(() => cur) : cur; } }
      busy = null; drawAbort = null; draw(); drawHist();
      return ok;
    }
    async function addToOutline(m) {   // a follow-up question goes into the interview outline only after the person looks at it and picks where
      const o = await api(`/projects/${p.id}/outline`), parts = o.summary ? o.summary.parts.filter((x) => x.kind !== "stimulus") : [];
      if (!parts.length) return toast("访谈大纲还没有内容，先到“访谈”页起草大纲", true);
      const mod = modal(`<h2>加进访谈大纲</h2><label class="f"><span>问题（可以改）</span><input id="aq" maxlength="300" value="${esc(m.followups[0] || m.suggestions[0] || "")}"></label>
        <label class="f"><span>放进哪一段</span><select id="ap">${parts.map((x, i) => `<option value="${i}">${esc(x.title)}</option>`).join("")}</select></label><div class="row"><button class="p" id="aok">加进大纲</button><button type="button" id="ano">取消</button></div>`);
      $("#ano", mod).onclick = () => mod.remove();
      $("#aok", mod).onclick = guard(async () => { const v = $("#aq", mod).value.trim(), part = parts[+$("#ap", mod).value]; if (!v) throw new Error("问题不能是空的");
        await api(`/projects/${p.id}/outline/edit`, { method: "POST", body: { op: "add", kind: part.kind, ref: part.ref, value: v } }); mod.remove(); toast("已加进访谈大纲，可以到“访谈”页查看"); });
    }
    box.innerHTML = `<div class="ins-chat"><div class="row ins-chead"><b>研究助手</b><span class="tag ${status[1] === "w" ? "o" : "ok"}">${esc(status[0])}</span><span class="sp"></span><span class="ins-hwrap"><button type="button" class="s" id="chbtn">历史对话 ▾</button><div class="ins-hist" id="chist" hidden></div></span><button type="button" class="s" id="cnew">新对话</button></div>
      <div class="ins-scope" id="cscope" role="group" aria-label="参考哪些内容">${SCOPES.map(([k, n]) => `<label class="chk"><input type="checkbox" data-sc="${k}" ${scope.includes(k) ? "checked" : ""}><span>${n}</span></label>`).join("")}</div>
      ${st.ready ? "" : `<div class="card empty"><b>小星星助手暂不可用</b><span>${esc(st.message)}</span></div>`}
      <div id="cthread" class="ins-thread"></div>
      <div id="cbar" hidden></div>
      <div class="ins-inp"><div class="rf-box"><textarea id="cmsg" rows="2" maxlength="1000" placeholder="${st.ready ? "问问研究助手，比如“受访者为什么觉得价格太贵？”" : "连接模型后才能提问"}" aria-label="提问" ${st.ready ? "" : "disabled"}></textarea><button class="p rf-send" type="button" id="csend" aria-label="发送" ${st.ready ? "" : "disabled"}>↑</button></div>
        </div></div>`;
    $("#csend").onclick = () => (busy ? (drawAbort && drawAbort.abort()) : send($("#cmsg").value));
    $("#cmsg").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#cmsg").value); } };
    $("#cnew").onclick = () => { cur = null; sessionStorage.removeItem(key); draw(); $("#cmsg").focus(); };
    $("#chbtn").onclick = (e) => { e.stopPropagation(); const h = $("#chist"); h.hidden = !h.hidden; if (!h.hidden) drawHist(); };
    pageOn("ins-chist", document, "click", (e) => { const h = $("#chist"); if (h && !h.hidden && !e.target.closest(".ins-hwrap")) h.hidden = true; });
    box.querySelectorAll("[data-sc]").forEach((c) => (c.onchange = () => { scope = [...box.querySelectorAll("[data-sc]:checked")].map((x) => x.dataset.sc); if (!scope.length) { c.checked = true; scope = [c.dataset.sc]; toast("至少选一项", true); } sessionStorage.setItem("ichat_scope", JSON.stringify(scope)); }));
    const last = sessionStorage.getItem(key); if (last && threads.some((t) => t.id === last)) { try { await open(last); } catch { draw(); } } else draw();
    drawHist();
    if (st.ready) {   // select words of a reply: ask about them on the side, or collect several and change them in one go (the quotes the server copied cannot be selected)
      const sel = ChatSel.attach({ roots: [$("#cthread")], bar: $("#cbar"),
        label: (k) => { const [kind, i] = k.split(":"), n = cur ? cur.messages.slice(0, +i + 1).filter((x) => x.role === "ai").length : 0; return kind === "g" ? `第 ${n} 条回答的建议` : `第 ${n} 条回答`; },
        ask: async ({ quote, text }) => {
          if (!cur) throw new Error("先问一个问题，再针对回答追问");
          const r = await api(`/chats/${cur.id}/ask`, { method: "POST", body: { message: `关于你上一条回答里的这一段：“${quote}”\n${text}\n请只回答这个问题，不要改写前面的结论。`, scope, ask_only: true } });
          return [r.message.answer, ...r.message.suggestions.map((x) => "· " + x)].join("\n");
        },
        send: async ({ text }) => send(text) });
      selOff = () => sel.destroy();
    }
  }
}
