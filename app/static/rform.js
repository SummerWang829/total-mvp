/* 甄别设定页的“招募问卷”工作台：左边和 AI 对话（说一句话，问卷就跟着改），右边是实时大纲（研究背景、研究目标、招募需求、问卷流程），
   左右两个框各自滚动，整页不滚。大纲里每道题也能直接点开手动改（题型、选项、跳转、标签）。切到“测试答题”就是候选人看到的样子；
   写好发布成链接和二维码，候选人在 screen.html 作答，合格的人自动进受访者名单。也可以改成导入在别的系统里做好的 HTML 问卷链接。
   依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）和 screen_run.js。 */
const RF_TYPE = { single: "选择题", multi: "多选题", text: "开放题", number: "数字题", scale: "量表题" };
const RF_OP = { lt: "小于", lte: "不大于", gt: "大于", gte: "不小于", eq: "等于" };
const RF_MAX_FILES = 5;
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
const RF_CLIP = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
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
function rfCard(p, form, rec) {
  let f = form, mode = form.source === "html" ? "html" : "ai", seg = "outline", Q = JSON.parse(JSON.stringify(form.questions || [])), goals = [...(form.goals || [])], need = form.need || "";
  const rm = (rec && rec.recruit_mode) || "screen_then_invite", hm = (rec && rec.host_mode) || "human";   // 招募方式 / 主持方式（决定阶段增减）
  if (rm === "onelink") { mode = "ai"; f = { ...f, source: "ai", source_confirmed: "ai" }; }   // 一站式 = AI 问卷 + AI 访谈，来源锁定为 AI，不弹来源选择、不可切换
  const stageNow = () => Math.min(STAGES.length, Math.max(2, (f.stage || 2)));   // 5 步主链；研究目标已带入，恒从「受访者与配额」起（显示为已完成）
  // 流程由 11 阶段重定义为 5 步后，旧项目残留的阶段号不再适用：新会话统一从「受访者与配额」开始（本地立即生效，后端随后同步）
  const flowKey = "rf_flow_" + p.id;
  if (sessionStorage.getItem(flowKey) !== "1") {
    sessionStorage.setItem(flowKey, "1");
    f = { ...f, stage: 2 };
    api(`/projects/${p.id}/recruiting/stage`, { method: "PUT", body: { stage: 2 } }).catch(() => {});
  }
  let timer = null, sending = false, pending = "", hist = [], redo = [], editing = new Set();
  let ver = "", vers = null;   // the language version being looked at (""/the source = the original) and the versions' states (app/static/rtrans.js)
  const isSource = () => !ver || (vers && ver === vers.source);
  let showSug = false, changedIds = new Set();   // showSug: the suggestion cards are open in the chat; changedIds: the questions the last change touched ("刚改")
  const useLib = true;   // the research library always goes along (no tick, 2026-11-03)
  let fail = null;   // fail: the last turn that did not get an answer { ask, text, fatal } (kept here only, never saved)
  let htmlLocked = !!form.html_url;   // the saved link is read-only until 编辑 is pressed (2026-11-03)
  let menu = null;   // the open ⋮ menu of a question ("q:<id>") or an option ("o:<id>:<number>"); one at a time
  let typeMenuOpen = null;   // 哪道题的题型下拉正打开（qid）
  let busy = false, focusGrip = null;   // busy: a moved card is still landing; focusGrip: the handle to focus after a keyboard move
  let files = [];   // documents read for the next chat message: { name, text, truncated } (kept here only, sent with one message, never saved)
  let askOpen = null;   // 题旁小对话：正在问的是哪一题（qid），null = 都收起
  let askBusy = false, askFail = "";   // askBusy: 该题小对话正在等 AI；askFail: 该题上次失败的原因
  let askImgs = [];   // 该题小对话里贴的截图（dataURL 预览），随消息一起发，当前模型不识别图片、仅作参考附言
  let askRec = null;   // 该题小对话的录音状态：null 空闲 | { stream, rec, chunks } 录音中 | "busy" 正在转写
  let ovRows = null, ovConfirming = false;   // 总览表（第4阶段）：null=未生成；rows 数组{stage,ask,n,time,goal}；ovConfirming=正在确认继续
  const passBack = new Map();   // 选项切红叉前记住原 goto，点回绿时恢复（不污染问卷结构）
  // AI 询问窗口的 5 步主链（2026-11-05 用户重定义）：研究目标已带入不问；主持已在「招募方式」选定，故删「主持与招募」；
  // 甄别问卷 / 同意与合规 / 访谈大纲并入「总览表」一并生成；试访 / 发布不属于 AI 询问窗口，去掉。
  const STAGES = [
    ["研究目标", true], ["受访者与配额", true], ["形式与语言", true], ["总览表", true], ["重点关注", true]
  ];
  // 行文语言（问卷与大纲用哪种语言书写）：必须让用户明确选一种
  const LANG_OPTS = [
    ["中文（普通话 · 简体）", "zh-CN"], ["中文（香港繁体）", "zh-HK"],
    ["中文（粤语口语）", "yue"], ["English（英文）", "en"]
  ];
  let chosenLang = "";
  // 上文摘要：把已自动带入的研究背景 / 研究目标 / 招募需求浓缩，让 AI 建议「根据上讲的来看」
  const ctxLead = () => {
    const lines = [...briefLines(), ...(goals || []), ...needLines()].map((x) => String(x).trim()).filter(Boolean);
    return lines.slice(0, 3).join("；").slice(0, 160);
  };
  const withCtx = (t) => t + (ctxLead() ? `\n请依据已带入的上文作答：${ctxLead()}；研究目标：${(goals || []).join("；")}` : "");
  // 主动性建议：每步 3 条让用户选，文案与回答都基于上文
  const quotaTips = () => [
    ["按已带入的研究背景与目的，帮我确定受访者配额和人群拆分", "按目标定配额"],
    ["结合上文的人群条件，给我 3 种配额分配方案让我选", "给 3 种配额方案"],
    ["把上文里的筛选条件整理成配额门槛，并确认可招募量", "确认筛选门槛"],
  ];
  const focusTips = () => Q.length
    ? Q.slice(0, 6).map((q, i) => [`第 ${i + 1} 题「${String(q.text || "").slice(0, 18)}」要重点关注什么？帮我逐条列出关注点`, `第 ${i + 1} 题关注点`])
    : [["请按总览表生成所有题目，再逐题确认关注点", "先生成题目"]];
  const tipsFor = (n) => n === 2 ? quotaTips() : n === 5 ? focusTips() : [];
  const RF_KIND = { answer: ["回答", "ans"], suggest: ["建议", "sug"], stat: ["统计", "stat"] };
  const aiOk = () => !f.ai || f.ai.ok;
  // ---------- 总览表（第 6 阶段「形式与语言」之后、第 7 阶段「访谈大纲」之前） ----------
  // 列：阶段 | 问什么 | 多少题 | 多少时间 | 什么目的。用户可编辑、下载 CSV 改后上传回，确认才继续出题/出大纲。
  const OV_STAGES = [["甄别问卷", "筛选是否符合受访者条件：常住地区、年龄、身份、消费习惯、到港情况等"], ["访谈大纲 · 甄别追问", "对通过甄别的受访者，分群深挖甄别结果背后的原因"], ["访谈大纲 · 主题追问", "围绕研究目标逐主题展开的开放式追问"]];
  const ovDefault = () => {
    const nq = Q.length;
    const per = nq ? Math.max(1, Math.round(nq / 3)) : 1;   // 粗分三段的题数（无题时给占位 1）
    return [
      { stage: "甄别问卷", ask: OV_STAGES[0][1], n: String(nq), time: "5 分钟", goal: "判断受访者是否符合，不符合即筛除" },
      { stage: "访谈大纲 · 甄别追问", ask: OV_STAGES[1][1], n: "—", time: "10 分钟", goal: "理解甄别选项背后的动机与场景" },
      { stage: "访谈大纲 · 主题追问", ask: OV_STAGES[2][1], n: "—", time: "15 分钟", goal: "覆盖研究目标，收集可用洞察" },
    ];
  };
  const ovRowsNow = () => ovRows || (ovRows = ovDefault());
  const ovSet = (rows) => { ovRows = rows; };
  function ovCsv() {   // 导出 CSV（带 BOM，Excel 可直接打开中文）
    const head = ["阶段", "问什么", "多少题", "多少时间", "什么目的"];
    const escCsv = (s) => { s = (s == null ? "" : String(s)); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const lines = [head, ...ovRowsNow().map((r) => [r.stage, r.ask, r.n, r.time, r.goal])].map((row) => row.map(escCsv).join(","));
    return "\uFEFF" + lines.join("\r\n");
  }
  function ovParse(text) {   // 解析 CSV（或 TSV）回行数组；列顺序按表头，缺失用空
    const rows = [], lines = (text || "").replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim());
    for (const line of lines) {
      const cells = [], m = line.match(/("([^"]|"")*"|[^,]*)(,|$)/g);   // 简单 CSV 解析，支持带引号
      if (m) for (const c of m) { let v = c.replace(/,$/, ""); if (v.startsWith('"') && v.endsWith('"') && v.length >= 2) v = v.slice(1, -1).replace(/""/g, '"'); cells.push(v); }
      else cells.push(line);
      if (!cells.length || !cells.join("").trim()) continue;
      rows.push(cells);
    }
    const head = rows[0] || [];
    const idx = (names) => { const i = head.findIndex((h) => names.includes((h || "").trim())); return i; };
    const iStage = idx(["阶段", "stage"]), iAsk = idx(["问什么", "ask"]), iN = idx(["多少题", "n", "题数"]), iTime = idx(["多少时间", "time", "时间"]), iGoal = idx(["什么目的", "goal", "目的"]);
    const g = (r, i) => (i >= 0 && i < r.length ? (r[i] || "").trim() : "");
    return rows.slice(1).filter((r) => r.some((c) => c.trim())).map((r) => ({ stage: g(r, iStage) || "阶段", ask: g(r, iAsk), n: g(r, iN), time: g(r, iTime), goal: g(r, iGoal) }));
  }
  function ovHtml() {   // 可编辑总览表 + 三个操作按钮
    const rows = ovRowsNow();
    const cell = (v, k, r, ph, width, ta) => ta
      ? `<textarea class="ov-in ov-ta" data-ovk="${k}" data-ovr="${r}" rows="1" placeholder="${ph}" style="width:${width}" aria-label="${ph}">${esc(v || "")}</textarea>`
      : `<input class="ov-in" data-ovk="${k}" data-ovr="${r}" value="${esc(v || "")}" placeholder="${ph}" style="width:${width}" aria-label="${ph}">`;
    const body = rows.map((row, r) => `<tr>
        <td>${cell(row.stage, "stage", r, "阶段", "120px", false)}</td>
        <td>${cell(row.ask, "ask", r, "问什么", "100%", true)}</td>
        <td>${cell(row.n, "n", r, "题数", "64px", false)}</td>
        <td>${cell(row.time, "time", r, "时间", "72px", false)}</td>
        <td>${cell(row.goal, "goal", r, "目的", "100%", true)}</td>
        <td><button class="s warn" type="button" data-ovdel="${r}" aria-label="删除这一行" title="删除这一行">×</button></td></tr>`).join("");
    return `<div class="rf-ov" id="rfov">
      <div class="rf-ov-hd"><span class="rf-ov-t">📋 访谈总览表</span><span class="sm mut">出题前先对齐：每个阶段问什么、多少题、多长时间、什么目的</span></div>
      <div class="rf-ov-tbl"><table><thead><tr><th style="width:120px">阶段</th><th>问什么</th><th style="width:64px">多少题</th><th style="width:72px">多少时间</th><th>什么目的</th><th style="width:36px"></th></tr></thead><tbody>${body}</tbody></table></div>
      <div class="rf-ov-ft">
        <button class="s" type="button" data-ovadd>＋ 加一行</button>
        <span class="sp"></span>
        <button class="s" type="button" data-ovdl>下载 CSV</button>
        <button class="s" type="button" data-ovup>上传 CSV</button>
        <input type="file" id="rfovfile" hidden accept=".csv,.txt">
        <button class="p" type="button" data-ovok ${ovConfirming ? "disabled" : ""}>${ovConfirming ? "确认中…" : "确认，按这个表继续出题"}</button>
      </div>
      <div class="sm mut" style="margin-top:6px">可直接在表里改，或下载成 CSV 用 Excel 改完再上传回来；确认后 AI 才按这张表一步步出所有题目和访谈大纲。</div></div>`;
  }
  const nl = (s) => esc(s).replace(/\n/g, "<br>");
  const sugs = () => f.suggestions || [];
  const sugSeen = () => { try { return sessionStorage.getItem("rf_seen_" + p.id) || ""; } catch { return ""; } };
  const markSeen = () => { try { sessionStorage.setItem("rf_seen_" + p.id, sugs().map((s) => s.sig).join(",")); } catch { /* no storage */ } };
  const diffIds = (before, after) => { const b = Object.fromEntries(before.map((q) => [q.id, JSON.stringify(q)])); return new Set(after.filter((q) => b[q.id] !== JSON.stringify(q)).map((q) => q.id)); };
  const chipsOf = (keys) => (keys && keys.length ? `<div class="rf-src">${keys.map((k) => `<span>${esc(k)}</span>`).join("")}</div>` : "");
  const bars = (m) => { const top = Math.max(1, ...(m.bars || []).map((b) => b.n)); return (m.bars || []).map((b) => `<div class="rf-bar"><span>${esc(b.label)}</span><i style="width:${Math.round((b.n / top) * 140)}px"></i>${b.n} 人</div>`).join(""); };
  const msgHtml = (m, last, idx) => {
    if (m.role === "user") return `<div class="rf-msg rf-me">${esc(m.text)}</div>`;
    if (m.kind === "change") {
      const ids = last ? [...changedIds].map((id) => Q.findIndex((q) => q.id === id) + 1).filter(Boolean).sort((a, b) => a - b) : [];
      return `<div class="rf-msg"><div class="rf-chgc"><b>已更新问卷</b><div data-selk="m:${idx}">${nl(m.text)}</div>${ids.length ? `<div class="sm">涉及：第 ${ids.join("、")} 题</div>` : ""}${last && hist.length ? `<button class="s" type="button" data-chip="undo">撤销本次修改</button>` : ""}</div></div>`;
    }
    const k = RF_KIND[m.kind], nx = last && m.next && m.next.length ? `<div class="rf-nx"><span class="sm mut">可继续查看：</span>${m.next.map((n, i) => `<button class="s" type="button" data-next="${i}">${esc(n.label)}</button>`).join("")}</div>` : "";
    return `<div class="rf-msg">${k ? `<span class="rf-kind ${k[1]}">${k[0]}</span>` : ""}<div data-selk="m:${idx}">${nl(m.text)}</div>${m.bars && m.bars.length ? `<div class="rf-bars">${bars(m)}</div>` : ""}${chipsOf(m.sources)}${nx}</div>`;
  };
  const sugCard = (s) => `<div class="rf-sg"><span class="rf-sgk">${esc(s.label)}${s.qno ? ` · 第 ${s.qno} 题` : ""}</span><div>${esc(s.text)}</div>${chipsOf(s.sources)}
    <div class="rf-sgb"><button class="p s" type="button" data-adopt="${esc(s.sig)}">${esc(s.button || "采纳")}</button><a class="rf-nomore" href="#" data-nomore="${esc(s.sig)}">不再提醒</a></div></div>`;
  const snap = () => JSON.stringify({ Q, goals, need, ov: ovRows });   // 撤销/重做把总览表（ovRows）也一起快照，改总览表也能撤销
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
  const apply = (s) => { const o = JSON.parse(s); Q = o.Q; goals = o.goals; need = o.need; ovRows = o.ov ?? ovRows; editing = new Set(); };
  const syncUndo = () => { const u = $("#rfundo"), r = $("#rfredo"); if (u) u.disabled = !hist.length; if (r) r.disabled = !(Q.length || goals.length || need || ovRows || (f.chat && f.chat.length)); };
  const changed = () => { fix(); save(); changedIds = new Set(); drawRight(); drawChat(); };  // after a manual edit: the "刚改" marks go, suggestions about edited questions are gone with the next save
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

  /* ---------- left: the conversation ---------- */
  // 11 阶段进度条（产品文档第 9 章）：当前阶段橙色，完成打勾可跳回，未到灰色；一站式时隐藏第 9 阶段「邀请信」
  function stageBar() {
    if (mode === "html") return "";
    const cur = stageNow(), list = STAGES.map(([n, show], i) => ({ n, i: i + 1, show: show || rm !== "onelink" }));
    const items = list.filter((s) => s.show);
    const html = items.map((s) => {
      const done = s.i < cur, on = s.i === cur;
      const cls = `rq-stg${done ? " done" : ""}${on ? " on" : ""}`;
      return `<button type="button" class="${cls}" data-stage="${s.i}" title="${done ? "已完成，点击跳回" : on ? "当前阶段" : "还没到"}" aria-label="阶段 ${s.i} ${s.n}">${done ? "✓" : s.i}<span>${s.n}</span></button>`;
    }).join("");
    return `<div class="rq-stages" role="tablist" aria-label="对话阶段">${html}</div>`;
  }
  function chatHtml() {
    if (mode === "html") return `<div class="rf-msg">问卷已经导入了，这里不用再出题。<div class="sm mut" style="margin-top:6px">下一步是生成访谈大纲：按这份问卷的筛选字段（常住地区、年龄、到港、商圈、收入）搭结构。</div></div>
      <div class="rf-steps">
        <div><i>1</i><span>确认<b>研究背景 · 研究目标 · 招募需求</b>（右侧可以直接改）</span></div>
        <div><i>2</i><span>生成<b>访谈大纲</b>，按分群（大湾区 / 非大湾区）拆分追问</span></div>
        <div><i>3</i><span>Confirmit 数据回来后，在<b>配额与受访者名单</b>里导入甄别数据</span></div>
      </div>
      <div class="row" style="margin:2px 0 10px"><button class="p" type="button" id="rfgooutline">生成访谈大纲 →</button></div>`;
    // 研究目标/背景已自动带入，展示出来即可，不再向用户发问；窗口里只保留主链内容，不出现通用打招呼与收尾建议
    const bLines = briefLines(), gLines = goals;
    const brought = (bLines.length || gLines.length) ? `<div class="rf-brought"><div class="sm mut">已自动带入项目的研究背景与研究目标：</div>
      ${bLines.length ? `<div class="rf-br-b"><span class="rf-br-k">研究背景</span>${bLines.map((l) => `<span>${esc(l)}</span>`).join("")}</div>` : ""}
      ${gLines.length ? `<div class="rf-br-b"><span class="rf-br-k">研究目标</span>${gLines.map((l) => `<span>${esc(l)}</span>`).join("")}</div>` : ""}
      </div>` : "";
    const all = f.chat || [], msgs = all.map((m, i) => msgHtml(m, i === all.length - 1 && !fail, i)).join("");
    const failHtml = fail ? `<div class="rf-msg rf-me">${esc(fail.ask)}</div><div class="rf-err" role="alert"><b>本次未能完成回复</b><div>${esc(fail.text)}</div>${fail.fatal ? "" : `<button class="s" type="button" data-chip="retry">重新尝试</button><button class="s" type="button" data-chip="rephrase">调整提问方式</button>`}</div>` : "";
    const down = aiOk() ? "" : `<div class="rf-bad" role="alert">${esc(f.ai.reason)}</div>`;
    // 选完行文语言后（第 4 阶段「总览表」）才出这张表：连带甄别问卷的访问大纲，确认后才逐步出题/出大纲
    const ovCard = stageNow() === 4 ? ovHtml() : "";
    return down + stageBar() + ovCard + brought + msgs + failHtml + (pending ? `<div class="rf-msg rf-me">${esc(pending)}</div><div class="rf-msg mut">正在处理…</div>` : "");
  }
  function bubbles() {   // AI 窗口只给主链的主动建议：配额 3 条 / 语言明确询问 / 总览表交表格 / 重点关注逐题
    if (mode === "html") return "";
    const why = aiOk() ? "" : ` disabled title="${esc(f.ai.reason)}"`;
    const cur = stageNow();
    if (cur === 2) {   // 受访者与配额：根据上文给 3 条主动建议让用户选，三条都可点；点「下一步」才推进
      return `<span class="rq-bubh">受访者与配额 · 请选一条</span>` + tipsFor(2).map((t, i) => `<button class="rq-bub" type="button" data-tip="2" data-tipi="${i}"${why}>${esc(t[0])}</button>`).join("")
        + `<button class="s" type="button" data-stage-go="3">下一步：选行文语言 →</button>`;
    }
    if (cur === 3) {   // 形式与语言：非常清楚地问「用哪一种行文语言」
      const label = (LANG_OPTS.find((x) => x[1] === chosenLang) || [])[0];
      return `<span class="rq-bubh">问卷与大纲用哪一种行文语言？${label ? `（当前：${esc(label)}）` : "请选一种"}</span>` + LANG_OPTS.map(([n, code]) => `<button class="rq-bub${chosenLang === code ? " on" : ""}" type="button" data-lang="${code}">${esc(n)}</button>`).join("");
    }
    if (cur === 4) return "";   // 总览表已在对话区，不叠加快捷建议
    if (cur === 5) {   // 重点关注：针对每一道题问要重点关注什么
      return `<span class="rq-bubh">重点关注 · 逐题确认</span>` + tipsFor(5).map((t, i) => `<button class="rq-bub" type="button" data-tip="5" data-tipi="${i}"${why}>${esc(t[0])}</button>`).join("");
    }
    return "";
  }
  const drawChat = () => { const c = $("#rfchat"); if (!c) return; c.innerHTML = chatHtml(); c.scrollTop = c.scrollHeight; const b = $("#rfbub"); if (b) b.innerHTML = pending ? "" : bubbles(); wireChips(); wireOverview(); const g = $("#rfgooutline"); if (g) g.onclick = goOutline; wireStages(); };
  function wireStages() {   // 点进度条跳回（或前进到）某个阶段：只允许往前跳不超过当前+1，向后随便跳回
    document.querySelectorAll("#rfchat [data-stage]").forEach((b) => (b.onclick = guard(async () => {
      const to = +b.dataset.stage, cur = stageNow();
      if (to < 2) return toast("研究目标与研究背景已自动带入，无需再问", true);
      if (to > cur + 1) return toast("前面还有未确认的阶段，先完成再往下", true);
      if (to === cur) return;
      if (cur === 4 && to > 4) return toast("请先确认「总览表」，再逐题确认重点关注", true);   // 选完语言出总览表，确认后才放行
      f = { ...f, ...(await api(`/projects/${p.id}/recruiting/stage`, { method: "PUT", body: { stage: to } })) };
      toast(`已进入第 ${to} 阶段：${(STAGES[to - 1] || ["", ""])[0]}`); drawChat();
    })));
  }
  // 总览表的操作：编辑、加行、删行、下载 CSV、上传 CSV、确认继续（编辑/加行/删行/上传都记入撤销历史）
  function wireOverview() {
    const ov = $("#rfov"); if (!ov) return;
    ov.querySelectorAll(".ov-ta").forEach((t) => rfAutosize(t));   // 长文本 textarea 自适应高度
    ov.querySelectorAll("[data-ovk]").forEach((inp) => {
      const k = inp.dataset.ovk, r = +inp.dataset.ovr;
      let before = null;
      inp.onfocus = () => { before = snap(); };   // 聚焦时记快照，编辑后离开时若变了才 push，避免每敲一个字都进历史
      inp.oninput = () => { const rows = ovRowsNow(); if (rows[r]) rows[r][k] = inp.value; };
      inp.onblur = () => { if (before !== null && before !== snap()) pushHist(); before = null; };
      inp.onchange = () => { if (before !== null && before !== snap()) pushHist(); before = null; };
    });
    ov.querySelectorAll("[data-ovdel]").forEach((b) => (b.onclick = () => {
      const rows = ovRowsNow(); pushHist(); rows.splice(+b.dataset.ovdel, 1); syncUndo(); drawChat();
    }));
    const add = ov.querySelector("[data-ovadd]"); if (add) add.onclick = () => { pushHist(); ovRowsNow().push({ stage: "阶段", ask: "", n: "", time: "", goal: "" }); syncUndo(); drawChat(); };
    const dl = ov.querySelector("[data-ovdl]"); if (dl) dl.onclick = () => {
      const blob = new Blob([ovCsv()], { type: "text/csv;charset=utf-8" }), a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "访谈总览表.csv"; a.click(); URL.revokeObjectURL(a.href);
    };
    const up = ov.querySelector("[data-ovup]"), fin = $("#rfovfile");
    if (up && fin) { up.onclick = () => fin.click(); fin.onchange = () => { const file = fin.files[0]; fin.value = ""; if (!file) return; const rd = new FileReader(); rd.onload = () => { const rows = ovParse(String(rd.result)); if (!rows.length) return toast("没读到有效行，请检查 CSV 格式（首行列名：阶段,问什么,多少题,多少时间,什么目的）", true); pushHist(); ovSet(rows); syncUndo(); drawChat(); toast(`已读入 ${rows.length} 行`); }; rd.readAsText(file, "utf-8"); }; }
    const ok = ov.querySelector("[data-ovok]"); if (ok) ok.onclick = guard(async () => {
      const rows = ovRowsNow(); if (!rows.length) return toast("总览表不能为空", true);
      ovConfirming = true; drawChat();
      // 确认后推进到第 5 阶段「重点关注」，AI 按这张表一步步出题/出大纲
      const to = 5;
      try {
        f = { ...f, ...(await api(`/projects/${p.id}/recruiting/stage`, { method: "PUT", body: { stage: to } })) };
        const brief = rows.map((r) => `${r.stage}：${r.ask}（${r.n ? `${r.n} 题` : ""}${r.time ? ` · ${r.time}` : ""}${r.goal ? ` · ${r.goal}` : ""}）`).join("\n");
        await send(`请按下面这张总览表，一步步生成所有题目和访谈大纲：\n${brief}`);
      } finally { ovConfirming = false; drawChat(); }
    });
  }
  async function send(text) {
    text = (text || "").trim(); if (!text || sending) return;
    if (incomplete()) return toast("有题目或选项还没写完，先写完再对话", true);
    sending = true; pending = text; fail = null; $("#rfmsg").value = ""; $("#rfsend").disabled = true; drawChat();
    const before = snap();
    try {
      const r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: text, questions: Q, goals, need, use_library: useLib, attachments: files.map((x) => ({ name: x.name, text: x.text })) } });
      if (r.changed || JSON.stringify(r.goals) !== JSON.stringify(goals) || r.need !== need) { hist.push(before); if (hist.length > 30) hist.shift(); redo = []; }
      changedIds = r.changed ? diffIds(Q, r.questions) : new Set();
      f = { ...f, ...r }; Q = JSON.parse(JSON.stringify(r.questions)); goals = [...(r.goals || [])]; need = r.need || ""; editing = new Set();
    } catch (e) { fail = { ask: text, text: e.message, fatal: e.status === 503 }; if (e.status === 503) f = { ...f, ai: { ...(f.ai || {}), ok: false, reason: e.message } }; }
    sending = false; pending = ""; const b = $("#rfsend"); if (b) b.disabled = false; if (!fail) files = []; syncUndo(); drawChat(); drawRight(); drawFiles();
    return !fail;
  }
  let sel = null;   // the "选中文字 → 修改清单" helper (app/static/chatsel.js)
  const selLabel = (key) => { const m = /^q:(.+)$/.exec(key || ""); const i = m ? Q.findIndex((q) => q.id === m[1]) : -1; return i >= 0 ? `第 ${i + 1} 题` : ""; };
  function wireSel() {
    sel = ChatSel.attach({
      roots: [$("#rfchat"), $("#rfright")], bar: $("#rfsel"), label: selLabel,
      ask: async ({ quote, text }) => {   // 只问不改: answered on the server without touching or saving the questionnaire
        const r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: `针对这一处：“${quote}”
${text}
请只回答，不要修改问卷。`, questions: Q, goals, need, use_library: useLib, ask_only: true } });
        return r.reply;
      },
      send: async ({ text }) => { const box = $("#rfmsg"), draft = box ? box.value : ""; const ok = await send(text); if (box && draft) box.value = draft; return ok; },
    });
  }
  const ASK = { draft: "帮我起草问卷", goals: "对照研究目标和招募需求，核查问卷是否有未覆盖之处，仅告知结论，暂不修改", neutral: "检查问卷是否存在引导性或带有倾向的措辞，如有，请修改得更为中立" };
  const drawFiles = () => {   // the documents waiting for the next message, above the input; each can be taken off
    const row = $("#rfarow"); if (!row) return;
    row.hidden = !files.length;
    row.innerHTML = files.map((x, i) => `<span class="rq-file">${esc(x.name)}${x.truncated ? "（超出部分未提供）" : ""}<button type="button" data-rmf="${i}" aria-label="去掉 ${esc(x.name)}">${rfIcon(RF_IC.x)}</button></span>`).join("");
    row.querySelectorAll("[data-rmf]").forEach((b) => (b.onclick = () => { files.splice(+b.dataset.rmf, 1); drawFiles(); }));
  };
  const showFerr = (msg) => { const err = $("#rfferr"); if (!err) return; err.hidden = !msg; err.textContent = msg || ""; };
  async function attach(file) {   // the file is read on the server into text (PDF, Word, text); images and video are refused there, and the reason is shown
    if (files.length >= RF_MAX_FILES) return showFerr(`一次最多附 ${RF_MAX_FILES} 个文件`);
    const form = new FormData(); form.append("file", file);
    showFerr(`正在读取 ${file.name}…`);
    try {
      const r = await fetch("/api/attachments/extract", { method: "POST", headers: { Authorization: "Bearer " + store.get("tt_token") }, body: form });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || "这个文件读不出来"); }
      const out = await r.json();
      files.push({ name: file.name, text: out.text, truncated: out.truncated }); showFerr(""); drawFiles();
    } catch (e) { showFerr(e.message); }
  }
  async function stats(kind) {   // 预估通过率 / 定位筛除率较高的题目: worked out on the server from the anonymous counts, no model
    if (sending) return; sending = true; fail = null; pending = kind === "rate" ? "预估通过率" : "定位筛除率较高的题目"; drawChat();
    try { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/stats`, { method: "POST", body: { kind } })) }; } catch (e) { fail = { ask: pending, text: e.message, fatal: true }; }
    sending = false; pending = ""; drawChat();
  }
  async function review() {
    if (sending) return; if (incomplete()) return toast("有题目或选项还没写完，先写完再检查", true);
    sending = true; fail = null; pending = "检查问卷"; drawChat();
    try { const r = await api(`/projects/${p.id}/recruiting/form/review`, { method: "POST" }); f = { ...f, ...r }; showSug = true; markSeen(); }
    catch (e) { fail = { ask: pending, text: e.message, fatal: e.status === 503 }; }
    sending = false; pending = ""; drawChat(); drawRight();
  }
  const adopt = guard(async (sig) => {
    const s = sugs().find((x) => x.sig === sig); if (!s) return;
    if (!s.fix) return send("采纳：" + s.instruction);   // the others go through the chat, which edits with the usual checks
    const before = snap(), beforeQ = JSON.parse(JSON.stringify(Q));
    const r = await api(`/projects/${p.id}/recruiting/form/suggestions/adopt`, { method: "POST", body: { sig } });
    hist.push(before); if (hist.length > 30) hist.shift(); redo = [];
    changedIds = diffIds(beforeQ, r.questions); f = { ...f, ...r }; Q = JSON.parse(JSON.stringify(r.questions)); editing = new Set(); syncUndo(); markSeen(); drawChat(); drawRight();
  });
  const nomore = guard(async (sig) => { const r = await api(`/projects/${p.id}/recruiting/form/suggestions/dismiss`, { method: "POST", body: { sig } }); f = { ...f, suggestions: r.suggestions }; markSeen(); drawChat(); drawRight(); });
  function wireChips() {
    document.querySelectorAll("[data-chip]").forEach((b) => (b.onclick = () => {
      const c = b.dataset.chip;
      if (c === "test") { seg = "test"; drawRight(); } else if (c === "pub") publish(); else if (c === "review") review();
      else if (c === "showsug") { showSug = true; markSeen(); drawChat(); drawRight(); }
      else if (c === "rate" || c === "worst") stats(c);
      else if (c === "retry" && fail) send(fail.ask);
      else if (c === "rephrase" && fail) { const m = $("#rfmsg"), a = fail.ask; fail = null; drawChat(); m.value = a; m.focus(); }
      else if (c === "undo") { const u = $("#rfundo"); if (u && !u.disabled) u.click(); }
      else if (ASK[c]) send(ASK[c]);
    }));
    // 主链建议（配额 / 重点关注）：只把「根据上文」的建议发出去，不强行推进，三条都可以逐个点；由「下一步」按钮显式推进
    document.querySelectorAll("[data-tip]").forEach((b) => (b.onclick = guard(async () => {
      const tip = tipsFor(+b.dataset.tip)[+(b.dataset.tipi || 0)];
      if (tip) await send(withCtx(tip[0]));
    })));
    // 行文语言：明确选一种，选完即进入「总览表」（表格不是聊天，不调 AI）
    document.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = guard(async () => {
      chosenLang = b.dataset.lang;
      const label = (LANG_OPTS.find((x) => x[1] === chosenLang) || [chosenLang])[0];
      f = { ...f, ...(await api(`/projects/${p.id}/recruiting/stage`, { method: "PUT", body: { stage: 4 } })) };
      toast(`行文语言：${label}。请确认下面的总览表`); drawChat();
    })));
    // 显式「下一步」，把主动权交给用户
    document.querySelectorAll("[data-stage-go]").forEach((b) => (b.onclick = guard(async () => {
      const to = +b.dataset.stageGo;
      f = { ...f, ...(await api(`/projects/${p.id}/recruiting/stage`, { method: "PUT", body: { stage: to } })) };
      toast(`已进入：${(STAGES[to - 1] || ["", ""])[0]}`); drawChat();
    })));
    document.querySelectorAll("[data-next]").forEach((b) => (b.onclick = () => { const last = [...(f.chat || [])].reverse().find((m) => m.next && m.next.length); if (last) send(last.next[+b.dataset.next].ask); }));
    document.querySelectorAll("[data-adopt]").forEach((b) => (b.onclick = () => adopt(b.dataset.adopt)));
    document.querySelectorAll("[data-nomore]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); nomore(a.dataset.nomore); }));
  }

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
  const qBadge = (q) => (changedIds.has(q.id) ? `<span class="rf-bd new">刚改</span>` : showSug && sugs().some((s) => s.qid === q.id) ? `<span class="rf-bd sug">建议</span>` : "");
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
  // 题旁小对话：对这题说一句话，AI 只改这一题（或按范围），先给预览再应用
  function askBox(q, i) {
    const idx = i + 1;
    const imgs = askImgs.map((s, k) => `<span class="rq-askimg"><img src="${s}" alt="截图 ${k + 1}"><button type="button" data-askdelimg="${k}" aria-label="去掉截图 ${k + 1}">${rfIcon(RF_IC.x)}</button></span>`).join("");
    const recLabel = askRec === "busy" ? "正在识别语音…" : (askRec ? "停止录音" : "语音说明");
    const recIcon = askRec === "busy" ? `<span class="rq-askrec-ic spin">${rfIcon(RF_IC.spin)}</span>` : askRec ? `<span class="rq-askrec-ic rec">${rfIcon(RF_IC.stop2)}</span>` : rfIcon(RF_IC.mic);
    return `<div class="rq-askbox" role="dialog" aria-label="对第 ${idx} 题问问 AI"><div class="rq-askhd"><b>改这题 · Q${idx}</b><span class="sm mut">${RF_TYPE[q.type]}${q.required ? " · 必答" : ""}</span><span class="sp"></span><button type="button" class="s" data-askclose="${q.id}">收起</button></div>
      <div class="rq-askbody">${askFail ? `<div class="rq-askerr" role="alert">${esc(askFail)}</div>` : ""}${imgs ? `<div class="rq-askimgs">${imgs}</div>` : ""}<textarea id="rqask-${q.id}" rows="2" maxlength="1000" placeholder="例如：选项太多，压缩到 4 个；或：改成量表 1-5 分（也可以贴截图、或按语音说明）" aria-label="对这题的要求"></textarea></div>
      <div class="rq-askft"><button type="button" class="p" data-asksend="${q.id}" ${askBusy ? "disabled" : ""}>${askBusy ? "正在改…" : "改这题"}</button>
        <button type="button" class="s rq-asktool" data-askimg="${q.id}" aria-label="贴截图" title="贴截图">${rfIcon(RF_IC.img)}<input type="file" accept="image/*" hidden></button>
        <button type="button" class="s rq-asktool rq-askrec" data-askrec="${q.id}" aria-label="${recLabel}" title="${recLabel}">${recIcon}</button>
        <span class="sm mut">AI 会先给修改预览，你确认后才应用。</span></div></div>`;
  }
  // 「改这题」栏与所点题目顶端对齐：面板内容从那道题的顶边开始，滚动题目列表时跟着走
  let askScrollBound = false, askScrollRaf = 0;
  function alignAskPanel(bringIntoView) {
    const panel = $("#rfaskpanel"); if (!panel || panel.hidden || !askOpen) return;
    const box = panel.querySelector(".rq-askbox"), out = $("#rfout");
    if (!box || !out) return;
    const key = (typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(askOpen) : askOpen;
    const card = out.querySelector(`.rf-q[data-qid="${key}"]`);
    if (!card) { panel.style.paddingTop = ""; return; }
    const or = out.getBoundingClientRect();
    let cr = card.getBoundingClientRect();
    if (bringIntoView && (cr.top < or.top + 4 || cr.top > or.bottom - 60)) {
      out.scrollTop = Math.max(0, out.scrollTop + cr.top - or.top - 12);
      cr = card.getBoundingClientRect();
    }
    panel.style.paddingTop = "16px"; panel.scrollTop = 0;
    const d0 = Math.round(cr.top - box.getBoundingClientRect().top);
    const max = Math.max(0, Math.round(panel.getBoundingClientRect().height - 160));
    panel.style.paddingTop = (16 + Math.max(0, Math.min(d0, max))) + "px";
  }
  function bindAskScroll() {
    if (askScrollBound) return; askScrollBound = true;
    document.addEventListener("scroll", (e) => {
      if (!askOpen) return;
      const t = e.target;
      if (!t || t.nodeType !== 1) return;
      if (t.id !== "rfout" && !t.closest("#rfout") && !t.querySelector("#rfout")) return;
      if (askScrollRaf) return;
      askScrollRaf = requestAnimationFrame(() => { askScrollRaf = 0; alignAskPanel(false); });
    }, true);
  }
  // 右侧并排弹出的「改这题」栏：点题目右上角 💬 时，左侧对话栏+问卷栏往里压缩，右侧并排挤进这栏（不遮罩）
  function drawAskPanel() {
    const panel = $("#rfaskpanel"); if (!panel) return;
    const qi = Q.findIndex((x) => x.id === askOpen);
    if (qi < 0) { panel.hidden = true; panel.innerHTML = ""; panel.style.paddingTop = ""; stopAskRec(); return; }
    panel.innerHTML = `${askBox(Q[qi], qi)}`;
    panel.hidden = false;
    const qid = Q[qi].id;
    const b = panel.querySelector(`[data-askclose="${qid}"]`); if (b) b.onclick = () => { askOpen = null; askFail = ""; askImgs = []; stopAskRec(); drawRight(); };
    // 贴截图：读成 dataURL 预览，随消息一起发（当前模型不识别图片内容，仅作参考）
    const img = panel.querySelector(`[data-askimg="${qid}"]`); if (img) { const fin = img.querySelector("input"); img.onclick = () => fin && fin.click(); if (fin) fin.onchange = () => { const file = fin.files[0]; fin.value = ""; if (!file) return; if (askImgs.length >= 4) return toast("最多贴 4 张截图", true); if (!file.type.startsWith("image/")) return toast("只能贴图片", true); const rd = new FileReader(); rd.onload = () => { askImgs.push(String(rd.result)); drawAskPanel(); }; rd.readAsDataURL(file); }; }
    panel.querySelectorAll("[data-askdelimg]").forEach((x) => (x.onclick = () => { askImgs.splice(+x.dataset.askdelimg, 1); drawAskPanel(); }));
    // 录音 / 语音说明：录一段，转成文字填进输入框
    const rec = panel.querySelector(`[data-askrec="${qid}"]`); if (rec) rec.onclick = () => toggleAskRec(qid);
    const s = panel.querySelector(`[data-asksend="${qid}"]`); if (s) s.onclick = guard(async () => {
      const qn = qi + 1;
      const box = $(`#rqask-${qid}`), text = (box ? box.value : "").trim();
      if (!text && !askImgs.length) return toast("先说要怎么改这题，或贴一张截图", true);
      if (incomplete()) return toast("有题目或选项还没写完，先写完再对话", true);
      const withImg = askImgs.length ? `（附 ${askImgs.length} 张截图作为参考）` : "";
      askBusy = true; askFail = ""; drawRight();
      try {
        const r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: `请只修改第 ${qn} 题（id 是 ${qid}），其它题目保持不变。对第 ${qn} 题的要求：${text || "请参考截图"}${withImg}`, questions: Q, goals, need, use_library: useLib } });
        if (r.changed || JSON.stringify(r.goals) !== JSON.stringify(goals) || r.need !== need) { hist.push(snap()); if (hist.length > 30) hist.shift(); redo = []; }
        changedIds = r.changed ? diffIds(Q, r.questions) : new Set();
        f = { ...f, ...r }; Q = JSON.parse(JSON.stringify(r.questions)); goals = [...(r.goals || [])]; need = r.need || ""; editing = new Set();
        askOpen = null; askImgs = []; syncUndo(); drawChat(); drawRight(); toast(r.changed ? "已按你的要求改好这一题" : (r.reply || "已处理"));
      } catch (e) { askFail = e.message; askBusy = false; drawRight(); }
    });
    bindAskScroll(); alignAskPanel(true);
  }
  // 录音：MediaRecorder 录一段，停止后上传 /api/attachments/transcribe，转出的文字填进输入框
  function toggleAskRec(qid) {
    if (askRec === "busy") return;
    if (askRec) { stopAskRec(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return toast("这个浏览器不支持录音", true);
    navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      const rec = new MediaRecorder(stream), chunks = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        askRec = null; drawAskPanel();
        if (!blob.size) { askFail = "没有录到声音，请允许麦克风权限后再试"; drawAskPanel(); return; }
        askRec = "busy"; drawAskPanel();
        try {
          const form = new FormData(); form.append("file", blob, "voice." + (rec.mimeType && rec.mimeType.includes("ogg") ? "ogg" : rec.mimeType && rec.mimeType.includes("mp4") ? "m4a" : "webm"));
          const r = await fetch("/api/attachments/transcribe?lang=zh", { method: "POST", headers: { Authorization: "Bearer " + store.get("tt_token") }, body: form });
          if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || `语音识别失败（${r.status}）`); }
          const out = await r.json();
          const text = (out.text || "").trim();
          if (!text) throw new Error("没有识别出文字，请再说一次或直接打字");
          const box = $(`#rqask-${qid}`); if (box) box.value = (box.value ? box.value + "\n" : "") + text;
          askFail = ""; toast("已转成文字，确认后点“改这题”");
        } catch (e) { askFail = e.message || "语音识别失败"; toast(askFail, true); }
        askRec = null; drawAskPanel();
      };
      askRec = { stream, rec, chunks }; drawAskPanel();
      rec.start();
    }).catch(() => { toast("没有麦克风权限，无法录音", true); });
  }
  function stopAskRec() {
    if (askRec && askRec.rec && askRec.rec.state !== "inactive") { try { askRec.rec.stop(); } catch (e) { /* already stopping */ } }
  }
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
      <div class="rq-top"><span class="rq-qn">Q${i + 1}</span><button class="rq-title" type="button" data-edit="${q.id}">${esc(q.text || "（还没写题目）")}</button>${qBadge(q)}<span class="sp"></span>
        <span class="rq-type-wrap"><button class="rq-type rq-type-btn" type="button" data-typetg="${q.id}" aria-haspopup="true" aria-expanded="false" title="点一下切换题型">${RF_TYPE[q.type] || q.type}${q.required ? " · 必答" : ""}${rfIcon(RF_IC.chev)}</button>${typeMenuOpen === q.id ? `<div class="rq-type-menu" role="menu">${Object.entries(RF_TYPE).map(([k, n]) => `<button type="button" role="menuitem" data-typeset="${q.id}" data-t="${k}" class="${q.type === k ? "on" : ""}">${n}</button>`).join("")}</div>` : ""}</span>
        <button class="rq-ask" type="button" data-ask="${q.id}" aria-label="对这题问问 AI" title="对这题问问 AI">💬</button>
        <button class="rq-more" type="button" data-menu="${qk}" aria-label="题目菜单" aria-expanded="${menu === qk}">${rfIcon(RF_IC.dots)}</button>${menu === qk ? qMenu(q, i) : ""}</div>
      ${opts}${rules}${exWarn(q)}</div>`;
  }
  // 研究背景: one bullet per line of the project's background; the numbering in front of a line is not shown
  const briefLines = () => (f.brief || "").split("\n").map((l) => l.replace(/^\s*(?:\d+\s*[.、)]|[-•·])\s*/, "").trim()).filter(Boolean);
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
    const n = qs.length ? qs.reduce((s, q) => s + (q.target || 0), 0) : (rec && rec.participants_count) || 0;
    return `<div class="rf-total">${rfIcon(RF_IC.users)}<span class="rf-total-n">${n}</span><span class="rf-total-u">人</span></div>`;
  };
  // 访谈结构：按总览表一行一环，标出「甄别」还是「访问」
  const flowStep = (r, i) => {
    const isScreen = /甄别|筛选|资格/.test(r.stage || "");
    const tag = isScreen ? `<span class="rf-flow-tag scr">甄别</span>` : `<span class="rf-flow-tag acc">访问</span>`;
    return `<div class="rf-flow-step">
      <div class="rf-flow-step-hd"><span class="rf-flow-no">${i + 1}</span><b>${esc(r.stage || "环节")}</b>${tag}<span class="sp"></span><span class="sm mut">${esc(r.n || "—")} 题${r.time ? ` · ${esc(r.time)}` : ""}</span></div>
      <div class="rf-flow-step-bd"><span class="sm mut">问什么：</span>${esc(r.ask || "—")}${r.goal ? `<div class="sm mut">目的：${esc(r.goal)}</div>` : ""}</div>
    </div>`;
  };
  function outline() {
    const ovRowsArr = ovRowsNow();
    const flow = ovRowsArr.length
      ? `<h3 class="rf-h rq-bold">访谈大纲结构</h3>${ovRowsArr.map(flowStep).join("")}`
      : `<h3 class="rf-h rq-bold">访谈大纲结构</h3><div class="empty"><b>还没有结构</b><span>在左边确认「总览表」后，这里会按环节展示甄别 / 访问。</span></div>`;
    return `<h3 class="rf-h rq-bold">研究背景</h3>${briefHtml()}
      <h3 class="rf-h rq-bold">研究目标</h3>${listHtml("rfgoalsl", goals, "研究目标的一条")}
      <h3 class="rf-h rq-bold">招募需求</h3>${totalHead()}${listHtml("rfneedl", needLines(), "招募需求的一条")}
      <h3 class="rf-h rq-bold rf-flow-h">访谈流程</h3>
      <div class="rf-flow-part"><span class="rf-flow-no">1</span><b>开始 · 欢迎消息</b></div>
      <div class="rf-welcome">${esc(f.welcome || "您好，感谢参与。本次文字访谈想了解您对相关话题的看法，没有标准答案，请根据实际经历作答；无需提供身份证件、保单、账户等私人资料。")}</div>
      ${flow}
      ${mode === "html" ? importedCard() : `<h3 class="rf-h rq-bold">问卷流程（共 ${Q.length} 题）<span class="rf-pass-tip"><span class="rq-ic y">✓</span>通过 <span class="rq-ic n">✗</span>筛掉 · 点图标即可切换，逻辑自动写好</span></h3>${Q.length ? Q.map(qCard).join("") : `<div class="empty"><b>还没有题目</b><span>在左边按引导确认总览表后，AI 会逐题生成。</span></div>`}
      <div class="row"><button class="s" type="button" data-add="single">+ 单选</button><button class="s" type="button" data-add="multi">+ 多选</button><button class="s" type="button" data-add="scale">+ 量表</button><button class="s" type="button" data-add="text">+ 文字填空</button><button class="s" type="button" data-add="number">+ 数字填空</button></div>`}`;
  }
  const rightHead = () => {
    const live = f.status === "published";
    if (mode === "html") return `<div class="rf-hd"><span class="pill">已导入</span><span class="sm mut">问卷在客户那边作答，本平台不出题</span><span class="sp"></span><button class="s" type="button" id="rfcol" aria-label="收起大纲">${rfIcon(RF_IC.collapse)}收起</button></div>`;
    // 发布四态（产品文档第 11 章）：①草稿可发布 ②发布中=已锁定(可解锁修改/停止收集) ③已停止可重新发布
    const pill = live ? `<span class="pill">已锁定 · 第 ${f.version || 1} 版</span>` : f.status === "closed" ? `<span class="pill o">已停止</span>` : `<span class="pill o">编辑中 · 未发布</span>`;
    const pubBtn = live ? `<button class="s" type="button" id="rfunlock">解锁修改</button><button class="s" type="button" id="rfclose">停止收集</button>`
      : `<button class="p" type="button" id="rfpub" ${Q.length ? "" : "disabled"}>${f.status === "closed" ? "重新发布" : "发布新版本"}</button>`;
    return `<div class="rf-hd"><div class="rf-seg" role="tablist"><button type="button" role="tab" data-seg="outline" class="${seg === "outline" ? "on" : ""}" aria-selected="${seg === "outline"}">${rfIcon(RF_IC.list)}招募问卷</button><button type="button" role="tab" data-seg="test" class="${seg === "test" ? "on" : ""}" aria-selected="${seg === "test"}">${rfIcon(RF_IC.eye)}测试答题</button></div>
      <span class="sp"></span>${vers && vers.languages.length > 1 || (vers && vers.languages.length) ? verMenu() : ""}<span class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version ? `第 ${f.version} 版` : ""}</span>${pill}
      ${pubBtn}<button class="s" type="button" id="rfcol" aria-label="收起大纲">${rfIcon(RF_IC.collapse)}收起</button></div>`;
  };
  function drawRight() {
    const r = $("#rfright"); if (!r) return;
    const keep = $("#rfout") ? $("#rfout").scrollTop : 0;
    r.innerHTML = rightHead() + `<div class="rf-sc" id="rfout"><div class="rf-col">${seg === "outline" ? (isSource() ? outline() : `<div id="rftr"><div class="mut" style="padding:12px">加载中…</div></div>`) : `<p class="sm mut" style="margin:0">就是候选人看到的样子：直接填写，看会走到哪里。</p><div id="rfrun"></div><div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>`}</div></div>`;
    $("#rfout").scrollTop = keep; wireRight(); wireVer(); syncUndo();
    drawAskPanel();
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
      onEnd: (result) => { res.hidden = false; res.textContent = result === "out" ? "测试结果：这位候选人会被筛掉（不符合），问卷到此结束。" : "测试结果：这位候选人符合，会进入留联系方式的步骤。"; },
    }).start();
  };
  const publish = guard(async () => {
    clearTimeout(timer); if (incomplete()) throw new Error("有题目或选项还没写完，写完再发布");
    f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: body() })) };
    f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/publish`, { method: "POST" })) }; toast("已发布，链接和二维码在“配额与受访者名单”页");
    sessionStorage.setItem("ptab", "list"); render();
  });

  function wireRight() {
    const co = $("#rfcol"); if (co) co.onclick = () => setRight(false);
    document.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { seg = b.dataset.seg; drawRight(); }));
    const pub = $("#rfpub"); if (pub) pub.onclick = publish;
    const un = $("#rfunlock"); if (un) un.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/unlock`, { method: "POST" })) }; drawRight(); toast("已解锁，进入编辑中；受访者仍看旧版，改完点「发布新版本」才生效"); });
    const cl = $("#rfclose"); if (cl) cl.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/close`, { method: "POST" })) }; drawRight(); toast("已停止收集"); });
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
    document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => { editing.add(b.dataset.edit); drawRight(); }));
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
    // 题旁小对话：点 💬 展开这题的小对话（右侧弹出面板）；收起、发送由 drawAskPanel 里绑定
    document.querySelectorAll("[data-ask]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); stopAskRec(); askOpen = askOpen === b.dataset.ask ? null : b.dataset.ask; askFail = ""; askImgs = []; drawRight(); }));
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
    return `<div class="rf-main"><div class="rf-ws${rightOpen ? "" : " rf-collapsed"}${mode === "html" ? " rf-import" : ""}" id="rfws"><div class="rf-pane"><div class="rf-hd"><span class="sp"></span><button class="s" type="button" id="rfundo" disabled title="撤销上一次对话带来的改动">${rfIcon(RF_IC.undo)}撤销</button><button class="s" type="button" id="rfredo" disabled title="除研究背景与目标外，清空问卷、需求、总览表与对话，全部重做">${rfIcon(RF_IC.redo)}重做</button><button class="s" type="button" id="rfexp">${rfIcon(RF_IC.expand)}展开大纲</button></div>
      <div class="rf-sc" id="rfchat"></div>
      <div id="rfsel" hidden></div><div class="rq-bubs" id="rfbub"></div><div class="rf-inp"><div class="rq-files" id="rfarow" hidden></div><div class="rf-box"><button class="rq-clip" type="button" id="rfclip" aria-label="附件" title="附件">${RF_CLIP}</button><input type="file" id="rffile" hidden accept=".pdf,.docx,.txt,.md,.csv,image/*,video/*"><textarea id="rfmsg" rows="2" maxlength="2000" placeholder="可上传资料、粘贴网页链接，或说“帮我搜索…”，辅助创建大纲" aria-label="对话输入"></textarea><button class="p rf-send" type="button" id="rfsend" aria-label="发送">${rfIcon(RF_IC.send)}</button></div><div class="rq-ferr" id="rfferr" role="alert" hidden></div></div></div>
      <div class="rf-split" id="rfsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="rfright"></div></div><div class="rf-askpanel" id="rfaskpanel" hidden></div></div>`;
  }
  let picking = rm === "onelink" ? false : !(f.source_confirmed || ""), pickMode = f.source === "html" ? "html" : "ai";   // 一站式来源锁定为 AI，不弹选择；其余只在还没选过来源时问一次
  const goOutline = () => { location.hash = `#/p/${p.id}/outline`; };
  const pickHtml = () => !picking ? "" : `<div class="rq-pick-back" id="rfpickback"><div class="rq-pick" role="dialog" aria-modal="true" aria-labelledby="rfpickt">
      <h3 id="rfpickt">这份招募问卷怎么来？</h3>
      <label><input type="radio" name="rfpick" value="ai" ${pickMode === "ai" ? "checked" : ""}><span><b>AI 生成招募问卷</b><span class="sm mut">在左边说清条件，AI 出题、你改逻辑，发布成链接和二维码</span></span></label>
      <label><input type="radio" name="rfpick" value="html" ${pickMode === "html" ? "checked" : ""}><span><b>导入招募问卷</b><span class="sm mut">客户已在 Confirmit 编好，用链接导入；本平台不出题，数据由 PM 导入</span></span></label>
      <div class="rq-pick-btns"><button class="s" type="button" id="rfpickno">取消</button><button class="p" type="button" id="rfpickok">确定</button></div></div></div>`;
  const draw = () => { $("#rfbody").innerHTML = panels() + pickHtml(); wire(); };
  const fit = () => { const w = $("#rfws"); if (w && w.offsetParent) { const h = Math.max(380, innerHeight - w.getBoundingClientRect().top - 18); w.style.height = h + "px"; const m = w.parentElement; if (m && m.classList.contains("rf-main")) m.style.height = h + "px"; } };

  function wire() {
    document.querySelectorAll("input[name=rfpick]").forEach((r) => (r.onchange = () => { pickMode = r.value; }));
    const pok = $("#rfpickok"); if (pok) pok.onclick = guard(async () => {
      const s = pickMode;
      if (!f.source_confirmed || f.source_confirmed !== s) {   // 记一次，之后从任何入口进来都不再问
        f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/source`, { method: "POST", body: { source: s } })) };
      }
      mode = s; picking = false; draw();
    });
    const pno = $("#rfpickno"); if (pno) pno.onclick = () => { picking = false; draw(); };   // 取消 = 没选过，下次进来还会问
    if (sel) { sel.destroy(); sel = null; }
    drawChat(); drawRight(); fit(); wireSplit();   // 两栏先画出来，两种来源都要画（导入态也有左引导 + 右大纲）
    // 「更换来源」按钮已移除（来源由「招募方式」页统一决定）；导入态的来源切换也一并去掉
    // 导入态右侧链接卡上的三个按钮属于 drawRight 画出来的元素，必须画完再绑
    const sv = $("#rfsave"); if (sv) sv.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "html", html_url: $("#rfurl").value.trim() } })) }; htmlLocked = true; draw(); toast("链接已保存，已锁定"); });
    const op = $("#rfopen"); if (op) op.onclick = () => window.open(f.html_url, "_blank", "noopener,noreferrer");
    const ed = $("#rfedit"); if (ed) ed.onclick = () => { htmlLocked = false; draw(); const u = $("#rfurl"); if (u) u.focus(); };
    if (mode !== "ai") return;   // 导入模式没有题目：不装版本、选句、题目菜单这些
    loadVers(true); wireSel();
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
    const clip = $("#rfclip"), fin = $("#rffile");
    if (clip && fin) { clip.onclick = () => fin.click(); fin.onchange = () => { const f = fin.files[0]; fin.value = ""; if (f) attach(f); }; }
    drawFiles();
    const m = $("#rfmsg");
    $("#rfsend").onclick = () => send(m.value);
    m.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(m.value); } };
    // 撤销 = 撤销上一次对话：回退到最近一次改动（对话 / 手动编辑）之前的状态
    $("#rfundo").onclick = () => { const s = hist.pop(); if (!s) return; redo.push(snap()); apply(s); syncUndo(); changed(); };
    $("#rfexp").onclick = () => setRight(true);
    // 重做 = 全部重做：除研究背景与目标外，清空问卷题目、招募需求、总览表与全部对话，一键重新来
    $("#rfredo").onclick = guard(async () => {
      if (!confirm("将清空问卷题目、招募需求、总览表与全部对话（保留研究背景和研究目标），确定全部重做吗？")) return;
      Q = []; goals = []; need = ""; ovRows = null; hist = []; redo = []; editing = new Set(); changedIds = new Set(); fail = null;
      await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "ai", idea: "", goals: [], need: "", questions: [] } });
      f = { ...f, questions: [], goals: [], need: "", chat: [], stage: 2 };
      syncUndo(); drawChat(); drawRight(); toast("已清空，仅保留研究背景与研究目标，可重新开始");
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
  const html = `<div class="rf" style="padding:0"><div id="rfbody">${panels()}${pickHtml()}</div></div>`;
  return { html, wire, fit };
}
