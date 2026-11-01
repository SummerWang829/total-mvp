/* 点受访者编号打开的窗口（取代原来的整页 #/i/…）：左边录像 + 可拖动的时间线（谁在说话、洞察、已剪片段），右边转写和页签。
   拖时间线或点任意一句，视频和转写一起跳；← → 前后 5 秒，空格播放 / 暂停。洞察、片段、引用与追问、情绪、发言情况、分歧地图沿用 index.html 里原来的页签函数，
   窗口挂在 #app 里，它们写进 #tab；它们刷新自己时调用的 render() 在窗口打开期间只刷新当前页签。依赖 index.html / app.js 里的 api / esc / modal / toast / guard / $ / fmt / speakerName / STATUS / STAFF / ME / runJob / showInvite。 */
const RSP = { open: false, iid: null, tab: "transcript", list: [], listLabel: "", refresh: null, close: null };
const RSP_TABS_ALL = [["transcript", "转写"], ["insights", "洞察"], ["clips", "片段"]];
const RSP_SPEAKER_COLOR = { moderator: "#9FC9B8", participant: "#1F6B54" };

/** The people the arrows ("上一位 / 下一位") walk through: the page that opens the window says who is in its list. */
function rspSetList(ids) { RSP.list = ids || []; }

// while the window is open, an old tab function that asks the page to redraw only redraws the window's tab (installed the first time the window opens)
function rspInstall() {
  if (RSP.patched) return; RSP.patched = true;
  const page = render;
  render = function () {   // eslint-disable-line no-global-assign
    if (!(RSP.open && RSP.refresh)) return page();
    if (location.hash !== RSP.base) { const m = /^#\/i\/[^/]+\/?(.*)$/.exec(location.hash); history.replaceState(history.state, "", RSP.base); if (m && m[1]) RSP.tab = m[1]; }   // an old function set the hash to #/i/…: keep the page's own address
    return RSP.refresh();
  };
}

async function openRespondent(iid, tab = "transcript") {
  rspInstall();
  if (RSP.open && RSP.close) RSP.close(false);
  const staff = STAFF(), obs = ME.role === "observer", my = renderSeq;
  const it = await api(`/interviews/${iid}`);
  if (my !== renderSeq) return;   // the user moved on to another page while this was loading
  const root = document.createElement("div"); root.className = "rsp"; root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true"); root.setAttribute("aria-label", "受访者");
  app.appendChild(root); document.body.classList.add("rsp-open");
  const [t, c] = STATUS[it.status] || [it.status, ""];
  const canRoom = it.status !== "ended" && !it.purged;
  const room = (as, label, cls) => `<a class="btn s ${cls}" href="/room.html?i=${iid}&as=${as}" target="_blank" rel="noopener">${label}</a>`;
  const tabs = [...RSP_TABS_ALL, ...(staff ? [["highlights", "引用与追问"], ["signals", "情绪"]] : []), ...(it.kind === "group" ? [["fairness", "发言情况"], ...(staff ? [["disagree", "分歧地图"]] : [])] : []), ...(staff ? [["info", "资料"]] : [])];
  const idx = RSP.list.indexOf(iid), hasNav = RSP.list.length > 1 && idx >= 0;
  const planned = it.duration_min || 45;
  root.innerHTML = `<button type="button" class="rsp-tab" id="rsp-col" aria-label="收起">›</button><div class="rsp-md" tabindex="-1"><div class="rsp-hd"><b>${esc(it.kind === "group" ? "座谈会" : (it.participant.code || "受访者"))}</b>${staff && it.kind !== "group" ? `<span class="mut">${esc(it.participant.name)}</span>` : ""}<span class="tag ${c}">${t}</span>
      ${obs ? "" : `<span class="tag">${esc(it.participant.country || "—")} · ${esc(LANGS[it.language] || it.language)}</span><span class="tag">预计 ${planned} 分${it.actual_min != null ? ` / 访谈 ${it.actual_min} 分` : ""}</span>`}<span class="sp"></span>
      ${canRoom ? (obs ? room("observer", "隐身观察", "") : staff ? room("moderator", "进入房间", "p") + room("observer", "隐身观察", "") : room("moderator", "进入房间", "p")) : ""}
      ${hasNav ? `<button type="button" class="s" id="rsp-prev" ${idx > 0 ? "" : "disabled"}>上一位</button><button type="button" class="s" id="rsp-next" ${idx < RSP.list.length - 1 ? "" : "disabled"}>下一位</button>` : ""}
      <button type="button" class="s" id="rsp-x" aria-label="关闭">✕</button></div>
    <div class="rsp-cols"><div class="rsp-l" id="rsp-l"></div><div class="rsp-r"><div class="rsp-tabs" role="tablist">${tabs.map(([k, n]) => `<button type="button" role="tab" data-rt="${k}" class="${tab === k ? "on" : ""}" aria-selected="${tab === k}">${n}</button>`).join("")}</div><div class="rsp-sc" id="tab"></div></div></div>
    <div class="rsp-ft">拖动时间线或点任意一句，视频和转写一起跳转；← → 前后 5 秒，空格播放 / 暂停</div></div>`;
  RSP.open = true; RSP.iid = iid; RSP.tab = tab; RSP.base = location.hash;
  const prevFocus = document.activeElement;
  function onPop() { if (RSP.open && RSP.close) done(false); }
  const done = (pop = true) => { window.removeEventListener("popstate", onPop); document.removeEventListener("keydown", onKey, true); const v = $("#rsp-v"); if (v) v.pause(); root.remove(); document.body.classList.remove("rsp-open", "rsp-collapsed"); RSP.open = false; RSP.refresh = null; RSP.close = null; if (prevFocus && prevFocus.focus) prevFocus.focus(); if (pop && history.state && history.state.rsp) history.back(); };
  RSP.close = done;
  const state = { video: null, segs: [], insights: [], clips: [], recs: [], dur: 0, sel: null };
  /* ----- keyboard ----- */
  const onKey = (e) => {
    if (e.key === "Escape") { if (document.querySelector(".modal")) return; e.preventDefault(); done(); return; }
    if (e.target.closest("input,textarea,select,[contenteditable]")) return;
    const v = state.video; if (!v) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); v.currentTime = Math.max(0, v.currentTime - 5); }
    else if (e.key === "ArrowRight") { e.preventDefault(); v.currentTime = Math.min(v.duration || 9e9, v.currentTime + 5); }
    else if (e.key === " " && !e.target.closest("button,a")) { e.preventDefault(); v.paused ? v.play().catch(() => {}) : v.pause(); }
  };
  document.addEventListener("keydown", onKey, true);
  $("#rsp-x").onclick = () => done();
  $("#rsp-col").onclick = () => { const c = document.body.classList.toggle("rsp-collapsed"); $("#rsp-col").textContent = c ? "‹" : "›"; $("#rsp-col").setAttribute("aria-label", c ? "展开" : "收起"); };
  if (hasNav) { $("#rsp-prev").onclick = () => openRespondent(RSP.list[idx - 1], RSP.tab); $("#rsp-next").onclick = () => openRespondent(RSP.list[idx + 1], RSP.tab); }
  history.pushState({ rsp: iid }, "");   // the browser's Back button closes the window
  window.addEventListener("popstate", onPop);
  root.querySelector(".rsp-md").focus();

  /* ----- the right side: tabs ----- */
  const show = async (k) => {
    RSP.tab = k; root.querySelectorAll("[data-rt]").forEach((b) => { const on = b.dataset.rt === k; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
    const box = $("#tab"); box.innerHTML = `<div class="mut" style="padding:12px">加载中…</div>`;
    try {
      if (k === "transcript") return await transcript(box);
      if (k === "insights") return await insightsTab(iid, staff, my);
      if (k === "clips") return await clipsTab(iid, staff, my);
      if (k === "highlights" && staff) return await highlightsTab(iid, my);
      if (k === "signals" && staff) return await signalsTab(iid, my);
      if (k === "fairness") return await fairnessTab(iid, my);
      if (k === "disagree" && staff) return await disagreeTab(iid, it, my);
      if (k === "info" && staff) return await info(box);
    } catch (e) { box.innerHTML = `<div class="card empty"><b>${esc(e.message)}</b></div>`; }
  };
  RSP.refresh = async () => { await loadData(); await show(RSP.tab); drawTimeline(); };
  root.querySelectorAll("[data-rt]").forEach((b) => (b.onclick = () => show(b.dataset.rt)));

  /* ----- data for the timeline and the transcript ----- */
  async function loadData() {
    const [segs, ins, clips, recs] = await Promise.all([api(`/interviews/${iid}/transcript`).catch(() => []), api(`/interviews/${iid}/insights`).catch(() => []), api(`/interviews/${iid}/clips`).catch(() => []), staff ? api(`/interviews/${iid}/recordings`).catch(() => []) : []]);
    state.segs = segs; state.insights = ins; state.clips = clips; state.recs = recs;
    const rec = recs.find((r) => r.status === "ready" && r.duration_ms) || recs.find((r) => r.duration_ms);
    state.dur = Math.max((rec && rec.duration_ms) || 0, segs.length ? segs[segs.length - 1].end_ms : 0, 1000);
  }
  /* ----- the left side: video, controls, timeline ----- */
  const left = $("#rsp-l");
  left.innerHTML = `<div class="rsp-vid" id="rsp-vbox"><div class="mut sm" id="rsp-vmsg">正在加载录像…</div></div>
    <div class="rsp-ctl"><button type="button" class="rsp-play" id="rsp-play" aria-label="播放 / 暂停">▶</button><span id="rsp-tm" class="mut">00:00 / 00:00</span><span class="sp"></span>${[0.75, 1, 1.5, 2].map((r) => `<button type="button" class="s rsp-rate${r === 1 ? " on" : ""}" data-rate="${r}">${r}×</button>`).join("")}</div>
    <div class="rsp-tl" id="rsp-tl" role="slider" tabindex="0" aria-label="播放位置" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="rsp-lab">说话</div><div class="rsp-tr" id="rsp-sp"></div><div class="rsp-lab r">洞察 ▲　片段</div><div id="rsp-mk"></div><div class="rsp-sel" id="rsp-sel" hidden><i data-h="a"></i><i data-h="b"></i></div><div class="rsp-ph" id="rsp-ph"></div></div>
    <div class="rsp-lg"><span class="a">主持人</span><span class="b">受访者</span><span class="d">洞察</span><span class="c">已剪片段</span></div>
    ${staff ? `<div class="row" id="rsp-clipbar"><button type="button" class="p" id="rsp-cut" disabled>剪成片段</button><span class="sm mut" id="rsp-cutmsg"></span></div>` : ""}`;
  const pct = (ms) => `${Math.max(0, Math.min(100, (ms / state.dur) * 100))}%`;
  function drawTimeline() {
    $("#rsp-sp").innerHTML = state.segs.map((s) => `<i style="left:${pct(s.start_ms)};width:${Math.max(0.3, ((s.end_ms - s.start_ms) / state.dur) * 100)}%;background:${RSP_SPEAKER_COLOR[s.speaker] || "#9FC9B8"}"></i>`).join("");
    $("#rsp-mk").innerHTML = state.clips.map((c) => `<b class="rsp-cl" style="left:${pct(c.start_ms)};width:${Math.max(0.6, ((c.end_ms - c.start_ms) / state.dur) * 100)}%" title="${esc(c.title)}"></b>`).join("")
      + state.insights.flatMap((i) => i.evidence.map((e) => `<u class="rsp-mkr" style="left:${pct(e.start_ms)}" title="${esc(i.theme)}"></u>`)).join("");
    draw();
  }
  function draw() {
    const v = state.video, now = v ? v.currentTime * 1000 : (RSP.pos || 0), dur = v && v.duration ? v.duration * 1000 : state.dur;
    $("#rsp-ph").style.left = `${Math.max(0, Math.min(100, (now / dur) * 100))}%`; $("#rsp-tl").setAttribute("aria-valuenow", String(Math.round((now / dur) * 100)));
    $("#rsp-tm").textContent = `${fmt(now)} / ${fmt(dur)}`;
    const cur = state.segs.findLastIndex ? state.segs.findLastIndex((s) => s.start_ms <= now) : -1;
    root.querySelectorAll(".rsp-ln").forEach((el) => el.classList.toggle("on", +el.dataset.i === cur));
    if (state.autoscroll && cur >= 0) { const el = root.querySelector(`.rsp-ln[data-i="${cur}"]`); if (el) el.scrollIntoView({ block: "nearest" }); }
  }
  const seek = (ms) => { RSP.pos = ms; if (state.video) { state.video.currentTime = ms / 1000; } draw(); };
  const tl = $("#rsp-tl");
  const at = (e) => { const r = tl.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (state.video && state.video.duration ? state.video.duration * 1000 : state.dur); };
  let drag = null;
  tl.onpointerdown = (e) => {
    const h = e.target.closest("[data-h]");
    drag = h ? h.dataset.h : "seek"; tl.setPointerCapture(e.pointerId);
    if (drag === "seek") seek(at(e));
  };
  tl.onpointermove = (e) => {
    if (!drag) return; const ms = at(e);
    if (drag === "seek") return seek(ms);
    const s = state.sel; if (!s) return;
    if (drag === "a") s.a = Math.min(ms, s.b - 1000); else s.b = Math.max(ms, s.a + 1000);
    drawSel();
  };
  tl.onpointerup = () => { drag = null; };
  tl.onkeydown = (e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); seek(Math.max(0, (state.video ? state.video.currentTime * 1000 : RSP.pos || 0) + (e.key === "ArrowLeft" ? -5000 : 5000))); } };
  function drawSel() {
    const s = state.sel, el = $("#rsp-sel"); if (!el) return; el.hidden = !s;
    if (!$("#rsp-cut")) return;   // only the team can cut clips
    if (!s) { $("#rsp-cut").disabled = true; $("#rsp-cutmsg").textContent = ""; return; }
    const d = state.video && state.video.duration ? state.video.duration * 1000 : state.dur;
    el.style.left = `${(s.a / d) * 100}%`; el.style.width = `${((s.b - s.a) / d) * 100}%`;
    $("#rsp-cut").disabled = false; $("#rsp-cutmsg").textContent = `${fmt(s.a)} – ${fmt(s.b)}（${Math.round((s.b - s.a) / 1000)} 秒）· 拖橙框两端调整`;
  }
  const cut = $("#rsp-cut");
  if (cut) {
    const startSel = () => { const now = state.video ? state.video.currentTime * 1000 : RSP.pos || 0; state.sel = { a: now, b: Math.min(now + 15000, state.dur) }; if (state.sel.b - state.sel.a < 1000) state.sel.a = Math.max(0, state.sel.b - 15000); drawSel(); };
    $("#rsp-clipbar").insertAdjacentHTML("afterbegin", `<button type="button" id="rsp-pick">选取片段</button>`);
    $("#rsp-pick").onclick = startSel;
    cut.onclick = guard(async () => {
      const s = state.sel; if (!s) return; const title = prompt("片段的名字（可不填）", "") ;
      if (title === null) return;
      await api(`/interviews/${iid}/clips`, { method: "POST", body: { start_ms: Math.round(s.a), end_ms: Math.round(s.b), title: (title || "").trim() || undefined } });
      toast("已生成片段"); state.sel = null; await loadData(); drawTimeline(); drawSel(); if (RSP.tab === "clips") show("clips");
    });
  }
  left.querySelectorAll("[data-rate]").forEach((b) => (b.onclick = () => { left.querySelectorAll(".rsp-rate").forEach((x) => x.classList.toggle("on", x === b)); if (state.video) state.video.playbackRate = +b.dataset.rate; }));
  $("#rsp-play").onclick = () => { const v = state.video; if (v) (v.paused ? v.play().catch(() => {}) : v.pause()); };

  /* ----- the video (the team only: observers and the client never get the recording) ----- */
  async function mountVideo() {
    const box = $("#rsp-vbox"), play = state.recs.find((r) => r.playable);
    if (!staff || !play) { box.innerHTML = `<div class="mut sm" style="padding:0 12px;text-align:center">${staff ? "没有可播放的录像（没有录制，或已按保留期清除）。转写和时间线仍可看。" : "这里不显示录像。"}</div>`; return; }
    const v = document.createElement("video"); v.id = "rsp-v"; v.controls = false; v.playsInline = true; v.setAttribute("controlsList", "nodownload"); v.disablePictureInPicture = true;
    box.innerHTML = ""; box.appendChild(v); state.video = v;
    v.addEventListener("timeupdate", draw); v.addEventListener("loadedmetadata", () => { drawTimeline(); }); v.addEventListener("play", () => { $("#rsp-play").textContent = "❚❚"; state.autoscroll = true; }); v.addEventListener("pause", () => { $("#rsp-play").textContent = "▶"; });
    v.addEventListener("error", () => { box.insertAdjacentHTML("beforeend", `<div class="mut sm" id="pverr">这个浏览器暂时无法播放录像，可以换 Chrome 或 Safari。</div>`); });
    const r = await api(`/interviews/${iid}/recordings/${play.id}/play`, { method: "POST" }); v.src = r.url;
  }

  /* ----- the transcript tab (the list follows the video; click a line to jump) ----- */
  async function transcript(box) {
    const segs = state.segs, ready = state.recs.find((r) => r.status === "ready"), busy = state.recs.some((r) => ["recording", "processing"].includes(r.status));
    const caps = staff ? await api(`/interviews/${iid}/captions?after=0`).catch(() => ({ lines: [] })) : { lines: [] };
    box.innerHTML = `<div class="rsp-tools"><input id="tsearch" type="search" placeholder="在这场转写里找" maxlength="100" aria-label="在转写里找" ${segs.length ? "" : "hidden"}><span class="sm mut" id="tcount"></span><span class="sp"></span>
        ${staff ? `<button type="button" class="${segs.length ? "" : "p"}" id="tr" ${ready ? "" : "disabled"} title="${ready ? "" : "有录制并合成完成后才能转写"}">${segs.length ? "重新转写" : "转写录制"}</button>${caps.lines.length ? `<button type="button" id="fromcaps">用实时字幕生成转写（${caps.lines.length} 句）</button>` : ""}` : ""}</div>
      ${segs.length ? segs.map((s, i) => `<div class="rsp-ln${s.speaker === "participant" ? " p" : ""}" data-i="${i}" data-t="${s.start_ms}" data-segtext="${esc((s.text_orig + " " + (s.text_zh || "")).toLowerCase())}" tabindex="0"><b>${fmt(s.start_ms)} ${esc(speakerName(s.speaker))}</b><span><div>${esc(s.text_orig)}</div>${s.text_zh ? `<div class="zh">${esc(s.text_zh)}</div>` : ""}${staff ? `<small class="rsp-sa"><a href="#" data-edit="${i}">校对</a>${s.speaker !== "moderator" ? ` · <a href="#" data-ins="${i}">写一条洞察</a>` : ""}</small>` : ""}</span></div>`).join("")
        : `<div class="card empty"><b>还没有转写</b><span>${busy ? "录制正在处理，这里会自动刷新。" : staff ? "有录制后点“转写录制”，或用实时字幕生成。" : "转写完成后会显示在这里。"}</span></div>`}`;
    box.querySelectorAll(".rsp-ln").forEach((l) => (l.onclick = (e) => { if (e.target.closest("a")) return; state.autoscroll = false; seek(+l.dataset.t); }));
    const ts = $("#tsearch"); if (ts) ts.oninput = () => { const q = ts.value.trim().toLowerCase(); let n = 0; box.querySelectorAll(".rsp-ln").forEach((d) => { const s = !q || d.dataset.segtext.includes(q); d.style.display = s ? "" : "none"; n += s; }); $("#tcount").textContent = q ? `${n} 句包含“${ts.value.trim()}”` : ""; };
    const b = $("#tr"); if (b) b.onclick = guard(async () => { if (segs.length && !confirm("现在的转写会被新的转写替换。继续吗？")) return; b.disabled = true; b.textContent = "转写中，请稍等…"; try { await runJob(iid, "transcribe"); toast("转写完成"); await RSP.refresh(); } finally { b.disabled = false; } });
    const fc = $("#fromcaps"); if (fc) fc.onclick = guard(async () => { if (segs.length && !confirm("现在的转写会被字幕生成的内容替换。继续吗？")) return; await api(`/interviews/${iid}/transcript/from-captions`, { method: "POST" }); toast("已用实时字幕生成转写"); await RSP.refresh(); });
    box.querySelectorAll("[data-edit]").forEach((a) => (a.onclick = (e) => {
      e.preventDefault(); const sg = segs[+a.dataset.edit];
      const m = modal(`<h2>校对转写 #${sg.idx}</h2><p class="sm mut">${esc(speakerName(sg.speaker))} · ${fmt(sg.start_ms)}。改动会记入审计日志。</p>
        <form id="f"><label class="f"><span>原文</span><textarea name="text_orig" rows="3" required>${esc(sg.text_orig)}</textarea></label><label class="f"><span>中文翻译</span><textarea name="text_zh" rows="3">${esc(sg.text_zh)}</textarea></label>
        <div class="row"><button class="p">保存</button><button type="button" id="c">取消</button></div></form>`);
      $("#c", m).onclick = () => m.remove();
      $("#f", m).onsubmit = guard(async (ev) => { ev.preventDefault(); const fd = new FormData(ev.target); const r = await api(`/transcript/${sg.id}`, { method: "PATCH", body: { text_orig: fd.get("text_orig"), text_zh: fd.get("text_zh") } }); m.remove(); toast(r.insights_flagged ? `已保存，${r.insights_flagged} 条洞察需要重新复核` : "已保存"); await RSP.refresh(); });
    }));
    box.querySelectorAll("[data-ins]").forEach((a) => (a.onclick = (e) => {
      e.preventDefault(); const sg = segs[+a.dataset.ins];
      const m = modal(`<h2>写一条洞察</h2>
        <form id="f"><label class="f"><span>主题（几个字）</span><input name="theme" required maxlength="200"></label><label class="f"><span>洞察（一句话）</span><textarea name="statement" rows="2" required></textarea></label>
        <label class="f"><span>证据原话</span><textarea name="quote" rows="3" required>${esc(sg.text_zh || sg.text_orig)}</textarea></label>
        <label class="f"><span>证据语言</span><select name="lang"><option value="${sg.text_zh ? "zh" : "orig"}">${sg.text_zh ? "中文翻译" : "原文"}</option>${sg.text_zh ? `<option value="orig">原文</option>` : ""}</select></label>
        <div class="row"><button class="p">保存洞察</button><button type="button" id="c">取消</button></div></form>`);
      $("#c", m).onclick = () => m.remove();
      $("[name=lang]", m).onchange = (ev) => { $("[name=quote]", m).value = ev.target.value === "orig" ? sg.text_orig : sg.text_zh; };
      $("#f", m).onsubmit = guard(async (ev) => { ev.preventDefault(); const fd = new FormData(ev.target); await api(`/interviews/${iid}/insights`, { method: "POST", body: { theme: fd.get("theme"), statement: fd.get("statement"), evidence: [{ segment_id: sg.id, quote: fd.get("quote"), lang: fd.get("lang") }] } }); m.remove(); toast("已保存，待审核"); await RSP.refresh(); show("insights"); });
    }));
    draw();
    if (busy) { clearTimeout(window._recT); window._recT = setTimeout(() => { if (RSP.open && RSP.iid === iid && RSP.tab === "transcript") RSP.refresh(); }, 3000); }
  }

  /* ----- 资料: what the person answered in the questionnaire, the invitation, candidate times, pre-interview questions, the material they marked ----- */
  async function info(box) {
    const full = await api(`/interviews/${iid}`);
    const seats = full.kind === "group" && full.group ? `<div class="card" id="groupcard"><h3>座谈会名单</h3><ul class="ptlist">${full.group.seats.map((s) => `<li><b>${esc(s.label)}</b> ${esc(s.name)} <span class="tag ${s.entered ? "ok" : ""}">${s.entered ? "已进入" : s.device_ok ? "检测通过" : "未到"}</span>${canRoom ? ` <button class="s" data-grot="${s.participant_id}">重新生成链接</button>` : ""}</li>`).join("")}</ul></div>` : "";
    box.innerHTML = `${seats}<div class="card"><h3>关键回答</h3>${(full.key_answers || []).length ? full.key_answers.map((a) => `<div class="rmq"><small>${esc(a.q)}</small>${esc(a.a)}</div>`).join("") : `<div class="mut sm">没有问卷答案。</div>`}</div>
      <div class="card"><h3>邀请</h3><div class="row sm"><span class="mut">邀请进度</span><span class="tag">${esc(({ sent: "已发送", opened: "已打开链接", device_ok: "设备已通过", consented: "已同意", entered: "已进房", done: "已完成", ended_absent: "未到场" })[full.invite_state] || "—")}</span><span class="sp"></span>
        ${canRoom && full.kind !== "group" ? `<button type="button" class="s" id="rot">重新生成邀请链接</button>` : ""}</div></div>
      <div class="card" id="timeslotcard"><p class="sm mut" style="margin:0">候选时间加载中…</p></div><div class="card" id="pretaskcard"><p class="sm mut" style="margin:0">访谈前问题加载中…</p></div><div class="card" id="markcard" hidden></div>`;
    const rot = $("#rot"); if (rot) rot.onclick = guard(async () => { if (!confirm("重新生成后，旧链接立刻失效。继续吗？")) return; const r = await api(`/interviews/${iid}/invite/rotate`, { method: "POST" }); showInvite(iid, location.origin + r.invite_path, false, full.scheduled_at, () => {}); });
    box.querySelectorAll("[data-grot]").forEach((b) => (b.onclick = guard(async () => { const r = await api(`/interviews/${iid}/participants/${b.dataset.grot}/invite/rotate`, { method: "POST" }); showInvite(iid + "_" + b.dataset.grot, location.origin + r.invite_path, false, full.scheduled_at, () => {}); })));
    loadTimeslotsCard(iid, canRoom); loadPretaskCard(iid, canRoom); loadMarksCard(iid);
  }

  await loadData();
  drawTimeline(); drawSel();
  mountVideo();
  await show(tab);
}

// every link to the old whole page (#/i/<id>[/tab]) opens the window instead; a pasted address does the same
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='#/i/']"); if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey) return;
  const [id, tab] = a.getAttribute("href").slice(4).split("/"); e.preventDefault(); openRespondent(id, tab || "transcript");
});
