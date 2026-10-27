/* “访谈”页：左边和 AI 对话（说一句话，大纲就跟着改），右边是实时的访谈大纲；大纲里每个问题都能直接改、删、加，也可以切到“文字”整段改，
   “设置”里改语言、时长、受访者话题，“预览测试”按大纲一题一题试走（不保存任何东西）。大纲存在项目里（GET/PUT /projects/{id}/outline），
   和招募问卷工作台一样左右两个框各自滚动、中间分隔条可拖。依赖 index.html 里的 api / esc / toast / guard / $ / shell。 */
const OL_KIND = { warmup: "warmup", topic: "topic", stimulus: "stimulus" };
const OL_SHARE = { warmup: 1, topic: 3, stimulus: 3 };  // how the minutes are shared between the parts (only for showing the time bar)

/** Set (or add) one header line of the outline text: 语言 / 时长 / 受访者话题. */
function olSetHeader(text, keys, label, value) {
  const lines = text.split("\n"), re = new RegExp("^\\s*(" + keys.join("|") + ")\\s*[:：]", "i");
  const at = lines.findIndex((l, i) => re.test(l) && !lines.slice(0, i).some((x) => x.startsWith("##")));
  const row = `${label}: ${value}`;
  if (at >= 0) lines[at] = row; else lines.unshift(row);
  return lines.join("\n");
}

/** Minutes for each part of a valid outline, adding up to the outline's length. */
function olMinutes(sum) {
  const w = sum.parts.map((x) => OL_SHARE[x.kind] || 1), tot = w.reduce((a, b) => a + b, 0) || 1;
  return w.map((x) => Math.max(1, Math.round((x / tot) * sum.minutes)));
}

async function outlineView(p) {
  const my = renderSeq;
  let S = await api(`/projects/${p.id}/outline`);
  if (stale(my)) return;
  let seg = sessionStorage.getItem("ol_seg") || "outline", sending = false, pending = "", editing = "", viewer = "host", step = 0, saveTimer = null;
  let useLib = sessionStorage.getItem("ol_lib") !== "0";
  const notice = () => (S.ai_local ? "AI 运行在我们自己的服务器上，对话内容不会发给其他公司。"
    : `你说的话、立项里的研究背景和本项目研究库里的资料，会发送给 AI 服务商${S.ai_vendor ? `“${S.ai_vendor}”` : ""}来改大纲。请不要写入受访者的姓名、电话等个人信息。`);
  const stat = (t) => { const s = $("#olstat"); if (s) s.textContent = t; };
  const take = (r) => { S = { ...S, ...r }; };

  /* ---------- left: the conversation ---------- */
  const QUICK = { "给每个核心问题补追问": "给每个核心问题都补一个追问", "检查有没有引导性问题": "检查有没有引导性或带倾向的问题，有就改掉", "缩短到 30 分钟": "把整个访谈缩短到 30 分钟左右", "帮我起草大纲": "帮我起草大纲" };
  const aiBody = (m) => {   // an assistant message: its words, then a change card (with undo) or the suggestions (adopt / skip)
    let h = `<div class="ol-tx">${esc(m.text)}</div>`;
    if (m.kind === "edit") {
      h += `<div class="ol-chg${m.undone ? " off" : ""}"><b>${m.undone ? "这次修改已撤销" : "已更新大纲"}</b><ul>${m.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`
        + (m.can_undo ? `<div class="row"><button class="s" type="button" data-undo="${m.id}">撤销这次修改</button></div>` : m.undone ? "" : `<div class="sm mut">之后大纲又改过，不能直接撤销（会丢掉后面的改动）。</div>`) + `</div>`;
    }
    if (m.kind === "suggest") {
      h += m.suggestions.map((s) => `<div class="ol-sg"><div>${esc(s.title)}</div>${s.state === "open" ? `<div class="row"><button class="p" type="button" data-adopt="${m.id}:${s.id}">采纳</button><button class="s" type="button" data-skip="${m.id}:${s.id}">先不用</button></div>`
        : `<div class="sm mut">${s.state === "adopted" ? "已采纳" : "已略过"}</div>`}</div>`).join("");
    }
    return h;
  };
  const ctxHtml = () => {
    const c = S.context || {}, parts = [c.brief ? "✓ 研究背景" : "· 没有研究背景"];
    if (useLib) parts.push(c.library_docs ? `✓ 研究库 ${c.library_docs} 份` : "· 研究库里还没有资料");
    parts.push(c.questions ? `✓ 当前大纲（${c.parts} 段 · ${c.questions} 题）` : "· 还没有大纲");
    return parts.map((x) => `<span>${x}</span>`).join("");
  };
  const chatHtml = () => {
    const hello = `<div class="rf-msg">你好！我是访谈大纲助手。你可以让我起草大纲，也可以直接问我问题、让我解释或给建议；你说“帮我改成……”我就直接改，并写清楚改了什么，不满意可以一键撤销。</div>`;
    const msgs = S.chat.map((m) => (m.role === "user" ? `<div class="rf-msg rf-me">${esc(m.text)}</div>` : `<div class="rf-msg">${aiBody(m)}</div>`)).join("");
    const fix = S.problem && !pending ? `<div class="rf-msg"><div class="ol-tx">我发现大纲文字里有一处写得不对，系统看不懂：</div><div class="ol-sg"><div>${esc(S.problem)}</div></div><div class="row"><button class="p" type="button" data-fix="1">帮我修好</button><button class="s" type="button" data-own="1">我自己改</button></div></div>` : "";
    const chips = S.summary ? ["给每个核心问题补追问", "检查有没有引导性问题", "缩短到 30 分钟"] : ["帮我起草大纲"];
    return hello + msgs + fix + (pending ? `<div class="rf-msg rf-me">${esc(pending)}</div><div class="rf-msg mut">AI 正在想…</div>`
      : `<div class="rf-chips">${chips.map((c) => `<button class="s" type="button" data-chip="${esc(c)}">${esc(c)}</button>`).join("")}</div>`);
  };
  const fixMessage = () => `请帮我修好大纲文字里的这个问题：${S.problem}。如果修不了，就按研究背景重新整理一份大纲。`.slice(0, 900);
  const drawChat = () => {
    const c = $("#olchat"); if (!c) return;
    c.innerHTML = chatHtml(); c.scrollTop = c.scrollHeight;
    const x = $("#olctx"); if (x) x.innerHTML = ctxHtml();
    c.querySelectorAll("[data-chip]").forEach((b) => (b.onclick = () => send(QUICK[b.dataset.chip] || b.dataset.chip)));
    c.querySelectorAll("[data-undo]").forEach((b) => (b.onclick = () => act(b.dataset.undo, { action: "undo" })));
    c.querySelectorAll("[data-adopt]").forEach((b) => (b.onclick = () => { const [mid, sid] = b.dataset.adopt.split(":"); act(mid, { action: "adopt", sid }); }));
    c.querySelectorAll("[data-skip]").forEach((b) => (b.onclick = () => { const [mid, sid] = b.dataset.skip.split(":"); act(mid, { action: "dismiss", sid }); }));
    c.querySelectorAll("[data-fix]").forEach((b) => (b.onclick = () => send(fixMessage())));
    c.querySelectorAll("[data-own]").forEach((b) => (b.onclick = () => { seg = "text"; sessionStorage.setItem("ol_seg", seg); drawRight(); }));
  };
  const act = guard(async (mid, body) => { take(await api(`/projects/${p.id}/outline/chat/${mid}`, { method: "POST", body })); drawChat(); drawRight(); });
  async function send(text) {
    text = (text || "").trim(); if (!text || sending) return;
    sending = true; pending = text; $("#olmsg").value = ""; $("#olsend").disabled = true; drawChat();
    try { take(await api(`/projects/${p.id}/outline/chat`, { method: "POST", body: { message: text, use_library: useLib } })); }
    catch (e) { S = { ...S, chat: [...S.chat, { role: "user", text }, { role: "ai", kind: "answer", text: "没有成功：" + e.message, changes: [], suggestions: [] }] }; toast(e.message, true); }
    sending = false; pending = ""; const b = $("#olsend"); if (b) b.disabled = false; drawChat(); drawRight();
  }

  /* ---------- right: outline / text / settings / test ---------- */
  const edit = guard(async (body) => { take(await api(`/projects/${p.id}/outline/edit`, { method: "POST", body })); editing = ""; drawRight(); drawChat(); });
  const save = (text) => {
    clearTimeout(saveTimer); stat("保存中…");
    saveTimer = setTimeout(async () => { try { take(await api(`/projects/${p.id}/outline`, { method: "PUT", body: { text } })); stat("已保存"); if (seg !== "text") drawRight(); else drawNotes(); drawChat(); } catch (e) { stat("还没保存：" + e.message); } }, 600);
  };
  const notes = () => `${S.problem ? `<div class="rf-warn" role="alert">${esc(S.problem)}<button class="s" type="button" data-fixnote="1">让 AI 帮我修</button></div>` : ""}${S.warning ? `<div class="rf-warn" role="note">${esc(S.warning)}</div>` : ""}`;
  const drawNotes = () => { const n = $("#olnotes"); if (n) { n.innerHTML = notes(); n.querySelectorAll("[data-fixnote]").forEach((b) => (b.onclick = () => send(fixMessage()))); } };

  const questionRow = (part, q, qi) => {
    if (q.cover) return `<div class="rf-o mut">· 要了解：${esc(q.text)}</div>`;
    const key = `${part.kind}:${part.ref}:${qi}`;
    if (editing === key) return `<div class="row" style="margin:4px 0"><input class="olq" data-key="${key}" value="${esc(q.text)}" maxlength="300" aria-label="问题" style="flex:1;min-width:160px"><button class="p" type="button" data-ok="${key}">保存</button><button class="s" type="button" data-no="1">取消</button></div>`;
    const mk = (S.marks || {})[q.text];
    return `<div class="ol-q${mk ? " ol-" + mk : ""}"><span class="rf-n">${qi + 1}</span><span class="ol-qt">${esc(q.text)}${mk ? `<em class="ol-mk">${mk === "new" ? "新增" : "已修改"}</em>` : ""}</span><span class="ol-qa"><button class="s" type="button" data-ed="${key}" aria-label="编辑这个问题">编辑</button><button class="s" type="button" data-rm="${key}" aria-label="删除这个问题">删除</button></span></div>`;
  };
  const partCard = (part, mins) => {
    const real = part.questions.filter((q) => !q.cover).length;
    const tkey = `title:${part.kind}:${part.ref}`, kindName = part.kind === "stimulus" ? "素材" : part.kind === "warmup" ? "" : "话题";
    const title = editing === tkey ? `<input class="olt" data-key="${tkey}" value="${esc(part.title)}" maxlength="120" aria-label="话题名称" style="flex:1;min-width:140px"><button class="p" type="button" data-tok="${tkey}">保存</button><button class="s" type="button" data-no="1">取消</button>`
      : `<b>${kindName ? kindName + "：" : ""}${esc(part.title)}</b><span class="sp"></span><span class="sm mut">${real} 题 · 约 ${mins} 分钟</span>${part.kind === "topic" ? `<button class="s" type="button" data-rn="${tkey}">改名</button>` : ""}<button class="s" type="button" data-dp="${part.kind}:${part.ref}" aria-label="删除这一段">删除这一段</button>`;
    return `<div class="rf-q"><div class="row">${title}</div>${part.questions.map((q, qi) => questionRow(part, q, qi)).join("")}
      ${editing === `add:${part.kind}:${part.ref}` ? `<div class="row" style="margin:4px 0"><input class="olq" data-add-key="${part.kind}:${part.ref}" maxlength="300" placeholder="新问题（一句话，以问号结尾）" aria-label="新问题" style="flex:1;min-width:160px"><button class="p" type="button" data-addok="${part.kind}:${part.ref}">添加</button><button class="s" type="button" data-no="1">取消</button></div>`
        : `<div><button class="s" type="button" data-add="${part.kind}:${part.ref}">＋ 添加问题</button></div>`}</div>`;
  };
  function outlineHtml() {
    const sum = S.summary;
    if (!sum) return S.text.trim() ? `<div class="empty"><b>文字还不能当大纲用</b><span>到“文字”页按提示改好，或在左边让 AI 重新起草。</span></div>` : `<div class="empty"><b>还没有访谈大纲</b><span>在左边说“帮我起草大纲”，或到“文字”页自己写。</span></div>`;
    const mins = olMinutes(sum), cols = ["#9FC9B8", "#1F6B54", "#EF9F27", "#6FD3A6", "#E8CF7A", "#B84900"];
    return `<h3 class="rf-h">共 ${sum.parts.length} 段 · ${sum.question_count} 题 · 约 ${sum.minutes} 分钟</h3>
      <div class="ol-bar" role="img" aria-label="各段时间分配">${sum.parts.map((x, i) => `<i style="flex:${mins[i]};background:${cols[i % cols.length]}" title="${esc(x.title)} ${mins[i]} 分钟"></i>`).join("")}</div>
      ${sum.parts.map((x, i) => partCard(x, mins[i])).join("")}
      <div class="row" style="margin-top:8px"><input id="olnewt" maxlength="60" placeholder="新话题的名字" aria-label="新话题的名字" style="flex:1;min-width:120px"><input id="olnewq" maxlength="300" placeholder="第一个问题" aria-label="第一个问题" style="flex:2;min-width:160px"><button class="s" type="button" id="oladdp">＋ 添加话题</button></div>`;
  }
  const settingsHtml = () => {
    const sum = S.summary, lang = sum ? sum.language : "zh", mins = sum ? sum.minutes : 45, topic = sum ? sum.topic : "";
    return `<h3 class="rf-h">访谈设置</h3><div class="ol-set">
      <label class="f"><span>访谈语言</span><select id="olset-lang"><option value="zh" ${lang === "zh" ? "selected" : ""}>中文</option><option value="en" ${lang === "en" ? "selected" : ""}>English</option></select></label>
      <label class="f"><span>预计时长（分钟）</span><input id="olset-min" type="number" min="10" max="180" step="5" value="${mins}"></label>
      <label class="f"><span>对受访者说的话题（18 字以内，不写品牌）</span><input id="olset-topic" maxlength="18" value="${esc(topic)}"></label></div>
      ${sum ? "" : `<p class="sm mut">先有一份大纲，这里的设置才会生效。</p>`}`;
  };
  function testHtml() {
    const sum = S.summary; if (!sum) return `<div class="empty"><b>还没有可以试走的大纲</b><span>先在左边让 AI 起草。</span></div>`;
    const flat = []; sum.parts.forEach((x, pi) => x.questions.forEach((q) => { if (!q.cover) flat.push({ part: x, pi, text: q.text }); }));
    if (!flat.length) return `<div class="empty"><b>大纲里还没有问题</b></div>`;
    step = Math.min(step, flat.length); const mins = olMinutes(sum), done = step >= flat.length, cur = flat[Math.min(step, flat.length - 1)];
    const used = Math.round(flat.slice(0, step).reduce((a, f) => a + mins[f.pi] / sum.parts[f.pi].questions.filter((q) => !q.cover).length, 0));
    const nav = `<div class="row" style="margin-top:10px"><button class="s" type="button" data-step="-1" ${step ? "" : "disabled"}>上一题</button><button class="p" type="button" data-step="1" ${done ? "disabled" : ""}>${step === flat.length - 1 ? "完成" : "下一题"}</button><button class="s" type="button" data-step="reset">重新开始</button></div>`;
    const host = `<div class="ol-dev"><div class="ol-bar2"><span>${esc(done ? "访谈结束" : cur.part.title)}</span><span>已用 ${used} / ${sum.minutes} 分钟</span></div>
      <div class="ol-bd">${done ? "<b>大纲走完了。</b>" : `<small>第 ${step + 1} / ${flat.length} 题</small><b>${esc(cur.text)}</b>${cur.part.questions.filter((q) => q.cover).length ? `<div class="sm mut" style="margin-top:8px">要了解：${cur.part.questions.filter((q) => q.cover).map((q) => esc(q.text)).join("；")}</div>` : ""}`}
      <ol class="ol-list">${flat.map((f, i) => `<li class="${i === step ? "on" : i < step ? "done" : ""}">${esc(f.text)}</li>`).join("")}</ol></div></div>`;
    const part = `<div class="ol-dev ol-pt"><div class="ol-bar2"><span>受访者的屏幕</span><span>${done ? "" : `${step + 1} / ${flat.length}`}</span></div>
      <div class="ol-bd">${done ? "<b>访谈结束，谢谢你的时间！</b>" : `<b>${esc(cur.text)}</b><textarea rows="3" aria-label="模拟受访者回答" placeholder="（自己扮演受访者，随便写点什么，不会保存）" style="width:100%;margin-top:10px"></textarea>`}</div></div>`;
    return `<div class="row"><div class="rf-seg" role="radiogroup" aria-label="看哪一边"><button type="button" role="radio" data-view="host" class="${viewer === "host" ? "on" : ""}" aria-checked="${viewer === "host"}">主持人看到的</button><button type="button" role="radio" data-view="part" class="${viewer === "part" ? "on" : ""}" aria-checked="${viewer === "part"}">受访者看到的</button></div></div>
      <div style="margin-top:10px">${viewer === "host" ? host : part}${nav}</div>`;
  }
  const rightHead = () => `<div class="rf-hd"><div class="rf-seg" role="tablist">${[["outline", "大纲"], ["text", "文字"], ["settings", "设置"], ["test", "预览测试"]].map(([k, n]) => `<button type="button" role="tab" data-seg="${k}" class="${seg === k ? "on" : ""}" aria-selected="${seg === k}">${n}</button>`).join("")}</div><span class="sp"></span><span class="sm mut" id="olstat" role="status" aria-live="polite"></span></div>`;
  function drawRight() {
    const r = $("#olright"); if (!r) return;
    const keep = $("#olout") ? $("#olout").scrollTop : 0;
    const body = seg === "outline" ? outlineHtml() : seg === "settings" ? settingsHtml() : seg === "test" ? testHtml()
      : `<textarea id="oltext" class="rf-blk" rows="16" maxlength="20000" aria-label="访谈大纲全文" style="width:100%;min-height:280px">${esc(S.text)}</textarea>`;
    r.innerHTML = rightHead() + `<div class="rf-sc" id="olout"><div class="rf-col"><div id="olnotes">${notes()}</div>${body}</div></div>`;
    $("#olout").scrollTop = keep; wireRight();
  }
  function wireRight() {
    document.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => { seg = b.dataset.seg; sessionStorage.setItem("ol_seg", seg); drawRight(); }));
    document.querySelectorAll("[data-fixnote]").forEach((b) => (b.onclick = () => send(fixMessage())));
    if (seg === "text") { const t = $("#oltext"); if (t) t.oninput = () => { S.text = t.value; save(t.value); }; return; }
    if (seg === "settings") {
      const apply = (keys, label, v) => { S.text = olSetHeader(S.text, keys, label, v); save(S.text); };
      const l = $("#olset-lang"), m = $("#olset-min"), tp = $("#olset-topic");
      l.onchange = () => apply(["语言", "language"], "语言", l.value);
      m.onchange = () => apply(["时长", "minutes"], "时长", Math.min(180, Math.max(10, +m.value || 45)));
      tp.onchange = () => apply(["受访者话题", "topic_for_participant"], "受访者话题", tp.value.trim());
      return;
    }
    if (seg === "test") {
      document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => { viewer = b.dataset.view; drawRight(); }));
      document.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => { step = b.dataset.step === "reset" ? 0 : Math.max(0, step + +b.dataset.step); drawRight(); }));
      return;
    }
    const keyOf = (s) => s.split(":");
    document.querySelectorAll("[data-ed]").forEach((b) => (b.onclick = () => { editing = b.dataset.ed; drawRight(); const i = document.querySelector(".olq"); if (i) { i.focus(); i.select(); } }));
    document.querySelectorAll("[data-rn]").forEach((b) => (b.onclick = () => { editing = b.dataset.rn; drawRight(); const i = document.querySelector(".olt"); if (i) { i.focus(); i.select(); } }));
    document.querySelectorAll("[data-no]").forEach((b) => (b.onclick = () => { editing = ""; drawRight(); }));
    document.querySelectorAll("[data-ok]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.ok), v = document.querySelector(".olq").value; edit({ op: "set", kind, ref: +ref, index: +idx, value: v }); }));
    document.querySelectorAll(".olq").forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); (document.querySelector("[data-ok]") || document.querySelector("[data-addok]")).click(); } else if (e.key === "Escape") { editing = ""; drawRight(); } }));
    document.querySelectorAll("[data-tok]").forEach((b) => (b.onclick = () => { const [, kind, ref] = keyOf(b.dataset.tok), v = document.querySelector(".olt").value; edit({ op: "rename", kind, ref: +ref, value: v }); }));
    document.querySelectorAll(".olt").forEach((i) => (i.onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); document.querySelector("[data-tok]").click(); } else if (e.key === "Escape") { editing = ""; drawRight(); } }));
    document.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { const [kind, ref, idx] = keyOf(b.dataset.rm); edit({ op: "delete", kind, ref: +ref, index: +idx }); }));
    document.querySelectorAll("[data-dp]").forEach((b) => (b.onclick = () => { if (!confirm("删除这一段和它下面的所有问题？")) return; const [kind, ref] = keyOf(b.dataset.dp); edit({ op: "del_part", kind, ref: +ref }); }));
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => { editing = "add:" + b.dataset.add; drawRight(); const i = document.querySelector(".olq"); if (i) i.focus(); }));
    document.querySelectorAll("[data-addok]").forEach((b) => (b.onclick = () => { const [kind, ref] = keyOf(b.dataset.addok), v = document.querySelector(".olq").value.trim(); if (!v) return toast("问题不能是空的", true); edit({ op: "add", kind, ref: +ref, value: v }); }));
    const ap = $("#oladdp"); if (ap) ap.onclick = () => { const t = $("#olnewt").value.trim(), q = $("#olnewq").value.trim(); if (!t || !q) return toast("话题名字和第一个问题都要写", true); edit({ op: "add_part", value: `${t.replace(/\|/g, " ")}|${q}` }); };
  }

  /* ---------- the whole page ---------- */
  shell(`<div class="rf" style="padding:0"><div class="rf-ws" id="olws"><div class="rf-pane"><div class="rf-hd"><b>和 AI 一起写大纲</b>${S.model ? `<span class="ol-mdl" title="回答问题的模型">${esc(S.model)}</span>` : ""}<span class="sp"></span><label class="chk"><input type="checkbox" id="ollib" ${useLib ? "checked" : ""}>参考研究库</label></div>
      <div class="ol-ctx" id="olctx"></div><div class="rf-sc" id="olchat"></div>
      <div class="rf-inp"><div class="sm mut" style="margin-bottom:6px">${esc(notice())}</div><div class="rf-box"><textarea id="olmsg" rows="2" maxlength="1000" placeholder="说说你想怎么改，或者直接问我问题……" aria-label="对话输入"></textarea><button class="p rf-send" type="button" id="olsend" aria-label="发送">↑</button></div></div></div>
      <div class="rf-split" id="olsplit" role="separator" aria-orientation="vertical" aria-label="拖动调整对话和大纲的宽度（双击恢复）" tabindex="0"><i></i></div><div class="rf-pane" id="olright"></div></div></div>`, { active: "outline", p });
  document.body.classList.add("rfmode", "pgw");
  const ws = $("#olws"), fit = () => { if (ws && ws.offsetParent) ws.style.height = Math.max(380, innerHeight - ws.getBoundingClientRect().top - 18) + "px"; };
  fit(); addEventListener("resize", fit);
  drawChat(); drawRight();
  const m = $("#olmsg");
  $("#olsend").onclick = () => send(m.value);
  m.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(m.value); } };
  $("#ollib").onchange = (e) => { useLib = e.target.checked; sessionStorage.setItem("ol_lib", useLib ? "1" : "0"); const x = $("#olctx"); if (x) x.innerHTML = ctxHtml(); };
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
