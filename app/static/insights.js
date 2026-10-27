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
    let q = "";
    const state = (r) => { const [t, c] = STATUS[r.status] || [r.status, ""]; return `<span class="tag ${c}">${t}</span>`; };
    const dur = (r) => (r.minutes != null ? `${r.minutes} 分` : "—");
    const tr = (r, i) => `<tr data-i="${i}" tabindex="0"><td data-l="编号"><a href="#/i/${r.id}"><b>${esc(r.code)}</b></a></td><td data-l="开始时间">${r.started_at ? when(r.started_at) : "—"}${r.scheduled ? ` <span class="mut sm">预约</span>` : ""}</td><td data-l="时长">${dur(r)}</td><td data-l="状态">${state(r)}</td>
      ${showTags ? `<td data-l="标签">${(r.tags || []).map((t) => `<span class="lch ${lcolor(t)}">${esc(t)}</span>`).join("") || `<span class="mut">—</span>`}</td>` : ""}<td data-l="洞察 / 片段">${r.insights ? `${r.insights}` : "—"} / ${r.clips || "—"}${r.review ? ` <span class="tag bad">${r.review} 待复核</span>` : ""}</td>
      <td data-l="AI 要点摘要">${r.summary.length ? r.summary.map((s) => esc(s)).join("<br>") : `<span class="mut">${r.status === "ended" ? "还没有洞察" : "访谈结束后自动生成"}</span>`}</td></tr>`;
    const hit = (r) => !q || [r.code, ...(r.tags || []), ...r.summary].join(" ").toLowerCase().includes(q);
    box.innerHTML = `<div class="ins-kp"><div class="rd-k"><span>已完成访谈</span><b>${k.completed}</b><small class="mut">${k.upcoming ? `另有 ${k.upcoming} 场待开始` : "没有待开始的场次"}${k.live ? ` · ${k.live} 场进行中` : ""}</small></div>
        <div class="rd-k"><span>平均时长</span><b>${k.avg_minutes != null ? k.avg_minutes + " 分钟" : "—"}</b></div><div class="rd-k ok"><span>洞察通过率</span><b>${k.approval_rate != null ? k.approval_rate + "%" : "—"}</b><small class="mut">已通过 ${k.approved} / 共 ${k.insights} 条</small></div></div>
      <div class="row ins-tool"><input id="isearch" type="search" placeholder="搜索 编号、标签、关键词" aria-label="搜索受访者" style="max-width:260px"><span class="sp"></span>${ME.role === "observer" ? "" : `<button type="button" id="xw">导出 Word</button><button type="button" id="xc">导出 CSV</button>`}</div>
      ${rows.length ? `<div class="ins-wrap" id="iwrap"><div class="card scroll" style="padding:6px 10px"><table class="tbl rc ins-tbl"><colgroup>${(showTags ? [9, 13, 8, 9, 14, 10, 37] : [10, 15, 9, 10, 12, 44]).map((w) => `<col style="width:${w}%">`).join("")}</colgroup><thead><tr><th>编号</th><th>开始时间</th><th>时长</th><th>状态</th>${showTags ? "<th>标签</th>" : ""}<th>洞察 / 片段</th><th>AI 要点摘要</th></tr></thead><tbody id="irows"></tbody></table></div><div class="ins-pop" id="ipop" hidden></div></div><div class="card empty" id="inone" hidden><b>没有符合的人。</b></div>`
        : `<div class="card empty"><b>还没有场次。</b><span>访谈结束并转写后，每位受访者的数据会列在这里。</span></div>`}`;
    if (ME.role !== "observer" && $("#xw")) { $("#xw").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=docx`, `${p.name}-洞察.docx`); $("#xc").onclick = insDownload(p, `/api/projects/${p.id}/insights/export?format=csv`, `${p.name}-洞察.csv`); }
    if (!rows.length) return;
    const pop = $("#ipop"), wrap = $("#iwrap"), body = $("#irows");
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
    let shown = [], open = -1;
    const place = (trEl) => {  // beside the row, kept inside the table's box
      const wr = wrap.getBoundingClientRect(), a = trEl.getBoundingClientRect(), top = a.bottom - wr.top + 4;
      pop.style.top = Math.max(0, Math.min(top, wrap.offsetHeight - pop.offsetHeight)) + "px";
    };
    const openCard = (i) => { const trEl = body.children[i]; if (!trEl) return; open = i; [...body.children].forEach((x) => x.classList.toggle("hv", x === trEl)); pop.innerHTML = card(shown[i]); pop.hidden = false; place(trEl); };
    const closeCard = () => { open = -1; pop.hidden = true; [...body.children].forEach((x) => x.classList.remove("hv")); };
    const draw = () => {
      shown = rows.filter(hit); body.innerHTML = shown.map(tr).join(""); closeCard(); $("#inone").hidden = shown.length > 0;
      [...body.children].forEach((trEl, i) => { trEl.onmouseenter = () => { if (matchMedia("(hover:hover)").matches) openCard(i); }; trEl.onfocus = () => { if (trEl.matches(":focus-visible")) openCard(i); }; trEl.onclick = (e) => { if (e.target.closest("a") || matchMedia("(hover:hover)").matches) return; open === i ? closeCard() : openCard(i); }; });   // a mouse already opened the card by hovering; a finger has no hover, so a tap opens and closes it
    };
    wrap.onmouseleave = () => { if (matchMedia("(hover:hover)").matches) closeCard(); };
    wrap.onkeydown = (e) => { if (e.key === "Escape") closeCard(); };
    $("#isearch").oninput = (e) => { q = e.target.value.trim().toLowerCase(); draw(); };
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
