/* “访谈”页：左边和 AI 对话（说一句话，大纲就跟着改），右边是实时的访谈大纲；大纲里每个问题都能直接改、删、加，也可以切到“文字”整段改，
   “设置”里是项目自己的资料（研究背景、研究目标、语言、访谈形式，和立项、招募问卷、项目总览是同一份）加对外标题和时长，“预览测试”按大纲一题一题试走（不保存任何东西）。
   大纲按“语言 × 访谈形式”分版本（右上角“版本”），“同步到”决定一处文字修改一起改到哪些版本；新增/删除问题永远改到所有版本，保证各版本对得上。大纲存在项目里（GET/PUT /projects/{id}/outline），
   和招募问卷工作台一样左右两个框各自滚动、中间分隔条可拖。依赖 index.html 里的 api / esc / toast / guard / $ / shell。 */
const OL_KIND = { warmup: "warmup", topic: "topic", stimulus: "stimulus" };
const OL_SHARE = { warmup: 1, topic: 3, stimulus: 3 };  // how the minutes are shared between the parts (only for showing the time bar)

/** Minutes for each part of a valid outline, adding up to the outline's length. */
function olMinutes(sum) {
  const w = sum.parts.map((x) => OL_SHARE[x.kind] || 1), tot = w.reduce((a, b) => a + b, 0) || 1;
  return w.map((x) => Math.max(1, Math.round((x / tot) * sum.minutes)));
}

async function outlineView(p) {
  const my = renderSeq;
  let V = ""; try { V = sessionStorage.getItem("ol_v_" + p.id) || ""; } catch { /* no storage */ }
  let S; try { S = await api(`/projects/${p.id}/outline${V ? "?v=" + encodeURIComponent(V) : ""}`); } catch { S = await api(`/projects/${p.id}/outline`); }
  if (stale(my)) return;
  V = S.v;
  let seg = sessionStorage.getItem("ol_seg") || "outline", editing = "", qmenu = "", viewer = "host", step = 0, pop = "", L = new Set(), F = new Set(), opened = new Set(), batch = false, picked = new Set(), autoPend = new Set(), autoFail = new Set();
  const RO = () => !!S.readonly;   // a client (viewer) sees the outline read-only but may still mark 重点关注
  if (!["outline", "settings", "test"].includes(seg)) seg = "outline";
  const stat = (t) => { const s = $("#olstat"); if (s) s.textContent = t; else if (t) toast(t); };
  const take = (r) => { S = { ...S, ...r }; V = S.v; try { sessionStorage.setItem("ol_v_" + p.id, V); } catch { /* no storage */ } };
  const cur = () => S.versions.find((x) => x.key === V) || S.versions[0];
  const resetSync = () => { const c = cur(); L = new Set([c.lang]); F = new Set([c.family]); };   // 同步到：一开始只有当前版本
  const syncKeys = () => S.versions.filter((x) => x.key !== V && L.has(x.lang) && F.has(x.family)).map((x) => x.key);
  const vname = (k) => { const x = S.versions.find((y) => y.key === k); return x ? x.name : k; };
  const syncNote = (s) => { if (!s) return; if (s.skipped.length) toast(`已改好当前版本；${s.skipped.map((x) => vname(x.key)).join("、")} 的这一段和当前版本对不上，没有改到`, true); else if (s.synced.length) stat(`已同步到 ${s.synced.length} 个版本`); };
  const goto = guard(async (key) => {   // 打开一个版本；还没有的先生成（译文或文字访谈改写）
    const x = S.versions.find((y) => y.key === key); if (!x) return;
    if (!x.exists) {   // 版本是自动生成的：还没好的先不能打开，生成失败的点一下重试
      if (autoFail.has(key)) { autoFail.delete(key); drawRight(); autoGen(); } else toast("这个版本正在生成，生成好就能打开");
      return;
    }
    take(await api(`/projects/${p.id}/outline?v=${encodeURIComponent(key)}`));
    pop = ""; editing = ""; step = 0; picked.clear(); resetSync(); drawRight();
  });
  const autoGen = async () => {   // 项目的语言和访谈形式定了，缺的版本自动在后台生成（一个接一个）
    if (autoGen.busy || !S.versions.some((x) => x.exists)) return;
    autoGen.busy = true;
    try {
      for (;;) {
        const x = S.versions.find((y) => !y.exists && !autoFail.has(y.key));
        if (!x) break;
        autoPend.add(x.key); if (seg === "outline") drawRight();
        try { await api(`/projects/${p.id}/outline/versions/${encodeURIComponent(x.key)}/generate`, { method: "POST" }); } catch (e) { autoFail.add(x.key); toast(`${x.name} 生成失败：${e.message}`, true); }
        autoPend.delete(x.key);
        const r = await api(`/projects/${p.id}/outline?v=${encodeURIComponent(V)}`);
        S = { ...S, versions: r.versions, families: r.families };
        resetSyncKeep(); drawRight();
      }
    } finally { autoGen.busy = false; }
  };
  const resetSyncKeep = () => { const c = cur(); if (!c) return; L = new Set([...L].filter((x) => S.versions.some((v) => v.lang === x))); F = new Set([...F].filter((x) => S.families.includes(x))); L.add(c.lang); F.add(c.family); };

  /* ---------- left: the stage-by-stage dialog (creation.js) is shared with the questionnaire page; here it only has to say what to do when it changed the study ---------- */
  const reload = guard(async () => { take(await api(`/projects/${p.id}/outline?v=${encodeURIComponent(V)}`)); drawRight(); autoGen(); });

  /* ---------- right: outline / text / settings / test ---------- */
  const edit = guard(async (body) => { const r = await api(`/projects/${p.id}/outline/edit`, { method: "POST", body: { ...body, v: V, sync: syncKeys() } }); take(r); editing = ""; drawRight(); syncNote(r.sync); });
  const putSettings = guard(async (body) => { const r = await api(`/projects/${p.id}/outline/settings`, { method: "PUT", body: { ...body, v: V, sync: syncKeys() } }); take(r); resetSync(); drawRight(); syncNote(r.sync); if (!(r.sync && r.sync.synced.length)) stat("已保存"); if (body.languages || body.formats) autoGen(); });
  const notes = () => `${S.problem ? `<div class="rf-warn" role="alert">${esc(S.problem)}</div>` : ""}${S.warning ? `<div class="rf-warn" role="note">${esc(S.warning)}</div>` : ""}`;
  const drawNotes = () => { const n = $("#olnotes"); if (n) { n.innerHTML = notes(); } };

  const DEPTH = { none: ["不追问", "只问这一题"], shallow: ["浅度追问", "追问 1 个"], deep: ["深度追问", "追问 2–3 个"], free: ["自由讨论", "追问 1–3 个，不限"] };
  const refOf = (pi, qi) => { const part = S.summary && S.summary.parts[pi], r = S.ref && S.ref[pi] && part && S.ref[pi].length === part.questions.length && S.ref[pi][qi]; return r ? `<span class="ol-ref">${esc(r.text)}</span>` : ""; };   // 其他语言的版本：原文（中文）放在下面对照
  const insRow = (part, qi) => `<div class="row ol-ins"><input class="olq" data-ins="${part.kind}:${part.ref}:${qi}" maxlength="300" placeholder="新问题（一句话，以问号结尾）" aria-label="新问题" style="flex:1;min-width:160px"><button class="p" type="button" data-insok="${part.kind}:${part.ref}:${qi}">添加</button><button class="s" type="button" data-no="1">取消</button></div>`;
  const questionRow = (part, q, qi, label, pi) => {
    if (q.cover) return "";   // “要了解”挂在环节标题下面（只有非中文版本显示）
    const key = `${part.kind}:${part.ref}:${qi}`;
    if (editing === key) return `<div class="row" style="margin:4px 0"><input class="olq" data-key="${key}" value="${esc(q.text)}" maxlength="300" aria-label="问题" style="flex:1;min-width:160px"><button class="p" type="button" data-ok="${key}">保存</button><button class="s" type="button" data-no="1">取消</button></div>`;
    if (batch) return `<label class="ol-q ol-bq"><input type="checkbox" class="ol-pk" data-pk="${key}" ${picked.has(key) ? "checked" : ""} aria-label="选择这个问题"><span class="rf-n">${label}</span><span class="ol-qt">${esc(q.text)}${refOf(pi, qi)}</span><span class="ol-dl" data-d="${q.depth || "shallow"}">${DEPTH[q.depth || "shallow"][0]}</span></label>`;
    const mk = (S.marks || {})[q.text], on = opened.has(key), depth = q.depth || "shallow", menu = qmenu === key, star = !!q.mark, ro = RO();
    const panel = !on ? "" : `<div class="ol-ex" data-ex="${key}">${ro ? "" : `<div class="ol-exr"><label for="d-${key}">追问深度</label><select id="d-${key}" class="ol-dp" data-dp-sel="${key}" aria-label="追问深度">${Object.entries(DEPTH).map(([k, v]) => `<option value="${k}"${k === depth ? " selected" : ""}>${v[0]} · ${v[1]}</option>`).join("")}</select></div><label for="g-${key}" class="ol-gl">访谈指引</label><textarea id="g-${key}" class="ol-gt" data-gt="${key}" maxlength="500" rows="3" aria-label="访谈指引">${esc(q.guide || "")}</textarea><div class="ol-gc"><span data-gc>${(q.guide || "").length}</span> / 500</div>`}<label for="r-${key}" class="ol-gl">关注原因</label><textarea id="r-${key}" class="ol-gt" data-reason="${key}" maxlength="500" rows="2" aria-label="关注原因" placeholder="想知道什么，给综合看板用">${esc(q.reason || "")}</textarea></div>`;
    const menuHtml = !menu || ro ? "" : `<div class="ol-qmenu" role="menu"><button type="button" role="menuitem" data-mdup="${key}">复制</button><button type="button" role="menuitem" data-mins="${key}:0">在上方添加问题</button><button type="button" role="menuitem" data-mins="${key}:1">在下方添加问题</button><hr><button type="button" role="menuitem" class="del" data-rm="${key}">删除</button></div>`;
    const above = editing === `ins:${key}:0` ? insRow(part, qi) : "", below = editing === `ins:${key}:1` ? insRow(part, qi + 1) : "";
    const markBy = q.mark && q.mark_by ? `<em class="ol-mkby">${q.mark_by === "client" ? "客户" : q.mark_by === "moderator" ? "主持人" : ""}</em>` : "";
    return `${above}<div class="ol-q ol-card2${mk ? " ol-" + mk : ""}" data-card="${key}">${ro ? "" : `<button class="ol-grip" type="button" data-grip="${key}" aria-label="拖动排序（也可以用上下方向键）">⋮⋮</button>`}
      <div class="ol-top"><span class="rf-n">${label}</span><button class="ol-star${star ? " on" : ""}" type="button" data-mk="${key}" aria-pressed="${star}" aria-label="${star ? "取消重点关注" : "标为重点关注"}" title="重点关注">${star ? "★" : "☆"}</button><span class="ol-qt"${ro ? "" : ` data-selk="q:${key}" data-ed="${key}" title="点一下修改"`}>${esc(q.text)}${mk ? `<em class="ol-mk">${mk === "new" ? "新增" : "已修改"}</em>` : ""}${markBy}${refOf(pi, qi)}</span><span class="ol-dl" data-d="${depth}">${DEPTH[depth][0]}</span>
        <button class="ol-cv${on ? " on" : ""}" type="button" data-tg="${key}" aria-expanded="${on}" aria-label="${on ? "收起" : "展开"}这个问题的设置">›</button>${ro ? "" : `<button class="ol-mn" type="button" data-qm="${key}" aria-haspopup="true" aria-expanded="${menu}" aria-label="更多">⋮</button>`}${menuHtml}</div>${panel}</div>${below}`;
  };
  const partCard = (part, mins, pi, n0) => {
    let k = n0; const live = cur().family === "live";
    const real = part.questions.filter((q) => !q.cover).length;
    const tkey = `title:${part.kind}:${part.ref}`, kindName = part.kind === "stimulus" ? "素材" : part.kind === "warmup" ? "" : "话题";
    const title = editing === tkey ? `<input class="olt" data-key="${tkey}" value="${esc(part.title)}" maxlength="120" aria-label="话题名称" style="flex:1;min-width:140px"><button class="p" type="button" data-tok="${tkey}">保存</button><button class="s" type="button" data-no="1">取消</button>`
      : `<span class="ol-pn">环节 ${pi + 1}</span><b>${kindName ? kindName + "：" : ""}${esc(part.title)}</b><span class="sp"></span><span class="sm mut">${real} 题${live ? ` · 约 ${mins} 分钟` : ""}</span>${RO() ? "" : `${part.kind === "topic" ? `<button class="s" type="button" data-rn="${tkey}">改名</button>` : ""}<button class="s" type="button" data-dp="${part.kind}:${part.ref}" aria-label="删除这一段">删除这一段</button>`}`;
    const covers = S.lang === "zh" ? "" : part.questions.map((q, qi) => (q.cover ? `<div class="rf-o mut">· ${esc(q.text)}${refOf(pi, qi)}</div>` : "")).join("");
    return `<div class="rf-q" data-part="${part.kind}:${part.ref}" data-n="${real}"><div class="row">${title}</div>${covers}${part.questions.map((q, qi) => questionRow(part, q, qi, q.cover ? "" : "Q" + (++k), pi)).join("")}
      ${editing === `add:${part.kind}:${part.ref}` ? `<div class="row" data-addrow style="margin:4px 0"><input class="olq" data-add-key="${part.kind}:${part.ref}" maxlength="300" placeholder="新问题（一句话，以问号结尾）" aria-label="新问题" style="flex:1;min-width:160px"><button class="p" type="button" data-addok="${part.kind}:${part.ref}">添加</button><button class="s" type="button" data-no="1">取消</button></div>`
        : RO() ? "" : `<div data-addrow><button class="s" type="button" data-add="${part.kind}:${part.ref}">＋ 添加问题</button></div>`}</div>`;
  };
  const famRow = () => {   // 访谈形式：语音/视频和文字访谈各有一份大纲，点一下切换（还没有的先生成）
    if (S.families.length < 2) return "";
    const c = cur();
    return `<div class="ol-fm"><span class="sm mut">访谈形式</span>${S.families.map((f) => { const v = S.versions.find((x) => x.lang === c.lang && x.family === f); return `<button type="button" class="ol-chip${f === c.family ? " on" : ""}" data-ver="${v.key}" aria-pressed="${f === c.family}">${esc(S.family_names[f])}${v.exists ? "" : autoFail.has(v.key) ? " ！" : " …"}</button>`; }).join("")}</div>`;
  };
  function outlineHtml() {
    const sum = S.summary, live = cur().family === "live";
    if (!sum) return famRow() + (S.text.trim() ? `<div class="empty"><b>文字还不能当大纲用</b><span>在左边修好或重新起草。</span></div>` : `<div class="empty"><b>还没有访谈大纲</b><span>在左边说“帮我起草大纲”。</span></div>`);
    const mins = olMinutes(sum), cols = ["#9FC9B8", "#1F6B54", "#EF9F27", "#6FD3A6", "#E8CF7A", "#B84900"];
    let n0 = 0; const cards = sum.parts.map((x, i) => { const c = partCard(x, mins[i], i, n0); n0 += x.questions.filter((q) => !q.cover).length; return c; });
    const real = sum.parts.flatMap((x, i) => x.questions.map((q, qi) => (q.cover ? null : `${x.kind}:${x.ref}:${qi}`))).filter(Boolean), n = syncKeys().length + 1;
    const bar = batch ? `<div class="ol-bt" role="toolbar" aria-label="批量操作"><label class="ol-ba"><input type="checkbox" data-pkall="1" ${picked.size && picked.size === real.length ? "checked" : ""}>全选</label><span class="sp">已选 ${picked.size} 题${n > 1 ? ` · 将改到 ${n} 个版本` : ""}</span><select data-bdepth aria-label="追问深度" class="ol-bs"><option value="">追问深度 ▾</option>${Object.entries(DEPTH).map(([k, v]) => `<option value="${k}">${v[0]} · ${v[1]}</option>`).join("")}</select><button type="button" class="ol-bd" data-bdel="1">删除</button><button type="button" class="ol-bx" data-bt="1">完成</button></div>` : "";
    return `${famRow()}<div class="row ol-hr"><h3 class="rf-h">共 ${sum.parts.length} 段 · ${sum.question_count} 题 · ${live ? `约 ${sum.minutes} 分钟` : "不计时长"}</h3><span class="sp"></span>${RO() || batch ? "" : `<button class="s" type="button" data-bt="1">批量</button>`}</div>${bar}
      ${live ? `<div class="ol-bar" role="img" aria-label="各段时间分配">${sum.parts.map((x, i) => `<i style="flex:${mins[i]};background:${cols[i % cols.length]}" title="${esc(x.title)} ${mins[i]} 分钟"></i>`).join("")}</div>` : ""}
      ${cards.join("")}
      ${RO() ? "" : `<div class="row" style="margin-top:8px"><input id="olnewt" maxlength="60" placeholder="新话题的名字" aria-label="新话题的名字" style="flex:1;min-width:120px"><input id="olnewq" maxlength="300" placeholder="第一个问题" aria-label="第一个问题" style="flex:2;min-width:160px"><button class="s" type="button" id="oladdp">＋ 添加话题</button></div>`}`;
  }
  const settingsHtml = () => {
    const s = S.settings, ed = s.can_edit_langs, ok = !!S.summary;
    const src = (x) => `<span class="ol-src">${x}</span>`;
    const chip = (on, attr, label, off) => `<button type="button" class="ol-chip${on ? " on" : ""}" ${attr} aria-pressed="${on}" ${off ? "disabled" : ""}>${esc(label)}</button>`;
    return `<div class="ol-card"><h3 class="rf-h">常规设置</h3><div class="ol-set">
      <label class="f"><span>对外标题 ${src("跟随“同步到”")}</span><input id="olset-topic" maxlength="18" value="${esc(s.title)}" ${ok ? "" : "disabled"}><small class="sm mut">18 字以内，不写品牌</small></label>
      <label class="f"><span>研究背景 ${src("与立项互通")}</span><textarea id="olset-brief" rows="4" maxlength="5000">${esc(s.brief)}</textarea></label>
      <label class="f"><span>研究目标 ${src("与招募问卷互通")}</span><textarea id="olset-goals" rows="4" placeholder="每行一条">${esc(s.goals.join("\n"))}</textarea></label>
      <div class="f"><span>访谈语言 ${src("与项目总览互通")}</span><div class="ol-chips">${s.languages.map((x) => chip(x.on, `data-sl="${x.code}"`, x.name, !ed || (x.on && x.in_use))).join("")}</div></div>
      <div class="ol-two"><label class="f"><span>预计时长（分钟）</span><input id="olset-min" type="number" min="10" max="180" step="5" value="${s.minutes}" ${ok ? "" : "disabled"}></label>
        <div class="f"><span>回答格式 ${src("与项目总览互通")}</span><div class="ol-chips">${Object.entries(s.format_names).map(([c, n]) => chip(s.formats.includes(c), `data-sf="${c}"`, n, !ed)).join("")}</div></div></div></div></div>
      ${ok ? "" : `<p class="sm mut">先有一份大纲，对外标题和时长才会生效。</p>`}`;
  };
  function testHtml() {
    const sum = S.summary; if (!sum) return `<div class="empty"><b>还没有可以试走的大纲</b><span>先在左边起草。</span></div>`;
    const flat = []; sum.parts.forEach((x, pi) => x.questions.forEach((q) => { if (!q.cover) flat.push({ part: x, pi, text: q.text }); }));
    if (!flat.length) return `<div class="empty"><b>大纲里还没有问题</b></div>`;
    step = Math.min(step, flat.length); const mins = olMinutes(sum), done = step >= flat.length, cur = flat[Math.min(step, flat.length - 1)];
    const used = Math.round(flat.slice(0, step).reduce((a, f) => a + mins[f.pi] / sum.parts[f.pi].questions.filter((q) => !q.cover).length, 0));
    const nav = `<div class="row" style="margin-top:10px"><button class="s" type="button" data-step="-1" ${step ? "" : "disabled"}>上一题</button><button class="p" type="button" data-step="1" ${done ? "disabled" : ""}>${step === flat.length - 1 ? "完成" : "下一题"}</button><button class="s" type="button" data-step="reset">重新开始</button></div>`;
    const host = `<div class="ol-dev"><div class="ol-bar2"><span>${esc(done ? "访谈结束" : cur.part.title)}</span><span>已用 ${used} / ${sum.minutes} 分钟</span></div>
      <div class="ol-bd">${done ? "<b>大纲走完了。</b>" : `<small>第 ${step + 1} / ${flat.length} 题</small><b>${esc(cur.text)}</b>${S.lang !== "zh" && cur.part.questions.filter((q) => q.cover).length ? `<div class="sm mut" style="margin-top:8px">${cur.part.questions.filter((q) => q.cover).map((q) => "· " + esc(q.text)).join("<br>")}</div>` : ""}`}
      <ol class="ol-list">${flat.map((f, i) => `<li class="${i === step ? "on" : i < step ? "done" : ""}">${esc(f.text)}</li>`).join("")}</ol></div></div>`;
    const part = `<div class="ol-dev ol-pt"><div class="ol-bar2"><span>受访者的屏幕</span><span>${done ? "" : `${step + 1} / ${flat.length}`}</span></div>
      <div class="ol-bd">${done ? "<b>访谈结束，谢谢你的时间！</b>" : `<b>${esc(cur.text)}</b><textarea rows="3" aria-label="模拟受访者回答" placeholder="（自己扮演受访者，随便写点什么，不会保存）" style="width:100%;margin-top:10px"></textarea>`}</div></div>`;
    return `<div class="row"><div class="rf-seg" role="radiogroup" aria-label="看哪一边"><button type="button" role="radio" data-view="host" class="${viewer === "host" ? "on" : ""}" aria-checked="${viewer === "host"}">主持人看到的</button><button type="button" role="radio" data-view="part" class="${viewer === "part" ? "on" : ""}" aria-checked="${viewer === "part"}">受访者看到的</button></div></div>
      <div style="margin-top:10px">${viewer === "host" ? host : part}${nav}</div>`;
  }
  const capsule = () => {   // 同步到：一处文字修改一起改到哪些版本（语言 × 访谈形式）
    if (S.versions.length < 2) return "";
    const c = cur(), n = S.versions.filter((x) => L.has(x.lang) && F.has(x.family)).length, all = n === S.versions.length;
    const label = n <= 1 ? "仅当前版本" : all ? "同步到：全部版本" : `同步到：${n} 个版本`;
    const langs = [...new Set(S.versions.map((x) => x.lang))], names = Object.fromEntries(S.versions.map((x) => [x.lang, x.name.split(" · ")[0]]));
    const box = (kind, code, text, locked, on) => `<label class="ol-o${locked ? " lk" : ""}"><input type="checkbox" data-${kind}="${code}" ${on ? "checked" : ""} ${locked ? "disabled" : ""}>${esc(text)}${locked ? "（当前）" : ""}</label>`;
    const menu = pop === "sync" ? `<div class="rf-vm ol-pop" role="group" aria-label="同步到"><label class="ol-o"><input type="checkbox" data-all="1" ${all ? "checked" : ""}><b>全部版本</b><span class="sp"></span><span class="sm mut">${S.versions.length} 个</span></label><div class="rf-vsep"></div>
      <div class="ol-h">语言</div>${langs.map((x) => box("lang", x, names[x], x === c.lang, L.has(x))).join("")}
      ${S.families.length > 1 ? `<div class="ol-h">访谈形式</div>${S.families.map((f) => box("fam", f, S.family_names[f], f === c.family, F.has(f))).join("")}` : ""}</div>` : "";
    return `<div class="rf-ver"><button type="button" class="ol-cap${n > 1 ? " on" : ""}" data-pop="sync" aria-haspopup="true" aria-expanded="${pop === "sync"}">${esc(label)} ▾</button>${menu}</div>`;
  };
  const verMenu = () => {
    if (S.versions.length < 2) return "";
    const c = cur();
    const rows = S.versions.map((x) => `<button type="button" class="rf-vi${x.key === V ? " on" : ""}" role="menuitemradio" aria-checked="${x.key === V}" data-ver="${x.key}"><span>${esc(x.name)}${x.base ? "（原文）" : ""}</span>${x.exists ? "" : autoPend.has(x.key) ? `<span class="rt-st o">正在生成…</span>` : autoFail.has(x.key) ? `<span class="rt-st r">生成失败 · 重试</span>` : `<span class="rt-st o">等待生成</span>`}</button>`).join("");
    return `<div class="rf-ver"><button type="button" data-pop="ver" aria-haspopup="true" aria-expanded="${pop === "ver"}">版本：${esc(c.name)} ▾</button>${pop === "ver" ? `<div class="rf-vm" role="menu">${rows}</div>` : ""}</div>`;
  };
  const segHtml = () => (RO() ? [["outline", "大纲"]] : [["outline", "大纲"], ["settings", "设置"], ["test", "预览测试"]]).map(([k, n]) => `<button type="button" role="tab" data-seg="${k}" class="${seg === k ? "on" : ""}" aria-selected="${seg === k}">${n}</button>`).join("");   // 大纲/设置/预览测试：放在外壳固定的二级行里（#olseg 带 data-subrow），这里只更新里面的按钮
  const rightHead = () => `<div class="rf-hd ol-hd"><span class="sp"></span><span class="sm mut" id="olstat" role="status" aria-live="polite"></span>${RO() ? "" : capsule()}${verMenu()}${RO() ? "" : exportMenu()}</div>`;
  const exportMenu = () => `<span class="ol-exp"><button type="button" data-pop="exp" aria-haspopup="true" aria-expanded="${pop === "exp"}" title="导出整份大纲">导出 ▾</button>${pop === "exp" ? `<div class="rf-vm ol-pop" role="menu"><button type="button" role="menuitem" data-exp="md">Markdown（.md）</button><button type="button" role="menuitem" data-exp="html">可打印网页（转 PDF / Word）</button></div>` : ""}</span>`;
  function drawRight() {
    const r = $("#olright"); if (!r) return;
    if (RO() && seg !== "outline") seg = "outline";
    const sg = $("#olseg"); if (sg) sg.innerHTML = segHtml();
    const keep = $("#olout") ? $("#olout").scrollTop : 0;
    const body = seg === "outline" ? outlineHtml() : seg === "settings" ? settingsHtml() : testHtml();
    r.innerHTML = `<div class="rf-seg ol-seg" id="olseg" role="tablist" aria-label="大纲视图">${segHtml()}</div>` + rightHead() + `<div class="rf-sc" id="olout"><div class="rf-col"><div id="olnotes">${notes()}</div>${body}</div></div>`;
    $("#olout").scrollTop = keep; wireRight();
  }
  const moveTo = async (from, to) => { const [kind, ref, idx] = from.split(":"); await edit({ op: "move", kind, ref: +ref, index: +idx, value: to }); drawRight(); };   // 失败时 edit 只弹提示，这里再画一遍，让卡片回到原位
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;   // 系统“减少动态效果”：只瞬间换位
  const slide = (c) => { const m = /matrix\(.*,\s*([-\d.]+)\)$/.exec(getComputedStyle(c).transform); return m ? +m[1] : 0; };
  const restTop = (c) => c.getBoundingClientRect().top - slide(c);   // 卡片“不算滑动动画”时的位置，判断落点不会被让位动画带偏
  const flip = (els, change) => {   // FLIP：先记下每张卡片的位置，改完顺序后让它们从旧位置滑到新位置（200ms）
    const first = new Map(els.map((c) => [c, restTop(c)]));
    change();
    if (calm()) return;
    els.forEach((c) => { const d = first.get(c) - restTop(c); if (!d) return; c.style.transition = "none"; c.style.transform = `translateY(${d}px)`; c.getBoundingClientRect(); c.style.transition = "transform .2s cubic-bezier(.2,.8,.2,1)"; c.style.transform = ""; });
  };
  function startDrag(grip, e0) {   // 按住手柄：卡片抬起跟着鼠标走，别的卡片顺滑让位，松手后缓缓落下再保存
    const card = grip.closest("[data-card]"), from = card.dataset.card, scroller = $("#olout"), grab = e0.clientY - card.getBoundingClientRect().top;
    let y = e0.clientY, timer = 0;
    card.classList.add("ol-lift"); card.classList.remove("ol-settle");
    const place = () => {
      const others = [...document.querySelectorAll("#olout [data-card]")].filter((c) => c !== card);
      card.style.transition = "none"; card.style.transform = "";
      const top = y - grab, mid = top + card.offsetHeight / 2;
      let before = others.find((c) => mid < restTop(c) + c.offsetHeight / 2) || null, box, target;
      if (before) {
        box = before.closest("[data-part]");
        const prev = box.previousElementSibling;
        if (before === box.querySelector("[data-card]") && prev && prev.matches("[data-part]") && mid < restTop(before)) box = prev, before = null;   // 还在下一环节的第一题上面：放在上一环节最后
      } else box = [...document.querySelectorAll("#olout [data-part]")].pop();
      target = before || box.querySelector("[data-addrow]");
      if (card.nextElementSibling !== target || card.parentElement !== box) flip(others, () => box.insertBefore(card, target));
      card.style.transform = `translateY(${top - card.getBoundingClientRect().top}px)`;
    };
    const move = (e) => { y = e.clientY; place(); };
    const edge = () => { const r = scroller.getBoundingClientRect(); if (y < r.top + 40) scroller.scrollTop -= 12; else if (y > r.bottom - 40) scroller.scrollTop += 12; else return; place(); };
    const stop = (cancel) => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", cancelled); clearInterval(timer);
      const box = card.closest("[data-part]"), [dk, dr] = box.dataset.part.split(":"), [fk, fr, fi] = from.split(":"), idx = [...box.querySelectorAll("[data-card]")].indexOf(card);
      const same = fk === dk && fr === dr, unchanged = cancel || (same && idx === +fi);
      card.classList.remove("ol-lift"); card.classList.add("ol-settle"); card.style.transition = ""; card.style.transform = "";   // 缓缓落到位（220ms）
      setTimeout(() => { card.classList.remove("ol-settle"); if (unchanged) { if (cancel || !same) drawRight(); return; } moveTo(from, `${dk}:${dr}:${same && idx > +fi ? idx + 1 : idx}`); }, calm() ? 0 : 230);
    };
    const up = () => stop(false), cancelled = () => stop(true);
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", cancelled);
    timer = setInterval(edge, 16); place();
  }
  function wireDrag() {   // 拖动手柄；手柄上按上下方向键一格一格挪（动效一样）
    document.querySelectorAll("[data-grip]").forEach((g) => {
      g.onpointerdown = (e) => { if (e.button) return; e.preventDefault(); startDrag(g, e); };
      g.onkeydown = (e) => {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        const [kind, ref, idx] = g.dataset.grip.split(":"), n = +document.querySelector(`[data-part="${kind}:${ref}"]`).dataset.n, i = +idx, up = e.key === "ArrowUp";
        const pos = up ? i - 1 : i + 2;
        if (pos < 0 || pos > n) return;
        const card = g.closest("[data-card]"), sib = up ? card.previousElementSibling : card.nextElementSibling;
        if (sib && sib.matches("[data-card]")) flip([card, sib], () => card.parentNode.insertBefore(up ? card : sib, up ? sib : card));
        setTimeout(async () => { await moveTo(g.dataset.grip, `${kind}:${ref}:${pos}`); const again = document.querySelector(`[data-grip="${kind}:${ref}:${up ? i - 1 : i + 1}"]`); if (again) again.focus(); }, calm() ? 0 : 210);
      };
    });
  }
  function wireRight() {
    document.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { seg = b.dataset.seg; sessionStorage.setItem("ol_seg", seg); drawRight(); }));
    document.querySelectorAll("[data-pop]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); pop = pop === b.dataset.pop ? "" : b.dataset.pop; drawRight(); }));
    document.querySelectorAll("[data-ver]").forEach((b) => (b.onclick = () => { if (b.dataset.ver === V) { pop = ""; drawRight(); } else goto(b.dataset.ver); }));
    document.querySelectorAll("[data-exp]").forEach((b) => (b.onclick = guard(async () => {   // 导出：下载需要登录头，取回后交给浏览器保存
      const fmt = b.dataset.exp, ext = fmt === "html" ? "html" : "md";
      const r = await fetch(`/api/projects/${p.id}/outline/export?format=${fmt}&v=${encodeURIComponent(V)}`, { headers: { Authorization: "Bearer " + store.get("tt_token") } });
      if (!r.ok) { const t = await r.text(); throw new Error(t ? (JSON.parse(t).detail || "导出失败") : "导出失败，请稍后再试"); }
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = `${(S.summary && S.summary.topic) || p.name || "访谈大纲"}-${V}.${ext}`; document.body.appendChild(a); a.click(); a.remove();
      toast(fmt === "html" ? "已导出可打印网页（浏览器打印即转 PDF）" : "已导出 Markdown");
    })));
    const picks = (set, code, on) => { if (on) set.add(code); else set.delete(code); drawRight(); };
    document.querySelectorAll("[data-lang]").forEach((i) => (i.onchange = () => picks(L, i.dataset.lang, i.checked)));
    document.querySelectorAll("[data-fam]").forEach((i) => (i.onchange = () => picks(F, i.dataset.fam, i.checked)));
    document.querySelectorAll("[data-all]").forEach((i) => (i.onchange = () => { if (i.checked) { L = new Set(S.versions.map((x) => x.lang)); F = new Set(S.versions.map((x) => x.family)); } else resetSync(); drawRight(); }));
    if (seg === "settings") {
      const s = S.settings, topic = $("#olset-topic"), mins = $("#olset-min"), brief = $("#olset-brief"), goals = $("#olset-goals");
      topic.onchange = () => putSettings({ title: topic.value.trim() });
      mins.onchange = () => putSettings({ minutes: Math.min(180, Math.max(10, +mins.value || 45)) });
      brief.onchange = () => putSettings({ brief: brief.value });
      goals.onchange = () => putSettings({ goals: goals.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 8) });
      document.querySelectorAll("[data-sl]").forEach((b) => (b.onclick = () => {
        const on = s.languages.filter((x) => x.on).map((x) => x.code), c = b.dataset.sl, next = on.includes(c) ? on.filter((x) => x !== c) : [...on, c];
        if (!next.length) return toast("至少保留一种语言", true);
        const def = (s.languages.find((x) => x.default) || {}).code;
        putSettings({ languages: next, default: next.includes(def) ? def : next[0] });
      }));
      document.querySelectorAll("[data-sf]").forEach((b) => (b.onclick = () => {
        const c = b.dataset.sf, next = s.formats.includes(c) ? s.formats.filter((x) => x !== c) : [...s.formats, c];
        if (!next.length) return toast("至少保留一种访谈形式", true);
        putSettings({ formats: next });
      }));
      return;
    }
    if (seg === "test") {
      document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { viewer = b.dataset.view; drawRight(); }));
      document.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => { step = b.dataset.step === "reset" ? 0 : Math.max(0, step + +b.dataset.step); drawRight(); }));
      return;
    }
    const keyOf = (s) => s.split(":");
    document.querySelectorAll("[data-ed]").forEach((b) => (b.onclick = () => { if (String(getSelection())) return; editing = b.dataset.ed; qmenu = ""; drawRight(); const i = document.querySelector(".olq"); if (i) { i.focus(); i.select(); } }));
    document.querySelectorAll("[data-qm]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); qmenu = qmenu === b.dataset.qm ? "" : b.dataset.qm; drawRight(); }));
    document.querySelectorAll("[data-mdup]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.mdup); qmenu = ""; edit({ op: "dup", kind, ref: +ref, index: +idx }); }));
    document.querySelectorAll("[data-mins]").forEach((b) => (b.onclick = () => { const [kind, ref, idx, off] = keyOf(b.dataset.mins); qmenu = ""; editing = `ins:${kind}:${ref}:${idx}:${off}`; drawRight(); const i = document.querySelector(".olq"); if (i) i.focus(); }));
    document.querySelectorAll("[data-insok]").forEach((b) => (b.onclick = () => { const [kind, ref, pos] = keyOf(b.dataset.insok), v = document.querySelector(".olq").value.trim(); if (!v) return toast("问题不能是空的", true); edit({ op: "insert", kind, ref: +ref, index: +pos, value: v }); }));
    wireDrag();
    document.querySelectorAll("[data-rn]").forEach((b) => (b.onclick = () => { editing = b.dataset.rn; drawRight(); const i = document.querySelector(".olt"); if (i) { i.focus(); i.select(); } }));
    const doBatch = guard(async (action, depth) => {
      const items = [...picked].map((k) => { const [kind, ref, idx] = keyOf(k); return [kind, +ref, +idx]; });
      const r = await api(`/projects/${p.id}/outline/batch`, { method: "POST", body: { action, items, depth: depth || "", v: V, sync: syncKeys() } });
      take(r); picked.clear(); editing = ""; drawRight(); syncNote(r.sync);
    });
    document.querySelectorAll("[data-bt]").forEach((b) => (b.onclick = () => { batch = !batch; picked.clear(); drawRight(); }));
    document.querySelectorAll("[data-pk]").forEach((i) => (i.onchange = () => { if (i.checked) picked.add(i.dataset.pk); else picked.delete(i.dataset.pk); drawRight(); }));
    document.querySelectorAll("[data-pkall]").forEach((i) => (i.onchange = () => { picked.clear(); if (i.checked) (S.summary ? S.summary.parts : []).forEach((x) => x.questions.forEach((q, qi) => { if (!q.cover) picked.add(`${x.kind}:${x.ref}:${qi}`); })); drawRight(); }));
    document.querySelectorAll("[data-bdepth]").forEach((s) => (s.onchange = () => { if (!s.value) return; if (!picked.size) { s.value = ""; return toast("先勾选要改的问题", true); } doBatch("depth", s.value); }));
    document.querySelectorAll("[data-bdel]").forEach((b) => (b.onclick = () => { if (!picked.size) return toast("先勾选要删除的问题", true); doBatch("delete"); }));
    document.querySelectorAll("[data-tg]").forEach((b) => (b.onclick = () => { const k = b.dataset.tg; opened.has(k) ? opened.delete(k) : opened.add(k); drawRight(); }));
    const saveMeta = (key) => { const box = document.querySelector(`[data-ex="${key}"]`), [kind, ref, idx] = keyOf(key); const r = box.querySelector("[data-reason]"), reason = r ? r.value.trim() : ""; edit({ op: "meta", kind, ref: +ref, index: +idx, value: `${box.querySelector("select").value}|${box.querySelector("textarea").value}` }).then(() => { if (reason) edit({ op: "mark", kind, ref: +ref, index: +idx, value: `1|${reason}` }); }); };
    document.querySelectorAll("[data-dp-sel]").forEach((s) => (s.onchange = () => saveMeta(s.dataset.dpSel)));
    document.querySelectorAll("[data-gt]").forEach((a) => { a.oninput = () => { const c = a.parentElement.querySelector("[data-gc]"); if (c) c.textContent = a.value.length; }; a.onchange = () => saveMeta(a.dataset.gt); });
    document.querySelectorAll("[data-reason]").forEach((a) => (a.onchange = () => { const key = a.dataset.reason, [kind, ref, idx] = keyOf(key); const on = a.closest("[data-ex]") ? true : false; edit({ op: "mark", kind, ref: +ref, index: +idx, value: `${on ? "1" : "0"}|${a.value.trim()}` }); }));
    document.querySelectorAll("[data-mk]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.mk); const cur = b.classList.contains("on"); edit({ op: "mark", kind, ref: +ref, index: +idx, value: cur ? "0" : "1" }); }));
    document.querySelectorAll("[data-no]").forEach((b) => (b.onclick = () => { editing = ""; drawRight(); }));
    document.querySelectorAll("[data-ok]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.ok), v = document.querySelector(".olq").value; edit({ op: "set", kind, ref: +ref, index: +idx, value: v }); }));
    document.querySelectorAll(".olq").forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); (document.querySelector("[data-ok]") || document.querySelector("[data-insok]") || document.querySelector("[data-addok]")).click(); } else if (e.key === "Escape") { editing = ""; drawRight(); } }));
    document.querySelectorAll("[data-tok]").forEach((b) => (b.onclick = () => { const [, kind, ref] = keyOf(b.dataset.tok), v = document.querySelector(".olt").value; edit({ op: "rename", kind, ref: +ref, value: v }); }));
    document.querySelectorAll(".olt").forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); document.querySelector("[data-tok]").click(); } else if (e.key === "Escape") { editing = ""; drawRight(); } }));
    document.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.rm); edit({ op: "delete", kind, ref: +ref, index: +idx }); }));
    document.querySelectorAll("[data-dp]").forEach((b) => (b.onclick = () => { const [kind, ref] = keyOf(b.dataset.dp); edit({ op: "del_part", kind, ref: +ref }); }));
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => { editing = "add:" + b.dataset.add; drawRight(); const i = document.querySelector(".olq"); if (i) i.focus(); }));
    document.querySelectorAll("[data-addok]").forEach((b) => (b.onclick = () => { const [kind, ref] = keyOf(b.dataset.addok), v = document.querySelector(".olq").value.trim(); if (!v) return toast("问题不能是空的", true); edit({ op: "add", kind, ref: +ref, value: v }); }));
    const ap = $("#oladdp"); if (ap) ap.onclick = () => { const t = $("#olnewt").value.trim(), q = $("#olnewq").value.trim(); if (!t || !q) return toast("话题名字和第一个问题都要写", true); edit({ op: "add_part", value: `${t.replace(/\|/g, " ")}|${q}` }); };
  }

  if (window._olOff) document.removeEventListener("click", window._olOff);
  window._olOff = (e) => { const out = !e.target.closest(".ol-pop, .rf-vm, [data-pop]"), qout = qmenu && !e.target.closest(".ol-qmenu, [data-qm]"); if ((pop && out) || qout) { if (pop && out) pop = ""; if (qout) qmenu = ""; if ($("#olright")) drawRight(); } };
  document.addEventListener("click", window._olOff);

  /* ---------- the whole page ---------- */
  shell(`<div class="rf" style="padding:0"><div class="rf-ws" id="olws"><div class="rf-pane">${RO() ? `<div class="ol-ro"><b>只读 · 客户视图</b><span>你可以在这里给问题标 ★ 重点关注、写关注原因；研究团队会在综合看板里看到。</span></div>` : `<div class="cc" id="ccroot"></div>`}</div>
      <div class="rf-split" id="olsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="olright"></div></div></div>`, { active: "outline", p });
  document.body.classList.add("rfmode", "pgw");
  const ws = $("#olws"), fit = () => { if (ws && ws.offsetParent) ws.style.height = Math.max(380, innerHeight - ws.getBoundingClientRect().top - 18) + "px"; };
  fit(); pageOn("fit", window, "resize", fit);
  resetSync(); drawRight(); autoGen(); if (!RO()) CreationChat.mount($("#ccroot"), p, { onChange: reload });
  const key = "ol_left_pct", setRatio = (pct) => ws.style.setProperty("--rfl", Math.min(60, Math.max(30, pct)) + "%");
  let saved = 42; try { saved = +localStorage.getItem(key) || 42; } catch { /* no storage: the default */ }
  setRatio(saved);
  const sp = $("#olsplit");
  const move = (e) => { const r = ws.getBoundingClientRect(); setRatio(((e.clientX - r.left) / r.width) * 100); };
  const done = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", done); try { localStorage.setItem(key, parseFloat(ws.style.getPropertyValue("--rfl")) || 42); } catch { /* no storage */ } };
  sp.onpointerdown = (e) => { e.preventDefault(); addEventListener("pointermove", move); addEventListener("pointerup", done); };
  sp.ondblclick = () => { setRatio(42); try { localStorage.removeItem(key); } catch { /* no storage */ } };
  sp.onkeydown = (e) => { if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return; e.preventDefault(); setRatio((parseFloat(ws.style.getPropertyValue("--rfl")) || 42) + (e.key === "ArrowRight" ? 2 : -2)); };
}
