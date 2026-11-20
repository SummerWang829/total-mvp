/* 甄别问卷 · 左栏 6 步（修正方案第 1 轮第 4 条，效果图 qa_shots/fix1_batch5_mockup.html，方案 docs/甄别问卷重做_方案.md）。
   ① 读取立项 ② 要筛进谁 ③ 要筛掉谁 ④ 配额 ⑤ 质量控制 ⑥ 预览与测试。确认一步，服务器只重做这一步的题，右栏的问卷跟着变、新题标“刚改”。
   没有单独的对话栏：改某一题用右栏题干旁的 AI 图标（用户 2026-11-20）。规则都在服务器（app/recruit_steps.py），这里只展示和提交。
   rform.js 的 drawLeft 调 RSteps.render(容器, { p, getQ, apply, test })。依赖 index.html 的 api / esc / toast。 */
const RSteps = (() => {
  const S = { pid: "", d: null, at: "plan", draft: {}, busy: false, open: "" };
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
    S.at = S.d.order.includes(S.at) && S.at !== "plan" ? S.at : first || "test";
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
  const from = () => { const p = S.d.plan; return `<div class="rs-from"><b>来自立项</b><span>${E((p.brief || "").slice(0, 60))}${p.need ? " · 招募需求：" + E(p.need.slice(0, 40)) : ""}</span><a href="#/p/${S.pid}/wiz">去立项改 ›</a></div>`; };
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
  function quotaTable() {
    const q = S.draft.quota || [];
    if (S.busy && !q.length) return `<div class="mut">正在读立项的配额……</div>`;
    const sum = q.reduce((a, x) => a + (+x.target || 0), 0);
    return `<table class="rs-tbl"><thead><tr><th>组别</th><th>要有的标签（用 + 连接）</th><th>人数</th><th></th></tr></thead><tbody>${q.map((x, i) => `<tr>
      <td><input class="rs-in" value="${E(x.name)}" maxlength="100" aria-label="组别名称" data-rs-qn="${i}"></td><td><input class="rs-in" value="${E(x.value)}" maxlength="100" aria-label="标签" data-rs-qv="${i}"></td>
      <td><input class="rs-in num" type="number" min="1" max="10000" value="${x.target}" aria-label="人数" data-rs-qt="${i}"></td><td><button type="button" class="rs-x" data-rs-qdel="${i}" aria-label="删除这一组">×</button></td></tr>`).join("")}
      <tr class="tot"><td colspan="2">合计</td><td>${sum}</td><td></td></tr></tbody></table><button type="button" class="s" data-rs="qadd">＋ 加一组</button>`;
  }
  function body() {
    const k = S.at, p = S.d.plan;
    if (k === "plan") return `<h3>① 读取立项</h3>${[["研究背景", p.brief], ["研究目标", (p.goals || []).map((g, i) => `${i + 1}. ${g}`).join("　")], ["招募需求", p.need], ["语言 · 访谈形式", `${(p.languages || []).join("、")} · ${(p.formats || []).join("、")}（在立项里定，这里不再问）`]]
      .map(([a, b]) => `<div class="rs-row"><b>${a}</b><span>${E(b) || "—"}</span></div>`).join("")}<div class="rs-nav"><a class="btn" href="#/p/${S.pid}/wiz">去立项改 ›</a><button type="button" class="p" data-rs="ok">确认，下一步</button></div>`;
    const nav = (label) => `<div class="rs-nav"><button type="button" data-rs="prev">‹ 上一步</button><button type="button" class="p" data-rs="ok"${S.busy ? " disabled" : ""}>${label}</button></div>`;
    if (k === "include") return `${from()}<h3>② 要筛进谁</h3><p class="sm mut">从招募需求里拆出的条件，勾选要用的，也可以改或补充。<button type="button" class="lnk" data-rs="resuggest">重新拆</button></p>${condList("include")}${nav("确认，生成这部分题目")}`;
    if (k === "exclude") return `${from()}<h3>③ 要筛掉谁</h3><p class="sm mut">常见排除项按访谈形式预先勾好；排除题放在问卷最前面，命中就终止。</p>${condList("exclude")}${nav("确认，生成排除题")}`;
    if (k === "quota") return `${from()}<h3>④ 配额</h3><p class="sm"><span class="rs-pill ok">已引用立项的配额表</span> 直接用，或在这里增、删、改；确认后“配额及受访者名单”马上按它算正式 / 备份。</p>${quotaTable()}${nav("确认配额")}`;
    if (k === "qc") {
      const q = S.draft.qc || {};
      return `${from()}<h3>⑤ 质量控制</h3>
        <label class="rs-tg"><input type="checkbox" data-rs-qc="trap"${q.trap ? " checked" : ""}><span><b>陷阱题</b><small>“这一题请选经常”，答错就终止（题数 6 题及以上时建议加）</small></span></label>
        <label class="rs-tg"><input type="checkbox" data-rs-qc="consistency"${q.consistency ? " checked" : ""}><span><b>前后一致性</b><small>把第一个条件换个顺序再问一次，两次回答不一样就终止</small></span></label>
        <label class="rs-tg"><input type="checkbox" data-rs-qc="timed"${q.min_seconds ? " checked" : ""}><span><b>最短作答时长</b><small>不到 <input class="rs-in num" type="number" min="5" max="1800" value="${q.min_seconds || 20}" aria-label="最短秒数" data-rs-qcs> 秒就终止。时间由服务器记，不信浏览器</small></span></label>
        <div class="rs-tg"><span class="rs-on">✓</span><span><b>重复参加识别</b><small>只查合格的人：同一个项目里姓名和邮箱都相同，就不再加进名单</small></span></div>${nav("确认，生成质控题")}`;
    }
    return `${from()}<h3>⑥ 预览与测试</h3><p class="sm mut">右边切到“测试答题”自己答一遍：和候选人走的是同一套逻辑，最后写通过还是终止、终止在哪一题；不保存、不计数。</p>
      <div class="rs-nav"><button type="button" data-rs="prev">‹ 上一步</button><span><button type="button" data-rs="test">测试答题</button> <button type="button" class="p" data-rs="list">去发布（配额及受访者名单）</button></span></div>`;
  }
  function draw() {
    if (!root || !S.d) return;
    if (["include", "exclude", "quota", "qc"].includes(S.at) && !S.draft[S.at] && !S.busy) { suggest(S.at); return; }
    const slot = document.querySelector(".sub2 #l2slot");   // 用户 2026-11-20：6 步放在左侧栏“甄别问卷”下面，作为子栏
    if (slot) {
      slot.innerHTML = `<div class="rs-side" role="tablist" aria-label="甄别问卷的步骤">${S.d.order.map((k, i) => { const st = status(k), on = S.at === k, ok = reachable(k);
        return `<button type="button" role="tab" class="rs-sst${on ? " on" : ""} ${st}" aria-selected="${on}" data-rs-go="${k}"${ok ? "" : ' disabled title="先确认前面的步骤"'}><span><i>${st === "confirmed" ? "✓" : st === "stale" ? "!" : i + 1}</i>${E(S.d.labels[k])}</span></button>`; }).join("")}</div>`;
      if (!slot.dataset.rsWired) { slot.dataset.rsWired = "1"; slot.addEventListener("click", onClick); }
      slot.hidden = false;   // the shell hides an empty slot when the page is drawn
    }
    root.innerHTML = `<div class="rs">${slot ? "" : bar()}<div class="rs-body${S.busy ? " busy" : ""}">${body()}</div></div>`;
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
      case "list": if (window.__ptabGo) window.__ptabGo("list"); return;
    }
  }
  function onInput(e) {
    const t = e.target, d = t.dataset;
    if (d.rsOtext) { const x = at(d.rsOtext); x.c.options[x.j].text = t.value; return; }
    if (d.rsQ) { at(d.rsQ).c.question = t.value; return; }
    if (d.rsQn !== undefined) { S.draft.quota[+d.rsQn].name = t.value; return; }
    if (d.rsQv !== undefined) { S.draft.quota[+d.rsQv].value = t.value; return; }
    if (d.rsQt !== undefined) { S.draft.quota[+d.rsQt].target = t.value; return; }
    if (d.rsQcs !== undefined) { S.draft.qc.min_seconds = +t.value; return; }
  }
  function onChange(e) {
    const t = e.target, d = t.dataset;
    if (d.rsUse) { at(d.rsUse).c.use = t.checked; return draw(); }
    if (d.rsQc) { if (d.rsQc === "timed") { S.draft.qc.timed = t.checked; if (t.checked && !S.draft.qc.min_seconds) S.draft.qc.min_seconds = 20; if (!t.checked) S.draft.qc.min_seconds = 0; } else S.draft.qc[d.rsQc] = t.checked; return draw(); }
  }

  return {
    render(el, c) {
      ctx = c; root = el;
      if (S.pid !== c.p.id) Object.assign(S, { pid: c.p.id, d: null, at: "plan", draft: {}, busy: false, open: "" });
      if (!el.dataset.rsWired) { el.dataset.rsWired = "1"; el.addEventListener("click", onClick); el.addEventListener("input", onInput); el.addEventListener("change", onChange); }
      if (!S.d) { el.innerHTML = `<div class="rs"><div class="mut" style="padding:16px">正在读取……</div></div>`; load(); } else draw();
    },
  };
})();
