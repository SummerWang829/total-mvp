/* 项目总览首屏（原版星桐）：整体进度大数字 + 各子项目（国家）星图 + 项目状态 + 子项目表；选中一个子项目后看它自己的进度和各方式入口。
   数据来自立项（/plan）和执行监控（/monitor）；没有立项记录的老项目不用这一块（还是原来的总览）。依赖 shell.js 的 shSub / SH_DESIGN。 */
const HM_POS = { 新加坡: [300, 238], 马来西亚: [380, 168], 印尼: [528, 236], 越南: [452, 64], 泰国: [330, 96], 菲律宾: [560, 120], 日本: [560, 46], 韩国: [500, 30],
  中国: [420, 50], 印度: [200, 140], 德国: [70, 62], 沙特: [88, 128], 阿联酋: [150, 208], 美国: [40, 230], 英国: [100, 40] };
const HM_EDGES = [["泰国", "越南"], ["泰国", "马来西亚"], ["马来西亚", "印尼"], ["越南", "印尼"], ["沙特", "泰国"], ["德国", "沙特"], ["沙特", "阿联酋"], ["阿联酋", "泰国"], ["新加坡", "马来西亚"], ["新加坡", "印尼"]];
const HM_STATES = ["立项", "招募中", "进行中", "已完成", "已归档"];
const hmPct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/** 每个子项目的数字：它名下的配额行（行标签里带这个国家名）合计。 */
function hmSubStats(mon, sub) {
  const mine = (T) => T.rows.filter((r) => r.label.split(" · ").includes(sub.name));
  const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
  const q = mine(mon.q), i = mine(mon.i), f = mine(mon.f), marked = (rows) => rows.filter((r) => r.async);  // marked = the groups the client chose for the asynchronous AI
  return { qc: sum(q, "ready"), qt: sum(q, "target"), ic: sum(i, "ready"), it: sum(i, "target"), fc: sum(f, "ready"), ft: sum(f, "target"), ia: sum(marked(i), "target"), fa: sum(marked(f), "target") };
}

/** 项目状态：立项 › 招募中 › 进行中 › 已完成 › 已归档（项目整体）。 */
function hmState(p, o) {
  if (p.closed_at) return p.purge_at && new Date(p.purge_at + "Z") < new Date() ? 4 : 3;
  return o.summary && (o.summary.sessions > 0 || o.summary.done > 0) ? 2 : 1;
}

/** 一个阶段（某个子项目的某种方法）现在的状态：招募中 / 进行中 / 已完成 / 已归档。 */
function hmStageState(p, o, kind, done, target) {
  if (p.closed_at) return p.purge_at && new Date(p.purge_at + "Z") < new Date() ? "已归档" : "已完成";
  if (target > 0 && done >= target) return "已完成";
  return done > 0 || (kind !== "quant" && o.summary && o.summary.sessions > 0) ? "进行中" : "招募中";
}

function hmHero(p, mon, o) {
  const subs = mon.subs, st = subs.map((s) => ({ s, x: hmSubStats(mon, s) }));
  const T = st.reduce((a, { x }) => ({ qc: a.qc + x.qc, qt: a.qt + x.qt, ic: a.ic + x.ic, it: a.it + x.it, fc: a.fc + x.fc, ft: a.ft + x.ft }), { qc: 0, qt: 0, ic: 0, it: 0, fc: 0, ft: 0 });
  const M = mon.methods || p.methods || [], quant = M.includes("quant"), main = quant ? [T.qc, T.qt, "定量完成", "份"] : M.includes("depth") ? [T.ic, T.it, "深访就绪", "人"] : [T.fc, T.ft, "座谈会就绪", "人"];
  const pos = {}; let k = 0; subs.forEach((s) => (pos[s.name] = HM_POS[s.name] || [120 + (k++) * 96, 150]));
  const dots = []; for (let x = 14; x < 640; x += 28) for (let y = 14; y < 300; y += 28) dots.push(`<circle class="sgrid" cx="${x}" cy="${y}" r="1.1"/>`);
  const edges = HM_EDGES.filter((e) => pos[e[0]] && pos[e[1]]).map((e) => { const a = pos[e[0]], b = pos[e[1]]; return `<path class="sedge ${Math.abs(a[0] - b[0]) > 200 ? "far" : ""}" d="M${a[0]} ${a[1]}Q${(a[0] + b[0]) / 2} ${(a[1] + b[1]) / 2 - 14} ${b[0]} ${b[1]}"/>`; }).join("");
  const nodes = st.map(({ s, x }) => {
    const [cx, cy] = pos[s.name], r = 15 + Math.min(10, (quant ? x.qt : x.it) / 300 * 10), R = r + 8, pct = hmPct(quant ? x.qc : x.ic, quant ? x.qt : x.it);
    return `<g class="sn" tabindex="0" role="button" data-sub="${s.id}" aria-label="${esc(s.name)}，完成 ${pct}%，进入子项目"><circle class="ring-bg" cx="${cx}" cy="${cy}" r="${R + 4}" fill="transparent" pointer-events="all" stroke-width="3"/>
      <circle class="ring" cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke-width="3" stroke-linecap="round" pathLength="100" stroke-dasharray="100" stroke-dashoffset="${100 - pct}" transform="rotate(-90 ${cx} ${cy})"/>
      <circle class="core" cx="${cx}" cy="${cy}" r="${r * 0.42}"/><text class="nm" x="${cx}" y="${cy + R + 17}" text-anchor="middle">${esc(s.name)}</text>
      <text class="vl" x="${cx}" y="${cy + R + 32}" text-anchor="middle">${quant ? x.qc + " / " + x.qt : x.ic + " / " + x.it}</text></g>`;
  }).join("");
  const meter = (n, a, b, u) => `<div class="meter"><dt>${n}</dt><dd><div class="qbar o"><i style="width:${hmPct(a, b)}%"></i></div></dd><dd>${a} / ${b} ${u}</dd></div>`;
  return `<section class="hero2" aria-label="项目整体进度"><div class="hero-l"><div><div class="hero-k">${main[2]}</div><div class="hero-n"><b>${main[0]}</b><span>/ ${main[1]} ${main[3]}</span></div></div>
    <div class="qbar o big" role="img" aria-label="${main[2]} ${hmPct(main[0], main[1])}%"><i style="width:${hmPct(main[0], main[1])}%"></i></div>
    <dl class="meters">${quant ? meter("定量", T.qc, T.qt, "份") : ""}${M.includes("depth") ? meter("深访", T.ic, T.it, "人") : ""}${M.includes("group") ? meter("座谈会", T.fc, T.ft, "人") : ""}${M.includes("listening") ? `<div class="meter"><dt>舆情</dt><dd></dd><dd>${(o.starmap.find((n) => n.key === "listening") || {}).big ?? 0} 条内容</dd></div>` : ""}</dl></div>
    <figure class="hero-r"><svg viewBox="0 0 640 300" preserveAspectRatio="xMidYMid meet" role="group" aria-label="各子项目的完成度">${dots.join("")}${edges}${nodes}</svg><figcaption class="hero-cap">圆环是完成度，点一个国家进入它的子项目</figcaption></figure></section>`;
}

function hmStatus(p, o) {
  const cur = hmState(p, o);
  return `<div class="row hm-status"><b>项目状态</b>${HM_STATES.map((x, i) => `<span class="hm-st ${i === cur ? "on" : i < cur ? "done" : ""}">${x}</span>`).join('<span class="mut">›</span>')}<span class="sp"></span><button class="s" id="hmcopy" type="button">复制项目</button></div>`;
}

function hmTable(p, mon, users) {
  const pm = (id) => (users.find((u) => u.id === id) || {}).name || "未指定";
  return `<div class="card"><div class="scroll"><table class="tbl"><thead><tr><th>子项目</th><th>研究顺序</th><th>定量</th><th>定性</th></tr></thead><tbody>
    ${mon.subs.map((s, i) => { const x = hmSubStats(mon, s), ks = (SH_DESIGN[s.design] || SH_DESIGN.iq)[1]; return `<tr><td><b>S${i + 1}</b> ${esc(s.name)}<div class="sm mut">${esc(s.lang)} · ${esc(pm(s.pm))}${s.listening ? " · 舆情" : ""}${s.async_ai ? " · 异步 AI" : ""}</div></td><td><span class="pill o">${SH_DESIGN[s.design] ? SH_DESIGN[s.design][0] : ""}</span></td>
      <td style="min-width:150px">${ks.includes("quant") ? `${x.qc} / ${x.qt}<div class="qbar o"><i style="width:${hmPct(x.qc, x.qt)}%"></i></div>` : '<span class="mut">不做</span>'}</td>
      <td>${ks.includes("depth") ? `深访 ${x.ic}/${x.it}${x.ia ? ` <span class="tag o">异步 AI ${x.ia}</span>` : ""}` : ""}${ks.includes("depth") && ks.includes("group") ? "<br>" : ""}${ks.includes("group") ? `座谈会 ${x.fc}/${x.ft} 人${x.fa ? ` <span class="tag o">异步 AI ${x.fa}</span>` : ""}` : ""}${ks.some((k) => k !== "quant") ? "" : '<span class="mut">不做</span>'}</td></tr>`; }).join("")}
    </tbody></table></div></div>`;
}

/** 研究阶段列表：每个子项目按自己的研究顺序列出各阶段、状态和进度，点进去看实际进度和配额监控。 */
function hmStages(p, mon, o, users, one) {
  const subs = one ? [one] : mon.subs, many = subs.length > 1, pm = (id) => (users.find((u) => u.id === id) || {}).name || "未指定";
  const block = (s) => {
    const x = hmSubStats(mon, s), ks = (SH_DESIGN[s.design] || SH_DESIGN.iq)[1], num = { quant: [x.qc, x.qt, "份"], depth: [x.ic, x.it, "人"], group: [x.fc, x.ft, "人"] }, asy = { depth: x.ia, group: x.fa };
    return `<div class="card hm-stages">${many || one ? `<div class="row"><b>${esc(s.name)}</b><span class="mut sm">${SH_DESIGN[s.design] ? SH_DESIGN[s.design][0] : ""} · 责任人 ${esc(pm(s.pm))}</span></div>` : ""}
      <ol class="hm-stagelist">${ks.map((k, i) => { const [d, t, u] = num[k], st = hmStageState(p, o, k, d, t); return `<li><span class="sic">${i + 1}</span><b>${SH_KIND[k]}</b><span class="pill ${st === "招募中" ? "o" : st === "进行中" ? "" : "g"}">${st}</span>
        <span class="mut sm">${d} / ${t} ${u}${asy[k] ? ` <span class="tag o">其中 ${asy[k]} ${u} 由异步 AI 执行</span>` : ""}</span><div class="qbar o"><i style="width:${hmPct(d, t)}%"></i></div><button class="s" type="button" data-gostage="${s.id}|${k}">看进度与配额</button></li>`; }).join("")}</ol></div>`;
  };
  return `<h2 style="margin:18px 0 8px">研究阶段</h2>${subs.map(block).join("")}`;
}

/** 选中一个子项目后的首屏：它的研究顺序、各阶段的状态和入口。 */
function hmSubView(p, mon, sub, users, o) {
  const idx = mon.subs.findIndex((s) => s.id === sub.id), pm = (users.find((u) => u.id === sub.pm) || {}).name || "未指定";
  return `<div class="head"><div><p class="crumb mut" style="margin:0">S${idx + 1} · ${esc(sub.lang)} · 责任人 ${esc(pm)}</p><h1>S${idx + 1} ${esc(sub.name)}</h1></div></div>${hmStages(p, mon, o, users, sub)}`;
}

/** 总览首屏的完整 HTML 和事件；返回 { html, wire }。 */
async function hmBuild(p, o) {
  const [mon, users] = await Promise.all([api(`/projects/${p.id}/monitor`), api("/users")]);
  if (!mon.published) return null;
  const sub = shSub(p);
  const html = sub ? hmSubView(p, mon, sub, users, o) : `${hmHero(p, mon, o)}${hmStatus(p, o)}${hmTable(p, mon, users)}${hmStages(p, mon, o, users, null)}`;
  const wire = () => {
    document.querySelectorAll(".sn").forEach((n) => { const open = () => { sessionStorage.setItem("sub_" + p.id, n.dataset.sub); render(); }; n.onclick = open; n.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }; });
    document.querySelectorAll("[data-gostage]").forEach((b) => (b.onclick = () => {  // open the stage's page for that sub-project (深访 / 座谈会 start at the recruiting setup)
      const [id, kind] = b.dataset.gostage.split("|"); sessionStorage.setItem("sub_" + p.id, id);
      if (kind !== "quant") sessionStorage.setItem("qmode", kind);
      location.hash = `#/p/${p.id}/${kind === "quant" ? "quant" : "people"}`;
    }));
    const cp = $("#hmcopy"); if (cp) cp.onclick = guard(async () => { const r = await api(`/projects/${p.id}/copy`, { method: "POST" }); await projects(true); toast(`已创建草稿「${r.name}」，在立项向导里继续编辑`); location.hash = `#/p/${r.id}/wiz`; });
  };
  return { html, wire, sub };
}
