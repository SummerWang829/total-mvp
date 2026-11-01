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
      ${ks.map((k, i) => { const [d, t, u] = num[k], st = hmStageState(p, o, k, d, t); return `<div class="hm-stage"><div class="hm-l"><div class="row"><span class="sic">${i + 1}</span><span class="hm-name">${SH_KIND[k]}</span></div>
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
    const cp = $("#hmcopy"); if (cp) cp.onclick = guard(async () => { const r = await api(`/projects/${p.id}/copy`, { method: "POST" }); await projects(true); toast(`已创建草稿「${r.name}」，在立项向导里继续编辑`); location.hash = `#/p/${r.id}/wiz`; });
  };
  return { html, wire, sub };
}

/* ---------- 项目语言：项目总览里的一张卡片，随时加或停用语言，立刻生效（不用重新发布） ---------- */
const HM_AI = { true: ["支持", "ok"], false: ["不支持", ""] };
function hmLangCard(info) {
  const rows = info.rows.map((r) => `<tr><td data-l="语言"><b>${esc(r.name)}</b>${r.default ? ` <span class="tag o">默认</span>` : ""}</td>
    <td data-l="同意书"><span class="tag ${r.consent_reviewed ? "ok" : "o"}">${r.consent_reviewed ? "已审" : "文案待复核"}</span></td>
    <td data-l="AI 主持"><span class="tag ${HM_AI[r.ai][1]}">${HM_AI[r.ai][0]}</span></td><td data-l="使用">${r.on ? (r.in_use ? r.in_use + " 场" : "0 场") : "—"}</td>
    <td class="acts" data-l="操作">${!info.can_edit ? "" : r.on ? (r.default ? "" : `<button type="button" class="s" data-lg="default|${r.code}">设为默认</button> <button type="button" class="s" data-lg="off|${r.code}">停用</button>`) : `<button type="button" class="p" data-lg="on|${r.code}">添加</button>`}</td></tr>`).join("");
  return `<div class="card hm-langs" id="hmlang"><h3 style="margin:0 0 8px">语言与受访形式 </h3>
    <table class="tbl rc"><thead><tr><th>语言</th><th>同意书</th><th>AI 主持</th><th>使用</th><th></th></tr></thead><tbody>${rows}</tbody></table>${hmFmtRows(info)}<div class="sm" id="hmlmsg" role="status" aria-live="polite" style="margin-top:6px"></div></div>`;
}
/** The interview formats each live method allows (深访 / 座谈会): tick the kinds candidates may choose; it applies at once. */
function hmFmtRows(info) {
  const names = info.format_names || {}, label = { depth: "深访", group: "座谈会" };
  return Object.keys(info.formats || {}).map((m) => `<div class="hm-fmt"><b>${label[m] || m}允许的访谈形式</b><div class="row" role="group" aria-label="${label[m] || m}允许的访谈形式">${Object.keys(names).map((k) => `<button type="button" class="s hm-chip${info.formats[m].includes(k) ? " on" : ""}" data-fmt="${m}|${k}" aria-pressed="${info.formats[m].includes(k)}"${info.can_edit ? "" : " disabled"}>${esc(names[k])}</button>`).join("")}</div></div>`).join("");
}
function hmLangWire(p, info) {
  const box = $("#hmlang"); if (!box || !info) return;
  box.querySelectorAll("[data-fmt]").forEach((b) => (b.onclick = async () => {
    const [m, k] = b.dataset.fmt.split("|"), now = info.formats[m], next = now.includes(k) ? now.filter((x) => x !== k) : [...now, k];
    try {
      const r = await api(`/projects/${p.id}/languages`, { method: "PUT", body: { formats: { [m]: next } } });
      Object.assign(info, r); box.outerHTML = hmLangCard(info); hmLangWire(p, info);
      const msg = $("#hmlmsg"); if (msg) msg.textContent = `${m === "depth" ? "深访" : "座谈会"}的访谈形式已更新，还没作答的候选人会看到新的选项。`;
    } catch (e) { const msg = $("#hmlmsg"); if (msg) msg.textContent = e.message; toast(e.message, true); }
  }));
  box.querySelectorAll("[data-lg]").forEach((b) => (b.onclick = async () => {
    const [act, code] = b.dataset.lg.split("|"), on = info.languages.slice();
    let langs = on, def = info.default;
    if (act === "on") langs = [...on, code]; else if (act === "off") langs = on.filter((x) => x !== code); else def = code;
    try {
      const r = await api(`/projects/${p.id}/languages`, { method: "PUT", body: { languages: langs, default: def } });
      Object.assign(info, r); box.outerHTML = hmLangCard(info); hmLangWire(p, info);
      const m = $("#hmlmsg"); const row = r.rows.find((x) => x.code === code);
      if (m && row) m.textContent = act === "on" ? (row.consent_reviewed ? `已添加${row.name}，邀请函和同意书里受访者可以选它。` : `已添加${row.name}。同意书文案还没经法务复核，请到团队里找人复核。`) : act === "off" ? `已停用${row.name}，已经开始的访谈不受影响。` : `默认访谈语言已改为${row.name}。`;
    } catch (e) { const m = $("#hmlmsg"); if (m) m.textContent = e.message; toast(e.message, true); }
  }));
}
