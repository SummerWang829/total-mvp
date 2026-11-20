/* 甄别设定页的“招募问卷”工作台：左边和 AI 对话（说一句话，问卷就跟着改），右边是实时大纲（研究背景、研究目标、招募需求、问卷流程），
   左右两个框各自滚动，整页不滚。大纲里每道题也能直接点开手动改（题型、选项、跳转、标签）。切到“测试答题”就是候选人看到的样子；
   写好发布成链接和二维码，候选人在 screen.html 作答，合格的人自动进受访者名单。也可以改成导入在别的系统里做好的 HTML 问卷链接。
   依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）和 screen_run.js。 */
const RF_TYPE = { single: "选择题", multi: "多选题", text: "开放题", number: "数字题", scale: "量表题" };
const RF_OP = { lt: "小于", lte: "不大于", gt: "大于", gte: "不小于", eq: "等于" };
const RF_IC = {
  list: '<path d="M9 4h6a1 1 0 0 1 1 1v1H8V5a1 1 0 0 1 1-1z"/><rect x="5" y="6" width="14" height="15" rx="2"/><path d="M9 12h6M9 16h4"/>',
  eye: '<circle cx="12" cy="12" r="8"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7v.5"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>',
  undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
  redo: '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7"/>',
  collapse: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m15 10-2 2 2 2"/>',
  expand: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="m13 10 2 2-2 2"/>',
  chev: '<path d="m6 9 6 6 6-6"/>',
  dots: '<circle cx="12" cy="5" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="19" r="1.3" fill="currentColor"/>',
  grip: '<circle cx="9" cy="6" r="1.2" fill="currentColor"/><circle cx="9" cy="12" r="1.2" fill="currentColor"/><circle cx="9" cy="18" r="1.2" fill="currentColor"/><circle cx="15" cy="6" r="1.2" fill="currentColor"/><circle cx="15" cy="12" r="1.2" fill="currentColor"/><circle cx="15" cy="18" r="1.2" fill="currentColor"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  spark: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  img: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  stop2: '<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  spin: '<path d="M21 12a9 9 0 1 1-9-9"/><path d="M12 3a9 9 0 0 1 9 9" opacity=".35"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 3-4.6 5.5-4.6s4.9 1.6 5.5 4.6"/><circle cx="17" cy="9" r="2.6"/><path d="M16 14.8c2.4.2 4.1 1.7 4.5 4.2"/>',
};
const rfIcon = (d) => `<svg class="rf-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
/** A text box as tall as its text: at least 2 lines, at most 8; beyond that it scrolls inside. */
function rfAutosize(t) {
  const fit = () => {
    const cs = getComputedStyle(t), lh = parseFloat(cs.lineHeight) || 24, pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    t.style.height = "auto";
    const max = lh * 8 + pad;
    t.style.height = Math.min(Math.max(t.scrollHeight, lh * 2 + pad), max) + "px";
    t.style.overflowY = t.scrollHeight > max + 1 ? "auto" : "hidden";
  };
  t.addEventListener("input", fit); fit();
}

/** 整个“招募问卷”卡片，返回 { html, wire, fit }。 */
function rfCard(p, form, rec, plannedN) {
  let f = form, mode = form.source === "html" ? "html" : "ai", seg = "outline", Q = JSON.parse(JSON.stringify(form.questions || [])), goals = [...(form.goals || [])], need = form.need || "";
  const rm = (rec && rec.recruit_mode) || "screen_then_invite", hm = (rec && rec.host_mode) || "human";   // 招募方式 / 主持方式（决定阶段增减）
  if (rm === "onelink") { mode = "ai"; f = { ...f, source: "ai", source_confirmed: "ai" }; }   // 一站式 = AI 问卷 + AI 访谈，来源锁定为 AI，不弹来源选择、不可切换
  let timer = null, hist = [], redo = [], editing = new Set();
  let ver = "", vers = null;   // the language version being looked at (""/the source = the original) and the versions' states (app/static/rtrans.js)
  const isSource = () => !ver || (vers && ver === vers.source);
  let olSum = null;   // the interview outline as the 访谈 page has it (summary), shown on the right as the 访谈大纲结构
  const loadOl = () => !STAFF() ? Promise.resolve() : api(`/projects/${p.id}/outline`).then((r) => { olSum = r.summary || null; drawRight(); }).catch(() => {});
  let changedIds = new Set();   // the questions the last change of the dialog touched ("刚改")
  const diffIds = (before, after) => { const b = Object.fromEntries(before.map((q) => [q.id, JSON.stringify(q)])); return new Set(after.filter((q) => b[q.id] !== JSON.stringify(q)).map((q) => q.id)); };
  let htmlLocked = !!form.html_url;   // the saved link is read-only until 编辑 is pressed (2026-11-03)
  let menu = null;   // the open ⋮ menu of a question ("q:<id>") or an option ("o:<id>:<number>"); one at a time
  let typeMenuOpen = null;   // 哪道题的题型下拉正打开（qid）
  let askOpen = null, askBusy = false, askFail = "", askDone = false, askDraft = {};   // 题旁「问 AI / 改这题」：正在改哪一题（面板就长在那道题里）
  let busy = false, focusGrip = null;   // busy: a moved card is still landing; focusGrip: the handle to focus after a keyboard move
  const snap = () => JSON.stringify({ Q, goals, need });
  const nextId = () => { let n = 0; Q.forEach((q) => { const m = /^q(\d+)$/.exec(q.id); if (m) n = Math.max(n, +m[1]); }); return "q" + (n + 1); };
  const optId = (q) => { let n = 0; q.options.forEach((o) => { const m = /^o(\d+)$/.exec(o.id); if (m) n = Math.max(n, +m[1]); }); return "o" + (n + 1); };
  const blank = () => [{ id: "o1", text: "", goto: "", tag: "" }, { id: "o2", text: "", goto: "", tag: "" }];
  const fix = () => {  // a jump may only go forward to a question that exists: drop the ones that no longer do
    Q.forEach((q, i) => {
      const ok = (g) => !g || g === "end_out" || g === "end_ok" || Q.findIndex((x) => x.id === g) > i;
      q.options.forEach((o) => { if (!ok(o.goto)) o.goto = ""; }); q.rules = (q.rules || []).filter((r) => ok(r.goto) && r.goto);
    });
  };
  const incomplete = () => Q.some((q) => !q.text.trim() || q.options.some((o) => !o.text.trim()) || (q.rules || []).some((r) => !Number.isFinite(+r.value)));  // an empty question or option is not sent anywhere yet
  const body = () => ({ source: "ai", idea: f.idea || "", goals, need, questions: Q });
  const stat = (t) => { const s = $("#rfstat"); if (s) s.textContent = t; };
  // an unfinished option or question is only reported in the status line; publishing checks it again
  const save = () => { clearTimeout(timer); if (incomplete()) return stat("有题目或选项还没写完，写完会自动保存"); timer = setTimeout(async () => { try { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: body() })) }; stat("已保存"); refreshVers(); } catch (e) { stat("还没保存：" + e.message); } }, 700); stat("保存中…"); };
  const pushHist = () => { hist.push(snap()); if (hist.length > 30) hist.shift(); redo = []; syncUndo(); };
  const apply = (s) => { const o = JSON.parse(s); Q = o.Q; goals = o.goals; need = o.need; editing = new Set(); };
  const syncUndo = () => { const u = $("#rfundo"), r = $("#rfredo"); if (u) u.disabled = !hist.length; if (r) r.disabled = !(Q.length || goals.length || need); };
  const changed = () => { fix(); save(); changedIds = new Set(); drawRight(); };  // after a manual edit: the "刚改" marks go, suggestions about edited questions are gone with the next save
  const openEx = new Set();  // option ids whose 互斥 picker is open
  const code = (q, o) => q.options.findIndex((x) => x.id === o.id) + 1;
  const exLabel = (q, o) => (o.excl || []).map((id) => q.options.findIndex((x) => x.id === id) + 1).filter(Boolean).sort((a, b) => a - b).join("、");
  const setExcl = (q, o, other, on) => {  // two-way: ticking "3 cannot go with 10" also ticks "10 cannot go with 3"
    const add = (a, b) => { a.excl = (a.excl || []).filter((x) => x !== b.id); if (on) a.excl.push(b.id); };
    add(o, other); add(other, o);
  };
  // a multi-choice question whose tagged options are not linked as mutually exclusive: the same person may get all those tags, so say so (never guessing what the names mean)
  const taggedPairs = (q) => { const tg = q.type === "multi" ? q.options.filter((o) => o.tag) : []; const out = []; tg.forEach((a, i) => tg.slice(i + 1).forEach((b) => { if (a.tag !== b.tag && !(a.excl || []).includes(b.id)) out.push([a, b]); })); return out; };
  const exWarn = (q) => {
    const pairs = taggedPairs(q); if (!pairs.length) return "";
    const opts = [...new Set(pairs.flat())], nums = opts.map((o) => q.options.indexOf(o) + 1).sort((a, b) => a - b).join("、"), tags = [...new Set(opts.map((o) => o.tag))].join("、");
    return `<div class="rf-warn" role="note">选项 ${nums} 都带标签（${esc(tags)}），又没有设“互斥”：同一个人可能同时拿到这些标签。如果它们不能同时成立，请设为互斥（或把这题改回单选）。<button class="s" type="button" data-exall="${esc(q.id)}">把这几项设为互斥</button></div>`;
  };
  const ref = (g) => (g === "end_out" ? "结束 · 不符合" : g === "end_ok" ? "结束 · 符合" : g ? `跳到第 ${Q.findIndex((x) => x.id === g) + 1} 题` : "");
  const actions = (i, cur, allowNext) => `<option value="" ${cur === "" ? "selected" : ""}>${allowNext ? "下一题" : "选择处理方式"}</option>${Q.slice(i + 1).map((x, k) => `<option value="${x.id}" ${cur === x.id ? "selected" : ""}>跳到第 ${i + k + 2} 题</option>`).join("")}<option value="end_out" ${cur === "end_out" ? "selected" : ""}>结束 · 不符合（筛掉）</option><option value="end_ok" ${cur === "end_ok" ? "selected" : ""}>结束 · 符合</option>`;
  const badge = (g) => (g === "end_out" ? `<span class="rf-lg out">结束 · 不符合</span>` : g === "end_ok" ? `<span class="rf-lg ok">结束 · 符合</span>` : g ? `<span class="rf-lg jump">${ref(g)}</span>` : "");

  /* ---------- left: 导入态 = a short card; 其余 = the stage-by-stage dialog shared with the outline page (creation.js) ---------- */
  const importHtml = () => `<div class="rf-msg">问卷已经导入了，这里不用再出题。<div class="sm mut" style="margin-top:6px">下一步是生成访谈大纲：按这份问卷的筛选字段（常住地区、年龄、到港、商圈、收入）搭结构。</div></div>
      <div class="rf-steps">
        <div><i>1</i><span>确认<b>研究背景 · 研究目标 · 招募需求</b>（右侧可以直接改）</span></div>
        <div><i>2</i><span>生成<b>访谈大纲</b>，按分群（大湾区 / 非大湾区）拆分追问</span></div>
        <div><i>3</i><span>Confirmit 数据回来后，在<b>配额与受访者名单</b>里导入甄别数据</span></div>
      </div>
      <div class="row" style="margin:2px 0 10px"><button class="p" type="button" id="rfgooutline">生成访谈大纲 →</button></div>`;
  const reload = guard(async () => {   // the dialog changed the study: read the questionnaire again and mark the questions it touched
    const before = JSON.parse(JSON.stringify(Q));
    f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`)) }; Q = JSON.parse(JSON.stringify(f.questions || [])); goals = [...(f.goals || [])]; need = f.need || ""; editing = new Set();
    changedIds = diffIds(before, Q); drawRight(); loadOl();
  });
  // ===== 单线六步（2026-11-16 变更包 2C）：欢迎语 → 要素卡片 → 确认背景 → 出题 → 确认问卷 → 配额与形式 =====
  let qfStep = 0, flowText = "", cardShown = false, cardDone = false, cardAnswers = {}, cardCustom = {}, flowBusy = false, showChecks = false, thinking = false, quotaBusy = false, quotaChat = "", fmtDone = false, quotaDone = false;
  let fmtPicked = Array.isArray(f.formats) ? f.formats.slice() : [];
  let quotaList = Array.isArray(f.quotas) ? JSON.parse(JSON.stringify(f.quotas)) : [];
  let frameSug = [], sugPicked = new Set(), sugBusy = false;            // 建议条：第 3/4 步，可多选后确认生效
  let quotaPlan = null, quotaClarify = [], clarifyPick = {}, planBusy = false, quotaRationale = "";  // 配额口径对齐：说明 + 表格 + 选择题
  try { const sv = JSON.parse(sessionStorage.getItem("qf_" + p.id) || "{}"); qfStep = sv.step || 0; flowText = sv.text || ""; cardShown = !!sv.shown; cardDone = !!sv.done; cardAnswers = sv.answers || {}; cardCustom = sv.custom || {}; if (Array.isArray(sv.fmt)) fmtPicked = sv.fmt; if (sv.fmtDone) fmtDone = true; if (sv.quotaDone) quotaDone = true; } catch (e) { /* private window */ }
  const saveFlow = () => { try { sessionStorage.setItem("qf_" + p.id, JSON.stringify({ step: qfStep, text: flowText, shown: cardShown, done: cardDone, answers: cardAnswers, custom: cardCustom, fmt: fmtPicked, fmtDone, quotaDone })); } catch (e) { /* private window */ } };
  // 把自定义输入拼进「研究想法」，供出题时一起交给 AI；固定选项只作引导，不强制回传。
  const cardIdea = () => {
    const parts = [];
    CARD.forEach((q, i) => { const c = (cardCustom[i] || "").trim(); if (c) parts.push(`${q[0]}${c}`); });
    return parts.length ? `${flowText || ""}\n\n（补充要素）\n${parts.join("\n")}` : (flowText || "");
  };
  const CARD = [
    ["这项研究的结果主要用来支撑哪个决策？", ["售后服务流程升级（推荐）", "新车定价策略", "品牌传播口径"]],
    ["谁可以参加？哪些人必须筛掉？", ["近 12 个月有维保（推荐）", "不限", "仅首任车主"]],
    ["要区分哪几组人？", ["一线 / 新一线（推荐）", "按车型", "不分组"]],
    ["问卷最多可以答多久？", ["3 分钟以内（推荐）", "5 分钟", "不限"]],
    ["有没有需要特别小心的话题？", ["没有（推荐）", "涉及品牌", "涉及个人信息"]]
  ];
  const esc2 = (x) => esc(String(x == null ? "" : x));
  const FORMATS = { video: "视频访谈", video_screen: "视频+屏幕共享", audio: "纯音频", text: "文字访谈", text_voice: "文字+语音转录" };
  const fmtLabel = (c) => lbl(FORMATS[c], c);
  const quotaLabel = (q) => q.name || `${q.field}=${q.value}`;
  const fmtToggle = (c) => { fmtPicked = fmtPicked.includes(c) ? fmtPicked.filter((x) => x !== c) : [...fmtPicked, c]; saveFlow(); };
  const fmtBtn = (c) => `<button class="s${fmtPicked.includes(c) ? " on" : ""}" type="button" data-fmt="${c}">${fmtLabel(c)}</button>`;
  // 2026-11-18 用户要求：访谈形式一改就同步到「项目总览 › 语言与受访形式」，两边永远一致（后端 confirm 接口写的就是 proj.formats）
  let fmtSync = "";   // "" | "saving" | "ok" | 错误文案
  const fmtSyncNote = () => {
    if (!fmtDone || !fmtPicked.length) return "";
    if (fmtSync === "saving") return `<div class="qf-hint">正在同步到项目总览…</div>`;
    if (fmtSync && fmtSync !== "ok") return `<div class="qf-hint" style="color:#B3261E">同步失败：${esc2(fmtSync)}</div>`;
    return `<div class="qf-hint" style="color:#1C7A55">✓ 已同步到「项目总览 › 语言与受访形式」：${esc2(fmtPicked.map(fmtLabel).join("、"))}</div>`;
  };
  const syncFormats = async () => {
    if (!fmtPicked.length) return;
    fmtSync = "saving"; drawLeft();
    try {
      const r = await api(`/projects/${p.id}/recruiting/form/questions/confirm`, { method: "POST", body: { formats: fmtPicked, quotas: [] } });
      if (Array.isArray(r.formats) && r.formats.length) { fmtPicked = r.formats.slice(); f = { ...f, formats: r.formats.slice() }; }
      fmtSync = "ok";
    } catch (e) { fmtSync = e.message || "未知错误"; }
    saveFlow(); drawLeft();
  };
  // 建议条：贴在输入框上方，最多 3 条，可多选，点「确认」把选中的建议交给 AI 生效
  const curSuggestions = () => {
    const st = flowState();
    if (st === 3) return frameSug.slice(0, 3);
    if (st === 4) return (f.suggestions || []).map((x, i) => ({ id: x.sig || `s${i + 1}`, text: String(x.text || x.label || "") })).filter((x) => x.text).slice(0, 3);
    return [];
  };
  const sugBarHtml = () => {
    const list = curSuggestions();
    if (!list.length) return "";
    if (sugBusy) return `<div class="cc-sugbar"><div class="sm mut">正在按要求修改，请稍候…</div></div>`;
    const n = sugPicked.size;
    return `<div class="cc-sugbar"><div class="cc-sughd">建议 <span class="mut">（可多选，最多 ${list.length} 条）</span></div>` +
      list.map((x) => `<button class="cc-sugitem${sugPicked.has(x.id) ? " on" : ""}" type="button" data-sug="${esc2(x.id)}"><span class="cc-sugck">${sugPicked.has(x.id) ? "✓" : ""}</span><span class="cc-sugt">${esc2(x.text)}</span></button>`).join("") +
      `<div class="cc-sugfoot"><button class="p" type="button" id="sugapply"${n ? "" : " disabled"}>${n ? `确认选中的 ${n} 条建议生效` : "选中建议后确认"}</button><button class="s" type="button" id="sugclear">清空</button></div></div>`;
  };
  // 配额口径对齐：说明 + 配额表 + 不清晰项的选择题 → 最后确认表格
  const quotaPanelHtml = () => {
    if (planBusy) return `<div class="qf-panel"><h4>配额组（口径对齐中…）</h4><div class="qf-think"><span></span><span></span><span></span></div><div class="sm mut">正在按研究目标对齐分组口径，请稍候。</div></div>`;
    const rat = quotaRationale ? `<div class="cc-adv"><b>口径说明</b>${esc2(quotaRationale)}</div>` : "";
    const clarify = quotaClarify.length
      ? `<div class="qf-clarify">${quotaClarify.map((c, i) => `<div class="qf-clarq"><b>${i + 1}. ${esc2(c.text)}</b><div class="qf-opts">${c.options.map((o) => `<button type="button" class="s${clarifyPick[i] === o ? " on" : ""}" data-clarify="${i}" data-claropt="${esc2(o)}">${esc2(o)}</button>`).join("")}</div></div>`).join("")}</div>`
      : "";
    const tbl = quotaList.length
      ? `<table><tr><th>分组</th><th>目标人数</th><th>条件</th></tr>${quotaList.map((q) => `<tr><td>${esc2(quotaLabel(q))}</td><td><input class="qf-target" type="number" min="1" max="10000" data-quota-target="${esc2(quotaLabel(q))}" value="${q.target}"></td><td>${esc2(q.field === "answer" ? (q.question || "") : q.value || "")}</td></tr>`).join("")}</table><div class="sm mut">合计 ${quotaList.reduce((s, q) => s + (+q.target || 0), 0)} 人</div>`
      : `<div class="mut">还没有配额组，可直接确认，或让 AI 按目标人群生成。</div>`;
    return `<div class="qf-panel"><h4>配额组（先对齐口径，再确认表格）</h4>${rat}${clarify}${tbl}` +
      `<div class="qf-aimsg"><textarea id="qfquotamsg" rows="2" placeholder="例如：一线城市加到 30 人，新增二线城市 15 人">${esc2(quotaChat)}</textarea><button class="s" type="button" id="qfquotago"${quotaBusy ? " disabled" : ""}>${quotaBusy ? "AI 调整中…" : "让 AI 调整目标人数"}</button></div>` +
      `<div class="qf-forward"><button class="p" type="button" id="qfquotadone">口径与表格没问题，确认配额 →</button><button class="s" type="button" id="qfquotaskip">跳过配额</button></div></div>`;
  };
  // step 6：访谈形式（单独确认）→ 配额组（口径对齐+表格确认）→ 进入测试/名单
  const step6Html = () => {
    if (!fmtDone) {
      return `<div class="cc-ai"><b>问卷已确认</b><br>先确认访谈形式（可多选），确认后再配置配额组。</div>` +
        `<div class="qf-panel"><h4>访谈形式（可多选）</h4><div class="row">${Object.keys(FORMATS).map(fmtBtn).join("")}</div>` +
        `<div class="qf-forward"><button class="p" type="button" id="qffmtgo">确认访谈形式 →</button><div class="qf-hint">确认后再配置配额；之后还可修改</div></div></div>`;
    }
    if (!quotaDone) {
      return `<div class="cc-ai"><b>访谈形式已确认</b>${fmtPicked.length ? `：${esc2(fmtPicked.map(fmtLabel).join("、"))}` : ""}<br>下面按研究目标对齐配额口径，确认表格后即保存。</div>` +
        `<div class="qf-panel"><h4>访谈形式（已确认）</h4><div class="row">${Object.keys(FORMATS).map(fmtBtn).join("")}</div><button class="s" type="button" id="qffmtredo">修改形式</button>${fmtSyncNote()}</div>` +
        quotaPanelHtml();
    }
    return `<div class="cc-ai"><b>访谈形式与配额已保存</b><br>下面可以进入测试答题，或进入「配额与受访者名单」。</div>` +
      `<div class="qf-panel"><h4>访谈形式</h4><div class="row">${Object.keys(FORMATS).map(fmtBtn).join("")}</div>${fmtSyncNote()}` +
      `<h4>配额组</h4>${quotaList.length ? `<table><tr><th>分组</th><th>目标人数</th><th>条件</th></tr>${quotaList.map((q) => `<tr><td>${esc2(quotaLabel(q))}</td><td>${q.target}</td><td>${esc2(q.field === "answer" ? (q.question || "") : q.value || "")}</td></tr>`).join("")}</table>` : `<div class="mut">未设置配额组</div>`}</div>` +
      `<div class="qf-forward" style="margin-top:4px"><button class="p" type="button" id="qftest">进入测试答题 →</button><button class="s" type="button" id="qflist">进入配额与名单</button></div>`;
  };
  const flowState = () => {
    if (qfStep) return qfStep;
    if (f.questions_confirmed) return 6;
    if (Q.length) return 5;
    if (f.framing_confirmed) return 3;
    if (cardShown) return 2;
    return 1;
  };
  const flowHtml = (st) => {
    const hello = `<div class="cc-ai"><b>您好，我是您的 AI 访谈助手。</b>我可以协助您完成招募问卷的设计：<ul style="margin:8px 0 0 18px;padding:0"><li>确认研究背景与研究目标</li><li>定义目标受众与访问场景</li><li>生成 8–12 题甄别问卷</li><li>检查问卷并确认配额与访谈形式</li></ul><div class="sm mut" style="margin-top:8px">请先说明研究目标与受访者；资料不足时我会先向您确认几个问题，再开始起草。</div></div>`;
    let body = "";
    if (st === 1) body = hello;
    if (st === 2) body = `<div class="cc-me">${esc2(flowText || "我想做一次用户研究")}</div><div class="cc-ai">好的，我先确认几个要素再出题。</div>` +
      CARD.map((q, i) => `<div class="qf-card" data-cardi="${i}"><b>确认问卷要素 ${i + 1}/${CARD.length}</b><div>${esc2(q[0])}</div><div class="qf-opts">${q[1].map((o, j) => `<button type="button" class="s${cardAnswers[i] === j && !(cardCustom[i] || "").trim() ? " on" : ""}" data-card="${i}" data-opt="${j}">${esc2(o)}</button>`).join("")}</div><input class="qf-custom" type="text" data-custom="${i}" value="${esc2(cardCustom[i] || "")}" placeholder="或输入自定义…" maxlength="120"></div>`).join("") +
      `<div class="qf-cardfill"><button class="s" type="button" data-fill="1">按推荐填满</button><button class="s" type="button" data-skip="1">跳过</button><button class="p" type="button" data-confirm="1">确认 →</button></div>`;
    if (st === 3) body = thinking
      ? `<div class="cc-me">确认背景与目标，生成问卷</div><div class="cc-ai"><b>正在思考与起草问卷…</b><div class="qf-think"><span></span><span></span><span></span></div><div class="sm mut">正在根据研究背景、目标与受访者条件生成甄别题目，通常需要几秒到十几秒，请稍候。</div></div>`
      : `<div class="cc-me">${esc2(flowText || "按推荐填好了")}</div><div class="cc-ai">研究背景与研究目标已按两轮回答写好，右侧可以直接改；确认后我开始出题。</div>` +
      `<div class="cc-adv"><b>为什么这样建议</b>先锁定决策用途与可参加人群，题目才不会问偏，也不会把不合格的人放进来。</div>` +
      `<div class="qf-forward"><button class="p" type="button" id="qfgo3"${flowBusy ? " disabled" : ""}>确认背景与目标，生成问卷 →</button><div class="qf-hint">此时只有这一个前进按钮；确认前不会出任何题目</div></div>`;
    if (st === 4) body = `<div class="cc-me">确认背景与目标，生成问卷</div><div class="cc-ai"><b>已生成 ${Q.length} 题甄别问卷</b><br>资格题 → 分组题 → 背景题；过去 6 个月真实行为、选项互斥、每个淘汰条件都有「结束 · 不符合」。</div>` +
      `<div class="qf-forward"><button class="p" type="button" id="qfgo5">确认问卷生成 →</button><div class="qf-hint">确认后才写配额、才锁定版本</div></div>`;
    if (st === 5) body = `<div class="cc-ai"><b>问卷已就绪（${Q.length} 题）</b><br>可继续让小星星助手整份改题，也可用每题 💬 只改一题。确认后进入下一步。</div>` +
      `<div class="qf-forward"><button class="p" type="button" id="qfgo5">确认问卷生成 →</button></div>`;
    if (st === 6) body = step6Html();
    const sugN = (f.suggestions || []).length;
    return `<div class="cc-hd"><b>与小星星助手共同创建</b><span class="mut">小星星助手</span><span class="sp"></span><button class="cc-ck${sugN ? " on" : ""}" type="button" id="qfck" aria-expanded="${showChecks}">检查提示 ${sugN}</button></div>` +
      (showChecks ? `<div class="cc-cklist">${sugN ? (f.suggestions || []).map((x) => `<div class="cc-ci warn"><span>${esc2(x.text || x.label)}</span></div>`).join("") : `<div class="mut">现在没有需要处理的提示</div>`}</div>` : "") +
      `<div class="cc-sc" id="ccscroll">${anchorBarHtml()}${body}</div>` +
      sugBarHtml() +
      `<div class="cc-box"><textarea id="ccmsg" data-ti="chat" data-ti-send="#ccsend" rows="2" maxlength="1000" placeholder="说一句话，或点上面的按钮……" aria-label="对话输入"></textarea>` +
      `<button class="p cc-send" type="button" id="ccsend" aria-label="发送">↑</button></div>`;
  };

  /**
   * 2026-11-18 用户要求：AI 改完题目后，对话框要与「被修改的那道题」顶部对齐，
   * 一眼能看出这段对话对应哪道题。做法：右侧把该题滚到顶部、左侧把这条回复滚到顶部，
   * 两栏起始线一致；回复下方再给可点的「Q3 / Q5」锚点，点一下就定位到那道题。
   */
  const alignAnchor = (qid) => {
    const out = $("#rfout");
    if (out && qid) {
      const qEl = out.querySelector(`.rf-q[data-qid="${qid}"]`);
      if (qEl) out.scrollTop = Math.max(0, qEl.offsetTop - 8);
    }
    const sc = $("#ccscroll");
    if (sc) {
      const bar = sc.querySelector(".cc-anchorbar");
      const target = bar || sc.querySelector(".cc-ai:last-of-type");
      if (target) sc.scrollTop = Math.max(0, target.offsetTop - 4);
    }
  };
  const anchorBarHtml = () => {
    if (!changedIds.size) return "";
    const ids = [...changedIds].filter((id) => Q.some((q) => q.id === id)).slice(0, 6);
    if (!ids.length) return "";
    return `<div class="cc-anchorbar"><b>这段对话改的是：</b>` +
      ids.map((id) => `<button type="button" class="cc-anchor" data-anchor="${esc2(id)}">Q${Q.findIndex((q) => q.id === id) + 1}</button>`).join("") +
      `<span class="mut">点题号跳到那道题</span></div>`;
  };

  const wireFlow = () => {
    const send = $("#ccsend"), box = $("#ccmsg");
    if (box) box.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (send) send.click(); } };
    const chatTurn = async (text) => {
      const before = JSON.parse(JSON.stringify(Q));
      const refs = window.TInput ? TInput.takeRefs("ccmsg") : [];   // 修正方案第 1 轮第 2 条: files / links for this message
      const r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: text, questions: Q, goals, need, use_library: true, ask_only: false, refs } });
      if (Array.isArray(r.questions)) { pushHist(); Q = JSON.parse(JSON.stringify(r.questions)); changedIds = diffIds(before, Q); }
      if (Array.isArray(r.goals)) goals = r.goals.slice();
      if (typeof r.need === "string") need = r.need;
      f = { ...f, ...r }; save(); drawLeft(); drawRight(); toast((r.reply || "已更新") + ((r.cites || []).length ? `（用到：${r.cites.map((c) => `资料 ${c.n} ${c.title}`).join("、")}）` : ""));
      const first = [...changedIds][0]; if (first) requestAnimationFrame(() => alignAnchor(first));
    };
    if (send) send.onclick = guard(async () => {
      const text = (box ? box.value : "").trim();
      if (!text || flowBusy) return;
      const st = flowState();
      if (st <= 3) { flowText = text; cardShown = true; qfStep = 2; drawLeft(); return; }
      flowBusy = true;
      try { await chatTurn(text); } catch (e) { toast(e.message, true); }
      flowBusy = false;
    });
    // 点选项/自定义只做局部更新，绝不重绘左栏，避免滚动位置与焦点被重置（用户反馈「点选项自动往上跳」）。
    const setCardUi = (i) => {
      const card = document.querySelector(`[data-cardi="${i}"]`); if (!card) return;
      card.querySelectorAll("[data-opt]").forEach((b) => { const on = cardAnswers[i] === +b.dataset.opt && !(cardCustom[i] || "").trim(); b.classList.toggle("on", on); });
      const inp = card.querySelector("[data-custom]"); if (inp) inp.value = cardCustom[i] || "";
    };
    document.querySelectorAll("[data-card]").forEach((b) => (b.onclick = () => { const i = +b.dataset.card; cardAnswers[i] = +b.dataset.opt; cardCustom[i] = ""; saveFlow(); setCardUi(i); }));
    document.querySelectorAll("[data-custom]").forEach((inp) => (inp.oninput = () => { const i = +inp.dataset.custom; cardCustom[i] = inp.value; if (inp.value.trim()) delete cardAnswers[i]; saveFlow(); setCardUi(i); }));
    const fill = document.querySelector("[data-fill]"); if (fill) fill.onclick = () => { CARD.forEach((_, i) => { cardAnswers[i] = 0; cardCustom[i] = ""; }); saveFlow(); CARD.forEach((_, i) => setCardUi(i)); };
    // 第 3 步：进入时静默拉「背景与目标」建议（还没有题目，用 framing/suggestions）
    const loadFrameSug = async () => {
      if (frameSug.length) return;
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/framing/suggestions`, { method: "POST", body: { brief: f.brief || "", goals, need, idea: cardIdea() } });
        frameSug = (r.suggestions || []).slice(0, 3); drawLeft();
      } catch (e) { /* 建议拿不到不影响流程 */ }
    };
    const enterStep3 = () => { cardDone = true; qfStep = 3; drawLeft(); loadFrameSug(); };
    const skip = document.querySelector("[data-skip]"); if (skip) skip.onclick = enterStep3;
    const confirm = document.querySelector("[data-confirm]"); if (confirm) confirm.onclick = enterStep3;
    const ck = $("#qfck"); if (ck) ck.onclick = () => { showChecks = !showChecks; drawLeft(); };
    document.querySelectorAll("[data-say]").forEach((b) => (b.onclick = () => { if (box) { box.value = b.dataset.say; box.focus(); } }));
    // 题号锚点：点一下对话与题目同时定位到那道题（顶部对齐）
    document.querySelectorAll("[data-anchor]").forEach((b) => (b.onclick = () => {
      document.querySelectorAll(".cc-anchor").forEach((x) => x.classList.toggle("on", x === b));
      alignAnchor(b.dataset.anchor);
    }));
    // 建议条：多选（局部更新不重绘）→ 确认后把选中的建议交给 AI 生效
    document.querySelectorAll("[data-sug]").forEach((b) => (b.onclick = () => {
      const id = b.dataset.sug;
      if (sugPicked.has(id)) sugPicked.delete(id); else sugPicked.add(id);
      b.classList.toggle("on", sugPicked.has(id));
      const tick = b.querySelector(".cc-sugck"); if (tick) tick.textContent = sugPicked.has(id) ? "✓" : "";
      const ap = $("#sugapply"); if (ap) { ap.disabled = !sugPicked.size; ap.textContent = sugPicked.size ? `确认选中的 ${sugPicked.size} 条建议生效` : "选中建议后确认"; }
    }));
    const sugclear = $("#sugclear"); if (sugclear) sugclear.onclick = () => { sugPicked = new Set(); drawLeft(); };
    const sugapply = $("#sugapply"); if (sugapply) sugapply.onclick = guard(async () => {
      const st = flowState();
      const list = curSuggestions().filter((x) => sugPicked.has(x.id));
      if (!list.length) return;
      sugBusy = true; sugPicked = new Set(); drawLeft();
      const msg = (st === 3 ? "请按以下建议调整研究背景与目标：" : "请按以下建议修改问卷：") + "\n" + list.map((x, i) => `${i + 1}. ${x.text}`).join("\n");
      try {
        await chatTurn(msg);
        if (st === 3) { frameSug = []; }
        if (st === 4) { try { const rv = await api(`/projects/${p.id}/recruiting/form/review`, { method: "POST" }); f = { ...f, ...rv }; } catch (e) { /* ignore */ } }
      } catch (e) { toast(e.message, true); }
      sugBusy = false; drawLeft();
    });
    const go3 = $("#qfgo3"); if (go3) go3.onclick = guard(async () => {
      flowBusy = true; go3.disabled = true; thinking = true; drawLeft();
      try {
        await api(`/projects/${p.id}/recruiting/form/framing/confirm`, { method: "POST" });
        const idea = cardIdea();
        let r = null;
        try { r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: idea || "请据此起草招募问卷", questions: [], goals, need, use_library: true } }); } catch (e) { r = null; }
        if (!r || !Array.isArray(r.questions) || !r.questions.length) r = await api(`/projects/${p.id}/recruiting/form/generate`, { method: "POST", body: { idea: idea || "" } });
        pushHist(); Q = JSON.parse(JSON.stringify(r.questions || []));
        if (Array.isArray(r.goals)) goals = r.goals.slice();
        if (typeof r.need === "string") need = r.need;
        f = { ...f, ...r, framing_confirmed: true }; cardDone = true; qfStep = 4; thinking = false; frameSug = []; sugPicked = new Set();
        save(); drawLeft(); drawRight(); toast(`已生成 ${Q.length} 题`);
        // 出题后主动检查问卷，把建议直接展示在输入框上方（用户反馈「确认后没有弹出建议」）
        try { const rv = await api(`/projects/${p.id}/recruiting/form/review`, { method: "POST" }); f = { ...f, ...rv }; drawLeft(); drawRight(); } catch (e) { /* 检查失败不阻塞 */ }
      } catch (e) { toast(e.message, true); thinking = false; qfStep = 3; drawLeft(); }
      flowBusy = false;
    });
    const go5 = $("#qfgo5"); if (go5) go5.onclick = guard(async () => {
      await api(`/projects/${p.id}/recruiting/form/questions/confirm`, { method: "POST", body: { formats: [], quotas: [] } });
      f = { ...f, questions_confirmed: true }; qfStep = 6; fmtDone = false; quotaDone = false; quotaPlan = null; drawLeft(); drawRight(); toast("问卷已确认");
    });
    // 访谈形式：多选，单独确认（样式补 .on 后才能看出选中）；已确认后再点任意一项即时同步到项目总览
    let fmtTimer = 0;
    document.querySelectorAll("[data-fmt]").forEach((b) => (b.onclick = () => {
      fmtToggle(b.dataset.fmt); b.classList.toggle("on", fmtPicked.includes(b.dataset.fmt));
      if (fmtDone) { clearTimeout(fmtTimer); fmtTimer = setTimeout(() => { syncFormats(); }, 350); }
    }));
    const fmtgo = $("#qffmtgo"); if (fmtgo) fmtgo.onclick = guard(async () => {
      if (!fmtPicked.length) fmtPicked = ["video"];   // 未选时默认视频访谈
      try { await api(`/projects/${p.id}/recruiting/form/questions/confirm`, { method: "POST", body: { formats: fmtPicked, quotas: [] } }); } catch (e) { toast(e.message, true); return; }
      fmtDone = true; fmtSync = "ok"; f = { ...f, formats: fmtPicked }; saveFlow(); drawLeft(); toast("访谈形式已保存，接着对齐配额口径");
      loadQuotaPlan();   // 进入配额阶段才拉口径
    });
    const fmtredo = $("#qffmtredo"); if (fmtredo) fmtredo.onclick = () => { fmtDone = false; saveFlow(); drawLeft(); };
    // 配额口径计划：口径说明 + 配额表 + 不清晰项的选择题
    const loadQuotaPlan = async () => {
      if (quotaPlan) return;
      planBusy = true; drawLeft();
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/quotas/plan`, { method: "POST", body: { brief: f.brief || "", goals, need, idea: cardIdea() } });
        quotaRationale = r.rationale || ""; quotaClarify = r.clarify || [];
        if (Array.isArray(r.quotas) && r.quotas.length) quotaList = JSON.parse(JSON.stringify(r.quotas));
        quotaPlan = true;
      } catch (e) { quotaPlan = true; toast(e.message, true); }
      planBusy = false; drawLeft();
    };
    document.querySelectorAll("[data-clarify]").forEach((b) => (b.onclick = () => {
      const i = +b.dataset.clarify, o = b.dataset.claropt; clarifyPick[i] = o;
      document.querySelectorAll(`[data-clarify="${i}"]`).forEach((x) => x.classList.toggle("on", x.dataset.claropt === o));
    }));
    // 目标人数：手动改 / AI 调整
    document.querySelectorAll("[data-quota-target]").forEach((inp) => (inp.onchange = () => { const q = quotaList.find((x) => quotaLabel(x) === inp.dataset.quotaTarget); if (q) q.target = +inp.value || q.target; }));
    const quotago = $("#qfquotago"); if (quotago) quotago.onclick = guard(async () => {
      const msg = $("#qfquotamsg"); if (!msg || !msg.value.trim()) return toast("先说想怎么调整配额", true);
      quotaChat = msg.value; quotaBusy = true; drawLeft();
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/quotas/chat`, { method: "POST", body: { message: msg.value, quotas: quotaList } });
        if (Array.isArray(r.quotas)) quotaList = JSON.parse(JSON.stringify(r.quotas));
        quotaChat = ""; toast(r.reply || "配额已调整");
      } catch (e) { toast(e.message, true); }
      quotaBusy = false; drawLeft();
    });
    const quotadone = $("#qfquotadone"); if (quotadone) quotadone.onclick = guard(async () => {
      // 有口径不清楚的选项时，必须先逐项选完（选择题二次确认）才能确认表格
      const unset = quotaClarify.map((c, i) => (clarifyPick[i] ? "" : (i + 1))).filter(Boolean);
      if (unset.length) return toast(`还有口径要拍板：第 ${unset.join("、")} 项请先选一个`, true);
      if (!quotaList.length) return toast("还没有配额组，可先让 AI 生成，或点「跳过配额」", true);
      try { await api(`/projects/${p.id}/recruiting/quotas`, { method: "PUT", body: { quotas: quotaList } }); } catch (e) { toast(e.message, true); return; }
      quotaDone = true; quotaPlan = null; clarifyPick = {}; saveFlow(); drawLeft(); toast("配额已确认保存");
    });
    const quotaskip = $("#qfquotaskip"); if (quotaskip) quotaskip.onclick = () => { quotaDone = true; drawLeft(); };
    const testgo = $("#qftest"); if (testgo) testgo.onclick = () => { seg = "test"; drawRight(); };
    const listgo = $("#qflist"); if (listgo) listgo.onclick = () => { if (window.__ptabGo) window.__ptabGo("list"); else toast("请稍后再试", true); };
  };

  const drawLeft = () => {
    const c = $("#ccroot"); if (!c) return;
    if (mode === "html") { c.innerHTML = `<div class="cc-sc">${importHtml()}</div>`; const g = $("#rfgooutline"); if (g) g.onclick = goOutline; return; }
    // 修正方案第 1 轮第 4 条：左栏是 6 步（app/static/rsteps.js），没有单独的对话栏；改某一题用右栏题干旁的 AI 图标
    RSteps.render(c, {
      p, getQ: () => JSON.parse(JSON.stringify(Q)),
      apply: (r) => { pushHist(); Q = JSON.parse(JSON.stringify(r.questions || [])); f = { ...f, ...r }; changedIds = new Set(r.fresh || []); drawRight(); },
      test: () => { seg = "test"; drawRight(); },
    });
  };

  /* ---------- right: the outline / the test ---------- */
  function qEditor(q, i) {
    const opts = q.type === "single" || q.type === "multi" || q.type === "scale";
    return `<div class="rf-edit" data-qi="${i}"><div class="row"><b>${i + 1}.</b><select data-f="type" aria-label="第 ${i + 1} 题题型">${Object.entries(RF_TYPE).map(([k, n]) => `<option value="${k}" ${q.type === k ? "selected" : ""}>${n}</option>`).join("")}</select>
      <label class="chk"><input type="checkbox" data-f="required" ${q.required ? "checked" : ""}>必答</label><span class="sp"></span>
      <button class="s" type="button" data-mv="-1" ${i ? "" : "disabled"} aria-label="上移">↑</button><button class="s" type="button" data-mv="1" ${i < Q.length - 1 ? "" : "disabled"} aria-label="下移">↓</button><button class="s warn" type="button" data-del="1">删除</button><button class="s" type="button" data-done="1">完成</button></div>
      <input data-f="text" value="${esc(q.text)}" maxlength="300" placeholder="题目" aria-label="第 ${i + 1} 题题目">
      ${opts ? `<div class="rf-optl">${q.options.map((o, j) => `<div class="rf-optrow" data-oj="${j}"><span class="rf-code" title="选项编号">${j + 1}</span><input data-o="text" value="${esc(o.text)}" maxlength="120" placeholder="选项" aria-label="选项 ${j + 1}">
        <select data-o="goto" aria-label="选了之后">${actions(i, o.goto, true)}</select>${q.type === "multi" ? `<button class="s rf-ex ${(o.excl || []).length || openEx.has(o.id) ? "on" : ""}" type="button" data-ex="${j}" aria-expanded="${openEx.has(o.id)}">${(o.excl || []).length ? `互斥：${exLabel(q, o)}` : "互斥"}</button>` : ""}
        <button class="s" type="button" data-odel="${j}" aria-label="删除选项" ${q.options.length > 2 ? "" : "disabled"}>×</button></div>${q.type === "multi" && openEx.has(o.id) ? `<div class="rf-expanel">“${esc(o.text || "（空选项）")}”（${j + 1}）不能和哪些选项同时选？点编号勾选（紫色 = 不能同时选）：
          <div class="rf-chs">${q.options.map((x, k) => (x.id === o.id ? "" : `<button class="s rf-ch ${(o.excl || []).includes(x.id) ? "on" : ""}" type="button" data-exo="${j}|${k}" aria-pressed="${(o.excl || []).includes(x.id)}" title="${esc(x.text)}">${k + 1}</button>`)).join("")}</div>
          <div class="sm mut" style="margin-top:6px">互斥是双向的：勾了“${j + 1} 与 3 互斥”，3 那一行也会自动显示“与 ${j + 1} 互斥”。一个都不勾 = 不互斥。</div></div>` : ""}`).join("")}<button class="s" type="button" data-oadd="1" ${q.options.length >= 12 ? "disabled" : ""}>+ 添加选项</button></div>` : ""}
      ${q.type === "number" ? `<div class="rf-optl">${(q.rules || []).map((r, j) => `<div class="rf-optrow" data-rj="${j}"><span>如果数字</span><select data-r="op" aria-label="条件">${Object.entries(RF_OP).map(([k, n]) => `<option value="${k}" ${r.op === k ? "selected" : ""}>${n}</option>`).join("")}</select>
        <input data-r="value" type="number" step="any" value="${r.value}" aria-label="数值" style="max-width:110px"><select data-r="goto" aria-label="就">${actions(i, r.goto, false)}</select><button class="s" type="button" data-rdel="${j}" aria-label="删除规则">×</button></div>`).join("")}
        <button class="s" type="button" data-radd="1" ${(q.rules || []).length >= 4 ? "disabled" : ""}>+ 添加规则（例如 小于 18 → 结束 · 不符合）</button></div>` : ""}</div>`;
  }
  const qBadge = (q) => (changedIds.has(q.id) ? `<span class="rf-bd new">刚改</span>` : "");
  // a jump must still go forward (or to an end) after the questions are moved: a move that would turn one back is refused, never silently dropped
  const orderOk = (list) => list.every((q, i) => [...q.options.map((o) => o.goto), ...(q.rules || []).map((r) => r.goto)].every((g) => {
    const at = list.findIndex((x) => x.id === g);
    return !g || g.startsWith("end_") || at < 0 || at > i;
  }));
  const blankQ = () => ({ id: nextId(), type: "single", text: "", required: true, options: blank(), rules: [] });
  const moveQ = (from, to) => {   // true when the order changed; a move that would turn a jump backwards is refused, and says so
    const next = [...Q], [m] = next.splice(from, 1); next.splice(to, 0, m);
    if (!orderOk(next)) { toast("这样换会让某题的跳题指回前面，请先改跳题", true); return false; }
    pushHist(); Q = next; changed(); return true;
  };
  function menuAct(act, i, j) {   // the ⋮ menus of a question and of an option; the question keeps its id whatever happens to its place
    const q = Q[i]; if (!q) return; menu = null;
    if (act === "logic") { editing.add(q.id); return drawRight(); }
    if (act === "excl") { editing.add(q.id); openEx.add(q.options[j].id); return drawRight(); }
    pushHist();
    if (act === "single" || act === "multi") { q.type = act; if (act !== "multi") q.options.forEach((o) => { o.excl = []; }); return changed(); }
    if (act === "copy") { const c = JSON.parse(JSON.stringify(q)); c.id = nextId(); Q.splice(i + 1, 0, c); return changed(); }
    if (act === "above" || act === "below") { const n = blankQ(); Q.splice(act === "above" ? i : i + 1, 0, n); editing.add(n.id); return changed(); }
    if (act === "delete") { editing.delete(q.id); Q.splice(i, 1); changed(); }
  }
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cardsOf = (list) => [...list.querySelectorAll(":scope > .rf-q[data-qi]")];
  // the cards that move slide to their new places: measure, change the order, then play the difference back (FLIP)
  const flipAll = (list, change) => {
    const els = cardsOf(list), first = new Map(els.map((c) => [c, c.getBoundingClientRect().top]));
    change();
    if (reduced()) return;
    els.forEach((c) => {
      const d = first.get(c) - c.getBoundingClientRect().top;
      if (!d) return;
      c.style.transition = "none"; c.style.transform = `translateY(${d}px)`; void c.offsetHeight;
      c.style.transition = "transform 200ms cubic-bezier(.2,.8,.2,1)"; c.style.transform = "";
    });
  };
  // the moved card has landed (220 ms): the change is made, or refused and the list drawn back as it was
  const land = (from, to, keyboard) => {
    busy = true;
    setTimeout(() => {
      busy = false;
      focusGrip = keyboard ? to : null;
      if (to === from || !moveQ(from, to)) { focusGrip = null; drawRight(); }
    }, reduced() ? 0 : 220);
  };
  function dragQ(e, grip) {   // press the handle: the card lifts and follows the pointer; the others slide out of its way
    if (busy) return;
    e.preventDefault();
    const card = grip.closest(".rf-q"), list = card.parentElement, from = cardsOf(list).indexOf(card);
    const grab = e.clientY - card.getBoundingClientRect().top;
    card.classList.add("rq-lift"); card.style.transition = "none";
    const move = (ev) => {
      const want = ev.clientY - grab, others = cardsOf(list).filter((c) => c !== card);
      const before = others.find((c) => want + card.offsetHeight / 2 < c.getBoundingClientRect().top + c.offsetHeight / 2) || null;
      const at = before || (others.length ? others[others.length - 1].nextSibling : null);
      if (at !== card && at !== card.nextSibling) flipAll(list, () => list.insertBefore(card, at));
      card.style.transform = "";
      const natural = card.getBoundingClientRect().top;
      card.style.transform = `translateY(${want - natural}px) scale(1.01)`;
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      card.classList.remove("rq-lift");
      const to = cardsOf(list).indexOf(card);
      card.style.transition = reduced() ? "none" : "transform 220ms cubic-bezier(.2,.8,.2,1)";
      card.style.transform = "";
      land(from, to, false);
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }
  function keyMove(e, grip) {   // ↑ ↓ on the focused handle: the same motion as dragging
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    if (busy) return;
    const card = grip.closest(".rf-q"), list = card.parentElement, cards = cardsOf(list), from = cards.indexOf(card), to = from + (e.key === "ArrowUp" ? -1 : 1);
    if (to < 0 || to >= cards.length) return;
    const other = cards[to];
    card.classList.add("rq-lift");
    flipAll(list, () => list.insertBefore(card, e.key === "ArrowUp" ? other : other.nextSibling));
    land(from, to, true);
  }
  const qMenu = (q, i) => `<div class="rq-menu" role="menu">
      <button type="button" role="menuitem" data-mact="logic" data-qi="${i}">条件逻辑（跳题）</button><button type="button" role="menuitem" data-mact="copy" data-qi="${i}">复制</button>
      <button type="button" role="menuitem" data-mact="above" data-qi="${i}">在上方添加问题</button><button type="button" role="menuitem" data-mact="below" data-qi="${i}">在下方添加问题</button>
      <hr><button type="button" role="menuitem" class="del" data-mact="delete" data-qi="${i}">删除</button></div>`;
  const oMenu = (q, i, j) => `<div class="rq-menu rq-om" role="menu">
      <button type="button" role="menuitem" data-mact="single" data-qi="${i}" data-oj="${j}" class="${q.type === "single" ? "on" : ""}">单选</button>
      <button type="button" role="menuitem" data-mact="multi" data-qi="${i}" data-oj="${j}" class="${q.type === "multi" ? "on" : ""}">多选</button>
      ${q.type === "multi" ? `<hr><button type="button" role="menuitem" data-mact="excl" data-qi="${i}" data-oj="${j}">设为互斥项</button>` : ""}</div>`;
  // 绿勾 = 选它不会被甄别 / 拒绝（通过）；红叉 = 会被甄别或拒绝（筛掉）。点一下直接写好 goto，无需再写跳题逻辑。
  const optMark = (q, o, j) => {
    const out = o.goto === "end_out";
    return `<button type="button" class="rq-ic rq-ic-btn ${out ? "n" : "y"}" data-pass="${q.id}" data-pj="${j}"
      title="${out ? "会被甄别 / 拒绝 · 点一下改为通过" : "不会被甄别 / 拒绝（通过）· 点一下改为筛掉"}"
      aria-label="${out ? "筛掉，点击改为通过" : "通过，点击改为筛掉"}" aria-pressed="${out}">${out ? "✗" : "✓"}</button>`;
  };
  const jumpBadge = (g) => (g && !g.startsWith("end_") ? badge(g) : "");   // the two endings show as ✓ / ✗; only a jump to a question needs its own label
  function qCard(q, i) {
    if (editing.has(q.id)) return `<div class="rf-q" data-qid="${q.id}" data-qi="${i}">${qEditor(q, i)}</div>`;
    const qk = "q:" + q.id, choice = q.type === "single" || q.type === "multi" || q.type === "scale";
    const opts = choice ? q.options.map((o, j) => {
      const ok = `o:${q.id}:${j}`;
      return `<div class="rq-opt">${optMark(q, o, j)}<span>${esc(o.text || "（空选项）")}</span>${jumpBadge(o.goto)}${o.tag ? `<span class="rf-lg jump">标签：${esc(o.tag)}</span>` : ""}${(o.excl || []).length ? `<span class="rf-lg exb">互斥：${exLabel(q, o)}</span>` : ""}<span class="sp"></span>
        <button class="rq-more" type="button" data-menu="${ok}" aria-label="选项 ${j + 1} 菜单" aria-expanded="${menu === ok}">${rfIcon(RF_IC.dots)}</button>${menu === ok ? oMenu(q, i, j) : ""}</div>`;
    }).join("") + `<button class="rq-add" type="button" data-oadd-view="${q.id}">＋ 添加选项</button>` : "";
    const rules = q.type === "number" ? (q.rules || []).map((r) => `<div class="rq-opt">如果数字${RF_OP[r.op]} ${r.value} ${badge(r.goto)}</div>`).join("") : "";
    return `<div class="rf-q${changedIds.has(q.id) ? " rf-hl" : ""}${askOpen === q.id ? " rf-ask-target" : ""}" data-qid="${q.id}" data-qi="${i}" data-selk="q:${q.id}">
      <span class="rq-grip" data-grip="${i}" tabindex="0" role="button" title="按住拖动排序，或用上下方向键移动" aria-label="拖动第 ${i + 1} 题">${rfIcon(RF_IC.grip)}</span>
      <div class="rq-top"><span class="rq-qn">Q${i + 1}</span><span class="rq-title" role="button" tabindex="0" data-edit="${q.id}" title="点一下改这一题">${esc(q.text || "（还没写题目）")}</span>${qBadge(q)}<span class="sp"></span>
        <span class="rq-type-wrap"><button class="rq-type rq-type-btn" type="button" data-typetg="${q.id}" aria-haspopup="true" aria-expanded="false" title="点一下切换题型">${lbl(RF_TYPE[q.type], q.type)}${q.required ? " · 必答" : ""}${rfIcon(RF_IC.chev)}</button>${typeMenuOpen === q.id ? `<div class="rq-type-menu" role="menu">${Object.entries(RF_TYPE).map(([k, n]) => `<button type="button" role="menuitem" data-typeset="${q.id}" data-t="${k}" class="${q.type === k ? "on" : ""}">${n}</button>`).join("")}</div>` : ""}</span>
        <button class="rq-ask" type="button" data-ask="${q.id}" aria-label="让 AI 改这一题" title="让 AI 改这一题">${rfIcon(RF_IC.spark)}</button>
        <button class="rq-more" type="button" data-menu="${qk}" aria-label="题目菜单" aria-expanded="${menu === qk}">${rfIcon(RF_IC.dots)}</button>${menu === qk ? qMenu(q, i) : ""}</div>
      ${opts}${rules}${exWarn(q)}${askOpen === q.id ? askInline(q, i) : ""}</div>`;
  }
  /**
   * 2026-11-18 用户确认（方案 B）：改这题目板直接长在这道题的卡片里（选项下面），
   * 打开后常驻，只有点「关闭」才收起；翻到别的题点 💬 会换成那道题，内容与题号自动跟着变。
   */
  function askInline(q, i) {
    const qn = i + 1;
    return `<div class="rq-askin" role="region" aria-label="对第 ${qn} 题问问 AI">
      <div class="rq-askin-hd"><b>改这题 · Q${qn}</b><span class="sm mut">${lbl(RF_TYPE[q.type], q.type)}${q.required ? " · 必答" : ""}</span><span class="sp"></span><button type="button" class="s" data-askclose="1">关闭</button></div>
      <div class="rq-askin-bd">
        <div class="sm mut rq-askin-q">${esc(q.text || "（还没写题目）")}</div>
        <textarea data-asktext="1" placeholder="说说要怎么改这一题，例如：把选项改成……／加一道追问……">${esc(askDraft[q.id] || "")}</textarea>
        ${askFail ? `<div class="rq-askerr">${esc(askFail)}</div>` : ""}
        ${askDone ? `<div class="rq-askok">✓ 已按你的要求改好这一题</div>` : ""}
      </div>
      <div class="rq-askin-ft"><button type="button" class="p" data-asksend="1"${askBusy ? " disabled" : ""}>${askBusy ? "正在改…" : "改这题"}</button><span class="sm mut">只改这一题，其它题目不变</span></div></div>`;
  }
  // 研究背景: one bullet per line of the project's background; the numbering in front of a line is not shown
  const briefLines = () => (f.brief || p.brief || "").split("\n").map((l) => l.replace(/^\s*(?:\d+\s*[.、)]|[-•·])\s*/, "").trim()).filter(Boolean);
  const listHtml = (id, lines, label) => `<ul class="rq-bl" id="${id}">${(lines.length ? lines : [""]).map((l) => `<li><span class="rq-bt" contenteditable="true" spellcheck="false" aria-label="${label}">${esc(l)}</span></li>`).join("")}</ul>`;
  const needLines = () => need.split(/[。；;\n]/).map((x) => x.trim()).filter(Boolean);   // 招募需求 is shown one sentence per bullet (split at 。 ； ;)
  const briefHtml = () => `<ul class="rq-bl" id="rfbrief">${(briefLines().length ? briefLines() : [""]).map((l) => `<li><span class="rq-bt" contenteditable="true" spellcheck="false" aria-label="研究背景的一条">${esc(l)}</span></li>`).join("")}</ul>`;
  const saveBrief = guard(async () => {   // on leaving a bullet: the whole list is saved as the project's 研究背景 (没有变化就不发)
    const text = [...document.querySelectorAll("#rfbrief .rq-bt")].map((e) => e.textContent.trim()).filter(Boolean).join("\n");
    if (text === briefLines().join("\n")) return;
    await api(`/projects/${p.id}/outline/settings`, { method: "PUT", body: { brief: text } });
    f = { ...f, brief: text }; stat("研究背景已保存");
  });
  function importedCard() {
    const u = String(f.html_url || "").trim(), safe = /^https?:\/\//i.test(u) ? esc(u) : "";
    // 直接映射导入链接的实际在线页面（Confirmit 等）；对方站点若禁止嵌入，仍可「新窗口打开」
    const live = safe ? `<div class="rf-imp-live">
        <div class="rf-imp-live-hd"><b>链接的实际展示</b><span class="sm mut">在线问卷页面 · 若对方站点禁止嵌入，请点「新窗口打开」</span></div>
        <iframe src="${safe}" title="导入的问卷实际页面" loading="lazy" referrerpolicy="no-referrer"></iframe>
      </div>` : "";
    return `<h3 class="rf-h rq-bold">已导入的问卷</h3>
      <div class="rf-impcard">
        <label class="field"><span>问卷链接（https://）</span><input id="rfurl" type="url" value="${esc(f.html_url)}" placeholder="https://…（在别的系统里做好的问卷）" maxlength="500" ${htmlLocked ? "readonly" : ""}></label>
        <div class="row" style="margin-top:8px">${htmlLocked ? `<span class="tag ok">已锁定</span><button class="s" id="rfedit" type="button">编辑</button>` : `<button class="p" id="rfsave" type="button">保存并锁定</button>`}<button class="s" id="rfopen" type="button" ${f.html_url ? "" : "disabled"}>${rfIcon(RF_IC.ext)}新窗口打开</button></div>
        <div class="sm mut" style="margin-top:8px;line-height:1.7">题目在客户那边作答（Confirmit）。数据回来后，在「配额与受访者名单 → 导入甄别数据」里按 Data Map 批量导入。</div>
      </div>
      ${live}
      <div class="row" style="margin-top:12px"><button class="s" type="button" id="rfgooutline2">生成访谈大纲 →</button><span class="sm mut">按这份问卷的筛选字段分段追问</span></div>`;
  }
  // 招募需求下方的「总共 N 人」抬头：N = 各配额 target 之和；没配配额就回退到受访者名单人数
  const totalHead = () => {
    const qs = (rec && rec.quotas) || [];
    const n = plannedN || (qs.length ? qs.reduce((s, q) => s + (q.target || 0), 0) : (rec && rec.participants_count) || 0);
    return `<div class="rf-total">${rfIcon(RF_IC.users)}<span class="rf-total-n">${n}</span><span class="rf-total-u">人</span></div>`;
  };
  // 访谈大纲结构：甄别问卷一行，再按访谈大纲的每个环节一行（读的是访谈页里的同一份大纲）
  const flowRows = () => {
    const rows = [{ stage: "甄别问卷", ask: Q.slice(0, 3).map((q) => q.text).join("；"), n: String(Q.length), time: Q.length ? `${Math.max(1, Math.round(Q.length / 2))} 分钟` : "", goal: "判断受访者是否符合，不符合即筛除" }];
    if (olSum) {
      const total = Math.max(1, olSum.question_count);
      olSum.parts.forEach((x) => {
        const qs = x.questions.filter((q) => !q.cover), cov = x.questions.filter((q) => q.cover);
        rows.push({ stage: x.title, ask: qs.slice(0, 2).map((q) => q.text).join("；"), n: String(qs.length), time: `${Math.max(1, Math.round(olSum.minutes * qs.length / total))} 分钟`, goal: cov.map((q) => q.text).join("；") });
      });
    }
    return rows;
  };
  const flowStep = (r, i) => {
    const isScreen = /甄别|筛选|资格/.test(r.stage || "");
    const tag = isScreen ? `<span class="rf-flow-tag scr">甄别</span>` : `<span class="rf-flow-tag acc">访问</span>`;
    return `<div class="rf-flow-step">
      <div class="rf-flow-step-hd"><span class="rf-flow-no">${i + 1}</span><b>${esc(r.stage || "环节")}</b>${tag}<span class="sp"></span><span class="sm mut">${esc(r.n || "—")} 题${r.time ? ` · ${esc(r.time)}` : ""}</span></div>
      <div class="rf-flow-step-bd"><span class="sm mut">问什么：</span>${esc(r.ask || "—")}${r.goal ? `<div class="sm mut">目的：${esc(r.goal)}</div>` : ""}</div>
    </div>`;
  };
  function outline() {
    return `<h3 class="rf-h rq-bold">研究背景</h3>${briefHtml()}
      <h3 class="rf-h rq-bold">研究目标</h3>${listHtml("rfgoalsl", goals, "研究目标的一条")}
      <h3 class="rf-h rq-bold">招募需求</h3>${totalHead()}${listHtml("rfneedl", needLines(), "招募需求的一条")}
      <h3 class="rf-h rq-bold rf-flow-h">访谈流程</h3>
      <div class="rf-flow-part"><span class="rf-flow-no">1</span><b>开始 · 欢迎消息</b></div>
      <div class="rf-welcome">${esc(f.welcome || "您好，感谢参与。本次文字访谈想了解您对相关话题的看法，没有标准答案，请根据实际经历作答；无需提供身份证件、保单、账户等私人资料。")}</div>
      <h3 class="rf-h rq-bold">访谈大纲结构</h3>${flowRows().map(flowStep).join("")}
      ${mode === "html" ? importedCard() : `<h3 class="rf-h rq-bold">问卷流程（共 ${Q.length} 题）<span class="rf-pass-tip"><span class="rq-ic y">✓</span>通过 <span class="rq-ic n">✗</span>筛掉 · 点图标即可切换，逻辑自动写好</span></h3>${Q.length ? Q.map(qCard).join("") : `<div class="empty"><b>还没有题目</b><span>在左边和小星星助手聊到“甄别”阶段，它会逐题生成。</span></div>`}
      <div class="row"><button class="s" type="button" data-add="single">+ 单选</button><button class="s" type="button" data-add="multi">+ 多选</button><button class="s" type="button" data-add="scale">+ 量表</button><button class="s" type="button" data-add="text">+ 文字填空</button><button class="s" type="button" data-add="number">+ 数字填空</button></div>`}`;
  }
  // 检查建议（qhash：题目一改，对应建议自动失效）；沿用现有 .rf-msg 卡片与 .s 胶囊按钮
  const sugHtml = () => {
    const list = f.suggestions || [];
    if (!list.length) return "";
    return `<h3 class="rf-h rq-bold">检查建议（${list.length}）</h3>` + list.map((x) =>
      `<div class="rf-msg"><b>${esc(x.label || "建议")}</b><div class="sm mut">${esc(x.text || "")}</div>` +
      `<div class="row"><button class="s" type="button" data-adopt="${esc(x.sig)}">${esc(x.button || "采纳")}</button>` +
      `<button class="s" type="button" data-dismiss="${esc(x.sig)}">不再提醒</button></div></div>`).join("");
  };
  const rightHead = () => {
    const live = f.status === "published";
    if (mode === "html") return `<div class="rf-hd"><span class="pill">已导入</span><span class="sm mut">问卷在客户那边作答，本平台不出题</span><span class="sp"></span></div>`;
    // 发布四态（产品文档第 11 章）：①草稿可发布 ②发布中=已锁定(可解锁修改) ③已停止可重新发布；「停止收集」已按需求去掉
    const pill = live ? `<span class="pill">已锁定 · 第 ${f.version || 1} 版</span>` : f.status === "closed" ? `<span class="pill o">已停止</span>` : `<span class="pill o">编辑中 · 未发布</span>`;
    const pubBtn = live ? `<button class="s" type="button" id="rfunlock">解锁修改</button>`
      : `<button class="p" type="button" id="rfpub" ${Q.length ? "" : "disabled"}>${f.status === "closed" ? "重新发布" : "发布新版本"}</button>`;
    // 2026-11-18 用户确认：顶部只留一行——删除「确认背景与目标 / 起草问卷 / 检查问卷」三个按钮（出题与检查都走左侧对话）
    return `<div class="rf-hd"><div class="rf-seg" role="tablist"><button type="button" role="tab" data-seg="outline" class="${seg === "outline" ? "on" : ""}" aria-selected="${seg === "outline"}">${rfIcon(RF_IC.list)}招募问卷</button><button type="button" role="tab" data-seg="test" class="${seg === "test" ? "on" : ""}" aria-selected="${seg === "test"}">${rfIcon(RF_IC.eye)}测试答题</button></div>
      <span class="sp"></span>${vers && vers.languages.length > 1 || (vers && vers.languages.length) ? verMenu() : ""}<span class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version ? `第 ${f.version} 版` : ""}</span>${pill}
      ${pubBtn}<button class="p" type="button" id="rfnext" title="甄别问卷测试好了，进入数据库筛选及发送">确认并下一步 →</button></div>`;
  };

  // 2026-11-18：改这题目板已改成行内（长在题目卡片里，见 askInline），这里只负责把旧浮层收掉
  function drawAskPanel() {
    const panel = $("#rfaskpanel"); if (!panel) return;
    panel.hidden = true; panel.innerHTML = "";
  }
  // 行内面板的事件：关闭 / 改这题（改完保持打开，只有点关闭才收）
  function wireAskInline() {
    const close = document.querySelector("[data-askclose]");
    if (close) close.onclick = () => { askOpen = null; askFail = ""; askDone = false; drawRight(); };
    const ta = document.querySelector("[data-asktext]");
    if (ta) {
      ta.oninput = () => { askDraft[askOpen] = ta.value; };
      ta.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); const b = document.querySelector("[data-asksend]"); if (b) b.click(); } };
    }
    const send = document.querySelector("[data-asksend]");
    if (send) send.onclick = guard(async () => {
      const q = Q.find((x) => x.id === askOpen); if (!q) return;
      const box = document.querySelector("[data-asktext]");
      const text = (box ? box.value : "").trim();
      if (!text) return toast("先说要怎么改这题", true);
      askBusy = true; askFail = ""; askDone = false; drawRight();
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/ask`, { method: "POST", body: { qid: q.id, instruction: text, questions: Q, goals, need } });
        if (r.changed && Array.isArray(r.questions)) {
          pushHist(); changedIds = diffIds(Q, r.questions); Q = JSON.parse(JSON.stringify(r.questions)); editing = new Set();
          askBusy = false; askFail = ""; askDone = true; askDraft[q.id] = ""; save(); drawRight();   // 面板保留，只有点「关闭」才收
          toast("已按你的要求改好这一题"); return;
        }
        askFail = r.reply || "模型没有改这一题，换个说法试试";
      } catch (e) { askFail = e.message || "改题失败"; }
      askBusy = false; drawRight();
    });
  }

  function drawRight() {
    const r = $("#rfright"); if (!r) return;
    const keep = $("#rfout") ? $("#rfout").scrollTop : 0;
    r.innerHTML = rightHead() + `<div class="rf-sc" id="rfout"><div class="rf-col">${seg === "outline" ? (isSource() ? outline() : `<div id="rftr"><div class="mut" style="padding:12px">加载中…</div></div>`) : `<p class="sm mut" style="margin:0">就是候选人看到的样子：直接填写，看会走到哪里。</p><div id="rfrun"></div><div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>`}</div></div>`;
    $("#rfout").scrollTop = keep; wireRight(); wireVer(); syncUndo();
    const rr = $("#rfright"); if (rr) rr.querySelectorAll("[data-ask]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); if (askOpen === b.dataset.ask) { askOpen = null; askFail = ""; askDone = false; drawRight(); return; } askOpen = b.dataset.ask; askFail = ""; askDone = false; drawRight(); const ta = document.querySelector("[data-asktext]"); if (ta) ta.focus(); }));
    drawAskPanel(); wireAskInline();
    if (askOpen) {   // 只在这道题跑出视野时才滚，尽量保住原题（题干和选项）和修改框一起看得见
      const sc = $("#rfout"), el = sc && sc.querySelector(`.rf-q[data-qid="${askOpen}"]`);
      if (el) {
        const top = el.offsetTop, bottom = top + el.offsetHeight, view = sc.clientHeight;
        if (top < sc.scrollTop + 8) sc.scrollTop = Math.max(0, top - 12);
        else if (bottom > sc.scrollTop + view - 8) sc.scrollTop = Math.max(0, top - 12);
      }
    }
    const g2 = $("#rfgooutline2"); if (g2) g2.onclick = goOutline;
  }
  const refreshVers = async () => {   // after the original is saved, other versions may have gone stale: update the menu (the banner shows when you open that version)
    const v = await rtVersions(p); if (!v) return; vers = v; const m = $("#rfvm"), b = $("#rfvb");
    if (m) m.innerHTML = rtMenuHtml(vers, ver || vers.source); if (b) { b.firstChild.textContent = "版本：" + verLabel(); wireVer(); }
  };
  function verLabel() { const x = vers && vers.languages.find((l) => l.code === (ver || vers.source)); return x ? x.name + (x.source ? "（原文）" : "") : "—"; }
  function verMenu() {   // 版本: one dropdown for the outline and the test; project languages carry no tag, team-only ones say 仅团队
    const cur = ver || vers.source, x = vers.languages.find((l) => l.code === cur) || {};
    return `<div class="rf-ver"><button type="button" id="rfvb" aria-haspopup="menu" aria-expanded="false" title="切换语言版本">版本：${esc(verLabel())}${x.team_only ? " · 仅团队" : ""}${rfIcon(RF_IC.chev)}</button><div class="rf-vm" id="rfvm" role="menu" hidden>${rtMenuHtml(vers, cur)}</div></div>`;
  }
  async function loadVers(auto) {   // every project language gets its version by itself (only what is missing or out of date is translated)
    if (!Q.length || incomplete()) return;
    try { vers = auto ? await api(`/projects/${p.id}/recruiting/form/translate-all`, { method: "POST", body: {} }) : await rtVersions(p); } catch { vers = vers || (await rtVersions(p)); }
    if (vers && ver && !vers.languages.some((l) => l.code === ver)) ver = "";
    drawRight();
  }
  async function drawTrans() {   // a non-original version of the outline: each question's translation, saved by hand if needed
    const box = $("#rftr"); if (!box || !vers) return;
    const b = rtBuild(p, Q, ver, vers); box.innerHTML = b.html; b.wire((r) => { vers = r; drawRight(); });
  }
  function wireVer() {
    const vb = $("#rfvb"), vm = $("#rfvm"); if (!vb) return;
    vb.onclick = (e) => { e.stopPropagation(); vm.hidden = !vm.hidden; vb.setAttribute("aria-expanded", String(!vm.hidden)); };
    vm.onclick = (e) => e.stopPropagation();
    vm.querySelectorAll("[data-ver]").forEach((b) => (b.onclick = () => { ver = b.dataset.ver === vers.source ? "" : b.dataset.ver; drawRight(); }));
    const add = vm.querySelector("[data-vadd]"); if (add) add.onclick = () => { const more = vm.querySelector(".rf-vmore"); more.hidden = !more.hidden; add.setAttribute("aria-expanded", String(!more.hidden)); };
    vm.querySelectorAll("[data-vnew]").forEach((b) => (b.onclick = guard(async () => {
      b.disabled = true; b.firstChild.textContent = "正在翻译…";
      vers = await api(`/projects/${p.id}/recruiting/form/translate`, { method: "POST", body: { lang: b.dataset.vnew, only_missing: true } }); ver = b.dataset.vnew; drawRight();
    })));
  }
  const retest = () => {
    const host = $("#rfrun"); if (!host) return; const res = $("#rfresult"); res.hidden = true;
    if (!Q.length) { host.innerHTML = `<div class="empty"><b>还没有题目</b><span>先在左边起草，或到“大纲”里自己添加。</span></div>`; return; }
    if (incomplete()) { host.innerHTML = `<div class="empty"><b>有题目还没写完</b><span>题目和选项都写好后，这里就能测试。</span></div>`; return; }
    scrRunner(host, {
      lang: ["zh", "en", "ms", "ta"].includes(ver) ? ver : "zh",
      step: async (answers) => { const r = await api(`/projects/${p.id}/recruiting/form/preview`, { method: "POST", body: { questions: Q, answers, lang: isSource() ? "" : ver } }); return r.result ? { result: r.result } : { question: r.question }; },
      onEnd: (result, answers, ctx) => {
        res.hidden = false;
        const back = ctx && ctx.back ? `<button type="button" class="s" id="scrbackretry">← 返回上一题</button>` : "";
        if (result === "out") {
          // 被甄别：可以回到上一题改答案再试（2026-11-16 已做，这里保持）
          res.innerHTML = "测试结果：这位候选人会被筛掉（不符合），问卷到此结束。 " + back;
        } else {
          // 通过：由你手动点「发布新版本」，发布后直接进入「数据库筛选及发送」（2026-11-18 用户确认）
          res.innerHTML = `<b style="color:#1C7A55">✓ 测试通过</b>：流程能走完，没有死循环。确认无误后发布新版本。 ` +
            `<button type="button" class="p" id="scrpub" style="border-radius:999px">发布新版本 → 第 ${(f.version || 0) + 1} 版</button> ${back}`;
          const pb = $("#scrpub");
          if (pb) pb.onclick = () => publish();   // 发布后留在本页，用户再点「确认并下一步」
        }
        const b = $("#scrbackretry"); if (b && ctx && ctx.back) b.onclick = () => { res.hidden = true; ctx.back(); };
      },
    }).start();
  };
  let publishNext = null;   // 保留变量：测试答题通过后点发布也不跳页，只留在本页
  const publish = guard(async () => {
    clearTimeout(timer); if (incomplete()) throw new Error("有题目或选项还没写完，写完再发布");
    f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: body() })) };
    f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/publish`, { method: "POST" })) };
    // 2026-11-18 用户确认：发布后留在本页（不跳名单/不跳数据库），由用户自己点「确认并下一步 →」
    toast(`已发布第 ${f.version || ""} 版，链接和二维码在「配额及受访者名单」页；确认无误后点「确认并下一步 →」`);
    drawRight();
  });

  function wireRight() {
    const nx = $("#rfnext"); if (nx) nx.onclick = guard(async () => {
      try { await api(`/projects/${p.id}/recruiting/form/questions/confirm`, { method: "POST", body: { formats: [], quotas: [] } }); } catch (e) { /* 还没出题时保留原有跳转 */ }
      if (window.__ptabGo) window.__ptabGo("db"); else toast("请稍后再试", true);
    });
    const fr = $("#rfframing"); if (fr) fr.onclick = guard(async () => {
      const r = await api(`/projects/${p.id}/recruiting/form/framing/confirm`, { method: "POST" });
      f = { ...f, framing_confirmed: r.framing_confirmed }; toast("研究背景与目标已确认，可以开始出题了");
    });
    const dr = $("#rfdraft"); if (dr) dr.onclick = guard(async () => {
      if (!f.framing_confirmed) return toast("请先点「确认背景与目标」，再起草问卷", true);
      dr.disabled = true;
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/generate`, { method: "POST", body: { idea: f.idea || "" } });
        pushHist(); Q = JSON.parse(JSON.stringify(r.questions || [])); editing = new Set(); changedIds = new Set();
        f = { ...f, ...r }; save(); drawRight(); toast(`已起草 ${Q.length} 题`);
      } catch (e) { toast(e.message, true); } finally { dr.disabled = false; }
    });
    const rv = $("#rfreview"); if (rv) rv.onclick = guard(async () => {
      const r = await api(`/projects/${p.id}/recruiting/form/review`, { method: "POST" });
      f = { ...f, ...r }; drawRight(); toast(r.reply || "检查完成");
    });
    document.querySelectorAll("[data-adopt]").forEach((b) => (b.onclick = guard(async () => {
      const r = await api(`/projects/${p.id}/recruiting/form/suggestions/adopt`, { method: "POST", body: { sig: b.dataset.adopt } });
      if (Array.isArray(r.questions)) { pushHist(); Q = JSON.parse(JSON.stringify(r.questions)); save(); }
      f = { ...f, ...r }; drawRight(); toast(r.reply || "已采纳");
    })));
    document.querySelectorAll("[data-dismiss]").forEach((b) => (b.onclick = guard(async () => {
      const r = await api(`/projects/${p.id}/recruiting/form/suggestions/dismiss`, { method: "POST", body: { sig: b.dataset.dismiss } });
      f = { ...f, ...r }; drawRight(); toast("已不再提醒");
    })));
    document.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { seg = b.dataset.seg; drawRight(); }));
    const pub = $("#rfpub"); if (pub) pub.onclick = publish;
    const un = $("#rfunlock"); if (un) un.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/unlock`, { method: "POST" })) }; drawRight(); toast("已解锁，进入编辑中；受访者仍看旧版，改完点「发布新版本」才生效"); });
    if (seg === "test") return retest();
    if (seg === "outline" && !isSource()) return drawTrans();
    document.querySelectorAll("[data-exall]").forEach((b) => (b.onclick = () => {   // link every pair of tagged options of this question as mutually exclusive
      const q = Q.find((x) => x.id === b.dataset.exall); if (!q) return; pushHist();
      taggedPairs(q).forEach(([a, c]) => setExcl(q, a, c, true)); changed();
    }));
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
      pushHist(); const type = b.dataset.add, q = { id: nextId(), type, text: "", required: true, options: type === "single" || type === "multi" || type === "scale" ? blank() : [], rules: [] };
      Q.push(q); editing.add(q.id); changed();
    }));
    document.querySelectorAll("[data-edit]").forEach((b) => { b.onclick = () => { editing.add(b.dataset.edit); drawRight(); }; b.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); b.click(); } }; });   // a question's text is the text, not a short button label: it may wrap
    // 题型下拉：常态卡片上点题型标签展开，选一种即切换题型并重绘（选择题/量表自动带选项，切换后即可「＋添加选项」）
    document.querySelectorAll("[data-typetg]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation(); typeMenuOpen = typeMenuOpen === b.dataset.typetg ? null : b.dataset.typetg; drawRight();
    }));
    document.querySelectorAll("[data-typeset]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      const q = Q.find((x) => x.id === b.dataset.typeset), t = b.dataset.t;
      if (!q || !t || q.type === t) { typeMenuOpen = null; return drawRight(); }
      pushHist();
      q.type = t;
      // 切换到选择题/多选题/量表题：确保有选项（至少两个空选项，便于直接添加/编辑）
      if (t === "single" || t === "multi" || t === "scale") { if (!q.options || !q.options.length) q.options = blank(); q.options.forEach((o) => { o.excl = t === "multi" ? (o.excl || []) : []; }); }
      else q.options = [];
      if (t !== "number") q.rules = [];
      typeMenuOpen = null; changed(); toast(`已切换为「${RF_TYPE[t]}」`);
    }));
    // 绿勾 ⇄ 红叉：点一下直接写好筛选逻辑（通过 / 筛掉），可撤销；原来的跳题 goto 记在 passBack 里，点回绿时恢复
    document.querySelectorAll("[data-pass]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      const q = Q.find((x) => x.id === b.dataset.pass), j = +b.dataset.pj;
      if (!q || !q.options[j]) return;
      const o = q.options[j], key = q.id + "|" + j;
      pushHist();
      if (o.goto === "end_out") o.goto = passBack.get(key) || "";
      else { passBack.set(key, o.goto || ""); o.goto = "end_out"; }
      changed();
      toast(o.goto === "end_out" ? "已设为：选这项会被甄别 / 拒绝" : "已设为：选这项通过，不会被拒绝");
    }));
    const bindBullet = (el, leave) => {   // a bullet list: Enter adds a line after this one; leaving a bullet saves the list, an empty one is dropped
      el.onblur = () => { if (!el.textContent.trim() && el.parentElement.parentElement.children.length > 1) el.parentElement.remove(); leave(); };
      el.onkeydown = (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        const li = document.createElement("li"), t = document.createElement("span");
        t.className = "rq-bt"; t.contentEditable = "true"; t.spellcheck = false; li.append(t); el.parentElement.after(li); bindBullet(t, leave); t.focus();
      };
    };
    const commitList = (kind) => {   // 研究目标: up to 8 goals; 招募需求: its lines; saved with the form, and only when something changed
      const lines = [...document.querySelectorAll(`#${kind === "goals" ? "rfgoalsl" : "rfneedl"} .rq-bt`)].map((e) => e.textContent.trim()).filter(Boolean);
      if (kind === "goals") { const next = lines.slice(0, 8); if (JSON.stringify(next) === JSON.stringify(goals)) return; pushHist(); goals = next; }
      else { if (lines.join("\n") === needLines().join("\n")) return; pushHist(); need = lines.join("\n"); }
      save();
    };
    document.querySelectorAll("#rfbrief .rq-bt").forEach((el) => bindBullet(el, saveBrief));
    document.querySelectorAll("#rfgoalsl .rq-bt").forEach((el) => bindBullet(el, () => commitList("goals")));
    document.querySelectorAll("#rfneedl .rq-bt").forEach((el) => bindBullet(el, () => commitList("need")));
    document.querySelectorAll("[data-grip]").forEach((g) => { g.onpointerdown = (e) => dragQ(e, g); g.onkeydown = (e) => keyMove(e, g); });
    if (focusGrip !== null) { const gr = document.querySelector(`#rfright [data-grip="${focusGrip}"]`); if (gr) gr.focus(); focusGrip = null; }
    document.querySelectorAll("[data-menu]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); menu = menu === b.dataset.menu ? null : b.dataset.menu; drawRight(); }));
    document.querySelectorAll("[data-mact]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); menuAct(b.dataset.mact, +b.dataset.qi, +(b.dataset.oj ?? -1)); }));
    document.querySelectorAll("[data-oadd-view]").forEach((b) => (b.onclick = () => {
      const q = Q.find((x) => x.id === b.dataset.oaddView); if (!q) return;
      pushHist(); q.options.push({ id: optId(q), text: "", goto: "", tag: "" }); editing.add(q.id); changed();
    }));
    document.querySelectorAll(".rf-q .rf-edit").forEach((card) => {
      const i = +card.dataset.qi, q = Q[i];
      card.querySelectorAll("[data-f]").forEach((el) => (el.onchange = () => {
        const k = el.dataset.f; pushHist();
        if (k === "type") { q.type = el.value; if ((q.type === "single" || q.type === "multi" || q.type === "scale") && q.options.length < 2) q.options = blank(); if (q.type !== "number") q.rules = []; if (q.type !== "multi") q.options.forEach((o) => { o.excl = []; }); return changed(); }
        q[k] = k === "required" ? el.checked : el.value; save();
      }));
      card.querySelectorAll("[data-mv]").forEach((b) => (b.onclick = () => { pushHist(); const j = i + +b.dataset.mv; [Q[i], Q[j]] = [Q[j], Q[i]]; changed(); }));
      card.querySelector("[data-del]").onclick = () => { pushHist(); editing.delete(q.id); Q.splice(i, 1); changed(); };
      card.querySelector("[data-done]").onclick = () => { editing.delete(q.id); drawRight(); };
      card.querySelectorAll(".rf-optrow[data-oj]").forEach((row) => { const o = q.options[+row.dataset.oj]; row.querySelectorAll("[data-o]").forEach((el) => (el.onchange = () => { pushHist(); o[el.dataset.o] = el.value; save(); })); });
      card.querySelectorAll("[data-odel]").forEach((b) => (b.onclick = () => { pushHist(); const gone = q.options.splice(+b.dataset.odel, 1)[0]; q.options.forEach((x) => { x.excl = (x.excl || []).filter((id) => id !== gone.id); }); changed(); }));
      card.querySelectorAll("[data-ex]").forEach((b) => (b.onclick = () => { const o = q.options[+b.dataset.ex]; openEx.has(o.id) ? openEx.delete(o.id) : openEx.add(o.id); drawRight(); }));
      card.querySelectorAll("[data-exo]").forEach((b) => (b.onclick = () => { const [j, k] = b.dataset.exo.split("|").map(Number), o = q.options[j], other = q.options[k]; pushHist(); setExcl(q, o, other, !(o.excl || []).includes(other.id)); changed(); }));
      const oa = card.querySelector("[data-oadd]"); if (oa) oa.onclick = () => { pushHist(); q.options.push({ id: optId(q), text: "", goto: "", tag: "" }); changed(); };
      card.querySelectorAll(".rf-optrow[data-rj]").forEach((row) => { const r = q.rules[+row.dataset.rj]; row.querySelectorAll("[data-r]").forEach((el) => (el.onchange = () => { pushHist(); r[el.dataset.r] = el.dataset.r === "value" ? Number(el.value) : el.value; save(); })); });
      card.querySelectorAll("[data-rdel]").forEach((b) => (b.onclick = () => { pushHist(); q.rules.splice(+b.dataset.rdel, 1); changed(); }));
      const ra = card.querySelector("[data-radd]"); if (ra) ra.onclick = () => { pushHist(); (q.rules = q.rules || []).push({ op: "lt", value: 18, goto: "end_out" }); changed(); };
    });
  }

  /* ---------- the whole card ---------- */
  let rightOpen = sessionStorage.getItem("rf_right") !== "0";  // the outline (right) can be folded away; the choice is kept in this browser
  const setRight = (open) => { rightOpen = open; try { sessionStorage.setItem("rf_right", open ? "1" : "0"); } catch (e) { /* private window */ } const w = $("#rfws"); if (w) w.classList.toggle("rf-collapsed", !open); fit(); };
  function panels() {
    return `<div class="rf-main"><div class="rf-ws${rightOpen ? "" : " rf-collapsed"}${mode === "html" ? " rf-import" : ""}" id="rfws"><div class="rf-pane"><div class="rf-hd"><span class="sp"></span><button class="s" type="button" id="rfundo" disabled>${rfIcon(RF_IC.undo)}撤销</button><button class="s" type="button" id="rfredo" disabled>${rfIcon(RF_IC.redo)}重做</button><button class="s" type="button" id="rfexp">${rfIcon(RF_IC.expand)}展开大纲</button></div>
      <div class="cc" id="ccroot"></div></div>
      <div class="rf-split" id="rfsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="rfright"></div></div><div class="rf-askpanel" id="rfaskpanel" hidden></div></div>`;
  }
  // 旧「访谈大纲」页已删：这里只切到「生成大纲」的空白占位页（2026-11-18）
  const goOutline = () => {
    try { sessionStorage.setItem("ol_menu", "1"); sessionStorage.setItem("ol_seg", "outline"); } catch (e) { /* 隐私窗口 */ }
    if (window.__ptabGo) window.__ptabGo("ols-gen"); else location.hash = `#/p/${p.id}/people`;
  };
  // 问卷来源选择弹窗已按需求删除：来源统一在「招募方式」页的「问卷来源」决定，
  // mode 在开头已取 form.source，进页面直接用，不再二次询问。
  const draw = () => { $("#rfbody").innerHTML = panels(); wire(); };
  const fit = () => { const w = $("#rfws"); if (w && w.offsetParent) { const h = Math.max(380, innerHeight - w.getBoundingClientRect().top - 18); w.style.height = h + "px"; const m = w.parentElement; if (m && m.classList.contains("rf-main")) m.style.height = h + "px"; } };

  function wire() {
    drawLeft(); drawRight(); fit(); wireSplit(); loadOl();   // 两栏先画出来，两种来源都要画（导入态也有左引导 + 右大纲）
    // 导入态右侧链接卡上的三个按钮属于 drawRight 画出来的元素，必须画完再绑
    const sv = $("#rfsave"); if (sv) sv.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "html", html_url: $("#rfurl").value.trim() } })) }; htmlLocked = true; draw(); toast("链接已保存，已锁定"); });
    const op = $("#rfopen"); if (op) op.onclick = () => window.open(f.html_url, "_blank", "noopener,noreferrer");
    const ed = $("#rfedit"); if (ed) ed.onclick = () => { htmlLocked = false; draw(); const u = $("#rfurl"); if (u) u.focus(); };
    if (mode !== "ai") return;   // 导入模式没有题目：不装版本、选句、题目菜单这些
    loadVers(true);
    if (window._rfVerOff) document.removeEventListener("click", window._rfVerOff);
    window._rfVerOff = () => { const m = $("#rfvm"); if (m && !m.hidden) { m.hidden = true; const b = $("#rfvb"); if (b) b.setAttribute("aria-expanded", "false"); } };
    document.addEventListener("click", window._rfVerOff);
    if (window._rfExOff) document.removeEventListener("click", window._rfExOff);
    window._rfExOff = (e) => { if (openEx.size && !e.target.closest(".rf-expanel, [data-ex]")) { openEx.clear(); drawRight(); } };   // the 互斥 picker closes when you click anywhere else
    document.addEventListener("click", window._rfExOff);
    if (window._rfMenuOff) document.removeEventListener("click", window._rfMenuOff);
    window._rfMenuOff = (e) => { if (menu && !e.target.closest("[data-menu], .rq-menu")) { menu = null; drawRight(); } };   // a ⋮ menu closes on a click anywhere else
    document.addEventListener("click", window._rfMenuOff);
    if (window._rfTypeOff) document.removeEventListener("click", window._rfTypeOff);
    window._rfTypeOff = (e) => { if (typeMenuOpen && !e.target.closest(".rq-type-wrap")) { typeMenuOpen = null; drawRight(); } };   // 题型下拉点别处关闭
    document.addEventListener("click", window._rfTypeOff);
    // 撤销 = 撤销上一次对话：回退到最近一次改动（对话 / 手动编辑）之前的状态
    $("#rfundo").onclick = () => { const s = hist.pop(); if (!s) return; redo.push(snap()); apply(s); syncUndo(); changed(); };
    $("#rfexp").onclick = () => setRight(true);
    // 重做 = 全部重做：除研究背景与目标外，清空问卷题目、招募需求、总览表与全部对话，一键重新来
    $("#rfredo").onclick = guard(async () => {
      if (!confirm("将清空问卷题目和招募需求（保留研究背景和研究目标），确定全部重做吗？")) return;
      Q = []; goals = []; need = ""; hist = []; redo = []; editing = new Set(); changedIds = new Set();
      await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "ai", idea: "", goals: [], need: "", questions: [] } });
      f = { ...f, questions: [], goals: [], need: "" };
      syncUndo(); drawRight(); toast("已清空，仅保留研究背景与研究目标，可重新开始");
    });
    syncUndo();   // 每次重绘（draw）后同步按钮状态，避免按钮停留在旧的 disabled 状态
  }
  const ratioKey = "rf_left_pct", setRatio = (pct) => { const w = $("#rfws"); if (w) w.style.setProperty("--rfl", Math.min(60, Math.max(30, pct)) + "%"); };
  function wireSplit() {  // drag the divider to change the share of the conversation (30% to 60%); double click restores 42%; the choice is kept in this browser
    const s = $("#rfsplit"), w = $("#rfws"); if (!s || !w) return;
    let saved = 42; try { saved = +localStorage.getItem(ratioKey) || 42; } catch { /* no storage: the default */ }
    setRatio(saved);
    const move = (e) => { const r = w.getBoundingClientRect(); setRatio(((e.clientX - r.left) / r.width) * 100); };
    const done = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", done); try { localStorage.setItem(ratioKey, parseFloat(w.style.getPropertyValue("--rfl")) || 42); } catch { /* no storage */ } };
    s.onpointerdown = (e) => { e.preventDefault(); addEventListener("pointermove", move); addEventListener("pointerup", done); };
    s.ondblclick = () => { setRatio(42); try { localStorage.removeItem(ratioKey); } catch { /* no storage */ } };
    s.onkeydown = (e) => { if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return; e.preventDefault(); const cur = parseFloat(w.style.getPropertyValue("--rfl")) || 42; setRatio(cur + (e.key === "ArrowRight" ? 2 : -2)); try { localStorage.setItem(ratioKey, parseFloat(w.style.getPropertyValue("--rfl")) || 42); } catch { /* no storage */ } };
  }
  pageOn("rf-fit", window, "resize", fit);
  const html = `<div class="rf" style="padding:0"><div id="rfbody">${panels()}</div></div>`;
  return { html, wire, fit };
}
