/* 项目外壳：最左窄栏 + 顶部项目条（项目号、名称、状态、研究阶段、模块、子项目）+ 第二层菜单（一级、二级）。
   依赖 index.html 里的 esc / STAFF / ME / PROJECTS / ROLE / DEMO_ORG 和 wizard.js 里的 WZ_DESIGNS（8 种研究顺序）。 */
const SH_DESIGN = WZ_DESIGNS;  // key -> [label, [method kinds in order]]
const SH_KIND = { depth: "深访", group: "座谈会", quant: "定量" };
const SH_HUBS = [
  { id: "ov", n: "项目总览", tabs: [["home", "总览"], ["monitor", "执行监控"], ["pool", "洞察池"], ["members", "项目成员"]] },
  { id: "wz", n: "立项向导", tabs: [["wiz", "立项向导"]] },
  { id: "ql", n: "定性", tabs: [["people", "创建访谈"], ["outline", "访谈大纲"], ["rooms", "访谈直播"], ["insights", "洞察"], ["studies", "异步研究"]] },   // 用户 2026-11-04：甄别设定与访谈合并成“创建访谈”，直播间改“访谈直播”
  { id: "qt", n: "定量", tabs: [["quant", "定量结果"]] },
  { id: "cr", n: "舆情采集", tabs: [["listening", "舆情监测"]] },
];
const SH_COUNTRY = { 新加坡: "SG", 马来西亚: "MY", 印尼: "ID", 越南: "VN", 泰国: "TH", 菲律宾: "PH", 中国: "CN", 日本: "JP", 韩国: "KR", 德国: "DE", 沙特: "SA", 阿联酋: "AE", 美国: "US", 英国: "GB", 印度: "IN" };
const shHubOf = (page) => (SH_HUBS.find((h) => h.tabs.some((t) => t[0] === page)) || { id: "" }).id;  // "" for the settings pages (team, data retention, audit log)
const shSetup = (p) => !!p.plan_status && p.plan_status !== "published";

function shPageOk(page, p) {  // which pages this project has (the methods it uses decide)
  const M = p.methods || ["depth", "group", "async", "listening"], live = M.includes("depth") || M.includes("group");
  return { outline: live, rooms: live, studies: M.includes("async"), quant: M.includes("quant"), listening: M.includes("listening") }[page] ?? true;
}
const shNumber = (p) => { const all = (PROJECTS || []).slice().sort((a, b) => a.created_at.localeCompare(b.created_at)), i = all.findIndex((x) => x.id === p.id); return `${(p.created_at || "").slice(0, 4)}P${String((i < 0 ? all.length : i) + 1).padStart(3, "0")}`; };
const shStatus = (p) => (shSetup(p) ? [{ draft: "草稿", review: "审查中", approved: "待发布" }[p.plan_status] || "立项中", "o"] : p.closed_at ? ["已结项", "g"] : p.editing ? ["修改中", "o"] : ["进行中", ""]);
const shQmode = () => sessionStorage.getItem("qmode") || "";
const shSub = (p) => { const id = sessionStorage.getItem("sub_" + p.id) || "all"; return (p.setup && p.setup.subs.find((s) => s.id === id)) || null; };

/** The methods of the chosen sub-project in the order it runs them; for the whole project, every sub-project's methods (first appearance first). */
function shKinds(p) {
  const subs = (p.setup && p.setup.subs) || [], sub = shSub(p), out = [];
  (sub ? [sub] : subs).forEach((s) => (SH_DESIGN[s.design] || SH_DESIGN.iq)[1].forEach((k) => { if (!out.includes(k)) out.push(k); }));
  if (!out.length) ["depth", "group", "quant"].forEach((k) => { if ((p.methods || ["depth", "group"]).includes(k)) out.push(k); });  // a project made before plans existed: whatever methods it has
  return out.length ? out : ["depth"];
}
function shDesignName(p) {
  const subs = (p.setup && p.setup.subs) || [], sub = shSub(p), use = sub ? [sub] : subs, names = [...new Set(use.map((s) => (SH_DESIGN[s.design] || SH_DESIGN.iq)[0]))];
  return names.length === 1 ? names[0] : names.length ? "各子项目研究顺序不同" : "";
}

/** 子项目和访问方式的过滤：场次/直播间按受访者国家，按深访/座谈会。 */
function shFilterInterviews(p, its) {
  const sub = shSub(p), code = sub ? SH_COUNTRY[sub.name] : "", qm = shQmode();
  return its.filter((i) => (!qm || i.kind === qm) && (!code || !i.participant || !i.participant.country || i.participant.country === code));
}


/* ================= 外壳（2026-11-03，用户确认效果图 v14 平面版）=================
   三块固定位置：最左 88px 窄栏（凤凰、项目、研究库、设置、账号）；顶部项目条（项目号/名称/状态 · 中间各阶段与模块 · 右侧子项目）；
   进入项目后，窄栏右边有第二层菜单（一级菜单，当前项下面缩进展开页面自己的二级菜单）。切换页面只替换内容区，这三块不重建、不跳动。
   页面自己的二级菜单（直接子元素 `.hubtabs.sub`，或任意位置带 data-subrow 的元素）由 shell 搬进第二层菜单里当前一级菜单下面（节点不变，事件照常）。 */
/* 图标：同一套细线（1.5）几何线条，关键形状带一层很淡的同色底，24×24 */
const duo = (d) => `<path class="f" d="${d}"/><path d="${d}"/>`;
const rect = (x, y, w, h, r) => `<rect class="f" x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
const disc = (x, y, r) => `<circle class="f" cx="${x}" cy="${y}" r="${r}"/><circle cx="${x}" cy="${y}" r="${r}"/>`;
const VI = {
  folder: duo("M3.5 8a2 2 0 0 1 2-2h3.6l2 2.2h7.4a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"),
  book: duo("M4 5.5c2.7-.6 5.3-.2 8 1.5v12c-2.7-1.7-5.3-2.1-8-1.5z") + duo("M20 5.5c-2.7-.6-5.3-.2-8 1.5v12c2.7-1.7 5.3-2.1 8-1.5z"),
  gear: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/>' + disc(15, 7, 2) + disc(9, 17, 2),
  flag: '<path d="M6 20.5V4.5"/>' + duo("M6 5h11l-2.5 3.5L17 12H6"),
  grid: rect(4, 4, 6.5, 6.5, 1.5) + rect(13.5, 4, 6.5, 6.5, 1.5) + rect(4, 13.5, 6.5, 6.5, 1.5) + rect(13.5, 13.5, 6.5, 6.5, 1.5),
  chat: duo("M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-8l-4 3v-3H5A1.5 1.5 0 0 1 3.5 15V7A1.5 1.5 0 0 1 5 5.5z"),
  users: disc(9, 8.5, 3) + '<path d="M3.5 19a5.5 5.5 0 0 1 11 0M15.5 5.8a3 3 0 0 1 0 5.4M17.5 14.2a5.5 5.5 0 0 1 3 4.8"/>',
  pie: duo("M12 3.5v8.5h8.5A8.5 8.5 0 1 1 12 3.5z") + '<path d="M15 3.8a8.5 8.5 0 0 1 5.2 5.2H15z"/>',
  spark: duo("M12 3.5l2 5.5 5.5 2-5.5 2-2 5.5-2-5.5-5.5-2 5.5-2z"),
  listen: disc(12, 12, 1.8) + '<path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
  user: disc(9.5, 8.5, 3.2) + '<path d="M3.5 19.5a6 6 0 0 1 12 0M16 11.5l2 2 3.5-3.8"/>',
  cam: rect(3.5, 6.5, 11, 11, 2) + '<path d="M14.5 11l6-3.2v8.4l-6-3.2"/>',
  live: disc(12, 12, 2.4) + '<path d="M7.5 7.5a6.4 6.4 0 0 0 0 9M16.5 7.5a6.4 6.4 0 0 1 0 9"/>',
  bulb: duo("M12 3.5a5.5 5.5 0 0 0-3.2 10c.8.6 1.2 1.4 1.2 2.4V16h4v-.1c0-1 .4-1.8 1.2-2.4A5.5 5.5 0 0 0 12 3.5z") + '<path d="M10 19h4M10.8 21h2.4"/>',
  home: duo("M4 11l8-6.5 8 6.5v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z") + '<path d="M10 20v-5h4v5"/>',
  pulse: '<path d="M3.5 12h4l2.5-6 4 12 2.5-6h4"/>',
  clock: disc(12, 12, 8.5) + '<path d="M12 7.5V12l3 2"/>',
  doc: duo("M6 3.5h8l4 4v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z") + '<path d="M14 3.5v4h4M8.5 12.5h7M8.5 16h5"/>',
  undo: '<path d="M4 8v5h5"/><path d="M5.2 13a7.5 7.5 0 1 0 2-6.2"/>',
  wiz: duo("M6 5.5h12a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6.5a1 1 0 0 1 1-1z") + '<rect x="9" y="3" width="6" height="3.5" rx="1"/><path d="M8.5 11.5h7M8.5 15h4"/>',
};
const vIc = (k) => `<svg class="vi" viewBox="0 0 24 24" aria-hidden="true">${VI[k] || ""}</svg>`;
const SH_ICON = { people: "user", outline: "cam", rooms: "live", insights: "bulb", home: "home", monitor: "pulse", pool: "book", members: "users", quant: "pie", listening: "listen", studies: "spark", wiz: "wiz" };
const SH_STAGE_ICON = { depth: "chat", group: "users", quant: "pie" };
const SH_SETTINGS = [["team", "团队", "users", "/team"], ["retention", "数据保留", "clock", "/retention"], ["audit", "审计日志", "doc", "/audit"], ["ai", "AI 后台", "spark", "/ai"]];
const SH_TITLES = { projects: "全部项目", repo: "研究库", team: "团队", retention: "数据保留", audit: "审计日志", ai: "AI 后台", demo: "演示数据" };
const shSettingsList = () => SH_SETTINGS.concat(DEMO_ORG ? [["demo", "演示数据", "undo", "/demo"]] : []);

/** 最左窄栏：凤凰（回项目列表）、项目、研究库（团队成员）、设置（只有管理员）。进入项目后收成 12px 细边，鼠标移上去弹出（样式在 ui_v11.css）。 */
function shRail(active) {
  const inSet = shSettingsList().some((s) => s[0] === active);
  const it = (href, ic, label, on) => `<a class="rk${on ? " on" : ""}" href="#${href}" ${on ? 'aria-current="page"' : ""} data-tip="${esc(label)}" aria-label="${esc(label)}">${vIc(ic)}<span>${label}</span></a>`;
  return `<div class="rin"><a class="rlogo" href="#/projects" aria-label="星桐 TOTAL，回到项目列表" title="回到项目列表" data-tip="项目列表"><img src="/logo_phoenix.svg" alt=""></a>`
    + it("/projects", "folder", "项目", !inSet && active !== "repo") + (STAFF() ? it("/repo", "book", "研究库", active === "repo") : "")
    + `<span class="gap"></span>` + (ME.role === "admin" ? it("/team", "gear", "设置", inSet) : "") + `</div>`;
}

/* 图标名称提示（2026-11-03 用户要求）：鼠标停在图标上就显示这个图标叫什么。
   两处需要：① 进入项目后窄栏收成 12px 细边；② 第二层菜单收起（sub-off，只剩图标和圆点）。
   这两处容器都是 overflow:hidden，提示会被裁掉，所以做成 position:fixed 挂在 body 上，跟着图标位置走。 */
(function shRailTip() {
  const SEL = ".rail2 [data-tip], .sub2 [data-tip], .sub2 .l2slot a, .sub2 .l2slot button";
  let tip = null, timer = 0, cur = null;
  const hide = () => { clearTimeout(timer); if (tip) tip.classList.remove("on"); cur = null; };
  const needed = (el) => {   // 图标旁边已经有文字时不用再提示
    const app = document.querySelector(".v11app");
    if (!app) return false;
    return el.closest(".rail2") ? app.classList.contains("thin") : app.classList.contains("sub-off");
  };
  const textOf = (el) => (el.getAttribute("data-tip") || (el.textContent || "")).trim().slice(0, 24);   // 二级项（圆点）没有 data-tip，用它的文字
  const show = (el) => {
    const text = textOf(el);
    if (!text || !needed(el)) return;
    if (!tip) { tip = document.createElement("div"); tip.className = "railtip"; document.body.appendChild(tip); }
    const r = el.getBoundingClientRect();
    tip.textContent = text;
    tip.style.left = Math.round(r.right + 10) + "px";
    tip.style.top = Math.round(r.top + r.height / 2) + "px";
    tip.classList.add("on");
  };
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest && e.target.closest(SEL);
    if (!el) { if (cur) hide(); return; }
    if (el === cur) return;
    hide(); cur = el;
    timer = setTimeout(() => { if (cur === el && el.isConnected) show(el); }, 120);
  });
  document.addEventListener("mouseout", (e) => {
    const el = e.target.closest && e.target.closest(SEL);
    if (el && !el.contains(e.relatedTarget)) hide();
  });
  window.addEventListener("hashchange", hide);
  window.addEventListener("blur", hide);
  document.addEventListener("scroll", hide, true);
})();


/** 右上角的身份：一行“姓名 · 职位 · 公司”（窄屏只留头像，悬停才显示）和头像（点一下看退出）。 */
function shWho() {
  const line = `${esc(ME.name)} · ${ROLE[ME.role]} · ${esc(ME.org_name)}`, initial = esc((ME.name || "?").trim().slice(0, 1).toUpperCase());
  return `<span class="who">${line}</span><div class="me2"><button type="button" class="av" id="mebtn" aria-haspopup="true" aria-expanded="false" aria-label="账号：${esc(ME.name)}">${initial}</button><span class="whotip" role="tooltip">${line}</span>`
    + `<div class="mecard2" id="mecard" hidden><b>${esc(ME.name)}</b><i>${ROLE[ME.role]}</i><em>${esc(ME.org_name)}</em><button type="button" id="out">退出</button></div></div>`;
}

/** 研究阶段（深访 → 座谈会 → 定量……）：有几个阶段就带编号和箭头，当前阶段橙色、已走完的打勾。 */
function shStages(p, active) {
  const M = p.methods || [], hub = shHubOf(active), qm = shQmode(), kinds = shKinds(p), firstQual = kinds.find((k) => k !== "quant");
  const cur = hub === "qt" ? "quant" : hub === "ql" && active !== "studies" ? (qm && kinds.includes(qm) ? qm : firstQual) : "", idxOn = kinds.indexOf(cur);
  return kinds.map((k, i) => {
    const done = idxOn >= 0 && i < idxOn, body = `${kinds.length > 1 ? `<i class="n">${done ? "✓" : i + 1}</i>` : vIc(SH_STAGE_ICON[k])}<span>${SH_KIND[k]}</span>`, cls = `mod stg${done ? " done" : ""}${i === idxOn ? " on" : ""}`;
    const one = M.includes(k) ? `<a class="${cls}" href="#/p/${p.id}/${k === "quant" ? "quant" : "people"}" ${i === idxOn ? 'aria-current="page"' : ""} ${k === "quant" ? "" : `data-qmode="${k}"`}>${body}</a>` : `<span class="mod stg off">${body}</span>`;   // 深访 / 座谈会 open the recruiting setup first
    return one + (i < kinds.length - 1 ? `<span class="arr" aria-hidden="true">→</span>` : "");
  }).join("");
}

/** 顶部项目条里的各个模块（中间）：因身份而异。 */
function shModules(p, active) {
  const hub = shHubOf(active), M = p.methods || ["depth", "group", "async", "listening"], live = M.includes("depth") || M.includes("group");
  const a = (page, label, ic, on) => `<a class="mod${on ? " on" : ""}" href="#/p/${p.id}/${page}" ${on ? 'aria-current="page"' : ""}>${vIc(ic)}<span>${label}</span></a>`;
  if (STAFF()) {
    if (shSetup(p)) return a("wiz", "立项向导", "flag", true);   // still in the wizard: nothing else to open yet
    return a("wiz", "立项向导", "flag", hub === "wz") + a("home", "项目总览", "grid", hub === "ov") + shStages(p, active)
      + (M.includes("async") ? a("studies", "异步研究", "spark", active === "studies") : "") + (M.includes("listening") ? a("listening", "舆情采集", "listen", hub === "cr") : "");
  }
  if (VIEWER()) return a("pool", "洞察池", "book", active === "pool") + (M.includes("listening") ? a("listening", "舆情监测", "listen", active === "listening") : "") + (M.includes("quant") ? a("quant", "定量结果", "pie", active === "quant") : "");
  if (RECRUITER()) return (p.plan_status ? a("wiz", "立项向导", "flag", active === "wiz") : "") + a("people", "创建访谈", "user", active === "people");
  return (live ? a("rooms", "访谈直播", "live", active === "rooms") : "") + a("insights", "洞察", "bulb", active === "insights");   // moderators and observers
}

/** 顶部：项目页是“项目图标 / 项目列表 › 项目号 / 名称 / 状态 | 模块 | 子项目”，其他页只有标题。 */
function shTop(p, active, title) {
  if (!p) return `<div class="pinfo"><span class="nm pname">${esc(SH_TITLES[active] || title || "")}</span></div><div class="mods"></div><div class="tr">${shWho()}</div>`;
  const [stLabel, stCls] = shStatus(p), subs = (p.setup && p.setup.subs) || [], sub = shSub(p);
  const sel = STAFF() && subs.length > 1 && !shSetup(p) ? `<label class="ctx-sub"><span>子项目</span><select id="subsel" aria-label="切换子项目"><option value="all" ${sub ? "" : "selected"}>总项目（全部 ${subs.length} 个）</option>${subs.map((s, i) => `<option value="${s.id}" ${sub && sub.id === s.id ? "selected" : ""}>S${i + 1} ${esc(s.name)}</option>`).join("")}</select></label>` : "";
  return `<span class="ptile" aria-hidden="true">${esc((p.name || "项").trim().slice(0, 1))}</span><div class="pinfo"><span class="bc"><a href="#/projects">项目列表</a> / ${shNumber(p)}</span><div class="r2"><span class="nm">${esc(p.name)}</span><span class="pill ${stCls}">${stLabel}</span></div></div>`
    + `<div class="mods" role="navigation" aria-label="项目模块">${shModules(p, active)}</div><div class="tr">${sel}${shWho()}</div>`;
}

/** 第二层菜单的内容：{head, items, slot}；没有就不显示这一层。 */
function shSubMenu(p, active) {
  const ic = (k) => SH_ICON[k] || "grid", it = (page, label) => ({ href: `/p/${p.id}/${page}`, key: page, label, ic: ic(page) });
  if (!p) {
    const list = shSettingsList();
    if (ME.role === "admin" && list.some((s) => s[0] === active)) return { head: { ic: "gear", title: "设置", meta: "" }, items: list.map(([k, n, i, h]) => ({ href: h, key: k, label: n, ic: i })) };
    return null;
  }
  const [stLabel] = shStatus(p), hub = shHubOf(active), M = p.methods || ["depth", "group", "async", "listening"], live = M.includes("depth") || M.includes("group");
  if (STAFF()) {
    const kinds = shKinds(p), qm = shQmode(), cur = hub === "ql" && active !== "studies" ? (qm && kinds.includes(qm) ? qm : kinds.find((k) => k !== "quant")) : hub === "qt" ? "quant" : "";
    let head, items = [], extra = null, onKey = active;
    if (hub === "wz" || shSetup(p)) head = { ic: "wiz", title: "立项向导", meta: stLabel };
    else if (hub === "ov") { head = { ic: "grid", title: "项目总览", meta: stLabel }; items = SH_HUBS[0].tabs.filter((t) => shPageOk(t[0], p)).map(([k, n]) => it(k, n)); }
    else if (hub === "ql" && active !== "studies") {
      const merged = active === "people" || active === "outline";   // 这两页共用“创建访谈”一个入口
      head = { ic: SH_STAGE_ICON[cur] || "chat", title: SH_KIND[cur] || "深访", meta: kinds.length > 1 ? `阶段 ${kinds.indexOf(cur) + 1} · ${stLabel}` : stLabel };
      items = SH_HUBS[2].tabs.filter((t) => t[0] !== "studies" && t[0] !== "outline" && shPageOk(t[0], p)).map(([k, n]) => it(k, n));
      if (merged) { onKey = "people"; }   // 「访谈大纲」已并入「创建访谈」下的「访问大纲生成及邀请」tab，不再单独加菜单项
    }
    else head = { ic: hub === "qt" ? "pie" : hub === "cr" ? "listen" : "spark", title: hub === "qt" ? "定量" : hub === "cr" ? "舆情采集" : "异步研究", meta: stLabel };
    return { head, items, staff: true, extra, onKey };
  }
  if (RECRUITER() && active === "people") return { head: { ic: "user", title: "创建访谈", meta: stLabel }, items: [it("people", "创建访谈")], slot: true };
  if (!VIEWER() && !RECRUITER()) return { head: { ic: "live", title: "访谈直播", meta: stLabel }, items: (live ? [it("rooms", "访谈直播")] : []).concat([it("insights", "洞察")]), slot: true };
  return null;
}
function shSubHtml(p, active, menu) {
  if (!menu) return "";
  const h = menu.head, on = menu.items.find((i) => i.key === (menu.onKey || active));
  const close = p && menu.staff && !shSetup(p) ? (p.closed_at ? `<button class="s" id="reopen" aria-label="重新打开" data-tip="重新打开"><span class="cf">重新打开</span><span class="cs">开</span></button>` : `<button class="s" id="closeproj" aria-label="结项" title="从结项那天起 ${p.retention_days} 天，受访者数据会自动删除" data-tip="结项"><span class="cf">结项</span><span class="cs">结</span></button>`) : "";
  return `<div class="shead"><span class="stile">${vIc(h.ic)}</span><div><b>${esc(h.title)}</b>${h.meta ? `<small>${esc(h.meta)}</small>` : ""}</div></div>`
    + `<div class="mn">${menu.items.map((i) => `<a class="l1${i === on ? " on" : ""}" href="#${i.href}" ${i === on ? 'aria-current="page"' : ""} data-tip="${esc(i.label)}"><span class="it">${vIc(i.ic)}</span><span class="tx">${i.label}</span></a>${i === on ? `<div class="l2slot" id="l2slot">${(menu.extra || []).map((x) => `<a class="l2e${x.on ? " on" : ""}" href="#${x.href}"${x.on ? ' aria-current="page"' : ""}>${x.label}</a>`).join("")}</div>` : ""}`).join("")}</div><div class="bot"><button type="button" class="fold" aria-label="收起或展开这一栏" aria-expanded="true" data-tip="收起这一栏"></button>${close}</div>`;
}

function shWire(p) {
  const sel = document.getElementById("subsel");
  if (sel) sel.onchange = () => { sessionStorage.setItem("sub_" + p.id, sel.value); render(); };
  document.querySelectorAll("[data-qmode]").forEach((el) => (el.onclick = () => { sessionStorage.setItem("qmode", el.dataset.qmode); sessionStorage.setItem("ptab", "mode"); if (location.hash === el.getAttribute("href")) render(); }));   // 从「深访 / 座谈会」阶段进来，第一个部分一定落在「招募方式」
}

/** 固定外壳：窄栏、顶部、第二层菜单是三个固定位置的节点；切换页面时没变的不动，只替换内容区。 */
function shMount({ rail, top, sub, notice, inner, cls, thin }) {
  let a = app.firstElementChild;
  if (!(a && a.classList.contains("v11app"))) {
    app.innerHTML = `<div class="v11app"><nav class="rail2" aria-label="主导航"></nav><div class="v11main"><header class="top2"></header><div class="body2"><aside class="sub2" aria-label="本页菜单" hidden></aside><main></main></div></div></div>`;
    a = app.firstElementChild;
    const was = (k) => { try { return !!localStorage.getItem(k); } catch (e) { return false; } };   // the fold choice is only a convenience of this browser
    const keep = (k, on) => { try { localStorage.setItem(k, on ? "1" : ""); } catch (e) { /* private window: this page only */ } };
    a.classList.toggle("sub-off", was("tt_fold_sub"));
    a.addEventListener("click", (e) => { const b = e.target.closest(".sub2 .fold"); if (!b) return; const off = !a.classList.contains("sub-off"); a.classList.toggle("sub-off", off); keep("tt_fold_sub", off); a.querySelectorAll(".sub2 .fold").forEach((x) => x.setAttribute("aria-expanded", String(!off))); });
  }
  const set = (el, h) => { if (el._h !== h) { el.innerHTML = h; el._h = h; } };
  const aside = a.querySelector(".sub2"), main = a.querySelector(".body2>main");
  set(a.querySelector(".rail2"), rail); set(a.querySelector(".top2"), top); aside.hidden = !sub; if (sub) set(aside, sub);
  a.classList.toggle("has-sub", !!sub); a.classList.toggle("thin", !!thin);
  a.querySelectorAll(".sub2 .fold").forEach((x) => x.setAttribute("aria-expanded", String(!a.classList.contains("sub-off"))));
  main.className = cls; main.innerHTML = notice + inner; main.scrollTop = 0;
  shMoveSub(a);
}
function shMoveSub(a) {   // the page's own second-level controls go under the current first-level menu item
  const slot = a.querySelector(".sub2 .l2slot"), main = a.querySelector(".body2>main"); if (!slot || !main) return;
  slot.querySelectorAll(":scope > .mvd").forEach((e) => e.remove());   // 只清上一次搬进来的；静态二级（.l2e）保留
  const anchor = slot.querySelector(":scope > .l2e");
  main.querySelectorAll(":scope > .hubtabs.sub, [data-subrow]").forEach((e) => {   // a direct .hubtabs.sub, or any element marked data-subrow (at any depth)
    e.classList.add("mvd");
    if (anchor) slot.insertBefore(e, anchor); else slot.appendChild(e);
  });
  slot.hidden = !slot.firstChild;
}
