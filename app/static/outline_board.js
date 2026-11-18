/* 访谈直播 › 实时看板（阶段 4）。数据来自 /api/projects/{pid}/outline-wb/board*；页面只展示和提交，规则（哪句要编码、饱和、待复核、20 人才显示百分比）都在服务器上。
   子栏（场次列表 / 实时看板 · 概览 / 单个关注问题 / 场次与告警）由 shell 搬进左侧第二层菜单。每 3 秒问一次“有没有变化”，变了才重读统计；列表要点“新到 N 条”才刷新。 */
(() => {
  const E = (s) => (typeof esc === "function" ? esc(s) : String(s ?? ""));
  const q = (s, r = document) => r.querySelector(s);
  const B = { pid: "", tab: "overview", d: null, focus: "", fd: null, origin: "all", drawer: null, msgs: [], sel: null, token: "", timer: 0, since: 0, on: false, dismissed: new Set(), busy: false, W: null, cl: null };
  const CODING = [["collect", "先收集原话，攒够 8 条再归类", "前面不预设类别；攒够 8 条后按意思相近分组，你起名、确认", "推荐"], ["attitude", "态度模板", "很认可 / 一般 / 不认可 / 说不清", "预设"], ["reason", "原因类型模板", "价格 / 功效 / 成分 / 包装 / 品牌 / 推荐 / 其他", "预设"], ["custom", "自己定编码表", "每行一个类别，最多 12 个", "自定义"]];
  const newW = () => ({ step: 0, text: "", cands: null, pick: new Set(), addQ: false, coding: "collect", custom: "", fid: "", bf: null, bfPick: new Set() });
  const ORIGIN = [["all", "全部"], ["asked", "被问到"], ["volunteered", "主动提到"]];
  const LV = { now: "立即", watch: "注意" };
  async function call(path, method = "GET", body) {
    try { return await api(`/projects/${B.pid}/outline-wb${path}`, { method, body }); }
    catch (e) { toast(e.message || "没有成功", true); throw e; }
  }

  function menu() {
    const host = q("#owbbar"); if (!host) return;
    const on = (k) => (B.on && B.tab === k ? " on" : "");
    host.innerHTML = `<a href="#" class="l2sub ow-l3${B.on ? "" : " on"}" data-ob="list">场次列表</a><a href="#" class="l2sub ow-l3${B.on ? " on" : ""}" data-ob="board">实时看板</a>`
      + (B.on ? [["overview", "概览"], ["focus", "单个关注问题"], ["alerts", "场次与告警"], ["add", "新增关注问题"]].map(([k, l]) => `<a href="#" class="l2sub ow-l4${on(k)}" data-ob-tab="${k}">${l}<span class="ow-st ${B.tab === k ? "cur" : ""}">${B.tab === k ? "●" : ""}</span></a>`).join("") : "");
  }
  const pill = (t, c = "") => `<span class="ow-pill ${c}">${E(t)}</span>`;
  const when = (iso) => (iso ? iso.slice(11, 19) : "");

  function card(c) {
    const sat = c.saturated ? pill("接近饱和", "warn") : pill("还在增加");
    return `<div class="ob-card"><div class="hd"><b>${E(c.text)}</b>${pill(c.role === "client" ? "客户" : "研究员")}</div>
      <div class="sm mut">已回答 <b class="ink">${c.answered}</b> 人 · 最多的说法：<b class="ink">${E(c.top || "—")}${c.top_n ? `（${c.top_n} 人）` : ""}</b></div>
      <div class="ob-stat"><div><span class="mut sm">新到</span><b>${c.new} 条</b></div><div><span class="mut sm">没问到</span><b>${c.missing}</b></div><div><span class="mut sm">待复核</span><b>${c.review}</b></div></div>
      <div class="ow-row">${sat}${pill(`被问到 ${c.asked} · 主动提到 ${c.volunteered}`)}${c.guessed ? pill(`题号是猜的 ${c.guessed}`, "warn") : ""}<span class="sp"></span><button type="button" class="s" data-ob-open="${c.id}">看详情</button></div></div>`;
  }
  function overview() {
    const d = B.d; if (!d) return `<div class="card ow-card mut">正在读取……</div>`;
    const feed = d.recent.map((r) => `<div class="ob-row"><span class="tm">${when(r.at)}</span>${pill(r.who)}<span>${r.origin === "volunteered" ? "主动提到了" : "回答了"}“${E(r.code)}”（${E(r.focus.slice(0, 16))}）</span><button type="button" class="s" data-ob-ans="${r.id}">看</button></div>`).join("");
    return `<div class="ow-row"><b>实时看板</b>${pill(`进行中 ${d.live} 场`, "ok")}${pill(`已完成 ${d.done} 场`)}<span class="mut sm">数据更新于 ${Math.max(0, Math.round(Date.now() / 1000 - d.now))} 秒前</span><span class="sp"></span><button type="button" class="s" data-ob-tab="add">＋ 新增关注问题</button></div>
      ${d.cards.length ? `<div class="ob-cards">${d.cards.map(card).join("")}</div>` : `<div class="card ow-card mut">还没有关注问题。到“生成大纲 › 关注问题”里添加，并对应到一道题。</div>`}
      <div class="card ow-card"><h3>刚刚</h3>${feed || `<div class="mut">还没有回答被编码。</div>`}</div>
      <div class="sm mut">每个问题的回答先攒成“新到 N 条”，点开才刷新列表；统计图照常实时更新。</div>`;
  }
  function clusterCard() {
    const c = B.cl;
    return `<div class="card ow-card"><h3>攒够 ${c.pending} 条，按意思归成 ${c.groups.length} 类 <span class="mut sm">你来起名、确认；不确认的不进统计</span></h3>${c.groups.map((g, n) => `<div class="ob-grp"><div class="gh"><input type="checkbox" aria-label="确认这个类别" data-ob-gk="${n}" checked><input class="ow-in" aria-label="类别名" data-ob-gn="${n}" value="${E(g.name)}"><span class="ow-pill">${g.members.length} 条</span></div>${g.members.slice(0, 4).map((m) => `<div class="q">“${E(m.text)}”</div>`).join("")}</div>`).join("")}
      <div class="ow-actions"><button type="button" class="p" data-ob="codes">确认所选类别</button><button type="button" data-ob="cluster-close">取消</button></div></div>`;
  }
  function addView() {
    const W = B.W || (B.W = newW());
    const dots = ["① 问题与对应的题", "② 怎么编码", "③ 回头补进已有的回答"].map((t, i) => `<span class="ow-pill ${i === W.step ? "o" : ""}">${t}</span>`).join("");
    const head = `<div class="ow-row"><b>新增关注问题</b><span class="sp"></span><span class="mut sm">访谈进行中也可以加</span></div><div class="ow-row">${dots}</div>`;
    if (W.step === 0) {
      const cands = W.cands ? `<div class="card ow-card"><h3>对应到哪道题 <span class="mut sm">按关键词和近义词推荐，由你确认</span></h3>${W.cands.candidates.map((c) => `<div class="ob-cand"><input type="checkbox" aria-label="对应 ${E(c.label)}" data-ob-cand="${c.uid}"${W.pick.has(c.uid) ? " checked" : ""}><span class="ow-pill">${E(c.label)}</span><span>${E(c.text)}</span><span class="ob-rel"><i style="width:${Math.round(c.score * 100)}%"></i></span></div>`).join("") || `<div class="mut">大纲里还没有题。</div>`}
        <div class="ow-actions"><label class="bk"><input type="checkbox" data-ob="addq"${W.addQ ? " checked" : ""}> 都对不上，给下一批场次加一道题 <span class="mut sm">（生成一条待定修订，到“生成大纲”页接受后新场次才问）</span></label></div></div>` : "";
      return `${head}<div class="card ow-card"><h3>要弄清什么</h3><textarea class="ow-ta" id="ob-wtext" aria-label="要弄清什么" rows="3" placeholder="比如：她们对“包装是否环保”有什么看法？">${E(W.text)}</textarea><div class="ow-actions"><button type="button" class="s" data-ob="wfind">找对应的题</button></div></div>${cands}
        <div class="ow-actions"><button type="button" class="p" data-ob="wnext1"${W.cands ? "" : " disabled"}>下一步：选编码方式</button></div>`;
    }
    if (W.step === 1) {
      return `${head}<div class="card ow-card"><h3>怎么给回答编码</h3>${CODING.map(([k, t, d, tag]) => `<label class="ob-opt${W.coding === k ? " on" : ""}"><input type="radio" name="ob-coding" value="${k}" data-ob-coding="${k}"${W.coding === k ? " checked" : ""}><span><b>${t}</b><small>${d}</small></span><span class="ow-pill">${tag}</span></label>`).join("")}
        ${W.coding === "custom" ? `<textarea class="ow-ta" id="ob-wcodes" aria-label="编码表" rows="4" placeholder="每行一个类别">${E(W.custom)}</textarea>` : ""}</div>
        <div class="ow-actions"><button type="button" class="p" data-ob="wnext2">下一步：回头补进已有的回答</button><button type="button" data-ob="wback">上一步</button></div>`;
    }
    const bf = W.bf;
    const rows = bf ? bf.candidates.map((c) => `<div class="ob-ex"><input type="checkbox" aria-label="补入" data-ob-bf="${c.line_id}"${W.bfPick.has(c.line_id) ? " checked" : ""}><span class="ow-pill">${E(c.who)}</span><span class="ow-pill">${E(c.in || "—")}</span><span>“${E(c.text)}”</span><span class="mut sm">${c.ended ? "已结束" : "进行中"}</span></div>`).join("") : "";
    return `${head}<div class="card ow-card"><h3>已有的回答里，找到 ${bf ? bf.total : "…"} 条可能讲到它 <span class="mut sm">在别的题里主动提到的，补进来，记为“主动提到”</span></h3>${rows || `<div class="mut">${bf ? "没有找到。" : "正在查……"}</div>`}</div>
      <div class="ow-actions"><button type="button" class="p" data-ob="wfinish">确认，加入看板</button><button type="button" data-ob="wskip">不补入，直接加入</button></div>`;
  }
  function focusView() {
    const d = B.d; if (!d) return `<div class="card ow-card mut">正在读取……</div>`;
    if (!B.focus && d.cards.length) { B.focus = d.cards[0].id; loadFocus(); }
    const pick = `<select class="ow-sel" aria-label="选关注问题" data-ob-pick>${d.cards.map((c) => `<option value="${c.id}"${c.id === B.focus ? " selected" : ""}>${E(c.text.slice(0, 20))}</option>`).join("")}</select>`;
    const f = B.fd;
    if (!f) return `<div class="ow-row"><b>单个关注问题</b>${pick}</div><div class="card ow-card mut">${d.cards.length ? "正在读取……" : "还没有关注问题。"}</div>`;
    const max = Math.max(1, ...f.dist.map((x) => x.n));
    const bars = f.dist.map((x) => `<div class="ob-bar"><span>${E(x.code)}</span><span class="t"><i style="width:${Math.round(x.n * 100 / max)}%"></i></span><span class="n">${x.n} 人${x.pct != null ? ` · ${x.pct}%` : ""}</span></div>`).join("");
    const grp = f.groups.map((g) => `<div class="ob-bar g"><span>${E(g.group)}</span><span class="t"><i style="width:${Math.round(g.total * 100 / Math.max(1, f.dist.reduce((a, x) => a + x.n, 0)))}%"></i></span><span class="n">${g.dist.slice(0, 2).map((x) => `${E(x.code)} ${x.n}`).join(" / ")}</span></div>`).join("");
    const rows = f.quotes.map((x) => `<div class="ob-q" data-ob-ans="${x.id}">${pill(x.who)}<span>“${E(x.text)}”${x.in ? ` <span class="mut sm">（主动提到，在 ${E(x.in)}）</span>` : ""}</span>${pill(x.code, x.review ? "warn" : "")}${x.src === "guess" ? pill("题号是猜的", "warn") : ""}<span class="conf${x.review ? " w" : ""}">${x.confidence.toFixed(2)}${x.review ? " 待复核" : ""}</span></div>`).join("");
    return `<div class="ow-row"><b>单个关注问题</b>${pick}${f.label ? pill(`对应 ${f.label}`) : ""}<span class="sp"></span><span class="ow-seg">${ORIGIN.map(([k, l]) => `<button type="button" class="${B.origin === k ? "on" : ""}" data-ob-origin="${k}">${l}</button>`).join("")}</span></div>
      <div class="card ow-card"><h3>回答分布 <span class="mut sm">${f.percent ? `（${f.n} 人）` : `（${f.n} 人，不到 20 人，只显示人数不显示百分比）`}</span></h3>${bars || `<div class="mut">还没有回答。</div>`}</div>
      ${f.pending ? `<div class="card ow-card"><h3>还没归类的回答 <span class="mut sm">（${f.pending} 条，不计入上面的统计）</span></h3>${f.can_cluster ? `<button type="button" class="p" data-ob="cluster">按意思归类</button>` : `<div class="mut">攒够 8 条后可以归类，现在 ${f.pending} 条。</div>`}</div>` : ""}
      ${B.cl ? clusterCard() : ""}
      ${grp ? `<div class="card ow-card"><h3>按组别拆分</h3>${grp}</div>` : ""}
      <div class="card ow-card"><h3>原话 <span class="mut sm">（编码由 AI 给出，把握值低于 0.75 的进入待复核）</span></h3>${rows || `<div class="mut">没有。</div>`}</div>`;
  }
  function alertsView() {
    const d = B.d; if (!d) return `<div class="card ow-card mut">正在读取……</div>`;
    const al = d.alerts.map((a) => `<div class="ob-row al">${pill(LV[a.level], a.level === "now" ? "bad" : "warn")}${pill(a.who)}<span>${E(a.text)}</span><a class="btn s" href="/room.html?i=${a.interview_id}&as=moderator" target="_blank" rel="noopener">${a.level === "now" ? "接管" : "查看"}</a></div>`).join("");
    const ss = d.sessions.map((s) => `<div class="ob-row ss">${pill(s.who)}<span class="mut">${s.moderation === "ai" ? "AI" : "真人"}</span><span>${s.current ? E(s.current) + " · " : ""}已进行 ${s.minutes} 分 / 计划 ${s.planned} 分</span><a class="btn s" href="/room.html?i=${s.interview_id}&as=observer" target="_blank" rel="noopener">隐身旁听</a></div>`).join("");
    return `<div class="ow-row"><b>场次与告警</b>${pill(`进行中 ${d.sessions.length} 场`)}</div>
      <div class="card ow-card"><h3>告警 <span class="mut sm">（需要人处理的在最上面）</span></h3>${al || `<div class="mut">现在没有告警。</div>`}</div>
      <div class="card ow-card"><h3>进行中的场次</h3>${ss || `<div class="mut">现在没有进行中的场次。</div>`}</div>`;
  }
  function drawer() {
    const a = B.drawer; if (!a) return "";
    return `<div class="ob-drawer"><div class="ow-row"><b>${E(a.who)} · ${E(a.focus.slice(0, 16))}</b><span class="sp"></span><button type="button" class="s" data-ob-close>关闭</button></div>
      <div class="ob-box"><div class="sm mut">AI 的提问</div>${E(a.question || "—")}</div><div class="ob-box"><div class="sm mut">原话</div>“${E(a.text)}”</div>
      ${a.translation ? `<div class="ob-box"><div class="sm mut">中文（机器翻译）${pill("进报告前请人工核对", "warn")}</div>${E(a.translation)}</div>` : ""}
      <div class="ow-row">${pill("编码：" + a.code)}${pill(`把握 ${a.confidence.toFixed(2)}`, a.confidence < 0.75 && !a.confirmed ? "warn" : "ok")}${a.confirmed ? pill("已确认", "ok") : ""}${a.ai_code && a.ai_code !== a.code ? pill("AI 原编码：" + a.ai_code) : ""}</div>
      <div class="ow-add"><input class="ow-in" id="ob-code" aria-label="改编码" value="${E(a.code)}"><button type="button" class="s" data-ob="recode" data-id="${a.id}">改编码</button></div>
      <div class="ow-row"><a class="btn s" href="#/i/${a.interview_id}">打开录像和转写</a><button type="button" class="p s" data-ob="report" data-id="${a.id}"${a.clip_id ? " disabled" : ""}>${a.clip_id ? "已加入报告引用" : "加入报告引用"}</button></div>
      <div class="sm mut">受访者只显示编号；原始录像不能下载。</div></div>`;
  }
  function chatHtml() {
    const sg = ["overview", "focus"].includes(B.tab) ? (B.d?.suggestions || []).filter((s) => !B.dismissed.has(s.id)) : [];
    if (!B.sel) B.sel = new Set(sg.filter((s) => s.can_adopt).slice(0, 2).map((s) => s.id));
    const intro = { overview: "现在的情况我替你看着：哪个问题接近饱和、哪个有很多场没问到、哪些编码在等你确认。", focus: "可以问我，比如“哪些人提到价格偏贵”，我只显示编号。", alerts: "需要人处理的告警在最上面；AI 已经停下的场次等你接管。", add: "三步：对应的题、怎么编码、回头补进已有的回答。对应的题只是我按词语推荐的，由你确认。" }[B.tab];
    return `<div class="h"><b>小星星助手</b></div><div class="m" id="ob-msgs"><div class="bub">${intro}</div>${B.msgs.map((m) => `<div class="bub ${m.me ? "me" : ""}">${E(m.text)}</div>`).join("")}</div>
      ${sg.length ? `<div class="ow-sug"><div class="t"><b>建议</b>（最多 3 条，可多选）</div>${sg.map((s) => `<label><input type="checkbox" aria-label="${E(s.title)}" data-ob-sg="${s.id}"${B.sel.has(s.id) ? " checked" : ""}${s.can_adopt ? "" : " disabled"}><span><b>${E(s.title)}</b><small>${E(s.reason)}</small><i class="x" data-ob-dis="${s.id}">不再提醒</i></span></label>`).join("")}<button type="button" class="p s" data-ob="adopt"${B.sel.size ? "" : " disabled"}>采纳所选</button></div>` : ""}
      <form class="f" id="ob-form"><textarea class="ow-ta" id="ob-in" aria-label="问小星星助手" rows="2" placeholder="问一句，比如：哪些人提到价格偏贵？"></textarea><button type="submit" class="p s">发送</button></form>`;
  }
  function paint() {
    const root = q("#owbroot"); if (!root || !B.on) return;
    const view = { overview, focus: focusView, alerts: alertsView, add: addView }[B.tab]();
    const keep = q("#ob-main")?.scrollTop || 0;
    root.innerHTML = `<div class="ow"><div class="ow-main wrapb" id="ob-main">${view}${drawer()}</div><aside class="ow-chat" id="ob-chat">${chatHtml()}</aside></div>`;
    const m = q("#ob-main"); if (m) m.scrollTop = keep; const c = q("#ob-msgs"); if (c) c.scrollTop = c.scrollHeight;
    menu();
  }
  async function load() { try { B.d = await call(`/board?since=${B.since}`); B.token = (await call("/board/changes")).token; } catch { /* toast */ } paint(); }
  async function loadFocus() { if (!B.focus) return; try { B.fd = await call(`/board/focus/${B.focus}?origin=${B.origin}`); } catch { /* toast */ } paint(); }
  async function poll() {
    if (!B.on || document.hidden) return;
    try { const t = (await api(`/projects/${B.pid}/outline-wb/board/changes`)).token; if (t !== B.token) { B.token = t; B.d = await call(`/board?since=${B.since}`); paint(); } } catch { /* the next poll tries again */ }
  }
  async function onClick(e) {
    const t = e.target.closest("[data-ob],[data-ob-tab],[data-ob-open],[data-ob-ans],[data-ob-origin],[data-ob-close],[data-ob-dis]"); if (!t) return;
    const d = t.dataset;
    if (d.ob === "list" || d.ob === "board") { e.preventDefault(); B.on = d.ob === "board"; q("#rmlistwrap").hidden = B.on; q("#owbroot").hidden = !B.on; document.body.classList.toggle("rfmode", !B.on); document.body.classList.toggle("ob-on", B.on); if (B.on) { clearInterval(B.timer); B.timer = setInterval(poll, 3000); await load(); } else { clearInterval(B.timer); menu(); } return; }
    if (d.obTab) { e.preventDefault(); B.tab = d.obTab; B.drawer = null; if (B.tab === "add" && !B.W) B.W = newW(); paint(); if (B.tab === "focus") loadFocus(); return; }
    if (d.obOpen) { B.focus = d.obOpen; B.tab = "focus"; B.fd = null; paint(); return loadFocus(); }
    if (d.obOrigin) { B.origin = d.obOrigin; return loadFocus(); }
    if (d.obClose !== undefined) { B.drawer = null; return paint(); }
    if (d.obDis) { e.preventDefault(); B.dismissed.add(d.obDis); B.sel = null; return paint(); }
    if (d.obAns) { try { B.drawer = await call(`/board/answer/${d.obAns}`); } catch { /* toast */ } return paint(); }
    if (d.ob === "recode") { const v = (q("#ob-code")?.value || "").trim(); if (!v) return toast("编码不能为空", true); try { B.drawer = await call(`/board/answer/${d.id}`, "PUT", { code: v }); } catch { /* toast */ } await loadFocus(); await load(); return; }
    if (d.ob === "report") { try { B.drawer = await call(`/board/answer/${d.id}/report`, "POST"); toast("已加入报告引用"); } catch { /* toast */ } return paint(); }
    if (d.ob === "wfind") { const W = B.W; W.text = (q("#ob-wtext")?.value || "").trim(); if (!W.text) return toast("先写下要弄清什么", true); try { W.cands = await call("/board/focus/suggest", "POST", { text: W.text }); W.pick = new Set(W.cands.candidates.filter((c) => c.checked).map((c) => c.uid)); W.addQ = W.cands.none_fit; } catch { /* toast */ } return paint(); }
    if (d.ob === "addq") { B.W.addQ = !B.W.addQ; return paint(); }
    if (d.ob === "wnext1") { B.W.text = (q("#ob-wtext")?.value || B.W.text).trim(); B.W.step = 1; return paint(); }
    if (d.ob === "wback") { B.W.step = 0; return paint(); }
    if (d.ob === "wnext2") {
      const W = B.W; W.custom = q("#ob-wcodes")?.value || W.custom;
      try {
        const r = await call("/board/focus/new", "POST", { text: W.text, item_uids: [...W.pick], coding: W.coding, codes: W.custom.split("\n") });
        W.fid = r.id;
        if (W.addQ) { await call(`/board/focus/${W.fid}/add-question`, "POST"); B.msgs.push({ text: "已提出一道新题的修订，到“生成大纲”页点“接受”，之后的新场次才会问。" }); }
        W.bf = await call(`/board/focus/${W.fid}/backfill`); W.bfPick = new Set(W.bf.candidates.filter((c) => c.relevance >= 0.4).map((c) => c.line_id)); W.step = 2;
      } catch { /* toast */ }
      return paint();
    }
    if (d.ob === "wfinish" || d.ob === "wskip") {
      const W = B.W;
      try { if (d.ob === "wfinish" && W.bfPick.size) { const r = await call(`/board/focus/${W.fid}/backfill`, "POST", { line_ids: [...W.bfPick] }); toast(`已补入 ${r.added} 条，记为“主动提到”`); } else toast("已加入看板"); } catch { /* toast */ }
      B.focus = W.fid; B.W = null; B.tab = "focus"; B.fd = null; await load(); return loadFocus();
    }
    if (d.ob === "cluster") { try { B.cl = await call(`/board/focus/${B.focus}/clusters`); } catch { /* toast */ } return paint(); }
    if (d.ob === "cluster-close") { B.cl = null; return paint(); }
    if (d.ob === "codes") {
      const groups = B.cl.groups.map((g, n) => ({ g, n })).filter(({ n }) => q(`[data-ob-gk="${n}"]`)?.checked).map(({ g, n }) => ({ name: (q(`[data-ob-gn="${n}"]`)?.value || g.name).trim(), answer_ids: g.members.map((m) => m.id) }));
      try { B.fd = await call(`/board/focus/${B.focus}/codes`, "POST", { groups }); B.cl = null; toast("已按你确认的类别统计"); } catch { /* toast */ }
      await load(); return paint();
    }
    if (d.ob === "adopt") {
      const ids = [...B.sel]; B.sel = null;
      try { const r = await call("/board/suggestions/adopt", "POST", { ids }); B.msgs.push({ text: r.proposed ? `已提出 ${r.proposed} 条修改，到“生成大纲”页点“接受”才会生效。` : "好的。" }); if (r.open_focus) { B.focus = r.open_focus; B.tab = "focus"; B.fd = null; await loadFocus(); } } catch { /* toast */ }
      return load();
    }
  }
  function onChange(e) {
    const t = e.target, d = t.dataset;
    if (d.obCand) { B.W.pick.has(d.obCand) ? B.W.pick.delete(d.obCand) : B.W.pick.add(d.obCand); return paint(); }
    if (d.obBf) { B.W.bfPick.has(d.obBf) ? B.W.bfPick.delete(d.obBf) : B.W.bfPick.add(d.obBf); return paint(); }
    if (d.obCoding) { B.W.coding = d.obCoding; B.W.custom = q("#ob-wcodes")?.value || B.W.custom; return paint(); }
    if (d.obSg) { B.sel.has(d.obSg) ? B.sel.delete(d.obSg) : B.sel.add(d.obSg); return paint(); }
    if (d.obPick !== undefined) { B.focus = t.value; B.fd = null; paint(); return loadFocus(); }
  }
  async function onSubmit(e) {
    if (e.target.id !== "ob-form") return; e.preventDefault();
    const text = (q("#ob-in").value || "").trim(); if (!text) return;
    B.msgs.push({ me: true, text });
    try { const r = await call("/board/chat", "POST", { text }); B.msgs.push({ text: r.reply }); } catch { /* toast */ }
    paint();
  }
  let wired = false;
  window.OutlineBoard = {
    init(p) {
      document.body.classList.remove("ob-on"); B.pid = p.id; B.on = false; B.d = null; B.fd = null; B.focus = ""; B.drawer = null; B.sel = null; clearInterval(B.timer);
      menu();
      if (wired) return; wired = true;
      document.addEventListener("click", (e) => { if (q("#owbbar")?.contains(e.target) || q("#owbroot")?.contains(e.target)) onClick(e); });
      document.addEventListener("change", (e) => { if (q("#owbroot")?.contains(e.target)) onChange(e); });
      document.addEventListener("submit", onSubmit);
    },
  };
})();
