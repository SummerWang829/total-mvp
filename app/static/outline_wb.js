/* 大纲工作台 · 「生成访问大纲 › 生成大纲」页（阶段 2）。
   修正方案第 1 轮（2026-11-19）：步骤条在内容区顶部（每一步可点），④ 题目的模块目录在内容区左侧，侧边栏只放页面；预览测试 / Pilot 是两个页面，各自的小页签在内容区顶部。右侧小星星助手占满高度。
   所有数据来自 GET /api/projects/{pid}/outline-wb；页面只展示和提交，规则都在服务器上。 */
(() => {
  const DEPTH = [["none", "不追问"], ["shallow", "1 次"], ["deep", "1 次以上"]];
  const SRC = { project: "来自项目设置", answer: "你的回答", inferred: "AI 推测", default: "默认", none: "还没有" };
  const FORMAT = { audio: "纯音频", text: "文字访谈", text_voice: "文字+语音转录", video: "视频", video_screen: "视频+屏幕共享" };
  const KIND_LABEL = { question: "题目", concept: "概念卡", video: "视频片段", website: "网站任务", rating: "打分题", note: "说明" };
  const S = { pid: "", d: null, step: "", mod: "", sel: "", msgs: [], adopt: null, busy: false, form: null, area: "gen", tab: "preview", pv: null, pvIdx: 0, pvLang: "", rh: null, pl: null, sum: null, pil: null, cand: [], pilId: "", markForm: null, pub: null };
  const TABS = { test: [["preview", "受访者预览"], ["rehearsal", "AI 快速检查"]], pilot: [["round", "本轮 Pilot"], ["pilot", "Pilot 修正"], ["publish", "发布"]] };   // 修正方案第 1 轮第 9 条: 模拟预演 is now AI 快速检查 (only a reference)   // 预览测试 and Pilot are two pages of the side menu now (修正方案第 1 轮第 1 条)
  const STATE = { draft: "草稿", pilot: "Pilot 中", published: "已发布", superseded: "已被替代" };
  const TAG = { unclear: "题目不清楚", leading: "诱导", missed: "漏追问", material: "物料问题", host: "主持问题", long: "太长", other: "其他" };
  const DEST = { outline: "大纲", material: "物料", host_engine: "主持引擎" };
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

  /** The version card at the foot of the side menu: one line (修正方案第 1 轮第 1 条); a click opens the Pilot page. */
  function menuState() {
    const st = q("#owstate"); if (!st || !S.d) return;
    const v = S.d.version, pub = v.status === "published", rd = S.rd, last = rd && rd.rounds.length ? rd.rounds[rd.rounds.length - 1] : null;
    const mid = rd && rd.version.skipped ? "跳过 Pilot" : last ? `Pilot 第 ${last.status === "reviewed" ? last.number + 1 : last.number} 轮` : S.d.pilot_done ? "已做 pilot" : "还没做 pilot";
    st.innerHTML = `<a class="ow-vline" href="#/p/${S.pid}/people/pilot" title="打开 Pilot 页"><span class="dot${pub ? " ok" : ""}"></span><b>v${v.number}</b> · ${mid} · ${pub ? "已发布" : "未发布"}</a>`;
  }
  /** The step bar at the top of 生成大纲 (every reachable step can be clicked) and, on 预览测试 / Pilot, the page's own tabs. */
  function stepBar() {
    if (S.area !== "gen") return `<div class="ow-tabs" role="tablist">${TABS[S.area].map(([k, l]) => `<a href="#" role="tab" class="${S.tab === k ? "on" : ""}" aria-selected="${S.tab === k}" data-ow-tab="${k}">${l}</a>`).join("")}</div>`;
    return `<div class="ow-steps" role="tablist" aria-label="生成大纲的步骤">${S.d.steps.map((x, i) => {
      const on = S.step === x.key, ok = stepOk(x.key), done = stepDone(x.key);
      return `${i ? `<span class="sep" aria-hidden="true"></span>` : ""}<a href="#" role="tab" class="ow-st${on ? " on" : ""}${done ? " done" : ""}${ok ? "" : " lock"}" aria-selected="${on}"${ok ? "" : ' aria-disabled="true" title="先完成前面的步骤"'} data-ow-step="${x.key}"><i>${done ? "✓" : ok ? i + 1 : "🔒"}</i>${E(x.label)}</a>`;
    }).join("")}</div>`;
  }
  function tocHtml() {   // the modules of ④ 题目, on the left of the page (not in the side menu)
    return `<nav class="ow-toc" aria-label="模块"><div class="t">模块</div>${mods().map((m) => `<a href="#" class="${S.mod === m.id ? "on" : ""}" data-ow-mod="${E(m.id)}">${E(m.name)}<span class="${m.status === "confirmed" ? "ok" : m.status === "draft" ? "cur" : ""}">${m.status === "confirmed" ? "✓" : m.status === "draft" ? "●" : ""}</span></a>`).join("")}</nav>`;
  }
  function stepNav() {   // 上一步 / 继续 at the foot of each step
    const keys = S.d.steps.map((x) => x.key), i = keys.indexOf(S.step), prev = keys[i - 1], next = keys[i + 1];
    return `<div class="ow-nav">${prev ? `<button type="button" data-ow-step="${prev}">‹ 上一步</button>` : "<span></span>"}${next && stepOk(next) ? `<button type="button" data-ow-step="${next}">下一步 ›</button>` : ""}</div>`;
  }
  /** Keep the step in the address so a reload stays on it (replace, not push: the steps are one page). */
  function keepStep() {
    if (S.area !== "gen" || !S.step) return;
    const want = `#/p/${S.pid}/people/outline/${S.step}`;
    if (location.hash !== want && location.hash.startsWith(`#/p/${S.pid}/people/outline`)) history.replaceState(null, "", want);
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
      <div class="ow-revbtn">${done ? `<span class="ow-pill ${r.status === "accepted" ? "ok" : ""}">${r.status === "accepted" ? "已接受" : "已拒绝"}</span>` : blocked ? "" : `<button type="button" class="p s" data-ow-rev="accept" data-id="${r.id}">接受</button>`}${done ? "" : `<button type="button" class="s" data-ow-rev="reject" data-id="${r.id}">拒绝</button>`}</div></div>`;
  }
  function chatHtml() {
    const sg = S.area === "gen" ? (S.d?.suggestions || []) : [];
    if (!S.adopt) S.adopt = new Set(sg.slice(0, 2).map((s) => s.id));
    const pend = (S.d?.revisions || []).filter((r) => r.status === "pending" && !S.msgs.some((m) => (m.revs || []).some((x) => x.id === r.id)));
    const sel = S.sel ? S.d.items.find((i) => i.uid === S.sel) : null;
    return `<div class="h"><b>小星星助手</b>${sel ? `<span class="ow-pill">已选 ${E(sel.label)} · ${KIND_LABEL[sel.kind]}<a href="#" data-ow="unsel" aria-label="取消选择"> ×</a></span>` : ""}</div>
      <div class="m" id="ow-msgs"><div class="bub">${S.area !== "gen" ? ({ preview: "这是受访者真实会看到的样子。切换语言看每种语言的版本；概念卡带受访者编号水印。", rehearsal: "AI 快速检查只看方向、只作参考：我用三种不同的受访者把大纲走一遍，告诉你哪里时间不够、哪里可能听不懂。", round: "先选：做 Pilot（推荐），或者客户不做时跳过 Pilot 直接上线。发起一轮后，被邀请的人在直播间里可以按 M 标记问题。", pilot: "每条标记都要处理：接受、拒绝或暂缓；也可以在笔录里选一句话点“圈出”，写下问题；也可以直接点“生成修改建议”，我会把标记和自动发现合并成修改，每条写明去向。客户只能圈和写意见。", publish: "发布前先看检查。发布之后新场次会用这一版；仍然可以继续改。" }[S.tab]) : S.step === "overview" ? "总览按你的设定排好了。追问深度我按每个模块的目的给了默认值，你可以直接改；下面有建议，可以多选后一起采纳。" : S.step === "questions" ? "选中一道题，再告诉我怎么改；我给出的修改只是“待定”，点“接受”才会写进大纲。" : "我先把项目里已有的信息读了一遍，还缺的在左边问你。"}</div>
      ${S.msgs.map((m) => `<div class="bub ${m.me ? "me" : ""}">${E(m.text)}</div>${(m.revs || []).map((r) => revCard(S.d.revisions.find((x) => x.id === r.id) || r)).join("")}`).join("")}${pend.map(revCard).join("")}</div>
      ${sg.length ? `<div class="ow-sug"><div class="t"><b>建议</b>（最多 3 条，可多选）<span class="ow-pill">已选 ${S.adopt.size}</span></div>${sg.map((s) => `<label><input type="checkbox" aria-label="${E(s.title)}" data-ow-sg="${s.id}"${S.adopt.has(s.id) ? " checked" : ""}><span><b>${E(s.title)}</b><small>${E(s.reason)}${s.basis ? `（依据：“${E(s.basis)}”）` : ""}</small><i class="x" data-ow-dis="${s.id}">不再提醒</i></span></label>`).join("")}<button type="button" class="p s" data-ow="adopt"${S.adopt.size ? "" : " disabled"}>采纳所选，重排结构</button></div>` : ""}
      <form class="f" id="ow-form"><textarea class="ow-ta" id="ow-in" aria-label="对小星星助手说" rows="2" placeholder="跟 AI 说……"></textarea><button type="submit" class="p s">发送</button></form>`;
  }


  // ---- 预览测试（阶段 3）---------------------------------------------------------------------------
  const chk = (c) => `<div class="ow-chk"><span class="ow-pill ${c.level === "ok" ? "ok" : c.level === "block" ? "bad" : "warn"}">${c.level === "ok" ? "通过" : c.level === "block" ? "不行" : "注意"}</span><span>${E(c.text)}</span></div>`;
  function previewView() {
    const pv = S.pv; if (!pv) return `<div class="card ow-card mut">正在读取……</div>`;
    const items = pv.items, it = items[Math.min(S.pvIdx, items.length - 1)];
    const langs = ["zh", "en"].map((l) => `<button type="button" class="${pv.language === l ? "on" : ""}" data-ow="pv-lang" data-l="${l}">${l === "zh" ? "中文" : "English"}</button>`).join("");
    const body = !it ? `<div class="mut">还没有题。</div>` : `<div class="sm mut">第 ${S.pvIdx + 1} 项 / 共 ${items.length} 项 · ${E(it.label)}</div>
      ${it.kind === "concept" || it.kind === "video" ? `<div class="ow-wm">${E(it.config.title || KIND_LABEL[it.kind])}<i>${it.watermark ? "P-0007" : ""}</i></div><div class="sm">只能观看，不能下载</div>` : ""}
      ${it.kind === "website" ? `<div class="ow-wm">网站任务</div><div class="sm">在新标签打开 · 开始共享屏幕</div>` : ""}
      <div class="ow-bub">${it.missing ? `<span class="mut">这个语言还没有文字</span>` : E(it.text)}</div><div class="ow-fake">在这里回答……</div>`;
    return `<div class="ow-row"><b>受访者预览</b><span class="ow-seg">${langs}</span><span class="sp"></span><button type="button" class="s" data-ow="pv-prev">上一项</button><button type="button" class="s" data-ow="pv-next">下一项</button></div>
      <div class="card ow-card ow-pvgrid"><div class="ow-phone"><div class="tt"><b>访谈</b><span class="sp"></span><span class="ow-pill">受访者编号 P-0007</span></div><div class="pb">${body}</div></div>
      <div>${pv.checks.map(chk).join("")}</div></div>`;
  }
  function rehearsalView() {
    const r = S.rh; if (!r) return `<div class="card ow-card mut">正在读取……</div>`;
    const last = r.runs[0];
    const kv = last ? `<div class="ow-kv"><div><span class="mut sm">场数</span><b>${last.sessions}</b></div><div><span class="mut sm">平均时长</span><b>${last.avg_minutes} 分</b><span class="sm mut">计划 ${last.planned} 分</span></div><div><span class="mut sm">必问覆盖</span><b>${Math.round(last.must_cover_rate * 100)}%</b></div><div><span class="mut sm">发现</span><b>${last.findings.length} 个</b></div></div>` : `<div class="mut">还没有跑过。点右上角“运行 AI 快速检查”。</div>`;
    const fr = last ? last.findings.map((f) => `<tr><td>${E(f.label)}</td><td>${E(f.text)}</td><td><span class="ow-pill">${DEST[f.destination] || "大纲"}</span></td><td><button type="button" class="s" data-ow="rh-fix" data-uid="${f.item_uid}" data-t="${E(f.text)}">生成修改建议</button></td></tr>`).join("") : "";
    return `<div class="ow-row"><b>AI 快速检查</b><span class="ow-pill warn">仅供参考，不算 pilot，也不是发布的前提</span><span class="ow-pill">基于 v${r.version.number} · 第 ${r.version.rev} 次修改</span>${last && last.stale ? `<span class="ow-pill warn">检查之后又改过大纲</span>` : ""}<span class="sp"></span><button type="button" class="p" data-ow="rh-run">运行 AI 快速检查</button></div>
      <div class="card ow-card"><h3>${last ? `上一次检查 · ${E(last.at.replace("T", " "))}` : "AI 快速检查"}</h3>${kv}<div class="sm mut" style="margin-top:8px">${E(r.note)}</div></div>
      ${last ? `<div class="card ow-card"><h3>发现的问题</h3><table class="ow-tbl"><thead><tr><th style="width:56px">位置</th><th>问题</th><th style="width:90px">去向</th><th style="width:130px"></th></tr></thead><tbody>${fr || `<tr><td colspan="4" class="mut">没有发现问题。</td></tr>`}</tbody></table></div>` : ""}`;
  }

  const LV3 = { now: ["立即", "bad"], watch: ["注意", "warn"], info: ["参考", ""] };
  function summaryCard() {
    const m = S.sum; if (!m) return "";
    const req = m.ended ? "" : `<div class="ow-banner">必须先做完至少一场 pilot，再发布。选一场访谈设为 pilot，结束后在这里看它发现的问题。</div>`;
    const g = m.groups.map((x) => `<span class="ow-dest d-${x.dest}"><b>${x.n}</b> ${E(x.label)}${x.open ? `<i>${x.open} 待处理</i>` : ""}</span>`).join("");
    const rows = m.items.slice(0, 40).map((i) => `<div class="ow-sumrow"><span class="ow-pill ${LV3[i.level][1]}">${LV3[i.level][0]}</span><span class="ow-dest d-${i.dest}">${E(DEST[i.dest] || "大纲")}</span><span class="ow-pill">${E(i.label || "—")}</span><span>${E(i.text)}</span><span class="mut sm">${E(i.who)}</span></div>`).join("");
    const host = m.host.length ? `<div class="ow-hostbox"><b>问话方式（给 AI 主持）</b>${m.host.map((h) => `<div class="ow-sumrow h"><span class="ow-pill ${h.status === "accepted" ? "ok" : ""}">${h.status === "accepted" ? "已接受" : h.status === "pending" ? "待你接受" : "已拒绝"}</span><span>${E(h.text)}</span></div>`).join("")}</div>` : "";
    return `${req}<div class="card ow-card"><h3>pilot 发现的问题汇总 <span class="mut sm">（${m.pilots} 场 pilot，${m.ended} 场已结束）</span></h3><div class="ow-dests">${g}</div>${rows || `<div class="mut">还没有发现问题。</div>`}${host}
      <div class="ow-actions"><span class="mut sm">建议：${m.revisions.pending} 待你接受 · ${m.revisions.accepted} 已接受 · ${m.revisions.rejected} 已拒绝</span><span class="sp"></span><button type="button" class="p" data-ow="pil-update"${m.can_update ? "" : " disabled"}>用已接受的修改生成更新版</button></div></div>`;
  }
  const SRC_TIP = { ai: "AI 主持告诉的", host: "主持人指定的", guess: "按题目相似度猜的", edited: "研究员改的" };
  function lineSel(ln, qs) {
    const g = ln.source === "guess";
    if (!qs.length) return `<span class="ow-pill">${E(ln.label || "—")}</span>`;
    return `<select class="ow-sel ow-lsel${g ? " guess" : ""}" aria-label="这句话属于哪一道题" title="${SRC_TIP[ln.source] || ""}${ln.source ? "；" : ""}选另一道题就改" data-ow-line="${ln.id}"><option value=""${ln.item_uid ? "" : " selected"}>—</option>${qs.map((x) => `<option value="${x.uid}"${x.uid === ln.item_uid ? " selected" : ""}>${E(x.label)}${x.uid === ln.item_uid && g ? " ?" : x.uid === ln.item_uid && ln.source === "edited" ? " ✎" : ""}</option>`).join("")}</select>`;
  }
  function pilotView() {
    if (!S.pl) return `<div class="card ow-card mut">正在读取……</div>`;
    const pick = (S.cand || []).length ? `<div class="ow-add"><select class="ow-sel" id="ow-cand" aria-label="选一场访谈">${S.cand.map((x) => `<option value="${x.id}">${E(x.title)}（${{ scheduled: "待开始", live: "进行中", ended: "已结束" }[x.status] || x.status}）</option>`).join("")}</select><button type="button" class="s" data-ow="pil-set">设为 pilot</button></div>` : `<div class="mut">这个项目还没有访谈可以设为 pilot。</div>`;
    if (!S.pl.length) return reviewCard();
    const pv = S.pil; if (!pv) return `<div class="card ow-card mut">正在读取……</div>`;
    const sel = S.pl.length > 1 ? `<select class="ow-sel" aria-label="选择 pilot" data-ow-pil>${S.pl.map((x) => `<option value="${x.id}"${x.id === S.pilId ? " selected" : ""}>${E(x.title)}</option>`).join("")}</select>` : "";
    const lines = pv.lines.map((ln) => {
      const mk = pv.marks.filter((m) => m.line_id === ln.id);
      const fi = pv.findings.filter((f) => f.line_id === ln.id);
      const form = S.markForm === ln.id ? `<div class="ow-mform"><select class="ow-sel" id="ow-mk-tag" aria-label="标签">${Object.entries(TAG).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select><input class="ow-in" id="ow-mk-note" aria-label="意见" placeholder="意见（可以不写）"><button type="button" class="p s" data-ow="mark-ok" data-line="${ln.id}">圈出</button><button type="button" class="s" data-ow="mark-cancel">取消</button></div>` : "";
      return `<div class="ow-tr"><span class="tm">${fmtT(ln.at_ms)}</span><span>${ln.speaker === "moderator" ? "主持" : "受访者"}</span>${lineSel(ln, pv.questions)}
        <span class="tx" data-line-text="${ln.id}">${E(ln.text)}${ln.why ? ` <span class="sm mut" title="${E(ln.why.reason)}">▸ ${E(ln.why.action)}：${E(ln.why.reason)}</span>` : ""}${fi.map((f) => ` <span class="ow-pill bad">${E(f.text)}</span>`).join("")}${mk.map((m) => ` <span class="ow-pill warn">${E(m.tag_text)}</span>`).join("")}</span>
        <button type="button" class="s" data-ow="mark-open" data-line="${ln.id}">圈出</button></div>${form}`;
    }).join("");
    const finds = pv.findings.filter((f) => !f.line_id).map((f) => `<tr><td style="width:64px">${E(f.label || "—")}</td><td>${E(f.text)}</td></tr>`).join("");
    const marks = pv.marks.map((m) => `<tr><td>${m.role === "client" ? "客户" : "研究员"}</td><td>“${E(m.quote)}”</td><td><span class="ow-pill">${E(m.tag_text)}</span></td><td>${E(m.note)}</td><td><span class="ow-pill ${m.status === "open" ? "warn" : "ok"}">${m.status === "open" ? "待处理" : "已解决"}</span></td></tr>`).join("");
    return `${reviewCard()}<div class="ow-row"><b>笔录</b>${sel}<span class="ow-pill">${pv.moderation === "ai" ? "AI 主持" : "真人主持"}</span><span class="sm mut">可以选一句话“圈出”，补一条标记</span></div>
      <div class="card ow-card">${lines || `<div class="mut">这场 pilot 还没有笔录。</div>`}</div>
      ${finds ? `<div class="card ow-card"><h3>其他自动发现</h3><table class="ow-tbl"><tbody>${finds}</tbody></table></div>` : ""}
      <div class="card ow-card"><h3>标记</h3><table class="ow-tbl"><thead><tr><th style="width:90px">谁圈的</th><th>引用</th><th style="width:110px">标签</th><th>意见</th><th style="width:80px">状态</th></tr></thead><tbody>${marks || `<tr><td colspan="5" class="mut">还没有标记。</td></tr>`}</tbody></table></div>`;
  }
  // ---- 真人 Pilot 多轮（修正方案第 1 轮第 9 条，效果图 qa_shots/fix1_batch2_mockup.html）------------------------------------------------
  const HOST = { ai: "AI 主持", human: "真人主持" };
  const DEC = [["accepted", "接受"], ["rejected", "拒绝"], ["deferred", "暂缓"]];
  const clock = (ms) => (ms == null ? "—" : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`);
  function roundView() {
    const rd = S.rd; if (!rd) return `<div class="card ow-card mut">正在读取……</div>`;
    const choose = rd.can_skip && !S.startOpen ? `<div class="card ow-card"><h3>这一版大纲怎么上线？</h3><div class="ow-two">
        <div class="ow-choice"><b>做 Pilot（推荐）</b><p class="sm mut">先在直播间真的访一场，标记问题、修正出新版本，再上线。</p><button type="button" data-ow="rd-open">发起第 1 轮 Pilot</button></div>
        <div class="ow-choice on"><b>跳过 Pilot，直接上线</b><p class="sm mut">客户看完大纲就要开始时用。直接发布当前这一版 v${rd.version.number}，正式场次马上可以用。</p><button type="button" class="p" data-ow="rd-skip">跳过 Pilot，直接上线</button></div></div></div>` : "";
    const ask = S.skipAsk ? `<div class="card ow-card ow-ask"><h3>跳过 Pilot，直接上线 v${rd.version.number}？</h3>${S.pub ? S.pub.checks.filter((c) => ["quality", "empty", "modules"].includes(c.id)).map((c) => chk(c.id === "modules" && c.level !== "ok" ? { ...c, level: "warn", text: c.text + "（不挡上线）" } : c)).join("") : `<div class="mut">正在检查……</div>`}
        <p class="sm mut">上线后仍然可以改大纲；改了以后新开始的场次用新内容，已开始的不受影响。</p><div class="ow-actions"><button type="button" class="p" data-ow="rd-skip-ok">确认上线</button><button type="button" data-ow="rd-skip-cancel">取消</button></div></div>` : "";
    const rows = rd.rounds.map((r) => `<tr><td><b>第 ${r.number} 轮</b></td><td>v${r.version}</td><td>${r.interviews.map((i) => `${E(i.who || "受访者")} · ${E(i.scheduled_at ? i.scheduled_at.replace("T", " ").slice(0, 16) : "时间待定")} · ${HOST[i.moderation] || "真人主持"} · ${{ scheduled: "待开始", live: "进行中", ended: "已结束" }[i.status] || "待开始"}`).join("<br>") || "还没有场次"}</td>
        <td><span class="ow-pill ${r.status === "reviewed" ? "" : "warn"}">${r.status === "reviewed" ? "已修正" : r.ended ? "待修正" : "进行中"}</span></td><td>${r.marks ? `接受 ${r.accepted} · 拒绝 ${r.rejected} · 暂缓 ${r.deferred}${r.open ? ` · 待处理 ${r.open}` : ""}` : "—"}${r.next_version ? ` → 生成 v${r.next_version}` : ""}</td></tr>`).join("");
    const table = rd.rounds.length ? `<div class="card ow-card"><h3>各轮 Pilot</h3><table class="ow-tbl"><thead><tr><th style="width:80px">轮次</th><th style="width:70px">用的大纲</th><th>场次</th><th style="width:90px">状态</th><th>结果</th></tr></thead><tbody>${rows}</tbody></table></div>` : "";
    const n = rd.rounds.length && rd.rounds[rd.rounds.length - 1].status !== "reviewed" ? rd.rounds[rd.rounds.length - 1].number : rd.next_number;
    const parts = (S.parts || []).filter((x) => !x.anonymized);
    const form = (!rd.can_skip || S.startOpen) ? `<div class="card ow-card"><h3>发起第 ${n} 轮 Pilot</h3><div class="ow-form">
        <span class="k">大纲</span><span>v${rd.version.number}</span>
        <span class="k">主持方式</span><span>${HOST[rd.host] || "真人主持"} <span class="sm mut">（按立项的选择）</span></span>
        <span class="k">时间</span><span><input class="ow-in" id="ow-r-time" type="datetime-local" aria-label="Pilot 时间"></span>
        <span class="k">受访者</span><span><span class="ow-seg"><button type="button" class="${S.rpk !== "col" ? "on" : ""}" data-ow="rd-pk" data-v="list">从名单选</button><button type="button" class="${S.rpk === "col" ? "on" : ""}" data-ow="rd-pk" data-v="col">同事</button></span>
          ${S.rpk === "col" ? `<input class="ow-in" id="ow-r-name" aria-label="同事姓名" placeholder="姓名"> <input class="ow-in" id="ow-r-email" type="email" aria-label="同事邮箱" placeholder="邮箱">`
            : parts.length ? `<select class="ow-sel" id="ow-r-part" aria-label="选一位受访者">${parts.map((x) => `<option value="${x.id}">${E(x.code || "")} · ${E(x.name)}</option>`).join("")}</select>` : `<span class="mut sm">名单里还没有人，选“同事”</span>`}</span>
        <span class="k">邮件邀请旁听</span><span class="ow-mem">${rd.people.map((u) => `<label><input type="checkbox" data-ow-w="${u.id}" checked> ${E(u.name)}<span class="mut sm">（${E(u.role_name)}）</span></label>`).join("")}</span>
        <span></span><span class="sm mut">他们收到一封邮件，里面是直播间链接；用自己的账号登录后隐身旁听，可以标记问题。${rd.mail ? "" : "没配邮件服务：发起后在这里显示链接让你复制。"}</span>
        <span></span><span><button type="button" class="p" data-ow="rd-start">发起第 ${n} 轮，发送邀请</button>${rd.can_skip ? ` <button type="button" data-ow="rd-close">返回</button>` : ""}</span></div>
        ${(S.started || []).length ? `<div class="ow-links">${S.started.map((w) => `<div><b>${E(w.name)}</b> ${w.status === "queued" ? `<span class="ow-pill ok">邮件已发出</span>` : `<span class="ow-pill">复制链接</span> <code>${E(w.link)}</code>`}</div>`).join("")}</div>` : ""}</div>` : "";
    return ask + choose + table + form;
  }
  function reviewCard() {
    const rd = S.rd, rv = S.rv;
    if (!rd) return "";
    if (!rd.rounds.length) return `<div class="card ow-card mut">还没有发起 Pilot。到“本轮 Pilot”发起一轮${rd.can_skip ? "，或者选“跳过 Pilot，直接上线”" : ""}。</div>`;
    if (!rv) return `<div class="card ow-card mut">正在读取……</div>`;
    const sel = rd.rounds.length > 1 ? `<select class="ow-sel" aria-label="选择一轮" data-ow-rid>${rd.rounds.map((r) => `<option value="${r.id}"${r.id === S.rid ? " selected" : ""}>第 ${r.number} 轮 · v${r.version}</option>`).join("")}</select>` : "";
    const done = rv.status === "reviewed", c = rv.counts;
    const canNext = !done && rv.ended > 0 && !c.open;
    const rows = rv.marks.map((m) => `<tr><td class="sm">${E(m.who)} · ${clock(m.at_ms)}${m.label ? ` · ${E(m.label)}` : ""}</td><td>${E(m.note)}</td>
        <td class="sm">${m.suggestions.length ? m.suggestions.map((x) => `${E(DEST[x.destination] || "大纲")}：${E(x.after?.text || x.after?.config?.note || "")}${x.blocked ? ` <span class="ow-pill bad">碰红线</span>` : ""}`).join("<br>") : `<span class="mut">（没有修改建议）</span>`}</td>
        <td><span class="ow-dec">${DEC.map(([k, l]) => `<button type="button" class="${m.decision === k ? "on" : ""}" data-ow-dec="${k}" data-id="${m.id}"${done ? " disabled" : ""}>${l}</button>`).join("")}</span></td></tr>`).join("");
    return `<div class="card ow-card ow-row2"><b>第 ${rv.number} 轮 · 大纲 v${rv.version}</b>${sel}<span class="ow-pill ${rv.ended ? "ok" : "warn"}">${rv.ended ? `${rv.ended}/${rv.interviews} 场已结束` : "还没有结束的场次"}</span>${done ? `<span class="ow-pill">已生成 v${rv.next_version}</span>` : ""}<span class="sp"></span>
        ${done ? "" : `<button type="button" data-ow="rv-suggest">生成修改建议</button><button type="button" class="p" data-ow="rv-next"${canNext ? "" : " disabled"} title="${canNext ? "" : rv.ended ? "每条标记都要先处理" : "这一轮还没有做完一场"}">用接受的修改生成 v${rd.version.number + 1}</button>`}
        <button type="button" data-ow="rv-ready" class="${rd.version.ready ? "on" : ""}">${rd.version.ready ? "✓ 已确认可以正式开始" : "可以正式开始"}</button></div>
      <div class="card ow-card"><h3>这一轮的标记 <span class="mut sm">（${c.marks} 条，都要处理：接受 / 拒绝 / 暂缓）</span></h3>
        <table class="ow-tbl"><thead><tr><th style="width:170px">谁 · 时间 · 题号</th><th>标记</th><th>修改建议</th><th style="width:190px">处理</th></tr></thead><tbody>${rows || `<tr><td colspan="4" class="mut">还没有标记。</td></tr>`}</tbody></table>
        <div class="sm mut" style="margin-top:8px">接受的修改不会改动 v${rv.version} 本身：生成下一版时，复制 v${rv.version} 再写入这些修改，v${rv.version} 原样保留。</div></div>`;
  }

  const fmtT = (ms) => { const t = Math.floor((ms || 0) / 1000); return [Math.floor(t / 3600), Math.floor(t / 60) % 60, t % 60].map((x) => String(x).padStart(2, "0")).join(":"); };
  function publishView() {
    const p = S.pub; if (!p) return `<div class="card ow-card mut">正在读取……</div>`;
    const pil = (v) => (v.skipped ? "跳过" : v.pilots ? `${v.pilots} 轮` : v.pilots_open ? "进行中" : "还没做");
    const vr = p.versions.map((v) => `<tr><td><b>v${v.number}</b>${v.current ? " · 当前" : ""}</td><td>${E(v.source)}</td><td><span class="ow-pill ${v.state === "published" ? "ok" : v.state === "pilot" ? "warn" : ""}">${STATE[v.state] || "草稿"}</span></td><td class="c">${pil(v)}</td><td class="c">${v.used_by} 场</td></tr>`).join("");
    return `<div class="ow-row"><b>发布</b><span class="ow-pill">v${S.d.version.number} · 第 ${S.d.version.rev} 次修改</span><span class="sp"></span><button type="button" class="p" data-ow="pub-go"${p.can_publish ? "" : " disabled"}>发布这一版</button></div>
      <div class="card ow-card"><h3>发布前检查</h3>${p.checks.map(chk).join("")}</div>
      <div class="card ow-card"><h3>版本</h3><table class="ow-tbl"><thead><tr><th style="width:110px">版本</th><th>来源</th><th style="width:120px">状态</th><th class="c" style="width:90px">Pilot</th><th class="c" style="width:90px">已被使用</th></tr></thead><tbody>${vr}</tbody></table>
      <div class="sm mut" style="margin-top:8px">新版本只在 Pilot 修正里接受修改、点“用已接受的修改生成更新版”时产生。</div></div>
      <div class="card ow-card"><h3>发布之后</h3><div class="ow-chk"><span class="ow-pill">新场次</span><span>开始时存一份快照，用这一版的内容；进行中的场次不受影响</span></div><div class="ow-chk"><span class="ow-pill">老场次</span><span>仍记着自己用的版本和修改次数</span></div></div>`;
  }
  async function loadTab() {
    try {
      if (S.tab === "preview") { S.pv = await call(`/preview${S.pvLang ? "?lang=" + S.pvLang : ""}`); S.pvLang = S.pv.language; }
      else if (S.tab === "rehearsal") S.rh = await call("/rehearsal");
      else if (S.tab === "round") { S.rd = await call("/pilot-rounds"); S.parts = await api(`/projects/${S.pid}/participants`).catch(() => []); }
      else if (S.tab === "pilot") { S.rd = await call("/pilot-rounds"); const rs = S.rd.rounds; if (!rs.some((x) => x.id === S.rid)) S.rid = rs.length ? rs[rs.length - 1].id : ""; S.rv = S.rid ? await call(`/pilot-rounds/${S.rid}`) : null; const r = await call("/pilots"); S.pl = r.pilots; S.cand = r.candidates; S.sum = await call("/pilot-summary"); S.pil = null; if (S.pl.length) { if (!S.pl.some((x) => x.id === S.pilId)) S.pilId = S.pl[0].id; S.pil = await call(`/pilots/${S.pilId}`); } }
      else if (S.tab === "publish") S.pub = await call("/publish/check");
    } catch { /* toast shown */ }
    paint();
  }

  // ---- 绘制与事件 ---------------------------------------------------------------------------------
  function paint() {
    const root = q(S.area === "gen" ? "#owroot" : "#owroot3"); if (!root) return;
    if (!S.d) { root.innerHTML = `<div class="card ow-card mut">正在读取……</div>`; return; }
    const keep = { y: q("#owmain")?.scrollTop || 0, chat: q("#ow-msgs")?.scrollTop || 0, focusId: document.activeElement?.id || "" };
    const view = S.area !== "gen" ? { preview: previewView, rehearsal: rehearsalView, round: roundView, pilot: pilotView, publish: publishView }[S.tab] : ({ goal: goalView, people: peopleView, overview: overviewView, questions: questionsView, focus: focusView }[S.step] || goalView);
    const body = S.area === "gen" && S.step === "questions" ? `<div class="ow-qwrap">${tocHtml()}<div>${view()}</div></div>` : view();
    root.innerHTML = `<div class="ow${S.busy ? " busy" : ""}"><div class="ow-main" id="owmain">${stepBar()}${body}${S.area === "gen" ? stepNav() : ""}</div><aside class="ow-chat" id="owchat">${chatHtml()}</aside></div>`;
    q("#owmain").scrollTop = keep.y; const m = q("#ow-msgs"); if (m) m.scrollTop = keep.chat || m.scrollHeight;
    menuState(); keepStep();
  }
  const val = (s) => q(s)?.value ?? "";
  const num = (s) => parseFloat(val(s)) || 0;

  async function onClick(e) {
    const t = e.target.closest("[data-ow-tab],[data-ow-dec],[data-ow],[data-ow-ans],[data-ow-freeok],[data-ow-sel],[data-ow-del],[data-ow-ins],[data-ow-rev],[data-ow-dis],[data-ow-step],[data-ow-mod]"); if (!t) return;
    const d = t.dataset;
    if (d.owTab !== undefined) { e.preventDefault(); S.tab = d.owTab; paint(); return loadTab(); }
    if (d.owDec !== undefined) { e.preventDefault(); try { S.rv = await call(`/marks/${d.id}/decision`, "PUT", { decision: d.owDec }); S.rd = await call("/pilot-rounds"); } catch { /* toast */ } return paint(); }
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
      case "pv-lang": S.pvLang = d.l; S.pvIdx = 0; return loadTab();
      case "pv-prev": S.pvIdx = Math.max(0, S.pvIdx - 1); return paint();
      case "pv-next": S.pvIdx = Math.min((S.pv?.items.length || 1) - 1, S.pvIdx + 1); return paint();
      case "rh-run": { if (S.busy) return; S.busy = true; paint(); try { S.rh = await call("/rehearsal", "POST"); } catch { /* toast */ } S.busy = false; return paint(); }
      case "rh-fix": { S.msgs.push({ me: true, text: "请修改：" + d.t }); S.busy = true; paint(); try { const r = await call("/chat", "POST", { text: "请修改：" + d.t, selected: d.uid }); S.d = await call(""); S.msgs.push({ text: r.reply, revs: r.revisions }); } catch { /* toast */ } S.busy = false; return paint(); }
      case "pil-set": { const id = val("#ow-cand"); if (!id) return; try { await call(`/pilots/${id}`, "PUT", { value: true }); S.pilId = id; } catch { /* toast */ } return loadTab(); }
      case "pil-new": S.pl = []; return paint();
      case "pil-update": { S.busy = true; paint(); try { const r = await call("/pilot-update", "POST"); S.d = await call(""); S.msgs.push({ text: `已生成 v${r.version}：${r.summary}。问话方式 ${r.host.length} 条已接受并记录为训练用例，需要开发处理后才会改变 AI 主持的问法。` }); toast("已生成更新版"); } catch { /* toast */ } S.busy = false; return loadTab(); }
      case "mark-open": S.markForm = d.line; return paint();
      case "mark-cancel": S.markForm = null; return paint();
      case "mark-ok": {
        const el = q(`[data-line-text="${d.line}"]`), sel = window.getSelection(); let a = 0, b = (S.pil.lines.find((x) => x.id === d.line)?.text || "").length;
        if (sel && sel.rangeCount && !sel.isCollapsed && el && el.firstChild && el.contains(sel.anchorNode) && sel.anchorNode === el.firstChild && sel.focusNode === el.firstChild) { a = Math.min(sel.anchorOffset, sel.focusOffset); b = Math.max(sel.anchorOffset, sel.focusOffset); }
        S.markForm = null;
        try { S.pil = await call(`/pilots/${S.pilId}/marks`, "POST", { line_id: d.line, start: a, end: b, tag: val("#ow-mk-tag") || "other", note: val("#ow-mk-note") }); } catch { /* toast */ }
        return paint();
      }
      case "pil-suggest": { S.busy = true; paint(); try { const r = await call(`/pilots/${S.pilId}/suggest`, "POST"); S.pil = r.view; S.d = await call(""); S.msgs.push({ text: r.revisions.length ? `我把标记和自动发现合并成 ${r.revisions.length} 条修改建议，每条写明去向。去向是“主持引擎”的不改大纲，接受后记入训练用例。` : "没有需要修改的地方。", revs: r.revisions }); } catch { /* toast */ } S.busy = false; return paint(); }
      case "rd-open": S.startOpen = true; return paint();
      case "rd-close": S.startOpen = false; return paint();
      case "rd-pk": S.rpk = d.v; return paint();
      case "rd-skip": S.skipAsk = true; paint(); try { S.pub = await call("/publish/check"); } catch { /* toast */ } return paint();
      case "rd-skip-cancel": S.skipAsk = false; return paint();
      case "rd-skip-ok": { S.busy = true; paint(); try { await call("/publish/skip-pilot", "POST"); S.skipAsk = false; S.d = await call(""); toast("已上线：这一版已发布，正式场次可以开始"); } catch { /* toast */ } S.busy = false; return loadTab(); }
      case "rd-start": {
        if (S.busy) return;
        const t = val("#ow-r-time"), watchers = qa("[data-ow-w]").filter((x) => x.checked).map((x) => x.dataset.owW);
        const picked = val("#ow-r-part"), name = val("#ow-r-name").trim(), email = val("#ow-r-email").trim();   // read before the page is redrawn
        S.busy = true; paint();
        try {
          let pid = picked;
          if (S.rpk === "col") {
            if (!name) { toast("写下同事的姓名", true); throw new Error("name"); }
            pid = (await api(`/projects/${S.pid}/participants`, { method: "POST", body: { name, email, country: "SG", language: S.d.context.language } })).id;
          }
          if (!pid) { toast("先选一位受访者", true); throw new Error("who"); }
          const r = await call("/pilot-rounds", "POST", { participant_id: pid, scheduled_at: t ? new Date(t).toISOString().slice(0, 19) : null, watcher_ids: watchers, send_email: true });
          S.started = r.watchers; S.startOpen = false; toast(`已发起第 ${r.round} 轮 Pilot`);
        } catch { /* toast */ }
        S.busy = false; return loadTab();
      }
      case "rv-suggest": { S.busy = true; paint(); try { S.rv = await call(`/pilot-rounds/${S.rid}/suggest`, "POST"); } catch { /* toast */ } S.busy = false; return paint(); }
      case "rv-next": { S.busy = true; paint(); try { const r = await call(`/pilot-rounds/${S.rid}/next-version`, "POST"); S.d = await call(""); toast(`已生成 v${r.version}（${r.changes} 处修改）`); } catch { /* toast */ } S.busy = false; return loadTab(); }
      case "rv-ready": { try { await call("/ready", "PUT", { value: !S.rd.version.ready }); } catch { /* toast */ } return loadTab(); }
      case "pub-go": { S.busy = true; paint(); try { await call("/publish", "POST"); S.d = await call(""); S.pub = await call("/publish/check"); toast("已发布"); } catch { /* toast */ } S.busy = false; return paint(); }
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
    if (d.owLine !== undefined) { if (!t.value) return paint(); try { await call(`/interviews/${S.pilId}/lines/${d.owLine}/item`, "PUT", { item_uid: t.value }); toast("题号已改，AI 之前对这句编的码作废并重新编"); S.pil = await call(`/pilots/${S.pilId}`); } catch { /* toast */ } return paint(); }
    if (d.owRid !== undefined) { S.rid = t.value; S.rv = null; paint(); return loadTab(); }
    if (d.owPil !== undefined) { S.pilId = t.value; S.pil = null; paint(); return loadTab(); }
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
    document.addEventListener("click", (e) => { if (["#owroot", "#owroot3"].some((k) => q(k)?.contains(e.target))) onClick(e); });
    document.addEventListener("change", (e) => { if (q("#owroot")?.contains(e.target) || q("#owroot3")?.contains(e.target)) onChange(e); });
    document.addEventListener("submit", onSubmit);
    document.addEventListener("focusin", onFocusIn);
  }

  window.OutlineWb = {
    async mount(pid, area) {
      wire();
      if (S.pid !== pid) Object.assign(S, { pid, d: null, step: "", mod: "", sel: "", msgs: [], adopt: null, form: null, pv: null, rh: null, pl: null, pil: null, pub: null });
      S.area = ["test", "pilot"].includes(area) ? area : "gen";
      if (S.area !== "gen" && !TABS[S.area].some(([k]) => k === S.tab)) S.tab = TABS[S.area][0][0];
      const fromUrl = (location.hash.split("/")[5] || "").split("?")[0];   // #/p/<id>/people/outline/<step>: a reload stays on that step
      paint();
      call("/pilot-rounds").then((r) => { S.rd = r; menuState(); }).catch(() => {});   // the version card says which pilot round we are at
      try { S.d = await call(""); S.step = (fromUrl && S.d.steps.some((x) => x.key === fromUrl) && stepOk(fromUrl) ? fromUrl : "") || S.step || S.d.step; S.adopt = null; } catch { return; }
      paint();
      if (S.area !== "gen") loadTab();
    },
  };
})();
