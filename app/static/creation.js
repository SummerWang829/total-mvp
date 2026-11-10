/* 创建访谈：阶段式对话（问卷页和大纲页左边是同一个，同一份对话）。
   AI 一个阶段一个阶段地带着走（目标 → 主持与招募 → 受访者 → 甄别 → 同意 → 形式 → 大纲 → 关注点 → 邀请信 → 试访 → 发布）；每条回复 = 一句确认 + 真正改了什么的清单 + 建议理由 + 需要你确认的事（带选项）。
   阶段进度是页面里带 data-subrow 的一个元素，由外壳搬进左侧第二层菜单。接口见 app/routers/creation.py。依赖 index.html 里的 api / esc / toast / guard / $ / store。 */
const CreationChat = (() => {
  let p = null, root = null, S = null, busy = false, pending = "", onChange = () => {}, files = [], reading = false, expandedPrev = false;
  const MAX_FILES = 3, MAX_MATERIAL = 30000;
  const path = (x = "") => `/projects/${p.id}/creation${x}`;
  const lastAi = () => [...(S ? S.messages : [])].reverse().find((m) => m.role === "ai");
  const stepsHtml = () => S.progress.map((s, i) => `<button type="button" role="tab" class="cc-st ${s.state}" data-st="${s.stage}" data-n="${s.state === "done" ? "✓" : i + 1}" aria-selected="${s.state === "current"}">${esc(s.label)}</button>`).join("");
  const aiHtml = (m, live) => {
    let h = `<div class="cc-tx">${esc(m.text)}</div>`;
    if ((m.actions || []).length) h += `<ul class="cc-acts">${m.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`;
    if (m.advice) h += `<div class="cc-adv"><b>为什么这样建议</b>${esc(m.advice)}</div>`;
    (m.needs || []).forEach((n) => {
      h += `<div class="cc-need"><b>需要你确认</b><div>${esc(n.question)}</div>${live && n.options.length ? `<div class="cc-opts">${n.options.map((o) => `<button type="button" data-opt="${esc(o)}">${esc(o)}</button>`).join("")}</div>` : ""}</div>`;
    });
    if (m.can_undo && live) h += `<div class="cc-meta"><button type="button" class="s" data-undo="${m.id}">撤销这次修改</button></div>`;
    if (m.undone) h += `<div class="cc-meta mut">这次修改已撤销</div>`;
    return h;
  };
  const filesHtml = () => files.map((f, i) => `<span class="cc-fl" title="${esc(f.name)}"><span>${esc(f.name)}${f.truncated ? "（只读了前面一部分）" : ""}</span><button type="button" data-fx="${i}" aria-label="去掉这个文件">×</button></span>`).join("") + (reading ? `<span class="cc-fl mut">正在读文件…</span>` : "");
  const html = () => {
    const last = lastAi(), stages = S.progress, i = stages.findIndex((s) => s.state === "current"), nx = stages[i + 1];
    const msgs = S.messages.map((m) => (m.role === "user" ? `<div class="cc-me">${esc(m.text)}</div>` : `<div class="cc-ai" data-selk="m:${m.id}">${aiHtml(m, m === last && !busy)}</div>`)).join("");
    const sug = !busy && last && (last.suggestions || []).length ? `<div class="cc-sl">建议</div>${last.suggestions.map((s) => `<button type="button" class="cc-sb" data-sug="${esc(s)}">${esc(s)}</button>`).join("")}` : "";
    return `<div class="cc-hd"><b>${esc(S.label)}</b><span class="mut">阶段 ${i + 1} / ${stages.length} · ${esc(S.model)}</span></div>
      <div class="cc-sc" id="ccscroll">${msgs}${busy ? `<div class="cc-me">${esc(pending)}</div><div class="cc-ai mut">正在思考…</div>` : ""}</div>
      <div class="cc-sug" id="ccsug">${sug}</div>
      ${nx ? `<button type="button" class="cc-next${last && last.ready ? " ready" : ""}" id="ccnext">进入下一阶段：${esc(nx.label)} →</button>` : ""}
      <div class="cc-files" id="ccfiles">${filesHtml()}</div>
      <div class="cc-box"><textarea id="ccmsg" rows="2" maxlength="1000" placeholder="说一句话，或点上面的建议……" aria-label="对话输入"></textarea>
        <button class="cc-att" type="button" id="ccatt" aria-label="添加附件（PDF、Word、文字文件）" title="添加附件"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.4 11.1l-9.2 9.2a5 5 0 0 1-7.1-7.1l9.2-9.2a3.3 3.3 0 0 1 4.7 4.7l-9.2 9.2a1.7 1.7 0 0 1-2.4-2.4l8.5-8.5"/></svg></button>
        <input type="file" id="ccfile" hidden accept=".pdf,.docx,.txt,.md,.csv,.png,.jpg,.jpeg,.webp,.mp4,.mov">
        <button class="p cc-send" type="button" id="ccsend" aria-label="发送">↑</button></div>`;
  };
  function draw(keepText) {
    if (!root) return;
    const old = keepText && $("#ccmsg") ? $("#ccmsg").value : "";
    root.innerHTML = html();
    const sc = $("#ccscroll"); sc.scrollTop = sc.scrollHeight;
    const m = $("#ccmsg"); m.value = old;
    $("#ccsend").onclick = () => say(m.value);
    m.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); say(m.value); } };
    $("#ccatt").onclick = () => $("#ccfile").click();
    $("#ccfile").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) addFile(f); };
    root.querySelectorAll("[data-sug]").forEach((b) => (b.onclick = () => say(b.dataset.sug)));
    root.querySelectorAll("[data-opt]").forEach((b) => (b.onclick = () => say(b.dataset.opt)));
    root.querySelectorAll("[data-undo]").forEach((b) => (b.onclick = () => call(`/undo/${b.dataset.undo}`)));
    root.querySelectorAll("[data-fx]").forEach((b) => (b.onclick = () => { files.splice(+b.dataset.fx, 1); draw(true); }));
    const nx = $("#ccnext"); if (nx) nx.onclick = () => call("/advance");
    const st = $("#ccsteps");   // 页面一开始就放好的空容器（外壳在页面画出来时把它搬进第二层菜单），这里只往里填
    if (st) { st.innerHTML = stepsHtml(); st.querySelectorAll("[data-st]").forEach((b) => (b.onclick = () => b.dataset.st !== S.stage && call("/stage", { stage: b.dataset.st }))); }
  }
  async function call(sub, body) {
    if (busy) return false;
    busy = true; pending = body && body.message ? body.message : ""; draw(true);
    let ok = true;
    try {
      const before = S.messages.length, r = await api(path(sub), { method: "POST", body: body || {} });
      S = r; if (r.messages.length !== before || sub.startsWith("/undo")) onChange(r);
    } catch (e) { ok = false; toast(e.message, true); }
    busy = false; pending = ""; draw(true);
    return ok;
  }
  async function say(text) {
    text = (text || "").trim(); if (!text || busy) return false;
    const material = files.map((f) => `【文件：${f.name}】\n${f.text}`).join("\n\n").slice(0, MAX_MATERIAL);
    const box = $("#ccmsg"); if (box) box.value = "";
    const ok = await call("/message", { message: text, material });
    if (ok) { files = []; draw(); } else if ($("#ccmsg")) $("#ccmsg").value = text;
    return ok;
  }
  async function addFile(file) {
    if (files.length >= MAX_FILES) return toast(`一次最多带 ${MAX_FILES} 个文件`, true);
    reading = true; draw(true);
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await fetch("/api/attachments/extract", { method: "POST", headers: { Authorization: "Bearer " + store.get("tt_token") }, body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "这个文件读不了");
      files.push({ name: file.name, text: d.text, chars: d.chars, truncated: !!d.truncated });
    } catch (e) { toast(e.message, true); }
    reading = false; draw(true);
  }
  async function mount(el, project, opts = {}) {
    p = project; root = el; onChange = opts.onChange || (() => {}); S = null; busy = true; pending = ""; files = [];
    root.innerHTML = `<div class="cc-sc"><div class="cc-ai mut">正在打开……</div></div>`;
    try { S = await api(path()); } catch (e) { root.innerHTML = `<div class="cc-sc"><div class="cc-ai">打不开：${esc(e.message)}</div></div>`; busy = false; return; }
    busy = false; draw();
    const ai = lastAi(); if (ai && (ai.actions || []).length) onChange(S);
  }
  return { mount, say, get stage() { return S ? S.stage : ""; } };
})();
