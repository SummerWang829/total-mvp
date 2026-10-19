/* 立项向导（原版星桐的 7 步向导）：基本信息 → 研究设计 → 人员与角色 → 人群与样本 → 渠道与触达 → 合规举证 → 确认发布。
   页面状态放在 WZ 里；每次改动 0.8 秒后自动保存（PUT /projects/{id}/plan），上一步/下一步前也会先保存。已发布的立项是锁定的，整页只读。
   依赖 index.html 里的 shell / head / api / esc / toast / guard / modal / $。 */
const WZ_STEPS = ["基本信息", "研究设计", "人员与角色", "人群与样本", "渠道与触达", "合规举证", "确认发布"];
const WZ_DESIGNS = {
  ql: ["先定性，后定量", "探索型：先摸清痛点和说法，再用问卷验证有多普遍。新品、新市场最常用。", ["定性探索", "定量验证", "定性追问"]],
  qt: ["先定量，后定性", "解释型：先用问卷找到问题，再从问卷里挑人追问和短深访。", ["定量摸底", "定性追问"]],
  sw: ["定性、定量、定性", "三明治：先探索，再验证，最后对关键人群深挖。大项目常用。", ["定性探索", "定量验证", "定性深挖"]],
};
const WZ_TYPES = {
  "使用习惯与态度": "人群细分、使用场景题库、动机编码表",
  "概念测试": "概念卡轮换顺序、随机分组、逐个概念对比",
  "视频与广告测试": "不可快进的播放、按时间点标记反应",
  "售后与体验": "低分自动邀请深访、投诉主题编码",
  "产品与包装测试": "样品发放与签收、开箱视频上传",
};
const WZ_LANGS = ["中文", "英语", "马来语", "泰米尔语"];
const WZ_STATUS = { draft: ["草稿", "o"], review: ["审查中", "o"], approved: ["审查通过，可发布", ""], published: ["已发布并锁定", ""] };
const WZ_PERMS = {  // what each account role can do in a project (shown as the permission preview)
  admin: ["招募", "邀请", "看个人信息", "写洞察"], researcher: ["招募", "邀请", "看个人信息", "写洞察"], recruiter: ["招募", "邀请"],
  moderator: ["发言", "观察"], observer: ["观察"], viewer: ["只读成果"],
};
const WZ_ROLE = { admin: "管理员", researcher: "研究员", recruiter: "招募员", moderator: "主持人", observer: "观察员", viewer: "客户（只读）" };
const WZ = { p: null, plan: null, cfg: null, step: 0, tab: "q", users: [], timer: null, saving: null, picks: {}, known: [] };

const wzLocked = () => WZ.plan.status === "published";
const wzMethod = (m) => WZ.cfg.methods.includes(m);
const wzInterviews = () => ["depth", "group", "async"].some(wzMethod);
const wzUid = (pre) => pre + Math.random().toString(36).slice(2, 8);
const wzNum = (v, d = 0) => { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 ? n : d; };
const wzSums = () => ({ q: WZ.cfg.qt.rows.reduce((a, r) => a + r.t, 0), i: WZ.cfg.lt.rows.reduce((a, r) => a + r.i, 0), f: WZ.cfg.ft.rows.reduce((a, r) => a + r.f, 0) });
const wzValues = (dim) => (WZ.cfg.vars[dim] || []);
const wzSel = (attr, val, opts, w) => `<select ${attr} ${w ? `style="max-width:${w}px"` : ""}>${opts.map((o) => `<option value="${esc(o[0])}" ${String(o[0]) === String(val) ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
const wzInp = (attr, v, extra = "") => `<input ${attr} value="${esc(v)}" ${extra}>`;
const wzCheck = (ok) => (ok ? `<span class="pill">✓ 通过</span>` : `<span class="pill r">待处理</span>`);

async function wizardView(p) {
  const my = renderSeq;
  const [plan, users] = await Promise.all([api(`/projects/${p.id}/plan`), api("/users")]);
  if (stale(my)) return;
  if (plan.legacy) {
    shell(`${head("立项向导", "这个项目创建时还没有立项向导，所以没有立项记录。新建项目会从向导开始。")}<div class="card empty"><b>没有立项记录</b><span>老项目照常使用，不受影响。</span><a class="btn p" href="#/p/${p.id}/home">回到项目总览</a></div>`, { active: "wiz", p });
    return;
  }
  Object.assign(WZ, { p, plan, cfg: plan.config, users: users.filter((u) => u.active), picks: {}, known: [], step: Math.min(WZ.p && WZ.p.id === p.id ? WZ.step : 0, 6) });
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
    WZ.plan = { ...WZ.plan, ...r }; WZ.cfg = r.config; WZ.name_edit = WZ.brief_edit = undefined;
    const lab = $("#wzstat"); if (lab) lab.textContent = "已保存";
  };
  if (now) return (WZ.saving = run());
  WZ.timer = setTimeout(() => { WZ.saving = run().catch((e) => toast(e.message, true)); }, 800);
  const lab = $("#wzstat"); if (lab) lab.textContent = "保存中…";
  return Promise.resolve();
}

/* ---------- pieces ---------- */
function wzDesignPick() {
  const cur = WZ.cfg.design;
  return `<div class="card" style="display:flex;flex-direction:column;gap:10px"><b>先选研究顺序</b><div class="grid">${Object.keys(WZ_DESIGNS).map((k) => `<label class="card pick ${cur === k ? "on" : ""}">
    <span class="chk"><input type="radio" name="design" data-w="design" value="${k}" ${cur === k ? "checked" : ""}><b>${WZ_DESIGNS[k][0]}</b></span>
    <div class="sm mut" style="margin-top:6px">${WZ_DESIGNS[k][1]}</div><div style="margin-top:6px">${WZ_DESIGNS[k][2].map((x, i) => `<span class="tag">${i + 1} ${x}</span>`).join("")}</div></label>`).join("")}</div>
    <div class="note">${cur === "ql" ? "定性的结论转成问卷题目和选项，进入问卷设计；做过定性的人不再放进定量样本。" : cur === "qt" ? "问卷里发现问题后，可以：① 针对原回答直接追问；② 邀请少数人做短深访。座谈会很少从定量里邀请。" : "第三阶段只对定量里发现的关键人群做短深访。"}</div></div>`;
}

function wzSubs() {
  const c = WZ.cfg, pms = [["", "未指定"], ...WZ.users.filter((u) => ["admin", "researcher"].includes(u.role)).map((u) => [u.id, u.name])];
  const single = c.split === "single";
  return `<div><b>子项目</b>
    <div class="row" style="margin:6px 0"><label class="chk"><input type="radio" name="split" data-split="country" ${single ? "" : "checked"}>按国家拆成子项目（日本、韩国……各一个）</label>
    <label class="chk"><input type="radio" name="split" data-split="single" ${single ? "checked" : ""}>只有一个子项目（在里面分组）</label></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>编号</th><th>${single ? "名称" : "国家（可改名）"}</th><th>执行语言</th><th>责任人</th><th>研究顺序</th><th></th></tr></thead><tbody>
    ${c.subs.map((s, i) => `<tr><td><b>S${i + 1}</b></td><td>${wzInp(`data-sub="${s.id}|name" aria-label="名称"`, s.name, 'size="12"')}</td>
      <td>${wzSel(`data-sub="${s.id}|lang" aria-label="执行语言"`, s.lang, WZ_LANGS.map((l) => [l, l]), 130)}</td>
      <td>${wzSel(`data-sub="${s.id}|pm" aria-label="责任人"`, s.pm, pms, 150)}</td>
      <td>${wzSel(`data-sub="${s.id}|design" aria-label="研究顺序"`, s.design, Object.keys(WZ_DESIGNS).map((k) => [k, WZ_DESIGNS[k][0]]), 170)}</td>
      <td>${c.subs.length > 1 ? `<button class="s" data-subdel="${s.id}" type="button">删除</button>` : ""}</td></tr>`).join("")}</tbody></table></div>
    ${single ? `<div class="note">一个子项目：定量一份问卷，定性按组拆分。</div>` : `<div class="row" style="margin-top:8px"><input id="subnew" placeholder="新增国家，例如：马来西亚" aria-label="新增国家"><button class="s p" id="subadd" type="button">+ 添加子项目</button></div>
    <div class="note">改名或删除后，配额和执行监控会同步更新。</div>`}</div>`;
}

function wzRoles() {
  const en = [wzMethod("quant"), wzMethod("depth"), wzMethod("group")], team = Object.fromEntries(WZ.cfg.team.map((m) => [m.user_id, m]));
  const us = WZ.users.filter((u) => u.role !== "admin" || true);
  return `<p class="sub">每个人参与哪些环节。能不能进项目由账号角色决定：研究员、招募员、客户在发布时加入项目；主持人和观察员按场次安排，不是项目成员。人员来自“团队”，在那里可以新建。</p>
    <div class="row"><span class="mut sm">按列勾选：</span>${["定量", "深访", "座谈会"].map((n, j) => en[j] ? `<span class="row" style="gap:4px"><b>${n}</b><button class="lnk" data-col="${j}|1" type="button">全选</button><button class="lnk" data-col="${j}|0" type="button">全不选</button></span>` : "").join("")}</div>
    <div class="scroll"><table class="tbl"><thead><tr><th>人员</th><th>角色</th><th>定量</th><th>深访</th><th>座谈会</th><th>权限预览</th></tr></thead><tbody>${us.map((u) => { const m = team[u.id] || {};
      return `<tr><td>${esc(u.name)}</td><td>${WZ_ROLE[u.role] || u.role}</td>${["q", "i", "f"].map((k, j) => `<td><input type="checkbox" aria-label="${esc(u.name)} ${["定量", "深访", "座谈会"][j]}" data-member="${u.id}|${k}" ${m[k] ? "checked" : ""} ${en[j] ? "" : "disabled"}></td>`).join("")}
      <td>${(WZ_PERMS[u.role] || []).map((t) => `<span class="tag">${t}</span>`).join("")}</td></tr>`; }).join("")}</tbody></table></div>
    <div class="note">只有主持人可以开麦；观察员和客户在后台，只看到受访者编号。招募员只能处理项目里的人，看不到访谈内容。</div>`;
}

function wzQuota(key, color, title, cols) {
  const c = WZ.cfg, T = c[key], names = Object.keys(c.vars);
  const dimSel = [0, 1].map((i) => (i < T.dims.length || (i === T.dims.length && T.dims.length < 2)) ? wzSel(`data-qdim="${key}|${i}" aria-label="配额变量 ${i + 1}"`, T.dims[i] || "", (i === 0 ? [] : [["", "（无）"]]).concat(names.map((n) => [n, n])), 150) : "").filter(Boolean).join('<span class="mut">×</span>');
  const sel = WZ.picks[key] || (WZ.picks[key] = new Set());
  const sums = cols.map((cl) => T.rows.reduce((a, r) => a + r[cl[0]], 0));
  return `<div class="bx ${color} wide"><div class="bxh"><i></i>${title}</div><div class="row"><span class="sm">配额变量：</span>${dimSel}</div>
    <div class="row selbar"><label class="chk"><input type="checkbox" data-qall="${key}" ${T.rows.length && T.rows.every((r) => sel.has(r.id)) ? "checked" : ""}>全选</label><span class="mut sm">已选 ${[...sel].filter((id) => T.rows.some((r) => r.id === id)).length} / ${T.rows.length}</span>
      <button class="s" data-qrow="${key}" type="button">+ 添加一行</button><button class="s" data-qrowdel="${key}" type="button">删除选中</button></div>
    <div class="scroll"><table class="tbl"><thead><tr><th></th>${T.dims.map((d) => `<th>${esc(d)}</th>`).join("")}${cols.map((cl) => `<th>${cl[1]}</th>`).join("")}${key === "ft" ? "<th>座谈会（人）</th>" : ""}</tr></thead><tbody>
    ${T.rows.map((r) => `<tr><td><input type="checkbox" aria-label="选中这一行" data-qpick="${key}|${r.id}" ${sel.has(r.id) ? "checked" : ""}></td>${T.dims.map((d, i) => `<td>${wzSel(`data-qcell="${key}|${r.id}|v${i}" aria-label="${esc(d)}"`, r.vs[i], wzValues(d).map((v) => [v.id, v.name]), 170)}</td>`).join("")}
      ${cols.map((cl) => `<td>${wzInp(`type="number" min="0" data-qcell="${key}|${r.id}|${cl[0]}" aria-label="${cl[1]}"`, r[cl[0]], 'style="width:90px"')}</td>`).join("")}${key === "ft" ? `<td>${r.f * c.n.fs}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${T.dims.length + cols.length + 2}" class="mut sm">还没有配额行，点“+ 添加一行”。</td></tr>`}
    <tr><td></td><td colspan="${T.dims.length}"><b>合计</b></td>${sums.map((s) => `<td><b>${s}</b></td>`).join("")}${key === "ft" ? `<td><b>${sums[0] * c.n.fs}</b></td>` : ""}</tr></tbody></table></div></div>`;
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
    ${c.tags.length ? `<div>${c.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}${c.est != null ? `<div class="note">${esc(c.estNote || `这个项目的受访者库里有 ${c.est} 人符合。`)}</div>` : ""}
    <div class="grid3">
      <div class="bx q"><div class="bxh"><i></i>定量问卷</div><label class="field">样本量（份）${wzInp('type="number" min="0" data-n="q"', c.n.q)}</label><div class="sm mut">配额合计 ${s.q} ${ok(s.q, c.n.q)}</div></div>
      <div class="bx i"><div class="bxh"><i></i>深访 IDI</div><label class="field">深访人数（人）${wzInp('type="number" min="0" data-n="i"', c.n.i)}</label><div class="sm mut">配额合计 ${s.i} ${ok(s.i, c.n.i)}</div></div>
      <div class="bx f"><div class="bxh"><i></i>座谈会 FGD</div><div class="stack"><label class="field">座谈会场数（场）${wzInp('type="number" min="0" data-n="f"', c.n.f)}</label><label class="field">每场人数（人）${wzInp('type="number" min="3" max="10" data-n="fs"', c.n.fs)}</label></div><div class="sm mut">共 ${c.n.f * c.n.fs} 人 · 配额场数合计 ${s.f} ${ok(s.f, c.n.f)}</div></div>
    </div>
    ${wzDims()}
    <div class="note">先定义变量和取值（上面），再给定量、深访、座谈会各选各的配额变量。这些配额会进入“执行监控”。</div>
    ${wzQuota("qt", "q", "定量配额", [["t", "目标（份）"]])}${wzQuota("lt", "i", "深访配额", [["i", "深访（人）"]])}${wzQuota("ft", "f", "座谈会配额", [["f", "座谈会（场）"]])}`;
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
    <div class="card"><b>补充确认</b><div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">${tick("xb", "已签署标准合同或完成跨境评估（c6）")}${tick("minor", "人群限定 18 岁以上并在筛选题校验（c11）")}
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
  return `<div class="grid"><div class="card"><h3>${esc(P.name)}</h3><div class="sm mut">${c.subs.map((s) => esc(s.name)).join("、")} · 留存 ${P.retention_days} 天 · ${esc(c.type)} · ${WZ_DESIGNS[c.design][0]}</div>
      <div style="margin-top:8px">${c.methods.map((m) => `<span class="tag">${M[m]}</span>`).join("")}${c.n.q ? `<span class="tag">定量 ${c.n.q}</span>` : ""}${c.n.i ? `<span class="tag">深访 ${c.n.i}</span>` : ""}${c.n.f ? `<span class="tag">座谈会 ${c.n.f} 场 × ${c.n.fs}</span>` : ""}</div></div>
    <div class="card"><h3>发布后会发生什么</h3><div class="sm mut" style="margin-top:6px">项目与配额锁定为 v1，邀请模板不可再改；所选研究方法进入左侧菜单；团队成员加入项目；“执行监控”按配额开始计数。</div></div></div>
    <div class="row"><button class="p" id="wzpub" type="button" ${approved ? "" : "disabled"}>${done ? "已发布并锁定" : "发布并锁定"}</button>${approved || done ? "" : `<span class="sm mut">需要先通过合规审查（第 6 步）。</span>`}${done ? `<a class="btn" href="#/p/${WZ.p.id}/monitor">去看执行监控</a><a class="btn" href="#/p/${WZ.p.id}/home">去项目总览</a>` : ""}</div>`;
}

function wzBody() {
  const c = WZ.cfg;
  switch (WZ.step) {
    case 0: return `<div class="grid"><label class="field">主项目名称${wzInp('data-meta="name" aria-label="主项目名称"', WZ.name_edit ?? WZ.plan.name)}</label>
      <label class="field">数据留存期限<input value="${WZ.plan.retention_days} 天（平台统一规定）" disabled aria-label="数据留存期限"><span class="mut" style="font-weight:400">同意书里写明的就是这个期限：项目结项后 ${WZ.plan.retention_days} 天自动删除个人数据，洞察结论保留。</span></label></div>
      <label class="field">研究背景（选填，只给团队看）<textarea data-meta="brief" rows="3">${esc(WZ.brief_edit ?? WZ.plan.brief ?? "")}</textarea></label>${wzSubs()}`;
    case 1: return `${wzDesignPick()}<label class="field" style="max-width:360px">研究类型（带出模板）${wzSel('data-w="type"', c.type, Object.keys(WZ_TYPES).map((x) => [x, x]))}</label>
      <div class="note">选“${esc(c.type)}”会预置：${WZ_TYPES[c.type]}。</div>
      <div class="card" style="display:flex;flex-direction:column;gap:8px"><b>研究方式（决定左侧菜单里有哪些）</b>${[["depth", "深访（一对一）", "真人或 AI 主持，实时字幕、录制、转写、洞察"], ["group", "座谈会", "最多 10 人同场，发言公平仪表盘和副主持提示卡"], ["async", "异步 AI 研究", "受访者随时自己和 AI 访谈员完成一场文字访谈"], ["listening", "舆情监测", "采集新闻、App 评论和公开网页上的声音"], ["quant", "定量结果", "接入外部问卷平台，推送人和结果"]].map((m) => `<label class="chk"><input type="checkbox" data-method="${m[0]}" ${wzMethod(m[0]) ? "checked" : ""}><span><b>${m[1]}</b> <span class="mut sm">${m[2]}</span></span></label>`).join("")}</div>`;
    case 2: return wzRoles();
    case 3: return wzSample();
    case 4: return wzChannels();
    case 5: return wzCompliance();
    default: return wzConfirm();
  }
}

function wzDraw() {
  const P = WZ.plan, [stLabel, stCls] = WZ_STATUS[P.status] || [P.status, ""], locked = wzLocked(), s = WZ.step;
  shell(`${head("立项向导", "一次选好角色、样本、人群和合规", `<span class="pill ${stCls}">${stLabel}</span><span class="sm mut" id="wzstat" role="status">${locked ? "已锁定，只读" : "改动会自动保存"}</span>`)}
    <div class="chain" role="list">${WZ_STEPS.map((x, i) => `<button class="step ${s === i ? "on" : ""}" data-step="${i}" type="button" ${s === i ? 'aria-current="step"' : ""}>${i + 1} ${x}</button>`).join("")}</div>
    <fieldset class="card wzbody" ${locked ? "disabled" : ""}>${wzBody()}</fieldset>
    <div class="row" style="justify-content:space-between"><button id="wzprev" type="button" ${s ? "" : "disabled"}>上一步</button><button class="p" id="wznext" type="button" ${s < 6 ? "" : "disabled"}>下一步</button></div>`,
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
  root.querySelectorAll("[data-step]").forEach((b) => (b.onclick = guard(async () => { await wzSave(true); WZ.step = +b.dataset.step; wzDraw(); scrollTo(0, 0); })));
  $("#wzprev").onclick = guard(async () => { await wzSave(true); WZ.step--; wzDraw(); scrollTo(0, 0); });
  $("#wznext").onclick = guard(async () => { await wzSave(true); WZ.step++; wzDraw(); scrollTo(0, 0); });
  root.addEventListener("change", wzChange);
  root.onclick = (e) => { const t = e.target.closest("button"); if (t) wzClick(t, e); };
}

function wzChange(e) {
  const t = e.target, d = t.dataset;
  if (wzLocked()) return;
  const c = WZ.cfg;
  if (d.meta) { WZ[d.meta + "_edit"] = t.value; return wzSave(); }
  if (d.w === "design") { c.design = t.value; c.subs.forEach((s) => (s.design = t.value)); wzSave(); return wzRedraw(); }
  if (d.w === "type") { c.type = t.value; wzSave(); return wzRedraw(); }
  if (d.w === "pop") { c.pop = t.value; return wzSave(); }
  if (d.split) { wzSplit(d.split); return; }
  if (d.sub) { const [id, f] = d.sub.split("|"), s = c.subs.find((x) => x.id === id); s[f] = t.value; if (f === "name" && !t.value.trim()) s.name = "未命名"; wzSyncCountry(); wzSave(); return wzRedraw(); }
  if (d.method) { const set = new Set(c.methods); t.checked ? set.add(d.method) : set.delete(d.method); if (!set.size) { t.checked = true; return toast("至少选一种研究方式", true); } c.methods = ["depth", "group", "async", "listening", "quant"].filter((m) => set.has(m)); wzSave(); return; }
  if (d.member) { const [uid, k] = d.member.split("|"); let m = c.team.find((x) => x.user_id === uid); if (!m) c.team.push((m = { user_id: uid, q: false, i: false, f: false })); m[k] = t.checked; c.team = c.team.filter((x) => x.q || x.i || x.f); wzSave(); return; }
  if (d.n) { c.n[d.n] = wzNum(t.value, d.n === "fs" ? 6 : 0); if (d.n === "fs") c.n.fs = Math.min(10, Math.max(3, c.n.fs)); wzSave(); return wzRedraw(); }
  if (d.qdim) return wzQDim(d.qdim, t.value);
  if (d.qcell) return wzQCell(d.qcell, t);
  if (d.qpick) { const [k, id] = d.qpick.split("|"), set = WZ.picks[k] || (WZ.picks[k] = new Set()); t.checked ? set.add(id) : set.delete(id); return wzRedraw(); }
  if (d.qall) { const set = (WZ.picks[d.qall] = new Set()); if (t.checked) c[d.qall].rows.forEach((r) => set.add(r.id)); return wzRedraw(); }
  if (d.dvn) { const [dim, id] = d.dvn.split("|"), v = c.vars[dim].find((x) => x.id === id); v.name = t.value.trim() || v.name; wzSave(); return wzRedraw(); }
  if (d.tab === undefined && d.tpl) { c[d.tpl] = t.value; wzSave(); return wzRedraw(); }
  if (d.lang) { c.langs[d.lang] = t.checked; wzSave(); return wzRedraw(); }
  if (d.ip === "on") { c.ip.on = t.checked; return wzSave(); }
  if (t.matches("[data-ipwin]")) { c.ip.win = +t.value; return wzSave(); }
  if (d.n2) { c[d.n2] = wzNum(t.value, d.n2 === "inv" ? 1 : 0); return wzSave(); }
  if (d.src) { c.src = d.src; return wzSave(); }
  if (d.cons) { c.cons[d.cons] = t.checked; wzSave(true).then(() => wzRedraw()).catch((er) => toast(er.message, true)); return; }
}

function wzSplit(kind) {
  const c = WZ.cfg;
  c.split = kind;
  if (kind === "single") { const s = c.subs[0]; c.subs = [{ ...s, name: s.name }]; } else if (c.subs.length === 0) c.subs = [{ id: wzUid("s"), name: "新加坡", lang: "英语", pm: "", design: c.design }];
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
    c.estNote = r.tags.length ? `这个项目的受访者库共 ${r.pool} 人，其中 ${r.reach} 人带有这些标签。` : `这个项目的受访者库共 ${r.pool} 人。${r.known.length ? "描述里没有提到已有的标签（已有标签：" + r.known.slice(0, 8).join("、") + "）。" : "还没有人带标签。"}`;
    await wzSave(true); wzRedraw();
  })();
  if (b.id === "subadd") { const v = $("#subnew").value.trim(); if (!v) return toast("先写国家名", true); wzMutate(() => { c.subs.push({ id: wzUid("s"), name: v, lang: "英语", pm: "", design: c.design }); wzSyncCountry(); }); return wzRedraw(); }
  if (d.subdel) { wzMutate(() => { c.subs = c.subs.filter((s) => s.id !== d.subdel); wzSyncCountry(); }); return wzRedraw(); }
  if (d.col) { const [j, on] = d.col.split("|"), k = ["q", "i", "f"][+j]; wzMutate(() => { WZ.users.forEach((u) => { let m = c.team.find((x) => x.user_id === u.id); if (!m) c.team.push((m = { user_id: u.id, q: false, i: false, f: false })); m[k] = on === "1"; }); c.team = c.team.filter((x) => x.q || x.i || x.f); }); return wzRedraw(); }
  if (d.qrow) { const T = c[d.qrow]; wzMutate(() => { T.rows.push({ id: wzUid("r"), vs: T.dims.map((x) => (wzValues(x)[0] || {}).id), t: 0, i: 0, f: 0 }); }); return wzRedraw(); }
  if (d.qrowdel) { const set = WZ.picks[d.qrowdel] || new Set(); wzMutate(() => { c[d.qrowdel].rows = c[d.qrowdel].rows.filter((r) => !set.has(r.id)); }); WZ.picks[d.qrowdel] = new Set(); return wzRedraw(); }
  if (d.dvadd) { const input = document.getElementById("dvn-" + d.dvadd), v = input.value.trim(); if (!v) return; wzMutate(() => c.vars[d.dvadd].push({ id: wzUid("v"), name: v })); return wzRedraw(); }
  if (d.dvdel) { const [dim, id] = d.dvdel.split("|"); wzMutate(() => { c.vars[dim] = c.vars[dim].filter((v) => v.id !== id); for (const k of ["qt", "lt", "ft"]) { const i = c[k].dims.indexOf(dim); if (i >= 0) c[k].rows = c[k].rows.filter((r) => r.vs[i] !== id); } }); return wzRedraw(); }
  if (b.id === "dimadd") { const v = $("#dimnew").value.trim(); if (!v || c.vars[v]) return toast(v ? "已经有这个变量" : "先写变量名", true); wzMutate(() => { c.vars[v] = [{ id: wzUid("v"), name: "取值 1" }]; }); return wzRedraw(); }
  if (d.dimdel) { wzMutate(() => { delete c.vars[d.dimdel]; for (const k of ["qt", "lt", "ft"]) { const i = c[k].dims.indexOf(d.dimdel); if (i >= 0) { c[k].dims.splice(i, 1); c[k].rows = c[k].rows.map((r) => ({ ...r, vs: r.vs.filter((_, j) => j !== i) })); } } }); return wzRedraw(); }
  if (d.tab) { WZ.tab = d.tab; return wzRedraw(); }
  const act = { wzsubmit: ["plan/submit", "已提交审查"], wzapprove: ["plan/approve", "已通过审查"], wzpub: ["plan/publish", "已发布并锁定"] }[b.id];
  if (act) return guard(async () => {
    await wzSave(true);
    if (b.id === "wzpub" && !confirm("发布后立项会锁定，研究方法和团队立即生效，不能再改。确定发布吗？")) return;
    const r = await api(`/projects/${WZ.p.id}/${act[0]}`, { method: "POST" });
    WZ.plan = { ...WZ.plan, ...r }; toast(act[1]);
    if (b.id === "wzpub") { PROJECTS = null; await projects(true); WZ.p = (PROJECTS || []).find((x) => x.id === WZ.p.id) || WZ.p; }
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
    <div class="scroll"><table class="tbl"><thead><tr><th>${T.dims.map(esc).join(" × ") || "配额"}</th><th>目标（${unit}）</th><th>已就绪</th><th>缺口</th><th>进度</th><th>状态</th></tr></thead><tbody>
    ${T.rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${r.target}</td><td>${r.ready}</td><td>${r.gap}</td><td style="min-width:140px"><div class="qbar o"><i style="width:${r.target ? Math.min(100, (r.ready / r.target) * 100) : 0}%"></i></div></td><td>${state(r.ready, r.target)}</td></tr>`).join("") || `<tr><td colspan="6" class="sm mut">没有配额行。到立项向导第 4 步添加（已发布的立项是锁定的，需要新项目）。</td></tr>`}
    </tbody></table></div><div class="sm mut">合计：目标 ${T.target}，已就绪 ${T.ready}。</div></div>`;
  shell(`${head("执行监控", "配额按立项里定义的变量显示。“已就绪”是项目受访者库里符合这一行、且没有被甄别淘汰的人。")}
    ${board(m.q, "q", "定量配额看板", "份")}${board(m.i, "i", "深访配额看板", "人")}${board(m.f, "f", `座谈会配额看板（每场 ${m.fs} 人）`, "人")}
    <div class="note">“已就绪”靠受访者的标签和国家来对：给受访者打上和配额取值同名的标签（在“受访者招募”里），这里就会计数。</div>`, { active: "monitor", p });
}
