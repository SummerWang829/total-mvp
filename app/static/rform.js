/* 甄别设定页的“招募问卷”工作台：左边和 AI 对话（说一句话，问卷就跟着改），右边是实时大纲（研究背景、研究目标、招募需求、问卷流程），
   左右两个框各自滚动，整页不滚。大纲里每道题也能直接点开手动改（题型、选项、跳转、标签）。切到“测试答题”就是候选人看到的样子；
   写好发布成链接和二维码，候选人在 screen.html 作答，合格的人自动进受访者名单。也可以改成导入在别的系统里做好的 HTML 问卷链接。
   依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）和 screen_run.js。 */
const RF_TYPE = { single: "单选", multi: "多选", text: "文字填空", number: "数字填空" };
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
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
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
function rfCard(p, form) {
  let f = form, mode = form.source === "html" ? "html" : "ai", seg = "outline", Q = JSON.parse(JSON.stringify(form.questions || [])), goals = [...(form.goals || [])], need = form.need || "";
  let timer = null, hist = [], redo = [], editing = new Set();
  let ver = "", vers = null;   // the language version being looked at (""/the source = the original) and the versions' states (app/static/rtrans.js)
  const isSource = () => !ver || (vers && ver === vers.source);
  let changedIds = new Set();   // the questions the last change of the dialog touched ("刚改")
  let htmlLocked = !!form.html_url;   // the saved link is read-only until 编辑 is pressed (2026-11-03)
  let menu = null;   // the open ⋮ menu of a question ("q:<id>") or an option ("o:<id>:<number>"); one at a time
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
  const syncUndo = () => { const u = $("#rfundo"), r = $("#rfredo"); if (u) u.disabled = !hist.length; if (r) r.disabled = !redo.length; };
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
    changedIds = diffIds(before, Q); drawRight();
  });
  const drawLeft = () => {
    const c = $("#ccroot"); if (!c) return;
    if (mode === "html") { c.innerHTML = `<div class="cc-sc">${importHtml()}</div>`; const g = $("#rfgooutline"); if (g) g.onclick = goOutline; }
    else CreationChat.mount(c, p, { onChange: reload });
  };

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
  const optMark = (q, o) => (o.goto === "end_ok" ? `<span class="rq-ic y" title="结束 · 符合" aria-label="结束 · 符合">✓</span>`
    : o.goto === "end_out" ? `<span class="rq-ic n" title="结束 · 不符合" aria-label="结束 · 不符合">✗</span>` : `<span class="rq-ic">${q.type === "single" ? "○" : "☐"}</span>`);
  const jumpBadge = (g) => (g && !g.startsWith("end_") ? badge(g) : "");   // the two endings show as ✓ / ✗; only a jump to a question needs its own label
  function qCard(q, i) {
    if (editing.has(q.id)) return `<div class="rf-q" data-qid="${q.id}" data-qi="${i}">${qEditor(q, i)}</div>`;
    const qk = "q:" + q.id, choice = q.type === "single" || q.type === "multi";
    const opts = choice ? q.options.map((o, j) => {
      const ok = `o:${q.id}:${j}`;
      return `<div class="rq-opt">${optMark(q, o)}<span>${esc(o.text || "（空选项）")}</span>${jumpBadge(o.goto)}${o.tag ? `<span class="rf-lg jump">标签：${esc(o.tag)}</span>` : ""}${(o.excl || []).length ? `<span class="rf-lg exb">互斥：${exLabel(q, o)}</span>` : ""}<span class="sp"></span>
        <button class="rq-more" type="button" data-menu="${ok}" aria-label="选项 ${j + 1} 菜单" aria-expanded="${menu === ok}">${rfIcon(RF_IC.dots)}</button>${menu === ok ? oMenu(q, i, j) : ""}</div>`;
    }).join("") + `<button class="rq-add" type="button" data-oadd-view="${q.id}">＋ 添加选项</button>` : "";
    const rules = q.type === "number" ? (q.rules || []).map((r) => `<div class="rq-opt">如果数字${RF_OP[r.op]} ${r.value} ${badge(r.goto)}</div>`).join("") : "";
    return `<div class="rf-q${changedIds.has(q.id) ? " rf-hl" : ""}" data-qid="${q.id}" data-qi="${i}" data-selk="q:${q.id}">
      <span class="rq-grip" data-grip="${i}" tabindex="0" role="button" title="按住拖动排序，或用上下方向键移动" aria-label="拖动第 ${i + 1} 题">${rfIcon(RF_IC.grip)}</span>
      <div class="rq-top"><span class="rq-qn">Q${i + 1}</span><button class="rq-title" type="button" data-edit="${q.id}">${esc(q.text || "（还没写题目）")}</button>${qBadge(q)}<span class="sp"></span>
        <span class="rq-type">${RF_TYPE[q.type]}${q.required ? " · 必答" : ""}</span>
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
    return `<h3 class="rf-h rq-bold">已导入的问卷</h3>
      <div class="rf-impcard">
        <label class="field"><span>问卷链接（https://）</span><input id="rfurl" type="url" value="${esc(f.html_url)}" placeholder="https://…（在别的系统里做好的问卷）" maxlength="500" ${htmlLocked ? "readonly" : ""}></label>
        <div class="row" style="margin-top:8px">${htmlLocked ? `<span class="tag ok">已锁定</span><button class="s" id="rfedit" type="button">编辑</button>` : `<button class="p" id="rfsave" type="button">保存并锁定</button>`}<button class="s" id="rfopen" type="button" ${f.html_url ? "" : "disabled"}>${rfIcon(RF_IC.ext)}新窗口打开</button></div>
        <div class="sm mut" style="margin-top:8px;line-height:1.7">题目在客户那边作答（Confirmit）。数据回来后，在「配额与受访者名单 → 导入甄别数据」里按 Data Map 批量导入。</div>
      </div>
      <div class="row" style="margin-top:12px"><button class="s" type="button" id="rfgooutline2">生成访谈大纲 →</button><span class="sm mut">按这份问卷的筛选字段分段追问</span></div>`;
  }
  function outline() {
    return `<h3 class="rf-h rq-bold">研究背景</h3>${briefHtml()}
      <h3 class="rf-h rq-bold">研究目标</h3>${listHtml("rfgoalsl", goals, "研究目标的一条")}
      <h3 class="rf-h rq-bold">招募需求</h3>${listHtml("rfneedl", needLines(), "招募需求的一条")}
      ${mode === "html" ? importedCard() : `<h3 class="rf-h rq-bold">问卷流程（共 ${Q.length} 题）</h3>${Q.length ? Q.map(qCard).join("") : `<div class="empty"><b>还没有题目</b><span>在左边说“帮我起草问卷”，或点下面的按钮自己加。</span></div>`}
      <div class="row"><button class="s" type="button" data-add="single">+ 单选</button><button class="s" type="button" data-add="multi">+ 多选</button><button class="s" type="button" data-add="text">+ 文字填空</button><button class="s" type="button" data-add="number">+ 数字填空</button></div>`}`;
  }
  const rightHead = () => {
    const live = f.status === "published";
    if (mode === "html") return `<div class="rf-hd"><span class="pill">已导入</span><span class="sm mut">问卷在客户那边作答，本平台不出题</span><span class="sp"></span><button class="s" type="button" id="rfswitch2">更换来源</button><button class="s" type="button" id="rfcol" aria-label="收起大纲">${rfIcon(RF_IC.collapse)}收起</button></div>`;
    return `<div class="rf-hd"><div class="rf-seg" role="tablist"><button type="button" role="tab" data-seg="outline" class="${seg === "outline" ? "on" : ""}" aria-selected="${seg === "outline"}">${rfIcon(RF_IC.list)}招募问卷</button><button type="button" role="tab" data-seg="test" class="${seg === "test" ? "on" : ""}" aria-selected="${seg === "test"}">${rfIcon(RF_IC.eye)}测试答题</button></div>
      <span class="sp"></span>${vers && vers.languages.length > 1 || (vers && vers.languages.length) ? verMenu() : ""}<span class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version ? `第 ${f.version} 版` : ""}</span><span class="pill ${live ? "" : "o"}">${live ? "发布中" : f.status === "closed" ? "已停止" : "草稿"}</span>
      ${live ? `<button class="s" type="button" id="rfclose">停止收集</button>` : `<button class="p" type="button" id="rfpub" ${Q.length ? "" : "disabled"}>${f.status === "closed" ? "重新发布" : "发布成链接和二维码"}</button>`}<button class="s" type="button" id="rfcol" aria-label="收起大纲">${rfIcon(RF_IC.collapse)}收起</button></div>`;
  };
  function drawRight() {
    const r = $("#rfright"); if (!r) return;
    const keep = $("#rfout") ? $("#rfout").scrollTop : 0;
    r.innerHTML = rightHead() + `<div class="rf-sc" id="rfout"><div class="rf-col">${seg === "outline" ? (isSource() ? outline() : `<div id="rftr"><div class="mut" style="padding:12px">加载中…</div></div>`) : `<p class="sm mut" style="margin:0">就是候选人看到的样子：直接填写，看会走到哪里。</p><div id="rfrun"></div><div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>`}</div></div>`;
    $("#rfout").scrollTop = keep; wireRight(); wireVer(); syncUndo();
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
    const cl = $("#rfclose"); if (cl) cl.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/close`, { method: "POST" })) }; drawRight(); toast("已停止收集"); });
    if (seg === "test") return retest();
    if (seg === "outline" && !isSource()) return drawTrans();
    document.querySelectorAll("[data-exall]").forEach((b) => (b.onclick = () => {   // link every pair of tagged options of this question as mutually exclusive
      const q = Q.find((x) => x.id === b.dataset.exall); if (!q) return; pushHist();
      taggedPairs(q).forEach(([a, c]) => setExcl(q, a, c, true)); changed();
    }));
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
      pushHist(); const type = b.dataset.add, q = { id: nextId(), type, text: "", required: true, options: type === "single" || type === "multi" ? blank() : [], rules: [] };
      Q.push(q); editing.add(q.id); changed();
    }));
    document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => { editing.add(b.dataset.edit); drawRight(); }));
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
    return `<div class="rf-ws${rightOpen ? "" : " rf-collapsed"}${mode === "html" ? " rf-import" : ""}" id="rfws"><div class="rf-pane"><div class="rf-hd">${mode === "html" ? "" : `<button class="s" type="button" id="rfswitch">更换来源</button>`}<span class="sp"></span><button class="s" type="button" id="rfundo" disabled>${rfIcon(RF_IC.undo)}撤销</button><button class="s" type="button" id="rfredo" disabled>${rfIcon(RF_IC.redo)}重做</button><button class="s" type="button" id="rfexp">${rfIcon(RF_IC.expand)}展开大纲</button></div>
      <div class="cc" id="ccroot"></div></div>
      <div class="rf-split" id="rfsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="rfright"></div></div>`;
  }
  let picking = !(f.source_confirmed || ""), pickMode = f.source === "html" ? "html" : "ai";   // 只在还没选过来源时问一次（选过就记在 source_confirmed 里，从任何入口进来都不再弹）
  const goOutline = () => { location.hash = `#/p/${p.id}/outline`; };
  const pickHtml = () => !picking ? "" : `<div class="rq-pick-back" id="rfpickback"><div class="rq-pick" role="dialog" aria-modal="true" aria-labelledby="rfpickt">
      <h3 id="rfpickt">这份招募问卷怎么来？</h3>
      <label><input type="radio" name="rfpick" value="ai" ${pickMode === "ai" ? "checked" : ""}><span><b>AI 生成招募问卷</b><span class="sm mut">在左边说清条件，AI 出题、你改逻辑，发布成链接和二维码</span></span></label>
      <label><input type="radio" name="rfpick" value="html" ${pickMode === "html" ? "checked" : ""}><span><b>导入招募问卷</b><span class="sm mut">客户已在 Confirmit 编好，用链接导入；本平台不出题，数据由 PM 导入</span></span></label>
      <div class="rq-pick-btns"><button class="s" type="button" id="rfpickno">取消</button><button class="p" type="button" id="rfpickok">确定</button></div></div></div>`;
  const draw = () => { $("#rfbody").innerHTML = panels() + pickHtml(); wire(); };
  const fit = () => { const w = $("#rfws"); if (w && w.offsetParent) w.style.height = Math.max(380, innerHeight - w.getBoundingClientRect().top - 18) + "px"; };

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
    drawLeft(); drawRight(); fit(); wireSplit();   // 两栏先画出来，两种来源都要画（导入态也有左引导 + 右大纲）
    const openPick = () => { pickMode = mode; picking = true; draw(); };
    ["#rfswitch", "#rfswitch2"].forEach((sel) => { const b = $(sel); if (b) b.onclick = openPick; });   // 放在画完之后：导入态的“更换来源”在右栏
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
  const html = `<div class="cc-steps" id="ccsteps" role="tablist" aria-label="创建访谈的阶段" data-subrow></div><div class="rf" style="padding:0"><div id="rfbody">${panels()}${pickHtml()}</div></div>`;
  return { html, wire, fit };
}
