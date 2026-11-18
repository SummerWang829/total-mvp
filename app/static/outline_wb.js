/* 大纲工作台 · 「生成访问大纲 › 生成大纲」页（阶段 2）。
   左侧栏里的步骤和模块是“生成大纲”的子栏（#owsteps，由 index.html 的二级菜单带走）；内容区是当前这一步；右侧小星星助手占满高度，建议在它最下方。
   所有数据来自 GET /api/projects/{pid}/outline-wb；页面只展示和提交，规则都在服务器上。 */
(() => {
  const DEPTH = [["none", "不追问"], ["shallow", "1 次"], ["deep", "1 次以上"]];
  const SRC = { project: "来自项目设置", answer: "你的回答", inferred: "AI 推测", default: "默认", none: "还没有" };
  const FORMAT = { audio: "纯音频", text: "文字访谈", text_voice: "文字+语音转录", video: "视频", video_screen: "视频+屏幕共享" };
  const KIND_LABEL = { question: "题目", concept: "概念卡", video: "视频片段", website: "网站任务", rating: "打分题", note: "说明" };
  const S = { pid: "", d: null, step: "", mod: "", sel: "", msgs: [], adopt: null, busy: false, form: null };
  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => [...r.querySelectorAll(s)];
  const E = (s) => (typeof esc === "function" ? esc(s) : String(s ?? ""));

  async function call(path, method = "GET", body) {
    try { return await api(`/projects/${S.pid}/outline-wb${path}`, { method, body }); }
    catch (e) { toast(e.message || "没有成功", true); throw e; }
  }
  async function act(path, method, body) {
    if (S.busy) return;
    S.busy = true; paint();
    try { S.d = await call(path, method, body); S.adopt = null; if (!S.step || !stepOk(S.step)) S.step = S.d.step; } catch { /* the toast already said why */ }
    S.busy = false; paint();
  }

  const mods = () => (S.d?.overview?.modules || []);
  const gates = () => S.d?.gates || {};
  const stepOk = (k) => ({ goal: true, people: true, overview: mods().length > 0, questions: !!gates().overview, focus: mods().length > 0 }[k]);
  const stepDone = (k) => ({ goal: !!gates().goal, people: mods().length > 0, overview: !!gates().overview, questions: !!gates().questions, focus: false }[k]);

  function menu() {
    const host = q("#owsteps"); if (!host || !S.d) return;
    host.innerHTML = S.d.steps.map((s, i) => {
      const on = S.step === s.key, ok = stepOk(s.key);
      const head = `<a href="#" class="l2sub ow-l3${on ? " on" : ""}${ok ? "" : " ow-off"}" data-ow-step="${s.key}">${"①②③④⑤"[i]} ${E(s.label)}<span class="ow-st ${stepDone(s.key) ? "ok" : on ? "cur" : ""}">${stepDone(s.key) ? "✓" : on ? "●" : ok ? "" : "🔒"}</span></a>`;
      const sub = s.key === "questions" && ok ? mods().map((m) => `<a href="#" class="l2sub ow-l4${S.step === "questions" && S.mod === m.id ? " on" : ""}" data-ow-mod="${E(m.id)}">${E(m.name)}<span class="ow-st ${m.status === "confirmed" ? "ok" : m.status === "draft" ? "cur" : ""}">${m.status === "confirmed" ? "✓" : m.status === "draft" ? "●" : ""}</span></a>`).join("") : "";
      return head + sub;
    }).join("");
  }

  // ---- 内容区 -------------------------------------------------------------------------------------
  function slotRows() {
    const L = { research_type: "研究类型", key_questions: "要回答的问题", audience: "访谈对象", minutes: "时长", materials: "物料", price: "价格怎么谈" };
    const v = (k, s) => (k === "key_questions" ? (s.value || []).map((x, i) => `${i + 1}. ${E(x)}`).join("<br>") : k === "minutes" ? `${E(s.value)} 分钟` : Array.isArray(s.value) ? E(s.value.join("、") || "不看素材") : E(s.value || "—"));
    const c = S.d.context;
    const base = `<div class="ow-slot"><span class="k">研究背景</span><span class="v">${E(c.background) || "—"}</span><span class="ow-tag">来自项目设置</span></div>`;
    return base + Object.entries(S.d.slots).map(([k, s]) => `<div class="ow-slot"><span class="k">${L[k] || k}</span><span class="v">${v(k, s)}</span><span class="ow-tag ${s.source === "inferred" || s.source === "default" ? "w" : ""}">${SRC[s.source]}${s.source === "inferred" ? ` · 把握 ${s.confidence.toFixed(1)}` : ""}</span></div>`).join("");
  }
  function roundCard(r) {
    const qs = S.d.round_questions[r] || [];
    if (!qs.length) return "";
    return `<div class="card ow-card"><h3>第 ${r} 轮 · 还需要确认 <span class="mut sm">（${qs.length} 个问题，已有的不再问）</span></h3>` + qs.map((x) => {
      const cur = S.d.slots[x.slot]?.value;
      const opts = x.options.map((o) => `<button type="button" class="ow-opt${o.recommended ? " rec" : ""}" data-ow-ans="${x.slot}" data-v='${E(JSON.stringify(o.value))}'>${E(o.text)}${o.recommended ? " · 推荐" : ""}</button>`).join("");
      const free = x.free_text ? `<textarea class="ow-ta" aria-label="${E(x.question)}" data-ow-free="${x.slot}" rows="3" placeholder="${x.slot === "key_questions" ? "每行一个问题，最多 4 个" : "写一句话"}">${x.slot === "key_questions" ? E((cur || []).join("\n")) : E(cur || "")}</textarea><button type="button" class="s" data-ow-freeok="${x.slot}">确定</button>` : "";
      return `<div class="ow-q"><b>${E(x.question)}</b><div class="ow-opts">${opts}</div>${free}</div>`;
    }).join("") + `</div>`;
  }
  function goalView() {
    return `<div class="card ow-card"><h3>已经知道的</h3>${slotRows()}</div>${roundCard(1)}
      <div class="ow-actions"><button type="button" class="p" data-ow="goal-next">${gates().goal ? "继续" : "确认目标，继续"}</button></div>`;
  }
  function peopleView() {
    const c = roundCard(2) + roundCard(3);
    return `<div class="card ow-card"><h3>已经知道的</h3>${slotRows()}</div>${c}
      <div class="ow-actions"><button type="button" class="p" data-ow="build">${mods().length ? "按这些回答重新生成大纲总览" : "生成大纲总览"}</button></div>`;
  }
  const depthSel = (cur, attr) => `<select class="ow-sel" aria-label="追问深度" ${attr}>${DEPTH.map(([v, l]) => `<option value="${v}"${v === cur ? " selected" : ""}>${l}</option>`).join("")}</select>`;
  function overviewView() {
    const o = S.d.overview, c = S.d.context;
    if (!o.modules.length) return `<div class="card ow-card mut">正在生成大纲总览……</div>`;
    const rows = o.modules.map((m) => `<tr class="${m.tight ? "tight" : ""}"><td><input class="ow-in nm" aria-label="模块名称" data-ow-m="${E(m.id)}" data-f="name" value="${E(m.name)}"></td>
      <td><input class="ow-in" aria-label="模块目的" data-ow-m="${E(m.id)}" data-f="purpose" value="${E(m.purpose)}"><input class="ow-in sm mut" aria-label="要了解的内容" data-ow-m="${E(m.id)}" data-f="content" value="${E(m.content)}">${m.materials.length ? m.materials.map((x) => `<span class="ow-tag">${E({ concept: "概念卡", image: "图片", video: "视频片段", website: "网站任务" }[x] || x)}</span>`).join("") : ""}</td>
      <td class="c">${m.serves.length ? m.serves.map((i) => "①②③④"[i - 1]).join("") : "—"}</td><td><input class="ow-in num" type="number" aria-label="时长（分钟）" min="1" max="120" data-ow-m="${E(m.id)}" data-f="minutes" value="${m.minutes}"></td><td class="c">${m.questions}</td>
      <td>${depthSel(m.depth, `data-ow-m="${E(m.id)}" data-f="depth"`)}</td></tr>`).join("");
    const fnd = o.findings.map((f) => `<span class="ow-pill ${f.level}">${f.level === "ok" ? "通过" : "注意"}</span><span class="fx">${E(f.text)}</span>`).join("");
    return `<div class="card ow-card"><h3>这份大纲是什么</h3>
      <div class="ow-slot"><span class="k">研究背景</span><span class="v">${E(c.background) || "—"}</span><span class="ow-tag">来自项目设置</span></div>
      <div class="ow-slot"><span class="k">要回答的问题</span><span class="v">${(c.goals.length ? c.goals : S.d.slots.key_questions.value || []).map((x, i) => `${"①②③④"[i]} ${E(x)}`).join("　")}</span></div>
      <div class="ow-slot"><span class="k">访谈对象</span><span class="v">${E(c.audience) || "—"}</span><span class="ow-tag">来自甄别问卷</span></div>
      <div class="ow-slot"><span class="k">形式与语言</span><span class="v">${E({ ai: "AI 主持", human: "真人主持" }[c.host] || c.host)} · ${E(FORMAT[c.format] || c.format)} · ${E(c.language === "zh" ? "中文" : "English")}</span><span class="ow-tag">来自项目设置</span></div></div>
      <div class="card ow-card"><h3>大纲由这几部分组成</h3>
      <table class="ow-tbl"><thead><tr><th style="width:90px">模块</th><th>目的与要了解的内容</th><th class="c" style="width:44px">问题</th><th class="c" style="width:78px">时长（分）</th><th class="c" style="width:46px">题数</th><th style="width:96px">追问</th></tr></thead><tbody>${rows}
      <tr class="tot"><td>合计</td><td></td><td></td><td>${o.totals.minutes} 分</td><td class="c">${o.totals.questions}</td><td></td></tr></tbody></table></div>
      <div class="card ow-card"><h3>检查</h3><div class="ow-find">${fnd}</div>
      <div class="ow-actions"><button type="button" class="p" data-ow="confirm-ov">${gates().overview ? "继续写题目" : "确认总览，开始写第一个模块"}</button><button type="button" data-ow="back-people">上一步</button></div></div>`;
  }
  const itemRow = (it) => `<div class="ow-item${S.sel === it.uid ? " sel" : ""}" data-ow-sel="${it.uid}">
      <div class="h"><span class="lab">${E(it.label)}</span><span class="kind">${KIND_LABEL[it.kind] || ""}</span>
      <textarea class="ow-in t" rows="1" aria-label="题目文字" data-ow-i="${it.uid}" data-f="text">${E(it.text)}</textarea>
      <span class="ow-min">${it.minutes} 分</span><button type="button" class="ow-x" data-ow-del="${it.uid}" aria-label="删除这一条">×</button></div>
      ${it.kind === "question" || it.kind === "rating" ? `<div class="fu">${it.followups.length ? "追问：" + E(it.followups.join(" · ")) : ""}<span class="ow-ctl">${depthSel(it.depth, `data-ow-i="${it.uid}" data-f="depth"`)}<label class="bk"><input type="checkbox" aria-label="设为备选" data-ow-i="${it.uid}" data-f="backup"${it.backup ? " checked" : ""}> 备选</label></span></div>` : this_cfg(it)}</div>`;
  function this_cfg(it) {
    const c = it.config || {}, p = c.precheck;
    const parts = [c.url ? `网址 ${E(c.url)}` : "", c.time_limit_s ? `时间上限 ${Math.round(c.time_limit_s / 60)} 分钟` : "", c.scale ? `量表 1–${c.scale}` : "", c.title ? E(c.title) : ""].filter(Boolean).join(" · ");
    const pre = p ? `<span class="ow-pill ${p.https && p.public_host && !p.maybe_login ? "ok" : "warn"}">${p.https ? "https" : "不是 https"}</span>${p.maybe_login ? `<span class="ow-pill warn">地址像要登录</span>` : ""}<span class="ow-pill">只检查了地址，没有实际打开</span>` : "";
    return `<div class="fu">${parts}${pre}</div>`;
  }
  function insertForm(after) {
    const f = S.form;
    if (!f || f.after !== after) return `<div class="ow-ins" data-after="${after}">＋ 插入 ${["question", "concept", "video", "website", "rating"].map((k) => `<button type="button" class="ow-chipb" data-ow-ins="${k}" data-after="${after}">${KIND_LABEL[k]}</button>`).join("")}</div>`;
    const web = f.kind === "website" || f.kind === "video";
    return `<div class="ow-ins open"><b>插入${KIND_LABEL[f.kind]}</b><textarea class="ow-ta" id="ow-f-text" aria-label="要对受访者说的话" rows="2" placeholder="要对受访者说的话"></textarea>
      ${web ? `<input class="ow-in" id="ow-f-url" aria-label="物料地址" placeholder="公开的 https 地址">` : ""}${f.kind === "website" ? `<input class="ow-in num" id="ow-f-limit" type="number" aria-label="时间上限（分钟）" placeholder="时间上限（分钟）" value="4">` : ""}${f.kind === "rating" ? `<input class="ow-in num" id="ow-f-scale" type="number" aria-label="量表长度" value="10" min="3" max="11"> <span class="mut sm">量表 1–N</span>` : ""}
      <div class="ow-actions"><button type="button" class="p s" data-ow="ins-ok" data-after="${after}">插入</button><button type="button" class="s" data-ow="ins-cancel">取消</button></div></div>`;
  }
  function questionsView() {
    const m = mods().find((x) => x.id === S.mod) || mods().find((x) => x.status !== "confirmed") || mods()[0];
    if (!m) return `<div class="card ow-card">还没有大纲总览。</div>`;
    S.mod = m.id;
    const next = mods()[mods().indexOf(m) + 1];
    const head = `<div class="card ow-card ow-mh"><b>${E(m.name)}</b><span class="ow-pill">${m.minutes} 分钟</span><span class="mut sm">追问</span>${depthSel(m.depth, `data-ow-m="${E(m.id)}" data-f="depth"`)}<span class="ow-pill ${m.status === "confirmed" ? "ok" : ""}">${{ todo: "还没写", draft: "待确认", confirmed: "已确认" }[m.status]}</span>
      <span class="sp"></span><button type="button" data-ow="gen" data-m="${E(m.id)}">${m.items.length ? "重新生成本模块" : "生成本模块题目"}</button>${m.items.length ? `<button type="button" class="p" data-ow="conf-mod" data-m="${E(m.id)}">${next ? "确认本模块，写下一个" : "确认本模块"}</button>` : ""}</div>`;
    const body = m.items.length ? m.items.map((it) => itemRow(it) + insertForm(it.uid)).join("") : `<div class="card ow-card mut">这个模块的目的：${E(m.purpose)}。点上面的“生成本模块题目”。</div>`;
    return head + body;
  }
  function focusView() {
    const items = S.d.items.filter((i) => i.kind === "question" || i.kind === "rating");
    const rows = S.d.focus.map((f) => `<tr><td>${f.role === "client" ? "客户" : "研究员"}</td><td>${E(f.text)}</td><td><select class="ow-sel" aria-label="对应的题" data-ow-link="${f.id}"><option value="">没有对应的题</option>${items.map((i) => `<option value="${i.uid}"${i.uid === f.item_uid ? " selected" : ""}>${E(i.label)} ${E(i.text.slice(0, 18))}</option>`).join("")}</select></td></tr>`).join("");
    const miss = S.d.focus.filter((f) => !f.item_uid).length;
    return `<div class="card ow-card"><h3>关注问题 <span class="mut sm">发布前，每个关注问题都要对应到一道题</span></h3>
      <table class="ow-tbl"><thead><tr><th style="width:70px">谁提的</th><th>问题</th><th style="width:240px">对应到</th></tr></thead><tbody>${rows || `<tr><td colspan="3" class="mut">还没有关注问题。</td></tr>`}</tbody></table>
      <div class="ow-add"><input class="ow-in" id="ow-focus-in" aria-label="新增关注问题" placeholder="新增关注问题"><button type="button" class="s" data-ow="focus-add">＋ 新增</button></div></div>
      <div class="card ow-card"><h3>发布前检查</h3><div class="ow-find">${miss ? `<span class="ow-pill warn">注意</span><span class="fx">${miss} 个关注问题没有对应的题</span>` : `<span class="ow-pill ok">通过</span><span class="fx">关注问题都有对应的题</span>`}</div></div>`;
  }

  // ---- 右侧对话 -----------------------------------------------------------------------------------
  function revCard(r) {
    const done = r.status !== "pending", blocked = r.quality?.blocked;
    const t = r.after?.text ?? r.after?.label ?? r.after?.minutes ?? "";
    return `<div class="ow-rev ${done ? "done" : ""}"><div class="sm mut">${E({ rewrite: "改写", add_followup: "加追问", insert: "插入", rename: "改题号", minutes: "改时长", config: "改配置" }[r.op] || r.op)} · 去向：${E({ outline: "大纲", material: "物料", host_engine: "主持引擎" }[r.destination] || "大纲")}</div>
      ${r.before?.text ? `<div class="bf">${E(r.before.text)}</div>` : ""}<div class="af">${E(t)}</div>${r.reason ? `<div class="sm mut">理由：${E(r.reason)}</div>` : ""}
      ${(r.quality?.issues || []).map((i) => `<span class="ow-pill ${i.level === "block" ? "bad" : "warn"}">${i.level === "block" ? "碰红线：" : ""}${E(i.text)}</span>`).join("")}
      ${done ? `<span class="ow-pill ${r.status === "accepted" ? "ok" : ""}">${r.status === "accepted" ? "已接受" : "已拒绝"}</span>` : blocked ? "" : `<button type="button" class="p s" data-ow-rev="accept" data-id="${r.id}">接受</button>`}${done ? "" : `<button type="button" class="s" data-ow-rev="reject" data-id="${r.id}">拒绝</button>`}</div>`;
  }
  function chatHtml() {
    const sg = (S.d?.suggestions || []);
    if (!S.adopt) S.adopt = new Set(sg.slice(0, 2).map((s) => s.id));
    const pend = (S.d?.revisions || []).filter((r) => r.status === "pending" && !S.msgs.some((m) => (m.revs || []).some((x) => x.id === r.id)));
    const sel = S.sel ? S.d.items.find((i) => i.uid === S.sel) : null;
    return `<div class="h"><b>小星星助手</b>${sel ? `<span class="ow-pill">已选 ${E(sel.label)} · ${KIND_LABEL[sel.kind]}<a href="#" data-ow="unsel" aria-label="取消选择"> ×</a></span>` : ""}</div>
      <div class="m" id="ow-msgs"><div class="bub">${S.step === "overview" ? "总览按你的设定排好了。追问深度我按每个模块的目的给了默认值，你可以直接改；下面有建议，可以多选后一起采纳。" : S.step === "questions" ? "选中一道题，再告诉我怎么改；我给出的修改只是“待定”，点“接受”才会写进大纲。" : "我先把项目里已有的信息读了一遍，还缺的在左边问你。"}</div>
      ${S.msgs.map((m) => `<div class="bub ${m.me ? "me" : ""}">${E(m.text)}</div>${(m.revs || []).map((r) => revCard(S.d.revisions.find((x) => x.id === r.id) || r)).join("")}`).join("")}${pend.map(revCard).join("")}</div>
      ${sg.length ? `<div class="ow-sug"><div class="t"><b>建议</b>（最多 3 条，可多选）<span class="ow-pill">已选 ${S.adopt.size}</span></div>${sg.map((s) => `<label><input type="checkbox" aria-label="${E(s.title)}" data-ow-sg="${s.id}"${S.adopt.has(s.id) ? " checked" : ""}><span><b>${E(s.title)}</b><small>${E(s.reason)}${s.basis ? `（依据：“${E(s.basis)}”）` : ""}</small><i class="x" data-ow-dis="${s.id}">不再提醒</i></span></label>`).join("")}<button type="button" class="p s" data-ow="adopt"${S.adopt.size ? "" : " disabled"}>采纳所选，重排结构</button></div>` : ""}
      <form class="f" id="ow-form"><textarea class="ow-ta" id="ow-in" aria-label="对小星星助手说" rows="2" placeholder="跟 AI 说……"></textarea><button type="submit" class="p s">发送</button></form>`;
  }

  // ---- 绘制与事件 ---------------------------------------------------------------------------------
  function paint() {
    const root = q("#owroot"); if (!root) return;
    if (!S.d) { root.innerHTML = `<div class="card ow-card mut">正在读取……</div>`; return; }
    const keep = { y: q("#owmain")?.scrollTop || 0, chat: q("#ow-msgs")?.scrollTop || 0, focusId: document.activeElement?.id || "" };
    const view = { goal: goalView, people: peopleView, overview: overviewView, questions: questionsView, focus: focusView }[S.step] || goalView;
    root.innerHTML = `<div class="ow${S.busy ? " busy" : ""}"><div class="ow-main" id="owmain">${view()}</div><aside class="ow-chat" id="owchat">${chatHtml()}</aside></div>`;
    q("#owmain").scrollTop = keep.y; const m = q("#ow-msgs"); if (m) m.scrollTop = keep.chat || m.scrollHeight;
    menu();
  }
  const val = (s) => q(s)?.value ?? "";
  const num = (s) => parseFloat(val(s)) || 0;

  async function onClick(e) {
    const t = e.target.closest("[data-ow],[data-ow-ans],[data-ow-freeok],[data-ow-sel],[data-ow-del],[data-ow-ins],[data-ow-rev],[data-ow-dis],[data-ow-step],[data-ow-mod]"); if (!t) return;
    const d = t.dataset;
    if (d.owStep !== undefined) { e.preventDefault(); if (stepOk(d.owStep)) { S.step = d.owStep; paint(); } return; }
    if (d.owMod !== undefined) { e.preventDefault(); S.step = "questions"; S.mod = d.owMod; S.sel = ""; paint(); return; }
    if (d.owAns) return act("/answer", "POST", { slot: d.owAns, value: JSON.parse(d.v) });
    if (d.owFreeok) { const v = q(`[data-ow-free="${d.owFreeok}"]`).value.trim(); return v ? act("/answer", "POST", { slot: d.owFreeok, value: v }) : toast("先写一句话", true); }
    if (d.owDel) { e.stopPropagation(); return act(`/items/${d.owDel}`, "DELETE"); }
    if (d.owIns) { S.form = { kind: d.owIns, after: d.after }; return paint(); }
    if (d.owRev) { e.stopPropagation(); return act(`/revisions/${d.id}/${d.owRev}`, "POST", {}); }
    if (d.owDis) { e.preventDefault(); return act(`/suggestions/${d.owDis}/dismiss`, "POST"); }
    if (d.owSel && !e.target.closest("textarea,select,input,button,label")) { S.sel = S.sel === d.owSel ? "" : d.owSel; return paint(); }
    switch (d.ow) {
      case "goal-next": S.step = "people"; await call("/goal/confirm", "POST").then((v) => { S.d = v; }).catch(() => {}); return paint();
      case "build": S.step = "overview"; return act("/overview", "POST");
      case "back-people": S.step = "people"; return paint();
      case "confirm-ov": { await act("/overview/confirm", "POST"); if (gates().overview) { S.step = "questions"; S.mod = (mods().find((m) => m.status === "todo") || mods()[0])?.id || ""; paint(); } return; }
      case "gen": S.mod = d.m; return act(`/modules/${d.m}/generate`, "POST");
      case "conf-mod": { const i = mods().findIndex((m) => m.id === d.m); await act(`/modules/${d.m}/confirm`, "POST"); const nx = mods()[i + 1]; if (nx) { S.mod = nx.id; paint(); } else if (gates().questions) { S.step = "focus"; paint(); } return; }
      case "adopt": { const ids = [...S.adopt], names = (S.d.suggestions || []).filter((x) => S.adopt.has(x.id)).map((x) => x.title); S.adopt = null; S.msgs.push({ text: `已按所选 ${ids.length} 条重排：${names.join("；")}。总时长不变，已写好的题目只有受影响的模块要重写。` }); return act("/suggestions/adopt", "POST", { ids }); }
      case "unsel": e.preventDefault(); S.sel = ""; return paint();
      case "ins-cancel": S.form = null; return paint();
      case "ins-ok": {
        const f = S.form, cfg = {};
        if (q("#ow-f-url")) cfg.url = val("#ow-f-url");
        if (q("#ow-f-limit")) cfg.time_limit_s = Math.round(num("#ow-f-limit") * 60);
        if (q("#ow-f-scale")) cfg.scale = num("#ow-f-scale");
        S.form = null; return act("/items", "POST", { kind: f.kind, text: val("#ow-f-text"), after_uid: d.after, config: cfg });
      }
      case "focus-add": { const v = val("#ow-focus-in").trim(); return v ? act("/focus", "POST", { text: v }) : toast("先写下问题", true); }
    }
  }
  async function onChange(e) {
    const t = e.target, d = t.dataset;
    if (d.owSg) { S.adopt.has(d.owSg) ? S.adopt.delete(d.owSg) : S.adopt.add(d.owSg); return paint(); }
    if (d.owM !== undefined && d.f) return act(`/overview/modules/${d.owM}`, "PUT", { [d.f]: d.f === "minutes" ? parseInt(t.value, 10) : t.value });
    if (d.owI !== undefined && d.f) return act(`/items/${d.owI}`, "PUT", { [d.f]: d.f === "backup" ? t.checked : t.value });
    if (d.owLink !== undefined) return act(`/focus/${d.owLink}`, "PUT", { item_uid: t.value || null });
  }
  async function onSubmit(e) {
    if (e.target.id !== "ow-form") return;
    e.preventDefault();
    const text = val("#ow-in").trim(); if (!text || S.busy) return;
    S.msgs.push({ me: true, text }); S.busy = true; paint();
    try {
      const r = await call("/chat", "POST", { text, selected: S.sel });
      S.d = await call(""); S.adopt = null;
      S.msgs.push({ text: r.reply, revs: r.revisions });
    } catch { /* toast shown */ }
    S.busy = false; paint();
  }

  function onFocusIn(e) {
    const uid = e.target?.dataset?.owI; if (!uid || S.sel === uid || !q("#owroot")?.contains(e.target)) return;
    S.sel = uid;
    qa(".ow-item").forEach((n) => n.classList.toggle("sel", n.dataset.owSel === uid));
    const keep = q("#ow-in")?.value || ""; const c = q("#owchat"); if (c) { c.innerHTML = chatHtml(); const i = q("#ow-in"); if (i) i.value = keep; }
  }
  let wired = false;
  function wire() {
    if (wired) return; wired = true;
    document.addEventListener("click", (e) => { if (q("#owroot")?.contains(e.target) || q("#owsteps")?.contains(e.target)) onClick(e); });
    document.addEventListener("change", (e) => { if (q("#owroot")?.contains(e.target)) onChange(e); });
    document.addEventListener("submit", onSubmit);
    document.addEventListener("focusin", onFocusIn);
  }

  window.OutlineWb = {
    async mount(pid) {
      wire();
      if (S.pid !== pid) Object.assign(S, { pid, d: null, step: "", mod: "", sel: "", msgs: [], adopt: null, form: null });
      paint();
      try { S.d = await call(""); S.step = S.step || S.d.step; S.adopt = null; } catch { return; }
      paint();
    },
  };
})();
