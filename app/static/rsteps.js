/* 甄别问卷 · 左栏 6 步（修正方案第 1 轮第 4 条，效果图 qa_shots/fix1_batch5_mockup.html，方案 docs/甄别问卷重做_方案.md）。
   ① 读取立项 ② 要筛进谁 ③ 要筛掉谁 ④ 配额 ⑤ 质量控制 ⑥ 预览与测试。确认一步，服务器只重做这一步的题，右栏的问卷跟着变、新题标“刚改”。
   没有单独的对话栏：改某一题用右栏题干旁的 AI 图标（用户 2026-11-20）。规则都在服务器（app/recruit_steps.py），这里只展示和提交。
   rform.js 的 drawLeft 调 RSteps.render(容器, { p, getQ, apply, test })。依赖 index.html 的 api / esc / toast。 */
const RSteps = (() => {
  const S = { pid: "", d: null, at: "plan", draft: {}, busy: false, open: "", msgs: {}, expandNote: "", expanded: false };
  const NUM = ["①", "②", "③", "④", "⑤", "⑥"];
  const E = (x) => esc(String(x ?? ""));
  let ctx = null, root = null;
  const url = (t = "") => `/projects/${S.pid}/recruiting/form/steps${t}`;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const status = (k) => (S.d ? S.d.steps.status[k] : "todo");
  const reachable = (k) => { const o = S.d.order, i = o.indexOf(k); return i === 0 || status(k) !== "todo" || o.slice(0, i).every((x) => status(x) === "confirmed" || x === "test"); };

  async function load() {
    try { S.d = await api(url()); } catch (e) { toast(e.message, true); return; }
    const first = S.d.order.find((k) => status(k) !== "confirmed");
    S.at = S.want && S.d.order.includes(S.want) ? S.want : S.d.order.includes(S.at) && S.at !== "plan" ? S.at : first || "test";
    S.want = "";
    draw();
  }
  async function suggest(k, force) {
    if (!force && S.draft[k]) return;
    const saved = { include: S.d.steps.conds, exclude: S.d.steps.excl, quota: S.d.steps.quotas, qc: Object.keys(S.d.steps.qc || {}).length ? S.d.steps.qc : null }[k];
    if (!force && saved && (Array.isArray(saved) ? saved.length : true)) { S.draft[k] = clone(saved); return draw(); }
    S.busy = true; draw();
    try {
      const r = await api(url(`/${k}/suggest`), { method: "POST", body: { questions: ctx.getQ() } });
      S.draft[k] = clone(r.conds || r.excl || r.quotas || r.qc || []);
    } catch (e) { toast(e.message, true); S.draft[k] = S.draft[k] || (k === "qc" ? {} : []); }
    S.busy = false; draw();
  }

  // ---- drawing ------------------------------------------------------------------------------------------------------------------
  function bar() {
    return `<div class="rs-steps" role="tablist" aria-label="甄别问卷的步骤">${S.d.order.map((k, i) => {
      const st = status(k), on = S.at === k, ok = reachable(k);
      return `<button type="button" role="tab" class="rs-st${on ? " on" : ""} ${st}" aria-selected="${on}" data-rs-go="${k}"${ok ? "" : ' disabled title="先确认前面的步骤"'}><i>${st === "confirmed" ? "✓" : st === "stale" ? "!" : i + 1}</i>${E(S.d.labels[k])}</button>`;
    }).join("")}</div>`;
  }
  const from = () => { const p = S.d.plan; return `<div class="rs-from"><b>来自立项</b><span>${E((p.brief || "").slice(0, 60))}${p.need ? " · 招募需求：" + E(p.need.slice(0, 40)) : ""}</span></div>`; };   // 修正方案第 2 轮 A1: no “去立项改” (the right side is edited in place and kept in sync)
  // 修正方案第 2 轮 A2: written like a document — paragraphs, numbered goals one per line, the need one item per line
  const paras = (t) => String(t || "").split(/\n+/).map((x) => x.trim()).filter(Boolean);
  const needItems = (t) => String(t || "").split(/[。；;\n]/).map((x) => x.trim()).filter(Boolean);
  // 修正方案第 2 轮 A4–A6: under ② ③ ④ (and ⑥) a dialog in the one input box; the AI returns the whole new table, nothing is saved until 确认
  const chatBox = (k, ph) => `<div class="rs-chat">${(S.msgs[k] || []).map((m) => `<div class="rs-bub${m.me ? " me" : ""}">${E(m.text)}${m.retry ? ` <button type="button" class="lnk" data-rs-retry="${k}">重新发送</button>` : ""}</div>`).join("")}
    <form class="rs-chatf" data-rs-chat="${k}"><textarea data-ti="chat" data-ti-key="rs-${k}" data-ti-send="#rs-send-${k}" ${k === "quota" ? "" : 'data-ti-attach="no" '}rows="2" maxlength="2000" aria-label="和小星星确认这一步" placeholder="${E(ph)}"></textarea><button type="submit" id="rs-send-${k}">发送</button></form></div>`;
  function optRows(k, i, c) {
    return c.options.map((o, j) => `<div class="rs-opt"><input class="rs-in" value="${E(o.text)}" maxlength="120" aria-label="选项 ${j + 1}" data-rs-otext="${k}|${i}|${j}">
      <button type="button" class="rs-pass ${o.pass ? "ok" : "out"}" data-rs-pass="${k}|${i}|${j}" aria-label="${o.pass ? "通过，点一下改成终止" : "终止，点一下改成通过"}">${o.pass ? "通过" : "终止"}</button>
      <button type="button" class="rs-x" data-rs-odel="${k}|${i}|${j}" aria-label="删除这个选项"${c.options.length > 2 ? "" : " disabled"}>×</button></div>`).join("")
      + `<button type="button" class="s" data-rs-oadd="${k}|${i}">＋ 选项</button>`;
  }
  function condList(k) {
    const list = S.draft[k] || [];
    if (S.busy && !list.length) return `<div class="mut">正在读招募需求……</div>`;
    return list.map((c, i) => {
      const id = `${k}|${i}`, open = S.open === id;
      return `<div class="rs-cond${c.use ? "" : " off"}"><label class="rs-ck"><input type="checkbox" data-rs-use="${id}"${c.use ? " checked" : ""} aria-label="用“${E(c.name)}”"></label>
        <div class="t"><b>${E(c.name)}</b>${c.src === "ai" ? ` <span class="rs-pill w">AI 补充</span>` : c.src === "plan" ? ` <span class="rs-pill">来自立项</span>` : ""}
          <small>${E(c.note || c.question)} · ${c.options.filter((o) => o.pass).map((o) => E(o.text)).join("、")} 通过</small>
          ${open ? `<div class="rs-edit"><label>题目<input class="rs-in" value="${E(c.question)}" maxlength="300" data-rs-q="${id}" aria-label="题目"></label>
            <div class="rs-type"><button type="button" class="${c.type !== "multi" ? "on" : ""}" data-rs-type="${id}|single">单选</button><button type="button" class="${c.type === "multi" ? "on" : ""}" data-rs-type="${id}|multi">多选</button></div>
            ${optRows(k, i, c)}</div>` : ""}</div>
        <button type="button" class="s" data-rs-open="${id}">${open ? "收起" : "改"}</button></div>`;
    }).join("") + `<button type="button" class="s" data-rs-add="${k}">＋ 自己加${k === "include" ? "条件" : "排除项"}</button>`;
  }
  function condTable(k) {
    const list = S.draft[k] || [];
    if (S.busy && !list.length) return `<div class="mut">小星星正在按研究背景和需求生成……</div>`;
    if (!list.length) return `<div class="mut">还没有条件，在下面对话里说说要筛的人。</div>`;
    return `<table class="rs-tbl rs-ctbl"><thead><tr><th style="width:34px">用</th><th>条件</th><th>怎么问</th><th>通过</th><th>终止</th></tr></thead><tbody>${list.map((c, i) => `<tr class="${c.use ? "" : "off"}">
      <td><input type="checkbox" data-rs-use="${k}|${i}"${c.use ? " checked" : ""} aria-label="用“${E(c.name)}”"></td><td><b>${E(c.name)}</b>${c.src === "plan" ? ` <span class="rs-pill">来自立项</span>` : c.src === "ai" ? ` <span class="rs-pill w">AI</span>` : ""}${c.note ? `<small>${E(c.note)}</small>` : ""}</td>
      <td>${E(c.question)}<small>${c.type === "multi" ? "多选" : "单选"}</small></td><td>${c.options.filter((o) => o.pass).map((o) => E(o.text)).join("；") || "—"}</td><td>${c.options.filter((o) => !o.pass).map((o) => E(o.text)).join("；") || "—"}</td></tr>`).join("")}</tbody></table>`;
  }
  function quotaTable() {
    const q = S.draft.quota || [];
    if (S.busy && !q.length) return `<div class="mut">正在读立项的配额……</div>`;
    const sum = q.reduce((a, x) => a + (+x.target || 0), 0);
    return `<table class="rs-tbl"><thead><tr><th>组别</th><th>要有的标签（用 + 连接）</th><th>人数</th><th></th></tr></thead><tbody>${q.map((x, i) => `<tr>
      <td><input class="rs-in" value="${E(x.name)}" maxlength="100" aria-label="组别名称" data-rs-qn="${i}"></td><td><input class="rs-in" value="${E(x.value)}" maxlength="100" aria-label="标签" data-rs-qv="${i}"></td>
      <td><input class="rs-in num" type="number" min="1" max="10000" value="${x.target}" aria-label="人数" data-rs-qt="${i}"></td><td><button type="button" class="rs-x" data-rs-qdel="${i}" aria-label="删除这一组">×</button></td></tr>`).join("")}
      <tr class="tot"><td colspan="2">合计</td><td id="rs-qsum">${sum}</td><td></td></tr></tbody></table>`;
  }
  function body() {
    const k = S.at, p = S.d.plan;
    if (k === "plan") return `<h3>① 读取立项</h3>
      <div class="rs-doc"><h4>研究背景</h4>${paras(p.brief).map((x) => `<p>${E(x)}</p>`).join("") || "<p>—</p>"}
      <h4>研究目标</h4>${(p.goals || []).length ? `<ol>${p.goals.map((g) => `<li>${E(g)}</li>`).join("")}</ol>` : "<p>—</p>"}
      <h4>招募需求</h4>${needItems(p.need).length ? `<ul>${needItems(p.need).map((x) => `<li>${E(x)}</li>`).join("")}</ul>` : "<p>—</p>"}
      <h4>语言 · 访谈形式</h4><p>${E((p.languages || []).join("、"))} · ${E((p.formats || []).join("、"))}</p></div>
      <div class="rs-nav"><span class="sm mut">要改的话直接在右边改，会同步到项目总览和立项</span><button type="button" class="p" data-rs="ok">确认，下一步</button></div>`;
    const nav = (label) => `<div class="rs-nav"><button type="button" data-rs="prev">‹ 上一步</button><button type="button" class="p" data-rs="ok"${S.busy ? " disabled" : ""}>${label}</button></div>`;
    if (k === "include") return `<h3>② 要筛进谁</h3><p class="sm mut">小星星按研究背景、目标和招募需求生成的条件；在下面对话里改，点“下一步”生成题目。<button type="button" class="lnk" data-rs="resuggest">全部重做</button></p>${condTable("include")}${chatBox("include", "说说要怎么改，比如“城市只要一线和新一线”")}${nav("下一步")}`;
    if (k === "exclude") return `<h3>③ 要筛掉谁</h3><p class="sm mut">常见排除项按访谈形式预先勾好，小星星补了本项目特有的；排除题放在问卷最前面，命中就终止。</p>${condTable("exclude")}${chatBox("exclude", "说说要怎么改，比如“去掉年龄那条”")}${nav("下一步")}`;
    if (k === "quota") return `<h3>④ 配额</h3><p class="sm">${(S.draft.quota || []).length ? `<span class="rs-pill ok">已有配额</span> ` : ""}可以导入配额文件（Word、Excel、PDF、PPT、图片），小星星识别后出配额表让你确认；也可以直接改表格或在对话里说。</p>
      <button type="button" class="s" data-rs="import">导入配额文件</button>${quotaTable()}${chatBox("quota", "上传配额文件，或说“A 组改成 4 人”")}${nav("下一步")}`;
    if (k === "qc") {
      const q = S.draft.qc || {};
      return `<h3>⑤ 质量控制</h3>
        <label class="rs-tg"><input type="checkbox" data-rs-qc="trap"${q.trap ? " checked" : ""}><span><b>陷阱题</b><small>“这一题请选经常”，答错就终止（题数 6 题及以上时建议加）</small></span></label>
        <label class="rs-tg"><input type="checkbox" data-rs-qc="timed"${q.min_seconds ? " checked" : ""}><span><b>最短作答时长</b><small>不到 <input class="rs-in num" type="number" min="5" max="1800" value="${q.min_seconds || 20}" aria-label="最短秒数" data-rs-qcs> 秒就终止。时间由服务器记，不信浏览器</small></span></label>
        <div class="rs-tg"><span class="rs-on">✓</span><span><b>重复参加识别</b><small>只查合格的人：同一个项目里姓名和邮箱都相同，就不再加进名单</small></span></div>${nav("下一步")}`;
    }
    return `<h3>⑥ 预览与测试</h3>${S.expandNote ? `<div class="rs-note">${E(S.expandNote)}</div>` : ""}<p class="sm mut">右边“测试答题”自己答一遍：和候选人走的是同一套逻辑，最后写通过还是终止、终止在哪一题；不保存、不计数。测到哪一题，就可以在下面对话里改这一题。</p>
      ${chatBox("test", "边测边改当前这一题，比如“加一个官方上门保养的选项”")}
      <div class="rs-nav"><button type="button" data-rs="prev">‹ 上一步</button><span><button type="button" class="rs-breathe" data-rs="test">测试答题</button> <button type="button" class="p" data-rs="list">去发布（配额及受访者名单）</button></span></div>`;
  }
  // 修正方案第 3 轮: the side menu (and its slot under 甄别问卷) may be drawn after us, or drawn again by the shell: whenever an empty slot appears, fill it
  let slotWatch = null;
  function watchSlot() {
    if (slotWatch) return;
    slotWatch = new MutationObserver(() => {
      const sl = document.querySelector(".sub2 #l2slot");
      if (sl && !sl.querySelector(".rs-side") && root && S.d && document.contains(root)) draw();
    });
    slotWatch.observe(document.body, { childList: true, subtree: true });
  }
  function draw() {
    if (!root || !S.d) return;
    if (["include", "exclude", "quota", "qc"].includes(S.at) && !S.draft[S.at] && !S.busy) { suggest(S.at); return; }
    if (S.at === "test" && !S.expanded && !S.busy) { S.expanded = true; expandQs(); }
    const slot = document.querySelector(".sub2 #l2slot");   // 用户 2026-11-20：6 步放在左侧栏“甄别问卷”下面，作为子栏
    if (slot) {
      slot.innerHTML = `<div class="rs-side" role="tablist" aria-label="甄别问卷的步骤">${S.d.order.map((k, i) => { const st = status(k), on = S.at === k, ok = reachable(k);
        return `<button type="button" role="tab" class="rs-sst${on ? " on" : ""} ${st}" aria-selected="${on}" data-rs-go="${k}"${ok ? "" : ' disabled title="先确认前面的步骤"'}><span><i>${st === "confirmed" ? "✓" : st === "stale" ? "!" : i + 1}</i>${E(S.d.labels[k])}</span></button>`; }).join("")}</div>`;
      if (!slot.dataset.rsWired) { slot.dataset.rsWired = "1"; slot.addEventListener("click", onClick); }
      slot.hidden = false;   // the shell hides an empty slot when the page is drawn
    }
    watchSlot();
    root.innerHTML = `<div class="rs">${slot ? "" : bar()}<div class="rs-body${S.busy ? " busy" : ""}">${body()}</div></div>`;
    if (S.drawnAt !== S.at) {   // 修正方案第 4 轮: a new step starts at its top
      S.drawnAt = S.at;
      for (let e = root; e && e !== document.body; e = e.parentElement) if (e.scrollTop) e.scrollTop = 0;
    }
  }

  // ---- actions ------------------------------------------------------------------------------------------------------------------
  const at = (key) => { const [k, i, j] = key.split("|"); return { list: S.draft[k], c: S.draft[k][+i], j: j === undefined ? -1 : +j }; };
  async function confirm() {
    const k = S.at, order = S.d.order, next = order[order.indexOf(k) + 1];
    if (k === "test") return;
    const data = k === "include" ? { conds: S.draft.include } : k === "exclude" ? { excl: S.draft.exclude } : k === "quota" ? { quotas: S.draft.quota.map((x) => ({ ...x, field: "tag", target: +x.target })) }
      : k === "qc" ? { qc: { ...S.draft.qc, min_seconds: S.draft.qc.timed === false ? 0 : +(S.draft.qc.min_seconds || 0) } } : {};
    S.busy = true; draw();
    try {
      const r = await api(url(`/${k}`), { method: "PUT", body: { data, questions: ctx.getQ() } });
      S.d = { ...S.d, steps: r.steps, at: r.at };
      if (["include", "exclude", "qc"].includes(k)) ctx.apply(r);
      toast(r.fresh && r.fresh.length ? `已生成 ${r.fresh.length} 道题` : "已确认");
      S.at = next || k;
    } catch (e) { toast(e.message, true); }
    S.busy = false; draw();
  }
  function onClick(e) {
    const rt = e.target.closest("[data-rs-retry]");
    if (rt) {   // 修正方案第 3 轮: send the failed message again (the failed pair is taken out first)
      const k = rt.dataset.rsRetry, list = S.msgs[k] || [], last = list[list.length - 1]; if (!last || !last.retry) return;
      list.splice(-2, 2); const f = root.querySelector(`[data-rs-chat="${k}"]`), ta = f && f.querySelector("textarea");
      if (ta) { ta.value = last.retry; f.requestSubmit(); } return;
    }
    const b = e.target.closest("[data-rs],[data-rs-go],[data-rs-open],[data-rs-add],[data-rs-pass],[data-rs-odel],[data-rs-oadd],[data-rs-type],[data-rs-qdel]"); if (!b || b.disabled) return;
    const d = b.dataset;
    if (d.rsGo) { S.at = d.rsGo; S.open = ""; return draw(); }
    if (d.rsOpen) { S.open = S.open === d.rsOpen ? "" : d.rsOpen; return draw(); }
    if (d.rsAdd) { const list = S.draft[d.rsAdd]; list.push({ id: "u" + Date.now().toString(36), name: "新条件", question: "", type: "single", use: true, src: "user", options: [{ text: "符合", pass: true }, { text: "不符合", pass: false }] }); S.open = `${d.rsAdd}|${list.length - 1}`; return draw(); }
    if (d.rsPass) { const x = at(d.rsPass); x.c.options[x.j].pass = !x.c.options[x.j].pass; return draw(); }
    if (d.rsOdel) { const x = at(d.rsOdel); x.c.options.splice(x.j, 1); return draw(); }
    if (d.rsOadd) { const x = at(d.rsOadd); x.c.options.push({ text: "", pass: false }); return draw(); }
    if (d.rsType) { const [k, i, t] = d.rsType.split("|"); S.draft[k][+i].type = t; return draw(); }
    if (d.rsQdel) { S.draft.quota.splice(+d.rsQdel, 1); return draw(); }
    switch (d.rs) {
      case "ok": return S.at === "plan" ? api(url("/plan"), { method: "PUT", body: { data: {}, questions: ctx.getQ() } }).then((r) => { S.d = { ...S.d, steps: r.steps }; S.at = "include"; draw(); }).catch((er) => toast(er.message, true)) : confirm();
      case "prev": { const o = S.d.order; S.at = o[Math.max(0, o.indexOf(S.at) - 1)]; return draw(); }
      case "resuggest": return suggest("include", true);
      case "qadd": S.draft.quota.push({ name: "新的一组", field: "tag", question: "", value: "", target: 1 }); return draw();
      case "test": return ctx.test();
      case "import": { const b = root.querySelector('.rs-chatf[data-rs-chat="quota"] [data-tia=file]'); if (b) b.click(); return; }
      case "list": if (window.__ptabGo) window.__ptabGo("list"); return;
    }
  }
  async function expandQs() {   // ⑥ 主动补题 (用户 2026-11-20): too few questions for this study and the AI adds some, once
    try {
      const r = await api(url("/test/expand"), { method: "POST", body: {} });
      if (r.fresh && r.fresh.length) { ctx.apply(r); S.expandNote = `按研究背景和人群，小星星补了 ${r.fresh.length} 道题（右边标“刚改”），不合适可以在下面对话里改或直接删。`; draw(); }
    } catch (e) { /* a refusal is shown by api() already */ }
  }
  async function onChat(e) {
    const f = e.target.closest("[data-rs-chat]"); if (!f) return;
    e.preventDefault();
    const k = f.dataset.rsChat, ta = f.querySelector("textarea"), text = (ta.value || "").trim();
    const refs = window.TInput ? TInput.takeRefs("rs-" + k) : [];
    if (!text && !refs.length) return;
    const say = text || "请读附件里的配额";
    (S.msgs[k] = S.msgs[k] || []).push({ me: true, text: say + (refs.length ? `（附 ${refs.length} 个文件）` : "") });
    ta.value = ""; S.busy = true; draw();
    try {
      if (k === "test") {
        const r = await ctx.askQ(say);
        S.msgs[k].push({ text: r });
      } else {
        const r = await api(url(`/${k}/chat`), { method: "POST", body: { text: say, draft: S.draft[k] || [], refs } });
        S.draft[k] = clone(r.conds || r.excl || r.quotas || S.draft[k] || []);
        S.msgs[k].push({ text: r.reply });
      }
    } catch (er) { S.msgs[k].push({ text: er.message || "没有改成，请再说一次", retry: er.status === 0 || er.status >= 500 ? say : "" }); }
    S.busy = false; draw();
  }
  function onInput(e) {
    const t = e.target, d = t.dataset;
    if (d.rsOtext) { const x = at(d.rsOtext); x.c.options[x.j].text = t.value; return; }
    if (d.rsQ) { at(d.rsQ).c.question = t.value; return; }
    if (d.rsQn !== undefined) { S.draft.quota[+d.rsQn].name = t.value; return; }
    if (d.rsQv !== undefined) { S.draft.quota[+d.rsQv].value = t.value; return; }
    if (d.rsQt !== undefined) { S.draft.quota[+d.rsQt].target = t.value; const el = document.getElementById("rs-qsum"); if (el) el.textContent = S.draft.quota.reduce((a, x) => a + (+x.target || 0), 0); return; }   // 修正方案第 2 轮 A6: the total follows every keystroke
    if (d.rsQcs !== undefined) { S.draft.qc.min_seconds = +t.value; return; }
  }
  function onChange(e) {
    const t = e.target, d = t.dataset;
    if (d.rsUse) { at(d.rsUse).c.use = t.checked; return draw(); }
    if (d.rsQc) { if (d.rsQc === "timed") { S.draft.qc.timed = t.checked; if (t.checked && !S.draft.qc.min_seconds) S.draft.qc.min_seconds = 20; if (!t.checked) S.draft.qc.min_seconds = 0; } else S.draft.qc[d.rsQc] = t.checked; return draw(); }
  }

  return {
    redraw() { if (root && S.d && document.contains(root)) draw(); },
    /** 修正方案第 4 轮: open a given step (从“招募/主持/问卷”点下一步 = ① 读取立项), also before the steps have loaded. */
    goto(k) { S.at = k; S.want = k; S.open = ""; if (root && S.d && document.contains(root)) draw(); },
    _dbg() { return { root: !!root, inDoc: !!(root && document.contains(root)), d: !!S.d, same: root === document.querySelector("#ccroot") }; },   // 修正方案第 2 轮 D2: the page came back into view without being drawn again
    render(el, c) {
      ctx = c; root = el;
      if (S.pid !== c.p.id) Object.assign(S, { pid: c.p.id, d: null, at: "plan", draft: {}, busy: false, open: "", msgs: {}, expandNote: "", expanded: false });
      if (!el.dataset.rsWired) { el.dataset.rsWired = "1"; el.addEventListener("click", onClick); el.addEventListener("input", onInput); el.addEventListener("change", onChange); el.addEventListener("submit", onChat); }
      if (!S.d) { el.innerHTML = `<div class="rs"><div class="mut" style="padding:16px">正在读取……</div></div>`; load(); } else draw();
    },
  };
})();
