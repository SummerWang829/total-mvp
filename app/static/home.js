/* 项目总览（有立项的项目）：只有“研究阶段”——每个阶段一行，左边是阶段、状态和进度，右边直接是这个阶段的配额图表；选中一个子项目后只看它自己的阶段。
   数据来自立项（/plan）和执行监控（/monitor）；没有立项记录的老项目不用这一块（还是原来的总览）。依赖 shell.js 的 shSub / SH_DESIGN。 */
const hmPct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);

/** 每个子项目的数字：它名下的配额行（行标签里带这个国家名）合计。 */
function hmSubStats(mon, sub) {
  const mine = (T) => (mon.subs.length === 1 ? T.rows : T.rows.filter((r) => r.label.split(" · ").includes(sub.name)));  // one sub-project owns every quota row
  const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
  const q = mine(mon.q), i = mine(mon.i), f = mine(mon.f), marked = (rows) => rows.filter((r) => r.async);  // marked = the groups the client chose for the asynchronous AI
  return { qc: sum(q, "ready"), qt: sum(q, "target"), ic: sum(i, "ready"), it: sum(i, "target"), fc: sum(f, "ready"), ft: sum(f, "target"), ia: sum(marked(i), "target"), fa: sum(marked(f), "target") };
}

/** 一个阶段（某个子项目的某种方法）现在的状态：招募中 / 进行中 / 已完成 / 已归档。 */
function hmStageState(p, o, kind, done, target) {
  if (p.closed_at) return p.purge_at && new Date(p.purge_at + "Z") < new Date() ? "已归档" : "已完成";
  if (target > 0 && done >= target) return "已完成";
  return done > 0 || (kind !== "quant" && o.summary && o.summary.sessions > 0) ? "进行中" : "招募中";
}

const HM_TABLE = { quant: "q", depth: "i", group: "f" }, HM_TITLE = { quant: "定量配额", depth: "深访配额", group: "座谈会配额" };

/** 右边：这个阶段的配额图表（每行一条进度条）；没有配额行时只有一条总进度和提示。 */
function hmChart(mon, kind, sub, only, d, t, unit) {
  const T = mon[HM_TABLE[kind]];
  let rows = T.rows.filter((r) => r.label.split(" · ").includes(sub.name));
  if (!rows.length && only) rows = T.rows;  // a quota not split by country belongs to the only sub-project
  const head = `<div class="sm"><b>${HM_TITLE[kind]}</b>${T.dims.length ? `　按 ${T.dims.map(esc).join(" × ")}` : ""}</div>`;
  if (!rows.length) return `${head}<div class="hm-row"><span>合计</span><div class="qbar o"><i style="width:${hmPct(d, t)}%"></i></div><span>${d} / ${t} ${unit}</span></div><div class="sm mut">还没有配额行：到立项向导第 4 步添加（锁定后请管理员或项目经理先解锁）。</div>`;
  return head + rows.map((r) => `<div class="hm-row"><span title="${esc(r.label)}">${esc(r.label.split(" · ").filter((x) => x !== sub.name).join(" · ") || r.label)}</span><div class="qbar o" role="img" aria-label="${esc(r.label)} ${r.ready} / ${r.target}"><i style="width:${hmPct(r.ready, r.target)}%"></i></div>
    <span>${r.ready} / ${r.target}${r.async ? ` <span class="tag o nw">异步 AI</span>` : ""}</span></div>`).join("");
}

/** 研究阶段：每个子项目按自己的研究顺序列出各阶段；左边阶段名（点开进入对应页面）、状态、进度，右边直接是配额图表。 */
function hmStages(p, mon, o, users, one) {
  const subs = one ? [one] : mon.subs, many = subs.length > 1, pm = (id) => (users.find((u) => u.id === id) || {}).name || "未指定";
  const block = (s) => {
    const x = hmSubStats(mon, s), ks = (SH_DESIGN[s.design] || SH_DESIGN.iq)[1], num = { quant: [x.qc, x.qt, "份"], depth: [x.ic, x.it, "人"], group: [x.fc, x.ft, "人"] };
    return `${many ? `<div class="row hm-sub"><b>${esc(s.name)}</b><span class="mut sm">${SH_DESIGN[s.design] ? SH_DESIGN[s.design][0] : ""} · 责任人 ${esc(pm(s.pm))}${s.listening ? " · 舆情" : ""}${s.async_ai ? " · 异步 AI" : ""}</span></div>` : ""}
      ${ks.map((k, i) => { const [d, t, u] = num[k], st = hmStageState(p, o, k, d, t); return `<div class="hm-stage"><div class="hm-l"><div class="row"><span class="sic">${i + 1}</span><button class="lnk hm-name" type="button" data-gostage="${s.id}|${k}">${SH_KIND[k]}</button></div>
        <div><span class="pill ${st === "招募中" ? "o" : st === "进行中" ? "" : "g"}">${st}</span></div><div class="mut sm">${d} / ${t} ${u}</div><div class="mut sm">${esc(s.name)} · 责任人 ${esc(pm(s.pm))}</div></div>
        <div class="hm-r">${hmChart(mon, k, s, mon.subs.length === 1, d, t, u)}</div></div>`; }).join("")}`;
  };
  return `<h2 style="margin:6px 0 10px">研究阶段</h2>${subs.map(block).join("")}`;
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
  const picked = shSub(p), sub = picked ? mon.subs.find((s) => s.id === picked.id) || null : null;  // the full sub-project (language, responsible person), not only the menu's summary
  const html = sub ? hmSubView(p, mon, sub, users, o) : hmStages(p, mon, o, users, null);
  const wire = () => {
    document.querySelectorAll("[data-gostage]").forEach((b) => (b.onclick = () => {  // open the stage's page for that sub-project (深访 / 座谈会 start at the recruiting setup)
      const [id, kind] = b.dataset.gostage.split("|"); sessionStorage.setItem("sub_" + p.id, id);
      if (kind !== "quant") sessionStorage.setItem("qmode", kind);
      location.hash = `#/p/${p.id}/${kind === "quant" ? "quant" : "people"}`;
    }));
    const cp = $("#hmcopy"); if (cp) cp.onclick = guard(async () => { const r = await api(`/projects/${p.id}/copy`, { method: "POST" }); await projects(true); toast(`已创建草稿「${r.name}」，在立项向导里继续编辑`); location.hash = `#/p/${r.id}/wiz`; });
  };
  return { html, wire, sub };
}
