/* 立项向导（原版星桐的 7 步向导）：基本信息 → 研究设计 → 人员与角色 → 人群与样本 → 渠道与触达 → 合规举证 → 确认发布。
   页面状态放在 WZ 里；每次改动 0.8 秒后自动保存（PUT /projects/{id}/plan），上一步/下一步前也会先保存。已发布的立项是锁定的，整页只读。
   依赖 index.html 里的 shell / head / api / esc / toast / guard / modal / $。 */
const WZ_STEPS = ["基本信息", "研究设计", "人员与角色", "人群与样本", "渠道与触达", "合规举证", "确认发布"];
const WZ_DESIGNS = {  // research order of one sub-project (a country): label + the methods it runs, in order
  fq: ["先座谈会，后定量", ["group", "quant"]], iq: ["先深访，后定量", ["depth", "quant"]], qi: ["先定量，后深访", ["quant", "depth"]],
  i: ["仅深访", ["depth"]], f: ["仅座谈会", ["group"]], q: ["仅定量", ["quant"]], fi: ["先座谈会，后深访", ["group", "depth"]], if: ["先深访，后座谈会", ["depth", "group"]],
};
const WZ_MNAME = { depth: "深访", group: "座谈会", quant: "定量" }, WZ_MCLS = { depth: "mi", group: "mf", quant: "mq" };  // each method has its own colour
const wzM = (m) => `<span class="${WZ_MCLS[m]}">${WZ_MNAME[m]}</span>`;
const wzDesignHtml = (k) => { const ms = WZ_DESIGNS[k][1]; return ms.length === 1 ? `仅${wzM(ms[0])}` : `先${wzM(ms[0])}，后${wzM(ms[1])}`; };
const WZ_RETENTION = [30, 60, 120, 180];
const WZ_TYPES = {
  "使用习惯与态度": "人群细分、使用场景题库、动机编码表",
  "概念测试": "概念卡轮换顺序、随机分组、逐个概念对比",
  "视频与广告测试": "不可快进的播放、按时间点标记反应",
  "售后与体验": "低分自动邀请深访、投诉主题编码",
  "产品与包装测试": "样品发放与签收、开箱视频上传",
};
const WZ_LANGS = ["中文", "英语", "马来语", "泰米尔语"];
const WZ_STATUS = { draft: ["草稿", "o"], review: ["审查中", "o"], approved: ["审查通过，可发布", ""], published: ["暂时锁定", ""] };
const WZ_PERMS = {  // what each account role can do in a project (shown as the permission preview)
  admin: ["招募", "邀请", "看个人信息", "写洞察"], support: ["招募", "邀请", "看个人信息", "写洞察"], researcher: ["招募", "邀请", "看个人信息", "写洞察"], recruiter: ["招募", "邀请"],
  moderator: ["发言", "观察"], observer: ["观察"], viewer: ["只读成果"],
};
const WZ_ROLE = { admin: "管理员", researcher: "研究员", recruiter: "项目经理", moderator: "主持人", observer: "观察员", viewer: "客户（只读）", support: "技术支持" };
const WZ_KIND = { quant: "定量研究员", qual: "定性研究员", all: "全科研究员" };
const WZ = { p: null, plan: null, cfg: null, step: 0, tab: "q", users: [], timer: null, saving: null, picks: {}, known: [], estNote: "", sugState: "", sugBrief: "", sugError: "" };

const wzLocked = () => WZ.plan.status === "published";
const wzMethods = () => {  // what the sub-projects use (the server derives the same list)
  const got = new Set();
  WZ.cfg.subs.forEach((s) => { WZ_DESIGNS[s.design][1].forEach((m) => got.add(m)); if (s.listening) got.add("listening"); if (s.async_ai) got.add("async"); });
  if ([...WZ.cfg.lt.rows, ...WZ.cfg.ft.rows].some((r) => r.a)) got.add("async");
  return got;
};
const wzMethod = (m) => wzMethods().has(m);
const wzInterviews = () => ["depth", "group", "async"].some(wzMethod);
const wzCanUnlock = () => ["admin", "recruiter"].includes(ME.role);
const wzUid = (pre) => pre + Math.random().toString(36).slice(2, 8);
const wzNum = (v, d = 0) => { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 ? n : d; };
const wzSums = () => ({ q: WZ.cfg.qt.rows.reduce((a, r) => a + r.t, 0), i: WZ.cfg.lt.rows.reduce((a, r) => a + r.i, 0), f: WZ.cfg.ft.rows.reduce((a, r) => a + r.f, 0) });
const wzValues = (dim) => (WZ.cfg.vars[dim] || []);
const wzSel = (attr, val, opts, w) => `<select ${attr} ${w ? `style="max-width:${w}px"` : ""}>${opts.map((o) => `<option value="${esc(o[0])}" ${String(o[0]) === String(val) ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
const wzInp = (attr, v, extra = "") => `<input ${attr} value="${esc(v)}" ${extra}>`;
const wzCheck = (ok) => (ok ? `<span class="pill">✓ 通过</span>` : `<span class="pill r">待处理</span>`);

async function wizardView(p) {
  const my = renderSeq;
  const [plan, users] = await Promise.all([api(`/projects/${p.id}/plan`), api(`/projects/${p.id}/plan/people`)]);
  const live = plan.live ? await api(`/projects/${p.id}/languages`).catch(() => null) : null;   // a published project: languages and formats are the live ones (shared with the overview page)
  if (stale(my)) return;
  if (plan.legacy) {
    shell(`${head("立项向导", "这个项目创建时还没有立项向导，所以没有立项记录。新建项目会从向导开始。")}<div class="card empty"><b>没有立项记录</b><span>老项目照常使用，不受影响。</span><a class="btn p" href="#/p/${p.id}/home">回到项目总览</a></div>`, { active: "wiz", p });
    return;
  }
  Object.assign(WZ, { p, plan, live, cfg: plan.config, users: users.filter((u) => u.active), picks: {}, known: [], step: Math.min(WZ.p && WZ.p.id === p.id ? WZ.step : 0, 6), estNote: "", sugState: WZ.p && WZ.p.id === p.id ? WZ.sugState : "", sugBrief: WZ.p && WZ.p.id === p.id ? WZ.sugBrief : "" });
  wzDraw();
}

function wzSyncCountry() {  // the country variable always equals the sub-projects (each value is one sub-project)
  WZ.cfg.vars["国家"] = WZ.cfg.subs.map((s) => ({ id: s.id, name: s.name }));
  for (const key of ["qt", "lt", "ft"]) {
    const q = WZ.cfg[key], idx = q.dims.indexOf("国家");
    if (idx >= 0) q.rows = q.rows.filter((r) => WZ.cfg.subs.some((s) => s.id === r.vs[idx]));
  }
}

function wzSave(now = false) {
  if (wzLocked()) return Promise.resolve();
  clearTimeout(WZ.timer);
  const run = async () => {
    const r = await api(`/projects/${WZ.p.id}/plan`, { method: "PUT", body: { name: WZ.name_edit ?? undefined, brief: WZ.brief_edit ?? undefined, config: WZ.cfg } });
    WZ.plan = { ...WZ.plan, ...r }; WZ.cfg.methods = r.config.methods;  // never swap in the server's copy: an edit made while this save was on its way would be lost
    WZ.name_edit = WZ.brief_edit = undefined;
    const lab = $("#wzstat"); if (lab) lab.textContent = "已保存";
  };
  if (now) return (WZ.saving = run());
  WZ.timer = setTimeout(() => { WZ.saving = run().catch((e) => toast(e.message, true)); }, 800);
  const lab = $("#wzstat"); if (lab) lab.textContent = "保存中…";
  return Promise.resolve();
}

/* ---------- pieces ---------- */
function wzDesignView() {
  const c = WZ.cfg;
  return `<div class="card" style="display:flex;flex-direction:column;gap:10px"><b>各子项目的研究设计</b>
    <p class="sm mut" style="margin:0">研究顺序在第 1 步按子项目（国家）选好，这里只用来核对，给研究员和项目经理看。要改请回第 1 步。</p>
    <div class="scroll"><table class="tbl"><thead><tr><th>子项目</th><th>研究顺序</th><th>阶段</th><th>舆情</th><th>异步 AI 研究</th></tr></thead><tbody>
    ${c.subs.map((s, i) => `<tr><td><b>S${i + 1}</b> ${esc(s.name)}<div class="sm mut">${esc(s.lang)}</div></td><td>${wzDesignHtml(s.design)}</td>
      <td>${WZ_DESIGNS[s.design][1].map((m, k) => `<span class="tag nw ${WZ_MCLS[m]}">${k + 1} ${WZ_MNAME[m]}</span>`).join("")}</td><td>${s.listening ? "做" : '<span class="mut">不做</span>'}</td><td>${s.async_ai ? "做" : '<span class="mut">不做</span>'}</td></tr>`).join("")}
    </tbody></table></div></div>`;
}

function wzNotice() {  // where the research background goes: the AI service (and the project's research library comes along)
  return CONFIG && CONFIG.ai_local ? "AI 运行在我们自己的服务器上，这段文字不会发给其他公司。"
    : `这段文字会发送给 AI 服务商${CONFIG && CONFIG.ai_vendor ? `“${CONFIG.ai_vendor}”` : ""}来生成内容，只用来写问卷和建议；本项目研究库里已有的资料也会一起发送。请不要写入受访者的姓名、电话等个人信息。`;
}

/** Leaving step 1 with a research background: it goes into the project's research library and the AI writes its suggestions in the background (shown on step 2). */
function wzAutoSuggest() {
  const brief = (WZ.brief_edit ?? WZ.plan.brief ?? "").trim();
  if (wzLocked() || brief.length < 8 || brief === WZ.sugBrief) return;
  WZ.sugBrief = brief; WZ.sugState = "running";
  (async () => {
    try {
      await api(`/projects/${WZ.p.id}/plan/brief-to-library`, { method: "POST" });
      WZ.plan.suggest = await api(`/projects/${WZ.p.id}/plan/suggest?use_library=true`, { method: "POST" }); WZ.sugState = "done";
    } catch (e) { WZ.sugState = "error"; WZ.sugError = e.message; WZ.sugBrief = ""; }
    if (WZ.step === 1 && $("#wzsug")) { const y = scrollY; wzDraw(); scrollTo(0, y); }
  })();
}

function wzSuggest() {  // step 2: what the AI suggests from the research background (to read and take over by hand; nothing is applied by itself)
  const s = WZ.plan.suggest, st = WZ.sugState, list = (a) => `<ul style="margin:4px 0 0 18px">${a.map((t) => `<li><b>${esc(t.title)}</b>：${t.questions.map(esc).join(" / ")}</li>`).join("")}</ul>`;
  if (!s && st !== "running" && st !== "error") return "";
  const pill = st === "running" ? `<span class="pill o">生成中…</span>` : st === "error" ? `<span class="pill r">没生成成功</span>` : `<span class="pill">已生成</span>`;
  return `<div class="card" id="wzsug" style="display:flex;flex-direction:column;gap:8px"><div class="row"><b>AI 建议（根据研究背景，后台生成）</b>${pill}</div>
    ${st === "error" ? `<div class="note">${esc(WZ.sugError || "")}。回到第 1 步改一下研究背景再点“下一步”会重新生成。</div>` : ""}
    ${s ? `<div class="grid"><div class="card"><h3>深访提纲建议</h3>${list(s.depth_topics)}</div><div class="card"><h3>座谈会大纲建议</h3>${list(s.group_topics)}</div>
      <div class="card"><h3>配额建议</h3><p class="sm" style="margin:6px 0 0">${esc(s.quota_hint)}</p></div><div class="card"><h3>舆情关键词建议</h3><div style="margin-top:6px">${s.listening_keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div></div></div>
    <div class="sm mut">仅供参考，需要的内容自己复制；招募问卷会自动参考研究库里的这份研究背景。</div>` : ""}</div>`;
}

const WZ_CODE = { 中文: "zh", 英语: "en", 马来语: "ms", 泰米尔语: "ta" }, WZ_FMT = { audio: "纯音频", text: "文字访谈", text_voice: "文字+语音转录", video: "视频", video_screen: "视频+屏幕共享" };
/** Step 1: the project's languages and the interview formats each live method allows. A project that is already published shows (and changes) the live values, the same as the overview page. */
function wzLangFmt() {
  const c = WZ.cfg, L = WZ.live, methods = L ? Object.keys(L.formats) : ["depth", ...(c.methods.includes("group") ? ["group"] : [])];
  const langChip = (l) => L ? `<label class="chk"><input type="checkbox" data-livelang="${WZ_CODE[l]}" ${L.languages.includes(WZ_CODE[l]) ? "checked" : ""}>${l}</label>` : `<label class="chk"><input type="checkbox" data-lang="${l}" ${c.langs[l] ? "checked" : ""}>${l}</label>`;
  const fmtRow = (m) => { const on = L ? L.formats[m] : (c.fmt && c.fmt[m]) || ["video"];
    return `<div class="field"><span style="font-size:14px">${m === "depth" ? "深访" : "座谈会"}允许的访谈形式（可多选；没选中的形式，受访者不会看到）</span><div class="row" style="margin-top:6px" role="group" aria-label="${m === "depth" ? "深访" : "座谈会"}允许的访谈形式">${Object.keys(WZ_FMT).map((k) => `<label class="chk"><input type="checkbox" ${L ? `data-livefmt="${m}|${k}"` : `data-fmtc="${m}|${k}"`} ${on.includes(k) ? "checked" : ""}>${WZ_FMT[k]}</label>`).join("")}</div></div>`; };
  return `<div class="field"><span style="font-size:14px">项目语言（可多选：受访者、同意书、邀请函、问卷可以使用这些语言；项目开始后在“项目总览”里随时加减）</span><div class="row" style="margin-top:6px" role="group" aria-label="项目语言">${WZ_LANGS.map(langChip).join("")}</div></div>${methods.map(fmtRow).join("")}`;
}
/** The languages a sub-project runs in: ticks from the project's languages; the first one ticked is the main one. */
function wzSubLangs(s) {
  const on = WZ_LANGS.filter((l) => WZ.cfg.langs[l]), mine = (s.langs && s.langs.length ? s.langs : [s.lang]).filter((l) => on.includes(l));
  return `<div class="wzlangs" role="group" aria-label="${esc(s.name)} 的执行语言">${on.map((l) => `<label class="chk"><input type="checkbox" data-sublang="${s.id}|${l}" ${mine.includes(l) ? "checked" : ""}>${l}</label>`).join("")}</div>`;
}
/** Keep every sub-project's languages inside the project's languages, and its main language (`lang`) the first of them. */
function wzFixLangs() {
  const c = WZ.cfg, on = WZ_LANGS.filter((l) => c.langs[l]);
  c.subs.forEach((s) => { const mine = (s.langs && s.langs.length ? s.langs : [s.lang]).filter((l) => on.includes(l)); s.langs = mine.length ? mine : on.slice(0, 1); s.lang = s.langs[0]; });
}
function wzSubs() {
  const c = WZ.cfg, pms = [["", "未指定"], ...WZ.users.filter((u) => ["admin", "researcher", "support", "recruiter"].includes(u.role)).map((u) => [u.id, u.name])];
  const single = c.split === "single";
  return `<div><b>子项目</b>
    <div class="row" style="margin:6px 0"><label class="chk"><input type="radio" name="split" data-split="country" ${single ? "" : "checked"}>按国家拆成子项目（日本、韩国……各一个）</label>
    <label class="chk"><input type="radio" name="split" data-split="single" ${single ? "checked" : ""}>只有一个子项目（在里面分组）</label>
    <span class="sp"></span><button class="s" id="subtpl" type="button">下载 Excel 模板</button><button class="s" id="subimp" type="button">导入表格</button><input type="file" id="subfile" accept=".csv,text/csv" hidden aria-label="导入子项目表格"></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>编号</th><th>${single ? "名称" : "国家（可改名）"}</th><th>执行语言</th><th>责任人</th><th>研究顺序</th><th>舆情</th><th>异步 AI 研究</th><th></th></tr></thead><tbody>
    ${c.subs.map((s, i) => `<tr><td><b>S${i + 1}</b></td><td>${wzInp(`data-sub="${s.id}|name" aria-label="名称"`, s.name, 'size="12"')}</td>
      <td>${wzSubLangs(s)}</td>
      <td>${wzSel(`data-sub="${s.id}|pm" aria-label="责任人"`, s.pm, pms, 150)}</td>
      <td>${wzSel(`data-sub="${s.id}|design" aria-label="研究顺序"`, s.design, Object.keys(WZ_DESIGNS).map((k) => [k, WZ_DESIGNS[k][0]]), 170)}<div class="sm" style="margin-top:4px">${wzDesignHtml(s.design)}</div></td>
      <td><label class="chk wzbig"><input type="checkbox" data-subx="${s.id}|listening" ${s.listening ? "checked" : ""} aria-label="${esc(s.name)} 做舆情收集"></label></td>
      <td><label class="chk wzbig"><input type="checkbox" data-subx="${s.id}|async_ai" ${s.async_ai ? "checked" : ""} aria-label="${esc(s.name)} 做异步 AI 研究"></label></td>
      <td>${c.subs.length > 1 ? `<button class="s" data-subdel="${s.id}" type="button">删除</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    ${single ? `<div class="note">一个子项目：定量一份问卷，定性按组拆分。</div>` : `<div class="row" style="margin-top:8px"><input id="subnew" placeholder="新增国家，例如：马来西亚" aria-label="新增国家"><button class="s p" id="subadd" type="button">+ 添加子项目</button></div>
    <div class="note">改名或删除后，配额和执行监控会同步更新。模板按“编号、国家、执行语言、责任人、研究顺序、舆情、异步AI研究”填好后导入。勾了“异步 AI 研究”，招募时选 AI 主持人就是异步 AI 研究。</div>`}</div>`;
}

const wzCsv = (rows) => "\ufeff" + rows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\r\n");
function wzParseCsv(text) {  // a small CSV reader (quotes, commas, line breaks inside quotes)
  const rows = []; let row = [], cur = "", q = false;
  text = text.replace(/^\ufeff/, "");
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cur); cur = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cur); cur = ""; if (row.some((x) => x.trim())) rows.push(row); row = []; }
    else cur += ch;
  }
  row.push(cur); if (row.some((x) => x.trim())) rows.push(row);
  return rows;
}
function wzSubTemplate() {
  const csv = wzCsv([["编号", "国家", "执行语言", "责任人", "研究顺序", "舆情", "异步AI研究"], ["S1", "新加坡", "英语", "", "先深访，后定量", "否", "否"], ["S2", "马来西亚", "马来语", "", "仅深访", "是", "否"],
    ["", "", "语言可选：" + WZ_LANGS.join("、"), "责任人写团队里的姓名，可留空", "研究顺序可选：" + Object.values(WZ_DESIGNS).map((d) => d[0]).join("、"), "是 / 否", "是 / 否"]]);
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); a.download = "子项目导入模板.csv"; document.body.appendChild(a); a.click(); a.remove();
}
function wzImportSubs(text) {
  const rows = wzParseCsv(text), head = rows.shift() || [], col = (n) => head.findIndex((h) => h.replace(/\s/g, "").includes(n));
  const [iName, iLang, iPm, iDes, iLis, iAs] = ["国家", "执行语言", "责任人", "研究顺序", "舆情", "异步"].map(col);
  if (iName < 0) throw new Error("表头里要有“国家”这一列，请用下载的模板");
  const yes = (v) => /^(是|yes|y|1|true|做|✓)$/i.test((v || "").trim()), byLabel = Object.fromEntries(Object.entries(WZ_DESIGNS).map(([k, d]) => [d[0], k]));
  const subs = [], bad = [];
  rows.forEach((r, n) => {
    const name = (r[iName] || "").trim(); if (!name || /^语言可选/.test(r[iLang] || "")) return;
    const lang = (r[iLang] || "").trim() || "英语", des = (r[iDes] || "").trim().replace(/,/g, "，") || "先深访，后定量", pm = (r[iPm] || "").trim();
    if (!WZ_LANGS.includes(lang)) bad.push(`第 ${n + 2} 行：执行语言“${lang}”不在可选范围`);
    if (!byLabel[des]) bad.push(`第 ${n + 2} 行：研究顺序“${des}”不在可选范围`);
    subs.push({ id: wzUid("s"), name, lang: WZ_LANGS.includes(lang) ? lang : "英语", pm: (WZ.users.find((u) => u.name === pm) || {}).id || "", design: byLabel[des] || "iq", listening: yes(r[iLis]), async_ai: yes(r[iAs]) });
  });
  if (!subs.length) throw new Error("表里没有可导入的子项目");
  if (bad.length) throw new Error(bad.slice(0, 3).join("；"));
  return subs;
}

function wzDefault(u) {  // the ticks a person gets when they are added, from their role (and for a researcher, the kind of researcher)
  const all = { q: true, i: true, f: true, l: true }, k = u.research_kind || "all";
  if (u.role === "admin" || u.role === "viewer" || u.role === "support") return all;
  if (u.role === "researcher") return k === "quant" ? { q: true, i: false, f: false, l: true } : k === "qual" ? { q: false, i: true, f: true, l: true } : all;
  if (u.role === "moderator" || u.role === "observer") return { q: false, i: true, f: true, l: false };
  return { q: false, i: false, f: false, l: false };  // the project manager: ticked one by one by the researcher
}
function wzRoles() {
  const en = [wzMethod("quant"), wzMethod("depth"), wzMethod("group"), wzMethod("listening")], names = ["定量", "深访", "座谈会", "舆情"], keys = ["q", "i", "f", "l"];
  const team = Object.fromEntries(WZ.cfg.team.map((m) => [m.user_id, m])), byId = Object.fromEntries(WZ.users.map((u) => [u.id, u]));
  const admins = WZ.users.filter((u) => u.role === "admin"), mine = WZ.cfg.team.filter((m) => byId[m.user_id]).map((m) => byId[m.user_id]);
  const addable = WZ.users.filter((u) => u.role !== "admin" && !team[u.id]);
  const roleName = (u) => (u.role === "researcher" ? WZ_KIND[u.research_kind || "all"] : WZ_ROLE[u.role] || u.role);
  const row = (u, m, fixed) => `<tr><td>${esc(u.name)}</td><td>${roleName(u)}</td>${keys.map((k, j) => `<td><label class="chk wzbig"><input type="checkbox" aria-label="${esc(u.name)} ${names[j]}" ${fixed ? "checked disabled" : `data-member="${u.id}|${k}" ${m[k] ? "checked" : ""} ${en[j] ? "" : "disabled"}`}></label></td>`).join("")}
    <td>${(WZ_PERMS[u.role] || []).map((t) => `<span class="tag">${t}</span>`).join("")}</td><td>${fixed ? "" : `<button class="s" data-memdel="${u.id}" type="button">移出</button>`}</td></tr>`;
  return `<p class="sub">每个人参与哪些环节。人员来自“团队”；加进来时按角色自动勾好，可以再改。管理员自动参与全部。研究员、项目经理、客户在发布时加入项目；主持人和观察员按场次安排，不是项目成员。</p>
    <div class="row"><label class="field" style="flex-direction:row;align-items:center;gap:8px">添加人员${wzSel('id="memsel" aria-label="选择要添加的人员"', "", [["", addable.length ? "选择一位同事" : "没有可添加的人"], ...addable.map((u) => [u.id, `${u.name}（${roleName(u)}）`])], 260)}</label>
      <button class="s p" id="memadd" type="button" ${addable.length ? "" : "disabled"}>添加</button>
      <span class="mut sm">按列勾选：</span>${names.map((n, j) => en[j] ? `<span class="row" style="gap:4px"><b>${n}</b><button class="lnk" data-col="${j}|1" type="button">全选</button><button class="lnk" data-col="${j}|0" type="button">全不选</button></span>` : "").join("")}</div>
    <div class="scroll"><table class="tbl"><thead><tr><th>人员</th><th>角色</th>${names.map((n) => `<th>${n}</th>`).join("")}<th>权限预览</th><th></th></tr></thead><tbody>${admins.map((u) => row(u, {}, true)).join("")}${mine.map((u) => row(u, team[u.id], false)).join("")}</tbody></table></div>
    <div class="note">只有主持人可以开麦；观察员和客户在后台，只看到受访者编号。项目经理只处理项目里的人，看不到访谈内容。灰掉的列，是这个项目没有用到的方法。</div>`;
}

function wzQuota(key, color, title, cols) {
  const c = WZ.cfg, T = c[key], names = Object.keys(c.vars);
  const dimSel = [0, 1].map((i) => (i < T.dims.length || (i === T.dims.length && T.dims.length < 2)) ? wzSel(`data-qdim="${key}|${i}" aria-label="配额变量 ${i + 1}"`, T.dims[i] || "", (i === 0 ? [] : [["", "（无）"]]).concat(names.map((n) => [n, n])), 150) : "").filter(Boolean).join('<span class="mut">×</span>');
  const sel = WZ.picks[key] || (WZ.picks[key] = new Set());
  const sums = cols.map((cl) => T.rows.reduce((a, r) => a + r[cl[0]], 0));
  return `<div class="bx ${color} wide"><div class="bxh"><i></i>${title}</div><div class="row"><span class="sm">配额变量：</span>${dimSel}</div>
    <div class="row selbar"><label class="chk"><input type="checkbox" data-qall="${key}" ${T.rows.length && T.rows.every((r) => sel.has(r.id)) ? "checked" : ""}>全选</label><span class="mut sm">已选 ${[...sel].filter((id) => T.rows.some((r) => r.id === id)).length} / ${T.rows.length}</span>
      <button class="s" data-qrow="${key}" type="button">+ 添加一行</button><button class="s" data-qrowdel="${key}" type="button">删除选中</button>
      ${key !== "qt" ? `<button class="s" data-qasync="${key}|1" type="button">选中的设为异步 AI 执行</button><button class="s" data-qasync="${key}|0" type="button">取消异步 AI</button>` : ""}</div>
    <div class="scroll"><table class="tbl"><thead><tr><th></th>${T.dims.map((d) => `<th>${esc(d)}</th>`).join("")}${cols.map((cl) => `<th>${cl[1]}</th>`).join("")}${key === "ft" ? "<th>座谈会（人）</th>" : ""}${key !== "qt" ? "<th>异步 AI 执行</th>" : ""}</tr></thead><tbody>
    ${T.rows.map((r) => `<tr><td><label class="chk wzbig"><input type="checkbox" aria-label="选中这一行" data-qpick="${key}|${r.id}" ${sel.has(r.id) ? "checked" : ""}></label></td>${T.dims.map((d, i) => `<td>${wzSel(`data-qcell="${key}|${r.id}|v${i}" aria-label="${esc(d)}"`, r.vs[i], wzValues(d).map((v) => [v.id, v.name]), 170)}</td>`).join("")}
      ${cols.map((cl) => `<td>${wzInp(`type="number" min="0" data-qcell="${key}|${r.id}|${cl[0]}" aria-label="${cl[1]}"`, r[cl[0]], 'style="width:90px"')}</td>`).join("")}${key === "ft" ? `<td>${r.f * c.n.fs}</td>` : ""}${key !== "qt" ? `<td><label class="chk wzbig"><input type="checkbox" data-qa="${key}|${r.id}" ${r.a ? "checked" : ""} aria-label="这一组由异步 AI 执行"></label></td>` : ""}</tr>`).join("") || `<tr><td colspan="${T.dims.length + cols.length + 3}" class="mut sm">还没有配额行，点“+ 添加一行”。</td></tr>`}
    <tr><td></td><td colspan="${T.dims.length}"><b>合计</b></td>${sums.map((s) => `<td><b>${s}</b></td>`).join("")}${key === "ft" ? `<td><b>${sums[0] * c.n.fs}</b></td>` : ""}${key !== "qt" ? `<td class="sm">${T.rows.filter((r) => r.a).length ? `异步 AI：${T.rows.filter((r) => r.a).reduce((a, r) => a + r[cols[0][0]], 0)} ${key === "ft" ? "场" : "人"}` : ""}</td>` : ""}</tr></tbody></table></div></div>`;
}

function wzDims() {
  const c = WZ.cfg;
  return `<div class="card" style="display:flex;flex-direction:column;gap:10px"><div class="row" style="justify-content:space-between"><b>变量与取值</b><span class="mut sm">配额不一定按国家。先在这里定义变量和取值，下面的配额表直接选用。</span></div>
    ${Object.keys(c.vars).map((dim) => `<div class="dvrow"><div class="row"><b>${esc(dim)}</b>${dim === "国家" ? `<span class="pill o">每个取值是一个子项目（在第 1 步改）</span>` : `<button class="lnk" data-dimdel="${esc(dim)}" type="button">删除这个变量</button>`}</div>
      <div class="row">${c.vars[dim].map((v) => dim === "国家" ? `<span class="dvchip"><span style="padding:0 10px">${esc(v.name)}</span></span>` : `<span class="dvchip"><input data-dvn="${esc(dim)}|${v.id}" value="${esc(v.name)}" aria-label="${esc(dim)}取值" size="${Math.max(3, v.name.length + 1)}"><button aria-label="删除" data-dvdel="${esc(dim)}|${v.id}" type="button">×</button></span>`).join("")}
      ${dim === "国家" ? "" : `<span class="dvchip add"><input id="dvn-${esc(dim)}" placeholder="新增取值" size="8" aria-label="新增取值"><button data-dvadd="${esc(dim)}" type="button" aria-label="添加取值">+</button></span>`}</div></div>`).join("")}
    <div class="row"><input id="dimnew" placeholder="新变量，例如：性别、收入" aria-label="新变量"><button class="s" id="dimadd" type="button">+ 添加变量</button></div></div>`;
}

function wzSample() {
  const c = WZ.cfg, s = wzSums(), ok = (a, b) => (a === b ? `<span class="pill">与上面一致</span>` : `<span class="pill r">应为 ${b}</span>`);
  return `<label class="field">用一句话描述目标人群<textarea data-w="pop" rows="3">${esc(c.pop)}</textarea></label>
    <div class="row"><button class="s" id="wztags" type="button">匹配标签</button><button class="s" id="wzest" type="button">人群规模估算</button></div>
    ${c.tags.length ? `<div>${c.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}${c.est != null ? `<div class="note">${esc(WZ.estNote || `这个项目的受访者库里有 ${c.est} 人符合。`)}</div>` : ""}
    <div class="grid3">
      ${wzMethod("quant") ? `<div class="bx q"><div class="bxh"><i></i>定量问卷</div><label class="field">样本量（份）${wzInp('type="number" min="0" data-n="q"', c.n.q)}</label><div class="sm mut">配额合计 ${s.q} ${ok(s.q, c.n.q)}</div></div>` : ""}
      ${wzMethod("depth") ? `<div class="bx i"><div class="bxh"><i></i>深访 IDI</div><label class="field">深访人数（人）${wzInp('type="number" min="0" data-n="i"', c.n.i)}</label><div class="sm mut">配额合计 ${s.i} ${ok(s.i, c.n.i)}</div></div>` : ""}
      ${wzMethod("group") ? `<div class="bx f"><div class="bxh"><i></i>座谈会 FGD</div><div class="stack"><label class="field">座谈会场数（场）${wzInp('type="number" min="0" data-n="f"', c.n.f)}</label><label class="field">每场人数（人）${wzInp('type="number" min="3" max="10" data-n="fs"', c.n.fs)}</label></div><div class="sm mut">共 ${c.n.f * c.n.fs} 人 · 配额场数合计 ${s.f} ${ok(s.f, c.n.f)}</div></div>` : ""}
    </div>
    <div class="row"><button class="p" id="wzconfirmn" type="button">确认样本量</button><span class="sm mut">填好数字后点一下，立即保存并核对配额合计。</span></div>
    ${wzDims()}
    <div class="note">先定义变量和取值（上面），再给用到的方法各选各的配额变量。这些配额会进入“执行监控”。深访和座谈会的配额表里，客户要哪些人（可以多人）或哪几组（可以多组）用异步 AI 执行，就在“异步 AI 执行”一列勾上，或选中几行后点“选中的设为异步 AI 执行”；项目总览和执行监控里会标出来。</div>
    ${wzMethod("quant") ? wzQuota("qt", "q", "定量配额", [["t", "目标（份）"]]) : ""}${wzMethod("depth") ? wzQuota("lt", "i", "深访配额", [["i", "深访（人）"]]) : ""}${wzMethod("group") ? wzQuota("ft", "f", "座谈会配额", [["f", "座谈会（场）"]]) : ""}`;
}

function wzChannels() {
  const c = WZ.cfg, q = WZ.tab === "q" || !wzInterviews(), text = q ? c.tq : c.tl, key = q ? "tq" : "tl";
  const has = (k) => text.includes("{{" + k + "}}");
  const body = q
    ? `<div class="grid"><div class="card"><h3>防重复作答（全局 IP 规则）</h3><label class="chk" style="margin-top:8px"><input type="checkbox" data-ip="on" ${c.ip.on ? "checked" : ""}><span>开启：同一 IP 命中终态后，窗口内再次访问将被拦截</span></label>
        <label class="field" style="margin-top:8px">拦截窗口${wzSel('data-ipwin aria-label="拦截窗口"', c.ip.win, [[7, "7 天"], [30, "30 天"], [90, "90 天"]])}</label></div>
        <div class="card"><h3>外部问卷平台</h3><p class="sm mut" style="margin:6px 0 0">问卷由外部平台发出，通过项目密钥把通过甄别的人和结果推送进来（见“定量结果”页）。这里设置的邀请文字用于合规检查。</p></div></div>`
    : `<div class="grid"><div class="card" style="display:flex;flex-direction:column;gap:8px"><h3>受访者来源</h3>${[["pool", "从受访者库按标签圈人（推荐）"], ["rec", "招募员名单"], ["panel", "外部样本库"]].map((x) => `<label class="chk"><input type="radio" name="src" data-src="${x[0]}" ${c.src === x[0] ? "checked" : ""}><span>${x[1]}</span></label>`).join("")}</div>
        <div class="card" style="display:flex;flex-direction:column;gap:8px"><h3>预约规则</h3>
        <label class="field">提前排期不少于（小时，保证现场支持）${wzInp('type="number" min="0" data-n2="lead"', c.lead)}</label><label class="field">每人 90 天内最多邀请（次，防疲劳）${wzInp('type="number" min="1" data-n2="inv"', c.inv)}</label>
        <label class="field">礼金（元）${wzInp('type="number" min="0" data-n2="gift"', c.gift)}</label><label class="field">超额招募备份（%）${wzInp('type="number" min="0" data-n2="back"', c.back)}</label></div></div>`;
  return `<div class="row"><button class="s ${q ? "dk" : ""}" data-tab="q" type="button">定量触达</button><button class="s ${!q ? "dk" : ""}" data-tab="l" type="button" ${wzInterviews() ? "" : "disabled"}>定性预约</button></div>${body}
    <label class="field">${q ? "定量邀请文字" : "定性预约邀请文字"}<textarea data-tpl="${key}" rows="3">${esc(text)}</textarea></label>
    <div class="row"><span>必含占位：</span>${wzCheck(has("purpose"))} 联系目的 {{purpose}} ${wzCheck(has("unsubscribe"))} 退订 {{unsubscribe}}</div>
    <div><b>发送语言</b><div class="row" style="margin-top:6px">${WZ_LANGS.map((l) => `<label class="chk"><input type="checkbox" data-lang="${l}" ${c.langs[l] ? "checked" : ""}>${l}</label>`).join("")}</div>
    <div class="sm mut">需要覆盖：${[...new Set(c.subs.map((s) => s.lang))].join("、") || "—"}</div></div>`;
}

function wzCompliance() {
  const P = WZ.plan, locked = wzLocked(), st = P.status, ok = P.blockers.length === 0;
  const tick = (k, label) => `<label class="chk"><input type="checkbox" data-cons="${k}" ${WZ.cfg.cons[k] ? "checked" : ""}><span>${label}</span></label>`;
  const chain = ["提交审查", "法务审查", "审查通过"].map((x, i) => `<div class="step ${["draft"].includes(st) ? "" : (st === "review" ? i < 2 : true) ? "on" : ""}"><b>${i + 1} ${x}</b></div>`).join("");
  const me = ME.role === "admin";
  return `<p class="sub">十四项合规要求，使用访谈时再加四项。系统根据前面的选择自动判定，未通过的在这里补。</p>
    <div class="card"><div class="row" style="justify-content:space-between"><b>补充确认</b><button class="s" id="wzall" type="button">一键全选同意以下所有条款</button></div><div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">${tick("xb", "已签署标准合同或完成跨境评估（c6）")}${tick("minor", "人群限定 18 岁以上并在筛选题校验（c11）")}
      ${wzInterviews() ? tick("rec", "进入房间前单独征得录音录像同意（q1）") + tick("recon", "定量问卷末尾询问是否同意再联系（q2）") + tick("mask", "观察员只见编号（q3）") + tick("ai", "告知 AI 转写、翻译与摘要（q4）") : ""}</div></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>编号</th><th>要求</th><th>依据</th><th>怎么满足</th><th>状态</th></tr></thead><tbody>${P.compliance.map((x) => `<tr class="${x.passed ? "" : "sel"}"><td>${x.id}</td><td><b>${x.title}</b></td><td><span class="pill ${x.basis === "定性新增" ? "o" : "g"}">${x.basis}</span></td><td class="sm mut">${esc(x.how)}</td><td>${wzCheck(x.passed)}</td></tr>`).join("")}</tbody></table></div>
    ${!ok ? `<div class="note"><b>还不能提交：</b>${P.blockers.slice(0, 6).map(esc).join("；")}</div>` : ""}
    <div class="row"><button class="p" id="wzsubmit" type="button" ${ok && st === "draft" ? "" : "disabled"}>提交法务审查</button>
      ${st !== "draft" ? `<span class="pill ${st === "review" ? "o" : ""}">${st === "review" ? `已提交，指纹 ${esc(P.review_hash)}，等待管理员审查` : st === "approved" ? "法务通过，可发布并锁定" : "已发布并锁定"}</span>` : ""}
      ${st === "review" && me ? `<button class="p" id="wzapprove" type="button">法务通过</button><button id="wzreturn" type="button">退回修改</button>` : ""}
      ${st === "approved" && me ? `<button id="wzreturn" type="button">退回修改</button>` : ""}${st === "review" && !me ? `<span class="sm mut">需要管理员审查。</span>` : ""}</div>
    ${P.review_note && st === "draft" ? `<div class="note">${esc(P.review_note)}</div>` : ""}<div class="chain">${chain}</div>${locked ? "" : ""}`;
}

function wzConfirm() {
  const c = WZ.cfg, P = WZ.plan, done = P.status === "published", approved = P.status === "approved";
  const M = { depth: "深访", group: "座谈会", async: "异步 AI 研究", listening: "舆情监测", quant: "定量结果" };
  return `<div class="grid"><div class="card"><h3>${esc(P.name)}</h3><div class="sm mut">${c.subs.map((s) => `${esc(s.name)}（${WZ_DESIGNS[s.design][0]}）`).join("、")} · 留存 ${c.retention} 天 · ${esc(c.type)}</div>
      <div style="margin-top:8px">${[...wzMethods()].map((m) => `<span class="tag">${M[m]}</span>`).join("")}${wzMethod("quant") && c.n.q ? `<span class="tag">定量 ${c.n.q}</span>` : ""}${wzMethod("depth") && c.n.i ? `<span class="tag">深访 ${c.n.i}</span>` : ""}${wzMethod("group") && c.n.f ? `<span class="tag">座谈会 ${c.n.f} 场 × ${c.n.fs}</span>` : ""}</div></div>
    <div class="card"><h3>发布后会发生什么</h3><div class="sm mut" style="margin-top:6px">项目与配额暂时锁定，邀请模板不可再改；用到的研究方法进入左侧菜单；团队成员加入项目；“执行监控”按配额开始计数。之后要改样本或加方案，由管理员或项目经理解锁，改完重新审查、发布，配额和监控同步更新。</div></div></div>
    <div class="row"><button class="p" id="wzpub" type="button" ${approved ? "" : "disabled"}>${done ? "已发布并锁定" : P.live ? `发布新版本（v${P.version + 1}）` : "发布并锁定"}</button>${approved || done ? "" : `<span class="sm mut">需要先通过合规审查（第 6 步）。</span>`}</div>`;
}

function wzBody() {
  const c = WZ.cfg;
  switch (WZ.step) {
    case 0: return `<div class="grid"><label class="field">主项目名称${wzInp('data-meta="name" aria-label="主项目名称"', WZ.name_edit ?? WZ.plan.name)}</label>
      <label class="field">数据留存期限${wzSel('data-w="retention" aria-label="数据留存期限"', c.retention, WZ_RETENTION.map((d) => [d, `${d} 天${d === 180 ? "（平台上限）" : ""}`]))}<span class="mut" style="font-weight:400">同意书里写明的就是这个期限：项目结项后 ${c.retention} 天自动删除个人数据，洞察结论保留。${WZ.plan.live ? "已发布过的项目只能缩短，不能延长。" : ""}</span></label></div>
      <label class="field">研究背景（选填，只给团队看）<textarea class="autosize" data-meta="brief" rows="3"placeholder="写清这个项目要解决什么问题、做给谁看。">${esc(WZ.brief_edit ?? WZ.plan.brief ?? "")}</textarea><span class="mut" style="font-weight:400">点“下一步”后，系统会自动把它存入研究库，并在后台生成深访提纲、座谈会大纲、配额建议和舆情关键词的建议，生成好后在“研究设计”页查看；招募问卷也会参考它。</span></label>
      ${WZ.live ? "" : wzLangFmt()}
      <div class="note">${esc(wzNotice())}</div>${wzSubs()}`;
    case 1: return `${wzSuggest()}${wzDesignView()}<label class="field" style="max-width:360px">研究类型（带出模板）${wzSel('data-w="type"', c.type, Object.keys(WZ_TYPES).map((x) => [x, x]))}</label>
      <div class="note">选“${esc(c.type)}”会预置：${WZ_TYPES[c.type]}。</div>`;
    case 2: return wzRoles();
    case 3: return wzSample();
    case 4: return wzChannels();
    case 5: return wzCompliance();
    default: return wzConfirm();
  }
}

function wzDraw() {
  const P = WZ.plan, [stLabel, stCls] = WZ_STATUS[P.status] || [P.status, ""], locked = wzLocked(), s = WZ.step;
  const lockNote = locked ? `<div class="note"><b>立项已确认，暂时锁定。</b>要更新样本、追加或新增执行方案，请管理员或项目经理点“解锁修改”；改完需要重新审查、发布，涉及的定量、深访、座谈会、舆情的配额、问卷和监测大盘会同步变动。${wzCanUnlock() ? ` <button class="s" id="wzunlock2" type="button">解锁修改</button>` : ""}</div>`
    : P.live ? `<div class="note"><b>修改中。</b>项目在这期间仍按已发布的版本（v${P.version}）运行；改完提交审查、通过后发布，配额和监控才会按新内容更新。</div>` : "";
  shell(`${head("立项向导", "一次选好角色、样本、人群和合规", `<span class="pill ${stCls}">${stLabel}</span><span class="sm mut" id="wzstat" role="status">${locked ? "已锁定，只读" : "改动会自动保存"}</span>`)}
    ${lockNote}<div class="chain" role="list">${WZ_STEPS.map((x, i) => `<button class="step ${s === i ? "on" : ""}" data-step="${i}" type="button" ${s === i ? 'aria-current="step"' : ""}>${i + 1} ${x}</button>`).join("")}</div>
    ${s === 0 && WZ.live ? `<div class="card wzlive">${wzLangFmt()}</div>` : ""}<fieldset class="card wzbody" ${locked ? "disabled" : ""}>${wzBody()}</fieldset>
    <div class="row" style="justify-content:space-between"><button id="wzprev" type="button" ${s ? "" : "disabled"}>上一步</button>${s < 6 ? `<button class="p" id="wznext" type="button">下一步</button>` : `<a class="btn p" id="wzhome" href="#/p/${WZ.p.id}/home" ${P.status === "published" || P.live ? "" : 'aria-disabled="true" hidden'}>去项目总览</a>`}</div>`,
    { active: "wiz", p: WZ.p });
  wzWire();
}

/* ---------- events ---------- */
function wzMutate(fn) { if (wzLocked()) return; fn(); wzSave(); }
function wzRedraw(keepScroll = true) {  // never redraw inside the event that caused it (the focused field's blur would redraw again)
  const y = scrollY;
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  setTimeout(() => { wzDraw(); if (keepScroll) scrollTo(0, y); }, 0);
}

function wzWire() {
  const root = $("main");
  root.querySelectorAll("[data-step]").forEach((b) => (b.onclick = guard(async () => { await wzSave(true); if (WZ.step === 0 && +b.dataset.step > 0) wzAutoSuggest(); WZ.step = +b.dataset.step; wzDraw(); scrollTo(0, 0); })));
  $("#wzprev").onclick = guard(async () => { await wzSave(true); WZ.step--; wzDraw(); scrollTo(0, 0); });
  const nx = $("#wznext"); if (nx) nx.onclick = guard(async () => { await wzSave(true); if (WZ.step === 0) wzAutoSuggest(); WZ.step++; wzDraw(); scrollTo(0, 0); });
  root.addEventListener("change", wzChange);
  root.querySelectorAll("textarea.autosize").forEach(wzAutosize);
  root.onclick = (e) => { const t = e.target.closest("button"); if (t) wzClick(t, e); };
}

/** A text box that is as tall as its text: at least 3 lines, at most 10, and a scrollbar inside the box beyond that. */
function wzAutosize(t) {
  const fit = () => {
    const cs = getComputedStyle(t), lh = parseFloat(cs.lineHeight) || 22, pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    t.style.height = "auto";
    const want = t.scrollHeight + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth), max = lh * 10 + pad, min = lh * 3 + pad;
    t.style.height = Math.min(Math.max(want, min), max) + "px";
    t.style.overflowY = want > max + 1 ? "auto" : "hidden";
  };
  t.addEventListener("input", fit); fit();
}

function wzChange(e) {
  const t = e.target, d = t.dataset;
  if (wzLocked()) return;
  const c = WZ.cfg;
  if (d.meta) { WZ[d.meta + "_edit"] = t.value; return wzSave(); }
  if (d.w === "retention") { c.retention = +t.value; return wzSave(true).then(() => wzRedraw()).catch((er) => { toast(er.message, true); c.retention = WZ.p.retention_days; wzRedraw(); }); }
  if (d.w === "type") { c.type = t.value; wzSave(); return wzRedraw(); }
  if (d.w === "pop") { c.pop = t.value; return wzSave(); }
  if (d.split) { wzSplit(d.split); return; }
  if (d.sub) { const [id, f] = d.sub.split("|"), s = c.subs.find((x) => x.id === id); s[f] = t.value; if (f === "name" && !t.value.trim()) s.name = "未命名"; wzSyncCountry(); wzSave(); return wzRedraw(); }
  if (d.subx) { const [id, f] = d.subx.split("|"); c.subs.find((x) => x.id === id)[f] = t.checked; wzSave(); return wzRedraw(); }
  if (d.member) { const [uid, k] = d.member.split("|"), m = c.team.find((x) => x.user_id === uid); if (m) m[k] = t.checked; wzSave(); return; }
  if (t.id === "subfile") { const f = t.files[0]; t.value = ""; if (!f) return; return f.text().then((txt) => { const subs = wzImportSubs(txt); if (c.subs.length > 1 && !confirm(`导入会替换现在的 ${c.subs.length} 个子项目，确定吗？`)) return; c.subs = subs; c.split = subs.length > 1 ? "country" : c.split; wzSyncCountry(); wzSave(); wzRedraw(); toast(`已导入 ${subs.length} 个子项目`); }).catch((er) => toast(er.message, true)); }
  if (d.n) { c.n[d.n] = wzNum(t.value, d.n === "fs" ? 6 : 0); if (d.n === "fs") c.n.fs = Math.min(10, Math.max(3, c.n.fs)); wzSave(); return wzRedraw(); }
  if (d.qdim) return wzQDim(d.qdim, t.value);
  if (d.qcell) return wzQCell(d.qcell, t);
  if (d.qa) { const [k, id] = d.qa.split("|"), row = c[k].rows.find((r) => r.id === id); row.a = t.checked; wzSave(); return wzRedraw(); }
  if (d.qpick) { const [k, id] = d.qpick.split("|"), set = WZ.picks[k] || (WZ.picks[k] = new Set()); t.checked ? set.add(id) : set.delete(id); return wzRedraw(); }
  if (d.qall) { const set = (WZ.picks[d.qall] = new Set()); if (t.checked) c[d.qall].rows.forEach((r) => set.add(r.id)); return wzRedraw(); }
  if (d.dvn) { const [dim, id] = d.dvn.split("|"), v = c.vars[dim].find((x) => x.id === id); v.name = t.value.trim() || v.name; wzSave(); return wzRedraw(); }
  if (d.tab === undefined && d.tpl) { c[d.tpl] = t.value; wzSave(); return wzRedraw(); }
  if (d.lang) {
    if (!t.checked && WZ_LANGS.filter((l) => c.langs[l]).length <= 1) { toast("项目至少要有一种语言", true); return wzRedraw(); }
    c.langs[d.lang] = t.checked; wzFixLangs(); wzSave(); return wzRedraw();
  }
  if (d.fmtc) {
    const [m, k] = d.fmtc.split("|"), now = (c.fmt && c.fmt[m]) || ["video"], next = t.checked ? [...new Set([...now, k])] : now.filter((x) => x !== k);
    if (!next.length) { toast("每个方法至少要允许一种访谈形式", true); return wzRedraw(); }
    c.fmt = { ...(c.fmt || {}), [m]: next }; wzSave(); return wzRedraw();
  }
  if (d.livelang || d.livefmt) {   // a published project: change the live settings (the overview page shows the same)
    const L = WZ.live, body = {};
    if (d.livelang) { const next = t.checked ? [...L.languages, d.livelang] : L.languages.filter((x) => x !== d.livelang); body.languages = next; body.default = next.includes(L.default) ? L.default : next[0]; }
    else { const [m, k] = d.livefmt.split("|"); body.formats = { [m]: t.checked ? [...L.formats[m], k] : L.formats[m].filter((x) => x !== k) }; }
    return api(`/projects/${WZ.p.id}/languages`, { method: "PUT", body }).then((r) => { WZ.live = r; wzRedraw(); }).catch((e) => { toast(e.message, true); wzRedraw(); });
  }
  if (d.sublang) {
    const [id, l] = d.sublang.split("|"), s = c.subs.find((x) => x.id === id), mine = (s.langs && s.langs.length ? s.langs : [s.lang]).filter((x) => c.langs[x]);
    s.langs = t.checked ? [...new Set([...mine, l])] : mine.filter((x) => x !== l);
    if (!s.langs.length) { toast("每个子项目至少要有一种执行语言", true); s.langs = [l]; }
    s.lang = s.langs[0]; wzSave(); return wzRedraw();
  }
  if (d.ip === "on") { c.ip.on = t.checked; return wzSave(); }
  if (t.matches("[data-ipwin]")) { c.ip.win = +t.value; return wzSave(); }
  if (d.n2) { c[d.n2] = wzNum(t.value, d.n2 === "inv" ? 1 : 0); return wzSave(); }
  if (d.src) { c.src = d.src; return wzSave(); }
  if (d.cons) { c.cons[d.cons] = t.checked; wzSave(true).then(() => wzRedraw()).catch((er) => toast(er.message, true)); return; }
}

function wzSplit(kind) {
  const c = WZ.cfg;
  c.split = kind;
  if (kind === "single") { const s = c.subs[0]; c.subs = [{ ...s, name: s.name }]; } else if (c.subs.length === 0) c.subs = [{ id: wzUid("s"), name: "新加坡", lang: "英语", pm: "", design: "iq", listening: false, async_ai: false }];
  wzSyncCountry(); wzSave(); wzRedraw();
}

function wzQDim(spec, value) {
  const [k, i] = spec.split("|"), T = WZ.cfg[k], idx = +i;
  if (value) T.dims[idx] = value; else T.dims.splice(idx, 1);
  T.dims = T.dims.slice(0, 2);
  T.rows = T.rows.map((r) => ({ ...r, vs: T.dims.map((d, j) => r.vs[j] && wzValues(d).some((v) => v.id === r.vs[j]) ? r.vs[j] : (wzValues(d)[0] || {}).id) })).filter((r) => r.vs.every(Boolean));
  wzSave(); wzRedraw();
}

function wzQCell(spec, t) {
  const [k, rid, f] = spec.split("|"), row = WZ.cfg[k].rows.find((r) => r.id === rid);
  if (f.startsWith("v")) row.vs[+f.slice(1)] = t.value; else row[f] = wzNum(t.value, 0);
  wzSave(); wzRedraw();
}

function wzClick(b, e) {
  const c = WZ.cfg, d = b.dataset;
  if (b.id === "wztags" || b.id === "wzest") return guard(async () => {
    await wzSave(true);
    const r = await api(`/projects/${WZ.p.id}/plan/tags`, { method: "POST" });
    c.tags = r.tags; c.est = r.reach;
    WZ.estNote = r.tags.length ? `这个项目的受访者库共 ${r.pool} 人，其中 ${r.reach} 人带有这些标签。` : `这个项目的受访者库共 ${r.pool} 人。${r.known.length ? "描述里没有提到已有的标签（已有标签：" + r.known.slice(0, 8).join("、") + "）。" : "还没有人带标签。"}`;
    await wzSave(true); wzRedraw();
  })();
  if (b.id === "subadd") { const v = $("#subnew").value.trim(); if (!v) return toast("先写国家名", true); wzMutate(() => { c.subs.push({ id: wzUid("s"), name: v, lang: "英语", pm: "", design: c.subs.length ? c.subs[c.subs.length - 1].design : "iq", listening: false, async_ai: false }); wzSyncCountry(); }); return wzRedraw(); }
  if (d.subdel) { wzMutate(() => { c.subs = c.subs.filter((s) => s.id !== d.subdel); wzSyncCountry(); }); return wzRedraw(); }
  if (d.col) { const [j, on] = d.col.split("|"), k = ["q", "i", "f", "l"][+j]; wzMutate(() => c.team.forEach((m) => (m[k] = on === "1"))); return wzRedraw(); }
  if (b.id === "memadd") { const id = $("#memsel").value, u = WZ.users.find((x) => x.id === id); if (!u) return toast("先选一位同事", true); wzMutate(() => c.team.push({ user_id: id, ...wzDefault(u) })); return wzRedraw(); }
  if (d.memdel) { wzMutate(() => (c.team = c.team.filter((m) => m.user_id !== d.memdel))); return wzRedraw(); }
  if (b.id === "subtpl") return wzSubTemplate();
  if (b.id === "subimp") return $("#subfile").click();
  if (b.id === "wzconfirmn") return guard(async () => { await wzSave(true); wzRedraw(); toast("样本量已确认并保存"); })();
  if (b.id === "wzall") { wzMutate(() => ["xb", "minor", "rec", "recon", "mask", "ai"].forEach((k) => (c.cons[k] = true))); return wzSave(true).then(() => wzRedraw()).catch((er) => toast(er.message, true)); }
  if (b.id === "wzunlock" || b.id === "wzunlock2") return guard(async () => {
    if (!confirm("解锁后可以修改立项。项目这期间照常按旧版本运行；改完要重新提交审查，通过并发布后，配额、问卷、监控才按新内容更新。确定解锁吗？")) return;
    const r = await api(`/projects/${WZ.p.id}/plan/unlock`, { method: "POST" });
    WZ.plan = { ...WZ.plan, ...r }; WZ.cfg = r.config; WZ.step = 0; toast("已解锁，可以修改");
    PROJECTS = null; await projects(true); WZ.p = (PROJECTS || []).find((x) => x.id === WZ.p.id) || WZ.p; wzDraw();
  })();
  if (d.qrow) { const T = c[d.qrow]; wzMutate(() => { T.rows.push({ id: wzUid("r"), vs: T.dims.map((x) => (wzValues(x)[0] || {}).id), t: 0, i: 0, f: 0, a: false }); }); return wzRedraw(); }
  if (d.qasync) { const [k, on] = d.qasync.split("|"), set = WZ.picks[k] || new Set(); if (!set.size) return toast("先在表里勾选要设置的行", true); wzMutate(() => c[k].rows.forEach((r) => { if (set.has(r.id)) r.a = on === "1"; })); return wzRedraw(); }
  if (d.qrowdel) { const set = WZ.picks[d.qrowdel] || new Set(); wzMutate(() => { c[d.qrowdel].rows = c[d.qrowdel].rows.filter((r) => !set.has(r.id)); }); WZ.picks[d.qrowdel] = new Set(); return wzRedraw(); }
  if (d.dvadd) { const input = document.getElementById("dvn-" + d.dvadd), v = input.value.trim(); if (!v) return; wzMutate(() => c.vars[d.dvadd].push({ id: wzUid("v"), name: v })); return wzRedraw(); }
  if (d.dvdel) { const [dim, id] = d.dvdel.split("|"); wzMutate(() => { c.vars[dim] = c.vars[dim].filter((v) => v.id !== id); for (const k of ["qt", "lt", "ft"]) { const i = c[k].dims.indexOf(dim); if (i >= 0) c[k].rows = c[k].rows.filter((r) => r.vs[i] !== id); } }); return wzRedraw(); }
  if (b.id === "dimadd") { const v = $("#dimnew").value.trim(); if (!v || c.vars[v]) return toast(v ? "已经有这个变量" : "先写变量名", true); wzMutate(() => { c.vars[v] = [{ id: wzUid("v"), name: "取值 1" }]; }); return wzRedraw(); }
  if (d.dimdel) { wzMutate(() => { delete c.vars[d.dimdel]; for (const k of ["qt", "lt", "ft"]) { const i = c[k].dims.indexOf(d.dimdel); if (i >= 0) { c[k].dims.splice(i, 1); c[k].rows = c[k].rows.map((r) => ({ ...r, vs: r.vs.filter((_, j) => j !== i) })); } } }); return wzRedraw(); }
  if (d.tab) { WZ.tab = d.tab; return wzRedraw(); }
  const act = { wzsubmit: ["plan/submit", "已提交审查"], wzapprove: ["plan/approve", "已通过审查"], wzpub: ["plan/publish", "已发布并锁定"] }[b.id];
  if (act) return guard(async () => {
    await wzSave(true);
    if (b.id === "wzpub" && !confirm("发布后立项会暂时锁定，研究方法和团队立即生效；之后要改需要管理员或项目经理解锁并重新审查。确定发布吗？")) return;
    const r = await api(`/projects/${WZ.p.id}/${act[0]}`, { method: "POST" });
    WZ.plan = { ...WZ.plan, ...r }; toast(act[1]);
    if (b.id === "wzpub") { PROJECTS = null; await projects(true); WZ.p = (PROJECTS || []).find((x) => x.id === WZ.p.id) || WZ.p; WZ.plan.live = true; }
    wzDraw();
  })();
  if (b.id === "wzreturn") return guard(async () => {
    const note = prompt("退回的原因（受访者看不到，团队会看到）：", "");
    if (note === null) return;
    const r = await api(`/projects/${WZ.p.id}/plan/return`, { method: "POST", body: { note } });
    WZ.plan = { ...WZ.plan, ...r }; toast("已退回"); wzDraw();
  })();
}

/* 新建项目：先起一个草稿，再进向导 */
function newProjectPrompt() {
  const m = modal(`<h2>新建项目</h2><p class="sm mut">先给项目起个名字，接下来在立项向导里一步一步配置（研究顺序、样本和配额、合规），审查通过后发布。</p>
    <form id="npf"><label class="f"><span>项目名称</span><input name="name" required maxlength="200" autofocus></label><div class="row" style="margin-top:14px"><button class="p">开始立项</button><button type="button" id="npc">取消</button></div></form>`);
  $("#npc", m).onclick = () => m.remove();
  $("#npf", m).onsubmit = guard(async (e) => {
    e.preventDefault();
    const p = await api("/projects/draft", { method: "POST", body: { name: new FormData(e.target).get("name") } });
    m.remove(); PROJECTS = null; await projects(true); WZ.p = null; WZ.step = 0; location.hash = `#/p/${p.id}/wiz`;
  });
}


/* ---------- 执行监控：配额进度（定量、深访、座谈会各一张看板） ---------- */
async function monitorView(p) {
  const my = renderSeq;
  const m = await api(`/projects/${p.id}/monitor`);
  if (stale(my)) return;
  if (!m.published) {
    shell(`${head("执行监控", "按立项里的配额，看每一行的目标、已就绪和缺口。")}<div class="card empty"><b>项目还没有发布。</b><span>在立项向导里通过合规审查并发布后，这里按配额开始计数。</span><a class="btn p" href="#/p/${p.id}/wiz">去立项向导</a></div>`, { active: "monitor", p });
    return;
  }
  const sub = shSub(p);  // the sub-project chosen in the project bar: only its rows
  for (const k of ["q", "i", "f"]) if (sub) m[k] = { ...m[k], rows: m[k].rows.filter((r) => r.label.split(" · ").includes(sub.name)) };
  const state = (a, b) => (b === 0 ? `<span class="pill g">未设</span>` : a >= b ? `<span class="pill r">已满</span>` : a >= b * 0.9 ? `<span class="pill o">接近满额</span>` : `<span class="pill">进行中</span>`);
  const board = (T, color, title, unit) => `<div class="bx ${color} wide"><div class="bxh"><i></i>${title}（按 ${T.dims.map(esc).join(" × ") || "—"}）</div>
    <div class="scroll"><table class="tbl"><thead><tr><th>${T.dims.map(esc).join(" × ") || "配额"}</th><th>目标（${unit}）</th><th>已就绪</th><th>缺口</th><th>进度</th><th>状态</th><th>执行方式</th></tr></thead><tbody>
    ${T.rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${r.target}</td><td>${r.ready}</td><td>${r.gap}</td><td style="min-width:140px"><div class="qbar o"><i style="width:${r.target ? Math.min(100, (r.ready / r.target) * 100) : 0}%"></i></div></td><td>${state(r.ready, r.target)}</td><td>${r.async === null ? '<span class="mut">—</span>' : r.async ? `<span class="tag o">异步 AI</span>` : '<span class="mut">真人主持</span>'}</td></tr>`).join("") || `<tr><td colspan="7" class="sm mut">没有配额行。到立项向导第 4 步添加（立项已锁定，要改请管理员或项目经理先解锁）。</td></tr>`}
    </tbody></table></div><div class="sm mut">合计：目标 ${T.target}，已就绪 ${T.ready}。</div></div>`;
  shell(`${head("执行监控", "配额按立项里定义的变量显示。“已就绪”是项目受访者库里符合这一行、且没有被甄别淘汰的人。")}
    ${board(m.q, "q", "定量配额看板", "份")}${board(m.i, "i", "深访配额看板", "人")}${board(m.f, "f", `座谈会配额看板（每场 ${m.fs} 人）`, "人")}
    <div class="note">“已就绪”靠受访者的标签和国家来对：给受访者打上和配额取值同名的标签（在“受访者招募”里），这里就会计数。</div>`, { active: "monitor", p });
}
