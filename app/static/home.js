/* 项目总览首屏（原版星桐）：整体进度大数字 + 各子项目（国家）星图 + 项目状态 + 子项目表；选中一个子项目后看它自己的进度和各方式入口。
   数据来自立项（/plan）和执行监控（/monitor）；没有立项记录的老项目不用这一块（还是原来的总览）。依赖 shell.js 的 shSub / SH_DESIGN。 */
const HM_POS = { 新加坡: [300, 238], 马来西亚: [380, 168], 印尼: [528, 236], 越南: [452, 64], 泰国: [330, 96], 菲律宾: [560, 120], 日本: [560, 46], 韩国: [500, 30],
  中国: [420, 50], 印度: [200, 140], 德国: [70, 62], 沙特: [88, 128], 阿联酋: [150, 208], 美国: [40, 230], 英国: [100, 40] };
const HM_EDGES = [["泰国", "越南"], ["泰国", "马来西亚"], ["马来西亚", "印尼"], ["越南", "印尼"], ["沙特", "泰国"], ["德国", "沙特"], ["沙特", "阿联酋"], ["阿联酋", "泰国"], ["新加坡", "马来西亚"], ["新加坡", "印尼"]];
const HM_STATES = ["草稿", "立项", "招募中", "进行中", "已完成", "已归档"];
const hmPct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/** 每个子项目的数字：它名下的配额行（行标签里带这个国家名）合计。 */
function hmSubStats(mon, sub) {
  const mine = (T) => T.rows.filter((r) => r.label.split(" · ").includes(sub.name));
  const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
  const q = mine(mon.q), i = mine(mon.i), f = mine(mon.f);
  return { qc: sum(q, "ready"), qt: sum(q, "target"), ic: sum(i, "ready"), it: sum(i, "target"), fc: sum(f, "ready"), ft: sum(f, "target") };
}

/** 项目状态：草稿 › 立项 › 招募中 › 进行中 › 已完成 › 已归档。 */
function hmState(p, o) {
  if (p.plan_status === "draft") return 0;
  if (["review", "approved"].includes(p.plan_status)) return 1;
  if (p.closed_at) return 4;
  return o.summary && (o.summary.sessions > 0 || o.summary.done > 0) ? 3 : 2;
}

function hmHero(p, mon, o) {
  const subs = mon.subs, st = subs.map((s) => ({ s, x: hmSubStats(mon, s) }));
  const T = st.reduce((a, { x }) => ({ qc: a.qc + x.qc, qt: a.qt + x.qt, ic: a.ic + x.ic, it: a.it + x.it, fc: a.fc + x.fc, ft: a.ft + x.ft }), { qc: 0, qt: 0, ic: 0, it: 0, fc: 0, ft: 0 });
  const quant = (p.methods || []).includes("quant") || T.qt > 0, main = quant ? [T.qc, T.qt, "定量完成", "份"] : [T.ic, T.it, "深访就绪", "人"];
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
    <dl class="meters">${quant ? meter("深访", T.ic, T.it, "人") : ""}${meter("座谈会", T.fc, T.ft, "人")}${(p.methods || []).includes("listening") ? `<div class="meter"><dt>舆情</dt><dd></dd><dd>${(o.starmap.find((n) => n.key === "listening") || {}).big ?? 0} 条内容</dd></div>` : ""}</dl></div>
    <figure class="hero-r"><svg viewBox="0 0 640 300" preserveAspectRatio="xMidYMid meet" role="group" aria-label="各子项目的完成度">${dots.join("")}${edges}${nodes}</svg><figcaption class="hero-cap">圆环是完成度，点一个国家进入它的子项目</figcaption></figure></section>`;
}

function hmStatus(p, o) {
  const cur = hmState(p, o);
  return `<div class="row hm-status"><b>项目状态</b>${HM_STATES.map((x, i) => `<span class="hm-st ${i === cur ? "on" : i < cur ? "done" : ""}">${x}</span>`).join('<span class="mut">›</span>')}<span class="sp"></span><button class="s" id="hmcopy" type="button">复制项目</button></div>`;
}

function hmTable(p, mon, users) {
  const pm = (id) => (users.find((u) => u.id === id) || {}).name || "未指定", D = (k) => (SH_DESIGN[k] || SH_DESIGN.ql)[0];
  const go = (sub, page) => `data-gosub="${sub}|${page}"`;
  return `<div class="card"><div class="scroll"><table class="tbl"><thead><tr><th>子项目</th><th>研究顺序</th><th>定量</th><th>定性</th><th>进入</th></tr></thead><tbody>
    ${mon.subs.map((s, i) => { const x = hmSubStats(mon, s); return `<tr><td><b>S${i + 1}</b> ${esc(s.name)}<div class="sm mut">${esc(s.lang)} · ${esc(pm(s.pm))}</div></td><td><span class="pill o">${D(s.design)}</span></td>
      <td style="min-width:150px">${x.qc} / ${x.qt}<div class="qbar o"><i style="width:${hmPct(x.qc, x.qt)}%"></i></div></td><td>深访 ${x.ic}/${x.it}<br>座谈会 ${x.fc}/${x.ft} 人</td>
      <td><div class="row" style="gap:4px">${(p.methods || []).includes("quant") ? `<button class="s" type="button" ${go(s.id, "quant")}>定量</button>` : ""}<button class="s" type="button" ${go(s.id, "people")}>定性</button>${(p.methods || []).includes("listening") ? `<button class="s" type="button" ${go(s.id, "listening")}>舆情</button>` : ""}</div></td></tr>`; }).join("")}
    </tbody></table></div></div>`;
}

/** 选中一个子项目后的首屏：它的研究顺序步骤、三种方式的进度和入口。 */
function hmSubView(p, mon, sub, users) {
  const x = hmSubStats(mon, sub), idx = mon.subs.findIndex((s) => s.id === sub.id), [dname, stages] = SH_DESIGN[sub.design] || SH_DESIGN.ql;
  const pm = (users.find((u) => u.id === sub.pm) || {}).name || "未指定";
  const page = { ql: "people", qt: "quant" };
  return `<div class="head"><div><p class="crumb mut" style="margin:0">S${idx + 1} · ${esc(sub.lang)} · 责任人 ${esc(pm)} · ${dname}</p><h1>S${idx + 1} ${esc(sub.name)}</h1></div></div>
    <div class="chain hm-chain">${stages.map(([k, n], i) => `<a class="step" href="#/p/${p.id}/${page[k] || "people"}">${i + 1} ${n}</a>`).join("")}</div>
    <div class="grid3"><div class="bx q"><div class="bxh"><i></i>定量</div><div style="font-size:26px;font-weight:700">${x.qc} / ${x.qt}</div><div class="qbar o"><i style="width:${hmPct(x.qc, x.qt)}%"></i></div><a class="btn s" href="#/p/${p.id}/quant">进入定量</a></div>
      <div class="bx i"><div class="bxh"><i></i>定性</div><div>深访 ${x.ic} / ${x.it} 人</div><div>座谈会 ${x.fc} / ${x.ft} 人</div><a class="btn s" href="#/p/${p.id}/people">进入定性</a></div>
      <div class="bx f"><div class="bxh"><i></i>舆情</div><div style="font-size:26px;font-weight:700">${(p.methods || []).includes("listening") ? "已开启" : "未开启"}</div><a class="btn s" href="#/p/${p.id}/listening">进入舆情</a></div></div>`;
}

/** 总览首屏的完整 HTML 和事件；返回 { html, wire }。 */
async function hmBuild(p, o) {
  const [mon, users] = await Promise.all([api(`/projects/${p.id}/monitor`), api("/users")]);
  if (!mon.published) return null;
  const sub = shSub(p);
  const html = sub ? hmSubView(p, mon, sub, users) : `${hmHero(p, mon, o)}${hmStatus(p, o)}${hmTable(p, mon, users)}`;
  const wire = () => {
    document.querySelectorAll(".sn").forEach((n) => { const open = () => { sessionStorage.setItem("sub_" + p.id, n.dataset.sub); render(); }; n.onclick = open; n.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }; });
    document.querySelectorAll("[data-gosub]").forEach((b) => (b.onclick = () => { const [id, page] = b.dataset.gosub.split("|"); sessionStorage.setItem("sub_" + p.id, id); location.hash = `#/p/${p.id}/${page}`; }));
    const cp = $("#hmcopy"); if (cp) cp.onclick = guard(async () => { const r = await api(`/projects/${p.id}/copy`, { method: "POST" }); await projects(true); toast(`已创建草稿「${r.name}」，在立项向导里继续编辑`); location.hash = `#/p/${r.id}/wiz`; });
  };
  return { html, wire, sub };
}
