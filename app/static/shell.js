/* 项目外壳（原版星桐的结构）：左侧按“研究阶段”分组的导航 + 顶部项目条（项目号、名称、状态、子项目、研究路径）+ 页签。
   依赖 index.html 里的 esc / STAFF / ME / PROJECTS 和 wizard.js 里的 WZ_DESIGNS（8 种研究顺序）。研究员/管理员用这套；客户、项目经理、主持人、观察员保留各自更简单的导航。 */
const SH_DESIGN = WZ_DESIGNS;  // key -> [label, [method kinds in order]]
const SH_KIND = { depth: "深访", group: "座谈会", quant: "定量" };
const SH_HUBS = [
  { id: "ov", n: "项目总览", tabs: [["home", "总览"], ["monitor", "执行监控"], ["pool", "洞察池"], ["members", "项目成员"]] },
  { id: "wz", n: "立项向导", tabs: [["wiz", "立项向导"]] },
  { id: "ql", n: "定性", tabs: [["people", "甄别设定"], ["outline", "访谈"], ["rooms", "直播间"], ["insights", "洞察与片段"], ["studies", "异步研究"]] },
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

function shNavStage(p, active) {
  const M = p.methods || [], hub = shHubOf(active), qm = shQmode(), kinds = shKinds(p), firstQual = kinds.find((k) => k !== "quant");
  const a = (href, label, on, cls = "", attrs = "") => `<a class="nv ${cls}" href="#${href}" ${on ? 'aria-current="page"' : ""} ${attrs}>${label}</a>`;
  let h = `<div class="sec">研究阶段 · ${shDesignName(p)}</div>`;
  kinds.forEach((k, i) => {
    const c = "c" + (i + 1), head = `<span class="sic">${i + 1}</span><span>${SH_KIND[k]}</span>`;
    if (k === "quant") { h += M.includes("quant") ? a(`/p/${p.id}/quant`, head, active === "quant", `stg ${c}`) : `<div class="stghead ${c} off">${head}</div>`; return; }
    if (!M.includes(k)) { h += `<div class="stghead ${c} off">${head}</div>`; return; }
    const on = hub === "ql" && active !== "studies" && (qm && kinds.includes(qm) ? qm === k : k === firstQual);  // no choice made yet: the first interview method
    h += a(`/p/${p.id}/people`, head, on, `stg ${c}`, `data-qmode="${k}"`);  // 深访 / 座谈会 open the recruiting setup first
  });
  return h;
}

/** 左侧导航（研究员/管理员，已发布的项目）：立项向导在项目总览前面。 */
function shNav(p, active) {
  const hub = shHubOf(active), a = (href, label, on, ic) => `<a class="nv" href="#${href}" ${on ? 'aria-current="page"' : ""}><span class="ic" aria-hidden="true">${ic}</span><span>${label}</span></a>`;
  let h = a(`/p/${p.id}/wiz`, "立项向导", hub === "wz", "✎") + a(`/p/${p.id}/home`, "项目总览", hub === "ov", "◉");
  h += shNavStage(p, active);
  const M = p.methods || [];
  if (M.includes("async") || M.includes("listening")) h += `<div class="sec">其他</div>`;
  if (M.includes("async")) h += a(`/p/${p.id}/studies`, "异步 AI 研究", active === "studies", "✦");
  if (M.includes("listening")) h += a(`/p/${p.id}/listening`, "舆情采集", hub === "cr", "◔");
  return h;
}

/** 顶部项目条：项目号、名称、状态、子项目选择、研究路径、访问方式。 */
function shCtx(p, active) {
  const [stLabel, stCls] = shStatus(p), hub = shHubOf(active), subs = (p.setup && p.setup.subs) || [], sub = shSub(p), kinds = shKinds(p), qm = shQmode();
  const cur = hub === "qt" ? "quant" : hub === "ql" ? (qm && kinds.includes(qm) ? qm : kinds.find((k) => k !== "quant")) : "", idxOn = kinds.indexOf(cur);
  const modes = [["定量", "quant"], ["深访", "depth"], ["座谈会", "group"], ["舆情", "listening"]].filter(([, m]) => (p.methods || []).includes(m));
  const hi = { quant: ["quant"], listening: ["listening"], pool: [], home: [], monitor: [] }[active] || (hub === "ql" ? (qm === "group" ? ["group"] : qm === "depth" ? ["depth"] : ["depth", "group"]) : []);
  const close = STAFF() && !shSetup(p) ? (p.closed_at ? `<button class="s" id="reopen">重新打开</button>` : `<button class="s" id="closeproj" title="从结项那天起 ${p.retention_days} 天，受访者数据会自动删除">结项</button>`) : "";
  const sel = subs.length > 1 && !shSetup(p) ? `<label class="ctx-sub"><span>子项目</span><select id="subsel" aria-label="切换子项目"><option value="all" ${sub ? "" : "selected"}>总项目（全部 ${subs.length} 个）</option>${subs.map((s, i) => `<option value="${s.id}" ${sub && sub.id === s.id ? "selected" : ""}>S${i + 1} ${esc(s.name)}</option>`).join("")}</select></label>` : "";
  const track = shSetup(p) ? "" : `<div class="track"><span class="track-cap">${shDesignName(p)}</span><ol aria-label="研究路径">${kinds.map((k, i) => `<li class="${idxOn >= 0 && i < idxOn ? "done" : ""} ${i === idxOn ? "on" : ""}" ${i === idxOn ? 'aria-current="step"' : ""}><i>${idxOn >= 0 && i < idxOn ? "✓" : i + 1}</i>${SH_KIND[k]}</li>`).join("")}</ol></div>`;
  const modesHtml = shSetup(p) ? "" : `<div class="ctx-modes" aria-label="访问方式">${modes.map(([n, m]) => `<span class="${hi.includes(m) ? "on" : ""}">${n}</span>`).join("")}</div>`;
  return `<div class="ctx"><div class="ctx-row"><span class="ctx-no">${shNumber(p)}</span><span class="ctx-name">${esc(p.name)}</span><span class="pill ${stCls}">${stLabel}</span>${sel}${track}<span class="sp"></span>${modesHtml}${close}</div></div>`;
}

/** 页签：同一类页面放在一排（原版的 hubtabs）。 */
function shTabs(p, active) {
  if (shSetup(p)) return "";
  const hub = SH_HUBS.find((h) => h.id === shHubOf(active));
  if (!hub) return "";
  const ts = hub.tabs.filter((t) => shPageOk(t[0], p));
  if (ts.length < 2) return "";
  return `<div class="hubtabs pin" role="tablist" aria-label="${hub.n}">${ts.map(([k, n]) => `<a role="tab" class="${active === k ? "on" : ""}" href="#/p/${p.id}/${k}" data-t="${n}" aria-selected="${active === k}" ${active === k ? 'aria-current="page"' : ""}>${n}</a>`).join("")}</div>`;
}

function shWire(p) {
  const pinTabs = () => { const c = document.querySelector(".ctx"), h = document.querySelector("main>.hubtabs.pin"); if (c) document.documentElement.style.setProperty("--ctxh", c.offsetHeight + "px"); if (h) document.documentElement.style.setProperty("--tabh", h.offsetHeight + "px"); };  // the tab row stays pinned right under the project bar
  pinTabs(); window.onresize = pinTabs;
  const sel = document.getElementById("subsel");
  if (sel) sel.onchange = () => { sessionStorage.setItem("sub_" + p.id, sel.value); render(); };
  document.querySelectorAll("[data-qmode]").forEach((el) => (el.onclick = () => { sessionStorage.setItem("qmode", el.dataset.qmode); if (location.hash === el.getAttribute("href")) render(); }));
}


/** 固定外壳（桌面）：项目条、一级页签、二级行是三个固定位置的节点，切换页签时不重建，只替换内容区；二级行里放页面自己的“同一级小页签/控件”。
 *  页面想放进二级行的东西：直接子元素 `.hubtabs.sub`，或任意位置带 data-subrow 属性的元素，shell 会把它们搬到二级行（节点不变，事件照常）。 */
const shHtml = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstElementChild; };
function shMount({ proj, p, active, nav, top, tabs, notice, inner }) {
  const key = proj ? `${p.id}|${shHubOf(active)}` : "";
  const cur = app.firstElementChild, hub = proj ? shHubOf(active) : "", isQl = hub === "ql" && !!tabs;
  const cls = `enter${proj && tabs ? " hubpage" : ""}`;
  if (proj && cur && cur.classList.contains("shfix") && cur.dataset.key === key) {   // same project and same group of tabs: keep the frame, swap the content
    const navEl = cur.querySelector("nav.side"), ctx = cur.querySelector(".work>.ctx"), main = cur.querySelector(".work>main"), tb = cur.querySelector(".work>.hubtabs.pin");
    if (cur._nav !== nav) { navEl.innerHTML = nav; cur._nav = nav; }
    if (cur._top !== top) { ctx.innerHTML = shHtml(top).innerHTML; cur._top = top; }
    if (tb) { const want = shHtml(tabs); tb.querySelectorAll("a").forEach((a, i) => { const b = want.children[i]; if (!b) return; a.className = b.className; ["aria-selected", "aria-current"].forEach((n) => (b.hasAttribute(n) ? a.setAttribute(n, b.getAttribute(n)) : a.removeAttribute(n))); }); }
    main.className = cls; main.innerHTML = notice + inner; main.scrollTop = 0;
    shMoveSub(cur);
    return;
  }
  if (!proj) { app.innerHTML = `<div class="app"><nav class="side" aria-label="主导航">${nav}</nav><div class="work">${top}<main class="${cls}">${notice}${inner}</main></div></div>`; return; }
  app.innerHTML = `<div class="app rail shfix" data-key="${esc(key)}"><nav class="side" aria-label="主导航">${nav}</nav><div class="work">${top}${tabs}${isQl ? `<div class="subrow" id="subrow"></div>` : ""}<main class="${cls}">${notice}${inner}</main></div></div>`;
  const a = app.firstElementChild; a._nav = nav; a._top = top; shMoveSub(a);
}
function shMoveSub(a) {   // the page's own second-level controls go into the fixed second row
  const row = a.querySelector(".work>.subrow"), main = a.querySelector(".work>main"); if (!row || !main) return;
  row.replaceChildren(); main.querySelectorAll(":scope > .hubtabs.sub, [data-subrow]").forEach((e) => row.appendChild(e));   // a direct .hubtabs.sub, or any element marked data-subrow (at any depth)
}
