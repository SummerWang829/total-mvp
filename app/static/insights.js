/* “洞察与片段”页：五个横向小页签——受访者数据、报告、问题分析、精彩片段、对话（研究员/管理员/技术支持；观察员只有受访者数据）。
   受访者数据：三个数字 + 受访者表，鼠标移到某一行出详细卡片（手机上点一下）；报告：整个项目的研究报告（页面里看 + 下载 Word）；
   问题分析：按“访谈”页大纲里的每个问题归纳回答；精彩片段：片段卡片墙 + 集锦；对话：只围绕本项目的 AI 提问（引文由服务器从转写里原样取）。
   依赖 index.html 里的 api / esc / toast / guard / $ / STAFF / ME / STATUS / CONFIG / reelsSection / shell。 */
const INS_TABS = [["r", "受访者数据"], ["p", "报告"], ["q", "问题分析"], ["v", "精彩片段"], ["c", "对话"]];
const insClock = (ms) => { const s = Math.max(0, Math.round((ms || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
const insAiNotice = () => (CONFIG && CONFIG.ai_local ? "" : `<div class="sm mut ins-note">你的提问和相关的访谈转写会发送给 AI 服务商${CONFIG && CONFIG.ai_vendor ? `“${esc(CONFIG.ai_vendor)}”` : ""}。请不要输入受访者的姓名、电话等个人信息。</div>`);
const insDownload = (p, url, name) => guard(async () => {  // a download needs the login header, so fetch it and hand the browser a file
  const r = await fetch(url, { headers: { Authorization: "Bearer " + store.get("tt_token") } });
  if (!r.ok) throw new Error("导出失败，请稍后再试");
  const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = name; document.body.appendChild(a); a.click(); a.remove();
});
/** Quotes the server copied out of the transcripts, as clickable lines. */
const insEvidence = (hits) => hits.map((h) => `<div class="ins-q">“${esc(h.text)}”<small> ${esc(h.who || "")} ${h.at_ms ? insClock(h.at_ms) : ""}${h.interview_id ? ` · <a href="#/i/${h.interview_id}">看原文</a>` : ""}</small></div>`).join("");
const insAnswer = (r) => (r.insufficient ? `<div class="mut">${esc(r.message)}</div>` : r.answer.map((c) => `<div class="ins-claim"><div>${esc(c.text)}</div>${insEvidence(c.evidence)}</div>`).join(""));

async function insightsHub(p, my) {
  const team = STAFF(), tabs = team ? INS_TABS : [INS_TABS[0]];
  let tab = sessionStorage.getItem("itab") || "r"; if (!tabs.some((t) => t[0] === tab)) tab = "r";
  shell(`${team ? `<div class="hubtabs sub" id="itabs" role="tablist" aria-label="洞察与片段">${tabs.map(([k, n]) => `<a href="#" role="tab" data-k="${k}">${n}</a>`).join("")}</div>` : ""}<div id="ipanel" class="ins ins-ws"></div>`, { active: "insights", p });
  document.body.classList.add("rfmode", "pgw");   // the page itself does not scroll: each tab's content scrolls inside its own box
  const fit = () => { const b = $("#ipanel"); if (b && b.offsetParent) b.style.height = Math.max(320, innerHeight - b.getBoundingClientRect().top - 18) + "px"; };
  fit(); addEventListener("resize", fit);
  const drawTabs = () => document.querySelectorAll("#itabs a").forEach((a) => { const on = a.dataset.k === tab; a.classList.toggle("on", on); a.setAttribute("aria-selected", on); });
  const show = async (k) => { tab = k; sessionStorage.setItem("itab", k); drawTabs(); const box = $("#ipanel"); box.innerHTML = `<div class="mut" style="padding:16px">加载中…</div>`; try { await ({ r: respondents, p: report, q: questions, v: reels, c: chat })[k](box); } catch (e) { if (!stale(my)) box.innerHTML = `<div class="card empty"><b>${esc(e.message)}</b></div>`; } };
  document.querySelectorAll("#itabs a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.k); }));
  show(tab);

  /* ---------- 受访者数据 ---------- */
  async function respondents(box) {
    const d = await api(`/projects/${p.id}/insights/overview`); if (stale(my)) return;
    const k = d.kpi, rows = d.rows, showTags = rows.length ? "tags" in rows[0] : team;
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
      if (c.id === "status") { const [t, cl] = STATUS[r.status] || [r.status, ""]; return `<span class="tag ${cl}">${t}</span>`; }
      if (c.id === "tags") return (r.tags || []).map((t) => `<span class="lch ${lcolor(t)}">${esc(t)}</span>`).join("") || `<span class="mut">—</span>`;
      if (c.id === "ins") return `${r.insights || "—"} / ${r.clips || "—"}${r.review ? ` <span class="tag bad">${r.review} 待复核</span>` : ""}`;
      const v = valueOf(r, c);
      if (c.group === "answer" && c.type === "text") return v ? `<div class="ins-cl" tabindex="0" role="button" aria-expanded="false">${esc(v)}</div>` : `<span class="mut">${r.status === "ended" ? "还没整理" : "访谈结束后生成"}</span>`;
      if (c.type === "choice") return asList(v, c).map((x) => `<span class="tag">${esc(x)}</span>`).join("") || `<span class="mut">—</span>`;
      return v ? `<div class="ins-cl" tabindex="0" role="button" aria-expanded="false">${esc(String(v))}</div>` : `<span class="mut">—</span>`;
    };
    const widthOf = (c) => (c.group === "answer" && c.type === "text" ? "w2" : c.type === "choice" || c.id === "tags" ? "w3" : "w1");
    box.innerHTML = `<div class="ins-kp"><div class="rd-k"><span>已完成访谈</span><b>${k.completed}</b><small class="mut">${k.upcoming ? `另有 ${k.upcoming} 场待开始` : "没有待开始的场次"}${k.live ? ` · ${k.live} 场进行中` : ""}</small></div>
        <div class="rd-k"><span>平均时长</span><b>${k.avg_minutes != null ? k.avg_minutes + " 分钟" : "—"}</b></div><div class="rd-k ok"><span>洞察通过率</span><b>${k.approval_rate != null ? k.approval_rate + "%" : "—"}</b><small class="mut">已通过 ${k.approved} / 共 ${k.insights} 条</small></div></div>
      <div class="row ins-tool"><input id="isearch" type="search" placeholder="搜索（所有列）" aria-label="搜索受访者" style="max-width:260px"><span class="sp"></span><span class="sm mut" id="igen-st" role="status" aria-live="polite"></span>
        ${d.can_generate ? `<button type="button" id="igen">整理访谈回答</button>` : ""}<button type="button" id="iview">视图与筛选</button>${ME.role === "observer" ? "" : `<button type="button" id="xw">导出 Word</button><button type="button" id="xc">导出 CSV</button>`}</div>
      ${d.can_generate ? insAiNotice().replace("你的提问和相关的访谈转写会发送给", "整理时，每位受访者的回答会发送给") : ""}
      ${rows.length ? `<div class="ins-wrap" id="iwrap"><div class="ins-sc"><div class="ins-sx" id="isx"><table class="ins-tbl2" id="itbl"></table></div></div><div class="ins-pop" id="ipop" hidden></div></div><div class="ins-ft"><span id="icount"></span></div>`
        : `<div class="card empty"><b>还没有场次。</b><span>访谈结束并转写后，每位受访者的数据会列在这里。</span></div>`}`;
    if (ME.role !== "observer" && $("#xw")) { $("#xw").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=docx`, `${p.name}-洞察.docx`); $("#xc").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=csv`, `${p.name}-洞察.csv`); }
    const gen = $("#igen");
    if (gen) gen.onclick = guard(async () => {   // the answers of every finished interview, one after the other (each call reads one transcript)
      const todo = rows.filter((r) => r.status === "ended" && !r.purged && r.kind !== "group"), st = $("#igen-st"); gen.disabled = true; let n = 0, skip = "";
      for (const r of todo) { st.textContent = `整理中 ${n}/${todo.length}…`; try { const out = await api(`/interviews/${r.id}/answers/generate`, { method: "POST" }); skip = out.themes_skipped_reason || skip; } catch (e) { toast(e.message, true); break; } n++; }
      st.textContent = `已整理 ${n}/${todo.length}`; if (skip) toast(skip, true); await show("r");
    });
    if (!rows.length) return;
    const wrap = $("#iwrap"), sx = $("#isx"), tbl = $("#itbl"), pop = $("#ipop");
    const fit = () => { const b = $(".ins-sc"), pane = $("#ipanel"); if (!b || !pane || !b.offsetParent) return; b.style.height = Math.max(200, pane.getBoundingClientRect().bottom - b.getBoundingClientRect().top - 34) + "px"; };
    addEventListener("resize", fit);
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
    const header = () => {
      const vs = visible(), groups = []; vs.forEach((c) => { const g = groups[groups.length - 1]; if (g && g.id === c.group) g.n++; else groups.push({ id: c.group, n: 1 }); });
      const fi = (c) => (filterable(c) || c.id === "status" || c.id === "tags" ? `<button type="button" class="fi${F[c.id] && ((F[c.id].sel && F[c.id].sel.size) || F[c.id].q) ? " on" : ""}" data-f="${c.id}" aria-label="筛选：${esc(c.label)}">☰</button>` : "");
      return `<thead><tr class="g"><th class="pin"></th>${groups.map((g) => `<th colspan="${g.n}">${GROUPS[g.id]}</th>`).join("")}</tr><tr class="c"><th class="pin">编号</th>${vs.map((c) => `<th class="${widthOf(c)}" title="${esc(c.label)}">${esc(c.label)}${fi(c)}</th>`).join("")}</tr></thead>`;
    };
    const draw = () => {
      const vs = visible(); shown = rows.filter(matches); closeCard();
      tbl.innerHTML = header() + `<tbody>${shown.map((r, i) => `<tr data-i="${i}"><td class="pin"><a href="#/i/${r.id}"><b>${esc(r.code)}</b></a></td>${vs.map((c) => `<td class="${widthOf(c)}" data-l="${esc(c.label)}">${cell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      $("#icount").textContent = `共 ${rows.length} 人 · 当前显示 ${shown.length} 人${activeFilters() ? ` · ${activeFilters()} 个筛选` : ""}`;
      tbl.querySelectorAll("td.pin").forEach((td) => { const i = +td.parentElement.dataset.i; td.onmouseenter = () => { if (matchMedia("(hover:hover)").matches) openCard(i, td); }; td.onclick = (e) => { if (e.target.closest("a") || matchMedia("(hover:hover)").matches) return; open === i ? closeCard() : openCard(i, td); }; });
      tbl.querySelectorAll(".ins-cl").forEach((c) => (c.onclick = c.onkeydown = (e) => { if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return; e.preventDefault(); const on = c.classList.toggle("open"); c.setAttribute("aria-expanded", String(on)); }));
      tbl.querySelectorAll(".fi").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); openFilter(b, cols.find((x) => x.id === b.dataset.f)); }));
      fit();
    };
    wrap.onmouseleave = () => { if (matchMedia("(hover:hover)").matches) closeCard(); };
    sx.onscroll = closeCard;
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

  /* ---------- 报告 ---------- */
  async function report(box) {
    const d = await api(`/projects/${p.id}/report?format=json`); if (stale(my)) return;
    const blocks = d.blocks, heads = blocks.filter((b) => b[0] === "h1").map((b) => b[1]);
    let html = "", list = false;
    blocks.forEach((b) => {
      if (b[0] !== "bullet" && list) { html += "</ul>"; list = false; }
      if (b[0] === "title") html += `<h2 class="ins-rt">${esc(b[1])}</h2>`;
      else if (b[0] === "subtitle") html += `<p class="mut">${esc(b[1])}</p>`;
      else if (b[0] === "h1") html += `<h3 id="rp-${heads.indexOf(b[1])}" class="ins-h1">${esc(b[1])}</h3>`;
      else if (b[0] === "h2") html += `<h4 class="ins-h2">${esc(b[1])}</h4>`;
      else if (b[0] === "p") html += `<p>${esc(b[1])}</p>`;
      else if (b[0] === "note") html += `<p class="mut sm">${esc(b[1])}</p>`;
      else if (b[0] === "bullet") { if (!list) { html += "<ul>"; list = true; } html += `<li>${esc(b[1])}</li>`; }
      else if (b[0] === "quote") html += `<div class="ins-q">${esc(b[1])}</div>`;
      else if (b[0] === "table") html += `<div class="scroll"><table class="tbl"><thead><tr>${b[1].map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${b[2].map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    });
    if (list) html += "</ul>";
    box.innerHTML = `<div class="row ins-tool"><b>研究报告</b><span class="sp"></span><button type="button" id="rpre">重新生成</button><button type="button" class="p" id="rpdl">下载 Word</button></div>
      <div class="ins-rep"><nav class="ins-toc" aria-label="目录"><b>目录</b>${heads.map((h, i) => `<a href="#" data-to="rp-${i}">${esc(h)}</a>`).join("")}</nav><div class="card ins-doc">${html}</div></div>`;
    $("#rpre").onclick = () => show("p");
    $("#rpdl").onclick = insDownload(p, `/api/projects/${p.id}/report?format=docx`, `${p.name}-研究报告.docx`);
    box.querySelectorAll("[data-to]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); const t = document.getElementById(a.dataset.to); if (t) t.scrollIntoView({ block: "start" }); }));
  }

  /* ---------- 问题分析 ---------- */
  const done = {};  // question text -> the answer, kept while the page is open
  async function questions(box) {
    const o = await api(`/projects/${p.id}/outline`); if (stale(my)) return;
    const qs = o.summary ? o.summary.parts.flatMap((x) => x.questions.filter((y) => !y.cover).map((y) => y.text)) : [];
    if (!qs.length) { box.innerHTML = `<div class="card empty"><b>还没有访谈大纲。</b><span>先到“访谈”页写好大纲，这里会按每个问题归纳回答。</span><a class="btn p" href="#/p/${p.id}/outline">去访谈页</a></div>`; return; }
    let cur = 0;
    const right = () => {
      const q = qs[cur], r = done[q];
      $("#qapane").innerHTML = `<div class="row ins-tool"><b>问题 ${cur + 1}</b><span class="sp"></span><button type="button" class="p" id="qago">${r ? "重新生成这题的分析" : "生成这题的分析"}</button></div><div class="card"><div><b>${esc(q)}</b></div>${insAiNotice()}</div>
        ${r ? `<div class="card"><h4 style="margin:0 0 6px">受访者怎么回答的</h4>${insAnswer(r)}</div>` : `<div class="card empty"><b>还没有分析</b><span>点右上角的按钮，按已有的转写归纳这一题。</span></div>`}`;
      $("#qago").onclick = guard(async () => { const b = $("#qago"); b.disabled = true; b.textContent = "正在分析…"; try { done[q] = await api("/repo/ask", { method: "POST", body: { question: `受访者是怎么回答这个问题的：${q}`, project_id: p.id } }); } finally { right(); list(); } });
    };
    const list = () => { $("#qalist").innerHTML = qs.map((q, i) => `<div role="button" tabindex="0" class="ins-ql${i === cur ? " on" : ""}" data-qi="${i}"><span>${i + 1}</span><div>${esc(q)}</div>${done[q] ? `<em>已分析</em>` : ""}</div>`).join(""); document.querySelectorAll("[data-qi]").forEach((b) => { const go = () => { cur = +b.dataset.qi; list(); right(); }; b.onclick = go; b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }; }); };
    box.innerHTML = `<div class="ins-qa"><div><div class="sm mut" style="margin:0 0 6px">访谈大纲里的问题</div><div id="qalist" class="ins-qls"></div></div><div id="qapane"></div></div>`;
    list(); right();
  }

  /* ---------- 精彩片段 ---------- */
  async function reels(box) {
    const clips = await api(`/projects/${p.id}/clips`); if (stale(my)) return;
    box.innerHTML = `<div class="row ins-tool"><b>片段 ${clips.length} 条</b></div>${clips.length ? `<div class="ins-wall">${clips.map((c) => `<a class="ins-clip" href="#/i/${c.interview_id}/clips"><div class="th"><span>${insClock(c.end_ms - c.start_ms)}</span><span aria-hidden="true">▶</span></div><div class="tx">${esc(c.title)}${c.quote ? `<div class="mut sm">“${esc(c.quote)}”</div>` : ""}<div><span class="tag">${esc(c.who)}</span>${c.theme ? ` <span class="tag ok">${esc(c.theme)}</span>` : ""}</div></div></a>`).join("")}</div>`
      : `<div class="card empty"><b>还没有片段。</b><span>在单场页面把洞察剪成片段，就会出现在这里。</span></div>`}
      <div class="group"><h2>集锦</h2></div><div id="reels"></div>`;
    reelsSection(p, my);
  }

  /* ---------- 对话：研究助手（接已配置的 AI 模型；回答 / 引用原话 / 建议 分开显示） ---------- */
  const ASK = ["这次研究的关键结论是什么？", "受访者最常提到的困扰有哪些？", "按不同人群比较一下回答的差别", "找出最有说服力的几句原话", "哪些问题受访者答得最犹豫？", "下一轮访谈该怎么改大纲？"];
  const SCOPES = [["transcripts", "本项目访谈转写"], ["insights", "已通过的洞察"], ["library", "研究库资料"]];
  async function chat(box) {
    const st = await api(`/projects/${p.id}/chat/status`); if (stale(my)) return;
    let threads = await api(`/projects/${p.id}/chats`).catch(() => []), cur = null, busy = null;   // cur: the open conversation { id, title, messages }
    let scope = SCOPES.map((s) => s[0]); try { const saved = JSON.parse(sessionStorage.getItem("ichat_scope") || "null"); if (Array.isArray(saved) && saved.length) scope = saved; } catch { /* the default */ }
    const key = `ichat_${p.id}`, status = st.mock ? ["演示模式", "w"] : st.ready ? [`${st.vendor || "本地模型"} · 已连接`, ""] : ["未连接", "w"];
    const answerHtml = (m) => esc(m.answer).replace(/\[(\d+)\]/g, (_, n) => `<sup class="ins-ref">[${n}]</sup>`).replace(/\n/g, "<br>");
    const aiHtml = (m, i) => `<div class="ins-msg ai"><h5>回答</h5><div>${answerHtml(m)}</div>
      ${m.quotes.length ? `<h5>引用原话（程序从转写里原样复制，不是 AI 写的）</h5>${m.quotes.map((q) => `<div class="ins-q"><b>[${q.n}]</b> “${esc(q.text)}”<small> ${esc(q.who || "")} ${q.at_ms ? insClock(q.at_ms) : ""}${q.interview_id ? ` · <a href="#/i/${q.interview_id}">看原文</a>` : ""}</small></div>`).join("")}` : ""}
      ${m.suggestions.length ? `<h5>建议（AI 的判断，不是受访者原话）</h5><div class="ins-sg"><ul>${m.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>` : ""}
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
      text = (text || "").trim(); if (!text || busy) return;
      if (!st.ready) return toast(st.message, true);
      busy = text; $("#cmsg").value = ""; draw();
      drawAbort = new AbortController();
      try {
        if (!cur) { cur = await api(`/projects/${p.id}/chats`, { method: "POST" }); sessionStorage.setItem(key, cur.id); }
        const r = await api(`/chats/${cur.id}/ask`, { method: "POST", body: { message: text, scope }, signal: drawAbort.signal });
        cur.messages.push({ role: "user", text }, r.message); cur.title = r.title;
        threads = [{ id: cur.id, title: r.title || "新对话", updated_at: new Date().toISOString().replace("Z", "") }, ...threads.filter((t) => t.id !== cur.id)];
      } catch (e) { if (e.name !== "AbortError") toast(e.message, true); else { cur = cur ? await api(`/chats/${cur.id}`).catch(() => cur) : cur; } }
      busy = null; drawAbort = null; draw(); drawHist();
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
      <div class="ins-scope" id="cscope" role="group" aria-label="让 AI 读哪些内容">${SCOPES.map(([k, n]) => `<label class="chk"><input type="checkbox" data-sc="${k}" ${scope.includes(k) ? "checked" : ""}><span>${n}</span></label>`).join("")}</div>
      ${st.ready ? "" : `<div class="card empty"><b>还没连接 AI 模型</b><span>${esc(st.message)}</span></div>`}
      <div id="cthread" class="ins-thread"></div>
      <div class="ins-inp"><div class="rf-box"><textarea id="cmsg" rows="2" maxlength="1000" placeholder="${st.ready ? "问问研究助手，比如“受访者为什么觉得价格太贵？”" : "连接模型后才能提问"}" aria-label="提问" ${st.ready ? "" : "disabled"}></textarea><button class="p rf-send" type="button" id="csend" aria-label="发送" ${st.ready ? "" : "disabled"}>↑</button></div>
        ${st.local ? "" : `<div class="sm mut ins-note">你的提问、本项目相关的转写片段和洞察会发送给 AI 服务商${st.vendor ? `“${esc(st.vendor)}”` : ""}。请不要输入受访者的姓名、电话等个人信息。</div>`}</div></div>`;
    $("#csend").onclick = () => (busy ? (drawAbort && drawAbort.abort()) : send($("#cmsg").value));
    $("#cmsg").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#cmsg").value); } };
    $("#cnew").onclick = () => { cur = null; sessionStorage.removeItem(key); draw(); $("#cmsg").focus(); };
    $("#chbtn").onclick = (e) => { e.stopPropagation(); const h = $("#chist"); h.hidden = !h.hidden; if (!h.hidden) drawHist(); };
    document.addEventListener("click", (e) => { const h = $("#chist"); if (h && !h.hidden && !e.target.closest(".ins-hwrap")) h.hidden = true; });
    box.querySelectorAll("[data-sc]").forEach((c) => (c.onchange = () => { scope = [...box.querySelectorAll("[data-sc]:checked")].map((x) => x.dataset.sc); if (!scope.length) { c.checked = true; scope = [c.dataset.sc]; toast("至少留一项让 AI 读", true); } sessionStorage.setItem("ichat_scope", JSON.stringify(scope)); }));
    const last = sessionStorage.getItem(key); if (last && threads.some((t) => t.id === last)) { try { await open(last); } catch { draw(); } } else draw();
    drawHist();
  }
}
