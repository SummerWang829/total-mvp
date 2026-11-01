/* 甄别设定页的“招募问卷”工作台：左边和 AI 对话（说一句话，问卷就跟着改），右边是实时大纲（研究背景、研究目标、招募需求、问卷流程），
   左右两个框各自滚动，整页不滚。大纲里每道题也能直接点开手动改（题型、选项、跳转、标签）。切到“测试答题”就是候选人看到的样子；
   写好发布成链接和二维码，候选人在 screen.html 作答，合格的人自动进受访者名单。也可以改成导入在别的系统里做好的 HTML 问卷链接。
   依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）和 screen_run.js。 */
const RF_TYPE = { single: "单选", multi: "多选", text: "文字填空", number: "数字填空" };
const RF_OP = { lt: "小于", lte: "不大于", gt: "大于", gte: "不小于", eq: "等于" };
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
function rfCard(p, form) {
  let f = form, mode = form.source === "html" ? "html" : "ai", seg = "outline", Q = JSON.parse(JSON.stringify(form.questions || [])), goals = [...(form.goals || [])], need = form.need || "";
  let timer = null, sending = false, pending = "", hist = [], redo = [], editing = new Set();
  let ver = "", vers = null;   // the language version being looked at (""/the source = the original) and the versions' states (app/static/rtrans.js)
  const isSource = () => !ver || (vers && ver === vers.source);
  let showSug = false, changedIds = new Set();   // showSug: the suggestion cards are open in the chat; changedIds: the questions the last change touched ("刚改")
  let useLib = true, fail = null;   // useLib: the "参考研究库" tick; fail: the last turn that did not get an answer { ask, text, fatal } (kept here only, never saved)
  const RF_KIND = { answer: ["回答", "ans"], suggest: ["建议", "sug"], stat: ["统计", "stat"] };
  const aiOk = () => !f.ai || f.ai.ok;
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
  const ctxHtml = () => {
    const a = f.ai || { ok: true, name: "" }, docs = f.library_docs || 0, started = (f.stats && f.stats.started) || 0;
    const cx = [f.brief && f.brief.trim() ? "✓ 研究背景" : "· 研究背景（未填写）", `${useLib && docs ? "✓" : "·"} 研究库 ${docs} 份`, ...(Q.length ? [`✓ 当前问卷（${Q.length} 题）`] : []), ...(started ? [`✓ 作答统计（${started} 人）`] : [])];
    return `<div class="rf-ctxh"><b>与 AI 共同编写问卷</b>${a.ok ? "" : `<span class="rf-model bad">不可用</span>`}<span class="sp"></span><label class="chk"><input type="checkbox" id="rflib" ${useLib ? "checked" : ""}>参考研究库</label></div>
      ${a.ok ? "" : `<div class="rf-bad" role="alert">${esc(a.reason)}</div>`}<div class="rf-cx">${cx.map((x) => `<span>${esc(x)}</span>`).join("")}</div>`;
  };
  const sugCard = (s) => `<div class="rf-sg"><span class="rf-sgk">${esc(s.label)}${s.qno ? ` · 第 ${s.qno} 题` : ""}</span><div>${esc(s.text)}</div>${chipsOf(s.sources)}
    <div class="rf-sgb"><button class="p s" type="button" data-adopt="${esc(s.sig)}">${esc(s.button || "采纳")}</button><a class="rf-nomore" href="#" data-nomore="${esc(s.sig)}">不再提醒</a></div></div>`;
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
  const syncUndo = () => { const u = $("#rfundo"), r = $("#rfredo"); if (u) u.disabled = !hist.length; if (r) r.disabled = !redo.length; };
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
  function chatHtml() {
    const hello = `<div class="rf-msg">您好！我可以根据研究背景和研究库资料协助设计招募问卷，也可以按您的要求修改，例如“年龄放最前面，18 岁以下筛掉”“每天都用的人跳过频率题”“把第 3 题改成多选”。可先说“帮我起草问卷”。</div>`;
    const all = f.chat || [], msgs = all.map((m, i) => msgHtml(m, i === all.length - 1 && !fail, i)).join("");
    const failHtml = fail ? `<div class="rf-msg rf-me">${esc(fail.ask)}</div><div class="rf-err" role="alert"><b>本次未能完成回复</b><div>${esc(fail.text)}</div>${fail.fatal ? "" : `<button class="s" type="button" data-chip="retry">重新尝试</button><button class="s" type="button" data-chip="rephrase">调整提问方式</button>`}</div>` : "";
    const cards = showSug && sugs().length ? `<div class="rf-sgs"><div class="sm mut">建议 ${sugs().length} 条。</div>${sugs().map(sugCard).join("")}</div>` : "";
    const news = !showSug && sugs().length && sugSeen() !== sugs().map((s) => s.sig).join(",") ? `<button type="button" class="rf-news" data-chip="showsug">有 ${sugs().length} 条建议，点这里查看</button>` : "";
    const started = (f.stats && f.stats.started) || 0, why = aiOk() ? "" : ` disabled title="${esc(f.ai.reason)}"`;   // the buttons that need the model are greyed out, with the reason, when it cannot be used
    const need = (c, label) => `<button class="s" type="button" data-chip="${c}"${why}>${label}</button>`, free = (c, label) => `<button class="s" type="button" data-chip="${c}">${label}</button>`;
    const row = !Q.length ? [need("draft", "帮我起草问卷")] : !started ? [free("review", "检查问卷"), need("goals", "对照研究目标"), need("neutral", "优化措辞的中立性"), free("test", "测试这份问卷")]
      : [free("review", "检查问卷"), free("rate", "预估通过率"), free("worst", "定位筛除率较高的题目"), need("neutral", "优化措辞的中立性")];
    const chips = `<div class="rf-chips">${row.join("")}</div>`;
    return hello + msgs + cards + failHtml + (pending ? `<div class="rf-msg rf-me">${esc(pending)}</div><div class="rf-msg mut">AI 正在处理…</div>` : news + chips);
  }
  const drawChat = () => { const c = $("#rfchat"); if (!c) return; const x = $("#rfctx"); if (x) { x.innerHTML = ctxHtml(); const l = $("#rflib"); if (l) l.onchange = () => { useLib = l.checked; drawChat(); }; } c.innerHTML = chatHtml(); c.scrollTop = c.scrollHeight; wireChips(); };
  async function send(text) {
    text = (text || "").trim(); if (!text || sending) return;
    if (incomplete()) return toast("有题目或选项还没写完，先写完再对话", true);
    sending = true; pending = text; fail = null; $("#rfmsg").value = ""; $("#rfsend").disabled = true; drawChat();
    const before = snap();
    try {
      const r = await api(`/projects/${p.id}/recruiting/form/chat`, { method: "POST", body: { message: text, questions: Q, goals, need, use_library: useLib } });
      if (r.changed || JSON.stringify(r.goals) !== JSON.stringify(goals) || r.need !== need) { hist.push(before); if (hist.length > 30) hist.shift(); redo = []; }
      changedIds = r.changed ? diffIds(Q, r.questions) : new Set();
      f = { ...f, ...r }; Q = JSON.parse(JSON.stringify(r.questions)); goals = [...(r.goals || [])]; need = r.need || ""; editing = new Set();
    } catch (e) { fail = { ask: text, text: e.message, fatal: e.status === 503 }; if (e.status === 503) f = { ...f, ai: { ...(f.ai || {}), ok: false, reason: e.message } }; }
    sending = false; pending = ""; const b = $("#rfsend"); if (b) b.disabled = false; syncUndo(); drawChat(); drawRight();
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
    document.querySelectorAll("[data-next]").forEach((b) => (b.onclick = () => { const last = [...(f.chat || [])].reverse().find((m) => m.next && m.next.length); if (last) send(last.next[+b.dataset.next].ask); }));
    document.querySelectorAll("[data-adopt]").forEach((b) => (b.onclick = () => adopt(b.dataset.adopt)));
    document.querySelectorAll("[data-nomore]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); nomore(a.dataset.nomore); }));
  }

  /* ---------- right: the outline / the test ---------- */
  function qEditor(q, i) {
    const opts = q.type === "single" || q.type === "multi";
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
  function qCard(q, i) {
    if (editing.has(q.id)) return `<div class="rf-q" data-qid="${q.id}">${qEditor(q, i)}</div>`;
    const lines = q.type === "single" || q.type === "multi"
      ? q.options.map((o, j) => `<div class="rf-o"><span class="rf-code">${j + 1}</span>${q.type === "single" ? "○" : "☐"} ${esc(o.text || "（空选项）")} ${badge(o.goto)}${o.tag ? `<span class="rf-lg jump">标签：${esc(o.tag)}</span>` : ""}${(o.excl || []).length ? `<span class="rf-lg exb">互斥：${exLabel(q, o)}</span>` : ""}</div>`).join("")
      : q.type === "number" ? (q.rules || []).map((r) => `<div class="rf-o">如果数字${RF_OP[r.op]} ${r.value} ${badge(r.goto)}</div>`).join("") : "";
    return `<div class="rf-q${changedIds.has(q.id) ? " rf-hl" : ""}" data-qid="${q.id}" data-selk="q:${q.id}"><div class="row"><span class="rf-n">${i + 1}</span><b>${esc(q.text || "（还没写题目）")}</b>${qBadge(q)}<span class="sp"></span><span class="tag nw">${RF_TYPE[q.type]}${q.required ? " · 必答" : ""}</span><button class="s" type="button" data-edit="${q.id}">编辑</button></div>${lines}${exWarn(q)}</div>`;
  }
  function outline() {
    return `<h3 class="rf-h">研究背景</h3><div class="rf-blk">${f.brief && f.brief.trim() ? `${esc(f.brief)} <span class="mut sm">（来自立项向导）</span>` : '<span class="mut">还没有研究背景。到立项向导第 1 步写，或在左边对话里告诉 AI。</span>'}</div>
      <h3 class="rf-h">研究目标</h3><textarea id="rfgoals" class="rf-blk" rows="3" placeholder="每行一条。可以让 AI 根据研究背景整理，也可以自己写。" aria-label="研究目标">${esc(goals.join("\n"))}</textarea>
      <h3 class="rf-h">招募需求</h3><textarea id="rfneed" class="rf-blk" rows="2" placeholder="谁、多少人、哪些情况要筛掉。可以让 AI 整理，也可以自己写。" aria-label="招募需求">${esc(need)}</textarea>
      <h3 class="rf-h">问卷流程（共 ${Q.length} 题）</h3>${Q.length ? Q.map(qCard).join("") : `<div class="empty"><b>还没有题目</b><span>在左边说“帮我起草问卷”，或点下面的按钮自己加。</span></div>`}
      <div class="row"><button class="s" type="button" data-add="single">+ 单选</button><button class="s" type="button" data-add="multi">+ 多选</button><button class="s" type="button" data-add="text">+ 文字填空</button><button class="s" type="button" data-add="number">+ 数字填空</button></div>`;
  }
  const rightHead = () => {
    const live = f.status === "published";
    return `<div class="rf-hd"><div class="rf-seg" role="tablist"><button type="button" role="tab" data-seg="outline" class="${seg === "outline" ? "on" : ""}" aria-selected="${seg === "outline"}">大纲</button><button type="button" role="tab" data-seg="test" class="${seg === "test" ? "on" : ""}" aria-selected="${seg === "test"}">测试答题</button></div>
      <span class="sp"></span>${vers && vers.languages.length > 1 || (vers && vers.languages.length) ? verMenu() : ""}<span class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version ? `第 ${f.version} 版` : ""}</span><span class="pill ${live ? "" : "o"}">${live ? "发布中" : f.status === "closed" ? "已停止" : "草稿"}</span>
      ${live ? `<button class="s" type="button" id="rfclose">停止收集</button>` : `<button class="p" type="button" id="rfpub" ${Q.length ? "" : "disabled"}>${f.status === "closed" ? "重新发布" : "发布成链接和二维码"}</button>`}<button class="s" type="button" id="rfcol" aria-label="收起大纲">收起</button></div>`;
  };
  function drawRight() {
    const r = $("#rfright"); if (!r) return;
    const keep = $("#rfout") ? $("#rfout").scrollTop : 0;
    r.innerHTML = rightHead() + `<div class="rf-sc" id="rfout"><div class="rf-col">${seg === "outline" ? (isSource() ? outline() : `<div id="rftr"><div class="mut" style="padding:12px">加载中…</div></div>`) : `<p class="sm mut" style="margin:0">就是候选人看到的样子：直接填写，看会走到哪里。</p><div id="rfrun"></div><div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>`}</div></div>`;
    $("#rfout").scrollTop = keep; wireRight(); wireVer(); syncUndo();
  }
  const refreshVers = async () => {   // after the original is saved, other versions may have gone stale: update the menu (the banner shows when you open that version)
    const v = await rtVersions(p); if (!v) return; vers = v; const m = $("#rfvm"), b = $("#rfvb");
    if (m) m.innerHTML = rtMenuHtml(vers, ver || vers.source); if (b) { b.firstChild.textContent = "版本：" + verLabel(); wireVer(); }
  };
  function verLabel() { const x = vers && vers.languages.find((l) => l.code === (ver || vers.source)); return x ? x.name + (x.source ? "（原文）" : "") : "—"; }
  function verMenu() {   // 版本: one dropdown for the outline and the test; project languages carry no tag, team-only ones say 仅团队
    const cur = ver || vers.source, x = vers.languages.find((l) => l.code === cur) || {};
    return `<div class="rf-ver"><button type="button" id="rfvb" aria-haspopup="menu" aria-expanded="false" title="切换语言版本">版本：${esc(verLabel())}${x.team_only ? " · 仅团队" : ""} ▾</button><div class="rf-vm" id="rfvm" role="menu" hidden>${rtMenuHtml(vers, cur)}</div></div>`;
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
    if (!Q.length) { host.innerHTML = `<div class="empty"><b>还没有题目</b><span>先在左边让 AI 起草，或到“大纲”里自己添加。</span></div>`; return; }
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
    const cl = $("#rfclose"); if (cl) cl.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/close`, { method: "POST" })) }; drawRight(); toast("已停止收集"); });
    if (seg === "test") return retest();
    if (seg === "outline" && !isSource()) return drawTrans();
    [$("#rfgoals"), $("#rfneed")].forEach((box) => { if (box) rfAutosize(box); });
    document.querySelectorAll("[data-exall]").forEach((b) => (b.onclick = () => {   // link every pair of tagged options of this question as mutually exclusive
      const q = Q.find((x) => x.id === b.dataset.exall); if (!q) return; pushHist();
      taggedPairs(q).forEach(([a, c]) => setExcl(q, a, c, true)); changed();
    }));
    const g = $("#rfgoals"); if (g) g.onchange = () => { pushHist(); goals = g.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 8); save(); };
    const n = $("#rfneed"); if (n) n.onchange = () => { pushHist(); need = n.value.trim(); save(); };
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
      pushHist(); const type = b.dataset.add, q = { id: nextId(), type, text: "", required: true, options: type === "single" || type === "multi" ? blank() : [], rules: [] };
      Q.push(q); editing.add(q.id); changed();
    }));
    document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => { editing.add(b.dataset.edit); drawRight(); }));
    document.querySelectorAll(".rf-q .rf-edit").forEach((card) => {
      const i = +card.dataset.qi, q = Q[i];
      card.querySelectorAll("[data-f]").forEach((el) => (el.onchange = () => {
        const k = el.dataset.f; pushHist();
        if (k === "type") { q.type = el.value; if ((q.type === "single" || q.type === "multi") && q.options.length < 2) q.options = blank(); if (q.type !== "number") q.rules = []; if (q.type !== "multi") q.options.forEach((o) => { o.excl = []; }); return changed(); }
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
    if (mode === "html") return `<div class="rf-htmlmode"><div>${modeSwitch()}</div><label class="field">问卷链接（https://）<input id="rfurl" type="url" value="${esc(f.html_url)}" placeholder="https://…（在别的系统里做好的问卷）" maxlength="500"></label>
      <div class="note">问卷在别的系统里制作和托管，这里只保存链接，点下面的按钮在新窗口里测试；不会嵌进本页面。</div><div class="row"><button class="p" id="rfsave" type="button">保存链接</button>${f.html_url ? `<button class="s" id="rftest" type="button">打开测试窗口</button>` : ""}</div>${f.html_url ? `<code class="rfurl">${esc(f.html_url)}</code>` : ""}</div>`;
    return `<div class="rf-ws${rightOpen ? "" : " rf-collapsed"}" id="rfws"><div class="rf-pane"><div class="rf-hd">${modeSwitch()}<span class="sp"></span><button class="s" type="button" id="rfundo" disabled>撤销</button><button class="s" type="button" id="rfredo" disabled>重做</button><button class="s" type="button" id="rfexp">展开大纲</button></div>
      <div class="rf-ctx" id="rfctx"></div><div class="rf-sc" id="rfchat"></div>
      <div id="rfsel" hidden></div><div class="rf-inp"><div class="rf-box"><textarea id="rfmsg" rows="2" maxlength="2000" placeholder="请输入修改要求或问题，例如“帮我起草问卷”" aria-label="对话输入"></textarea><button class="p rf-send" type="button" id="rfsend" aria-label="发送">↑</button></div></div></div>
      <div class="rf-split" id="rfsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="rfright"></div></div>`;
  }
  const modeSwitch = () => `<div class="rf-seg" role="radiogroup" aria-label="问卷来源"><button type="button" role="radio" data-mode="ai" class="${mode === "ai" ? "on" : ""}" aria-checked="${mode === "ai"}">对话编写</button><button type="button" role="radio" data-mode="html" class="${mode === "html" ? "on" : ""}" aria-checked="${mode === "html"}">导入问卷链接</button></div>`;
  const draw = () => { $("#rfbody").innerHTML = panels(); wire(); };
  const fit = () => { const w = $("#rfws"); if (w && w.offsetParent) w.style.height = Math.max(380, innerHeight - w.getBoundingClientRect().top - 18) + "px"; };

  function wire() {
    document.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; draw(); }));
    const sv = $("#rfsave"); if (sv) sv.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "html", html_url: $("#rfurl").value.trim() } })) }; draw(); toast("链接已保存"); });
    const tt = $("#rftest"); if (tt) tt.onclick = () => window.open(f.html_url, "_blank", "noopener,noreferrer");
    if (sel) { sel.destroy(); sel = null; }
    if (mode !== "ai") return;
    drawChat(); drawRight(); fit(); wireSplit(); loadVers(true); wireSel();
    if (window._rfVerOff) document.removeEventListener("click", window._rfVerOff);
    window._rfVerOff = () => { const m = $("#rfvm"); if (m && !m.hidden) { m.hidden = true; const b = $("#rfvb"); if (b) b.setAttribute("aria-expanded", "false"); } };
    document.addEventListener("click", window._rfVerOff);
    if (window._rfExOff) document.removeEventListener("click", window._rfExOff);
    window._rfExOff = (e) => { if (openEx.size && !e.target.closest(".rf-expanel, [data-ex]")) { openEx.clear(); drawRight(); } };   // the 互斥 picker closes when you click anywhere else
    document.addEventListener("click", window._rfExOff);
    const m = $("#rfmsg");
    $("#rfsend").onclick = () => send(m.value);
    m.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(m.value); } };
    $("#rfundo").onclick = () => { const s = hist.pop(); if (!s) return; redo.push(snap()); apply(s); syncUndo(); changed(); };
    $("#rfexp").onclick = () => setRight(true);
    $("#rfredo").onclick = () => { const s = redo.pop(); if (!s) return; hist.push(snap()); apply(s); syncUndo(); changed(); };
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
