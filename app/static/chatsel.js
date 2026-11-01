/* 共用组件：在 AI 回复（或右侧内容）里拖选一段文字 → 弹出“针对这段修改 / 加入修改清单” → 加入后原文位置自动标上 1、2、3，
   输入框上方出现“待一起修改”清单，点“一起修改 N 处”一次发给 AI。各页面只需要传“怎么发送”的回调。

   用法（招募问卷对话 app/static/rform.js 是范例）：
     const sel = ChatSel.attach({
       roots: [chatEl, rightEl],     // 可以选字的容器；其中带 data-selk="唯一键" 的元素才可选，键在页面重画后要保持一致
       bar: listEl,                  // 放“待一起修改”清单的空容器（通常在输入框上方）
       label: (key) => "第 3 题",     // 可选：由键得到位置说明，显示在清单里
       ask: async ({ quote, text }) => "回答文字",       // “只问不改”：只回答，不修改任何内容
       send: async ({ items, overall, text }) => true,   // “一起修改”：text 是已拼好的整段要求；返回 false 或抛错 = 保留清单
     });
     sel.refresh();   // 页面重画后重新标号（容器的子元素被替换时会自动调用，一般不用手动）
     sel.destroy();   // 页面重画整个工作台前调用
   文字都由页面用 esc() 转义后放进 data-selk 元素；本组件只读文字、不插入任何来自 AI 的 HTML。 */
const ChatSel = (() => {
  const SKIP = "button,select,textarea,input,.cs-no,[data-nosel]";
  const MAX_ITEMS = 8, QUOTE_MAX = 140, NOTE_MAX = 100, OVERALL_MAX = 200;
  const esc2 = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const short = (s, n) => (s.length > n ? s.slice(0, n) + "…" : s);
  const phone = () => matchMedia("(max-width:720px)").matches;

  /** The text nodes of a block that count as its text (buttons, inputs and our own number badges do not). */
  function textNodes(block) {
    const out = [], w = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentElement && n.parentElement.closest(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    for (let n = w.nextNode(); n; n = w.nextNode()) out.push(n);
    return out;
  }
  const blockText = (block) => textNodes(block).map((n) => n.data).join("");
  /** Character offset of (node, off) inside the block's own text, or -1 when the point is outside it. */
  function offsetIn(block, node, off) {
    let pos = 0;
    for (const n of textNodes(block)) {
      if (n === node) return pos + off;
      pos += n.data.length;
    }
    if (node === block || block.contains(node)) {   // a point on an element boundary: count the text before it
      const r = document.createRange(); r.selectNodeContents(block); r.setEnd(node, off);
      return textNodes(block).reduce((a, n) => a + (r.comparePoint(n, 0) <= 0 ? Math.min(n.data.length, n === node ? off : n.data.length) : 0), 0);
    }
    return -1;
  }

  function attach(opts) {
    const roots = (opts.roots || []).filter(Boolean);
    let items = [], seq = 0, overall = "", busy = false, cur = null, floatEl = null, dlg = null, mo = null;
    const label = (key) => (opts.label ? opts.label(key) : "") || "";

    const blockOf = (node) => { const el = node && (node.nodeType === 1 ? node : node.parentElement); return el ? el.closest("[data-selk]") : null; };
    const inRoots = (el) => el && roots.some((r) => r.contains(el));
    const findBlock = (key) => { for (const r of roots) for (const b of r.querySelectorAll("[data-selk]")) if (b.dataset.selk === key) return b; return null; };

    /* ----- the numbers in the text ----- */
    function clearMarks() {
      roots.forEach((r) => r.querySelectorAll(".cs-mk").forEach((m) => { const p = m.parentNode; while (m.firstChild) p.insertBefore(m.firstChild, m); p.removeChild(m); p.normalize(); }));
      roots.forEach((r) => r.querySelectorAll(".cs-no").forEach((n) => n.remove()));
    }
    function markRange(block, start, end, no) {   // wrap the characters [start,end) of the block's text, node by node
      let pos = 0, last = null;
      for (const n of textNodes(block)) {
        const a = Math.max(start, pos), b = Math.min(end, pos + n.data.length);
        if (a < b) {
          let t = n; if (b < pos + n.data.length) t.splitText(b - pos);
          if (a > pos) t = t.splitText(a - pos);
          const mk = document.createElement("mark"); mk.className = "cs-mk"; mk.dataset.cs = String(no);
          t.parentNode.insertBefore(mk, t); mk.appendChild(t); last = mk;
        }
        pos += n.data.length;
        if (pos >= end) break;
      }
      if (last) { const s = document.createElement("sup"); s.className = "cs-no"; s.textContent = String(no); s.setAttribute("aria-label", `第 ${no} 处`); last.after(s); }
    }
    function refresh() {
      if (mo) mo.disconnect();
      clearMarks();
      items.forEach((it, i) => {
        let b = findBlock(it.key), at = b ? blockText(b).indexOf(it.quote) : -1;
        if (at < 0) {   // the block moved or changed: look for the same words anywhere
          b = null;
          for (const r of roots) for (const x of r.querySelectorAll("[data-selk]")) { if (!b && blockText(x).includes(it.quote)) { b = x; at = blockText(x).indexOf(it.quote); } }
        }
        if (b && at >= 0) markRange(b, at, at + it.quote.length, i + 1);
      });
      drawList(); observe();
    }
    function observe() { if (mo) roots.forEach((r) => mo.observe(r, { childList: true })); }
    mo = new MutationObserver(() => refresh());

    /* ----- the list above the input ----- */
    function drawList() {
      const box = opts.bar; if (!box) return;
      if (!items.length) { box.innerHTML = ""; box.hidden = true; return; }
      box.hidden = false;
      box.innerHTML = `<div class="cs-list"><div class="cs-lh"><b>待一起修改（${items.length} 处）</b><span class="sp"></span><button class="s" type="button" data-cs-clear ${busy ? "disabled" : ""}>清空</button></div>
        <ul>${items.map((it, i) => `<li><span class="cs-n">${i + 1}</span><span class="cs-q">“${esc2(short(it.quote, 40))}”${label(it.key) ? `<i>${esc2(label(it.key))}</i>` : ""}${it.note ? `<em>${esc2(short(it.note, 50))}</em>` : ""}</span><button class="s" type="button" data-cs-del="${i}" aria-label="删除第 ${i + 1} 处" ${busy ? "disabled" : ""}>×</button></li>`).join("")}</ul>
        <div class="cs-ov"><input id="cs-overall" maxlength="${OVERALL_MAX}" value="${esc2(overall)}" placeholder="补充整体要求（选填）" aria-label="补充整体要求"><button class="p s" type="button" data-cs-go ${busy ? "disabled" : ""}>${busy ? "正在修改…" : `一起修改 ${items.length} 处`}</button></div></div>`;
      box.querySelector("[data-cs-clear]").onclick = () => { items = []; overall = ""; refresh(); };
      box.querySelectorAll("[data-cs-del]").forEach((b) => (b.onclick = () => { items.splice(+b.dataset.csDel, 1); refresh(); }));
      const ov = box.querySelector("#cs-overall"); ov.oninput = () => { overall = ov.value; };
      box.querySelector("[data-cs-go]").onclick = go;
    }
    const compose = () => `请一次性修改以下 ${items.length} 处，其余保持不变：\n` + items.map((it, i) => `${i + 1}. “${short(it.quote, QUOTE_MAX)}”${label(it.key) ? `（${label(it.key)}）` : ""}${it.note ? `——要求：${short(it.note, NOTE_MAX)}` : ""}`).join("\n") + (overall.trim() ? `\n整体要求：${overall.trim()}` : "");
    async function go() {
      if (busy || !items.length) return;
      busy = true; drawList();
      let ok = false;
      try { ok = (await opts.send({ items: items.map((x) => ({ ...x })), overall: overall.trim(), text: compose() })) !== false; } catch { ok = false; }
      busy = false;
      if (ok) { items = []; overall = ""; }
      refresh();
    }
    function add(sel, note) {
      if (!sel) return false;
      if (items.length >= MAX_ITEMS) { if (window.toast) toast(`一次最多修改 ${MAX_ITEMS} 处，请先点“一起修改”`, true); return false; }
      if (items.some((x) => x.key === sel.key && x.quote === sel.quote)) { if (window.toast) toast("这一处已在清单里"); return false; }
      items.push({ key: sel.key, quote: sel.quote, note: (note || "").trim().slice(0, NOTE_MAX) });
      refresh(); return true;
    }

    /* ----- reading the selection ----- */
    function readSelection() {
      const s = getSelection(); if (!s || s.isCollapsed || !s.rangeCount) return null;
      const r = s.getRangeAt(0), b = blockOf(r.startContainer);
      if (!b || b !== blockOf(r.endContainer) || !inRoots(b)) return null;
      const a = offsetIn(b, r.startContainer, r.startOffset), z = offsetIn(b, r.endContainer, r.endOffset);
      if (a < 0 || z < 0 || z <= a) return null;
      const quote = blockText(b).slice(a, z).replace(/\s+/g, " ").trim();
      return quote.length >= 2 ? { key: b.dataset.selk, quote: quote.slice(0, 300), rect: r.getBoundingClientRect() } : null;
    }
    function hideFloat() { if (floatEl) { floatEl.remove(); floatEl = null; } }
    function showFloat(sel) {
      hideFloat();
      const el = document.createElement("div"); el.className = "cs-float" + (phone() ? " cs-bottom" : ""); el.setAttribute("role", "toolbar"); el.setAttribute("aria-label", "对选中文字的操作");
      el.innerHTML = `<button type="button" class="s" data-cs-edit>针对这段修改</button><button type="button" class="s" data-cs-addq>加入修改清单</button>`;
      el.onmousedown = (e) => e.preventDefault();   // keep the selection while clicking
      el.querySelector("[data-cs-edit]").onclick = () => { const c = sel; hideFloat(); getSelection().removeAllRanges(); openDialog(c); };
      el.querySelector("[data-cs-addq]").onclick = () => { const c = sel; hideFloat(); getSelection().removeAllRanges(); add(c, ""); };
      document.body.appendChild(el);
      if (!phone()) {
        const w = el.offsetWidth, h = el.offsetHeight, x = Math.min(Math.max(8, sel.rect.left + sel.rect.width / 2 - w / 2), innerWidth - w - 8);
        const y = sel.rect.top - h - 8 >= 8 ? sel.rect.top - h - 8 : Math.min(sel.rect.bottom + 8, innerHeight - h - 8);
        el.style.left = x + "px"; el.style.top = y + "px";
      }
      floatEl = el;
    }
    let tick = 0;
    const onSel = () => { clearTimeout(tick); tick = setTimeout(() => { if (dlg) return; const s = readSelection(); cur = s; s ? showFloat(s) : hideFloat(); }, 120); };
    const onKey = (e) => { if (e.key === "Escape") { if (dlg) closeDialog(); else hideFloat(); } };
    const lk = "chatsel:" + ((opts.roots && opts.roots[0] && opts.roots[0].id) || "x");   // one pair of listeners per selectable area, removed when the page changes
    if (window.pageOn) { window.pageOn(lk + ":sel", document, "selectionchange", onSel); window.pageOn(lk + ":key", document, "keydown", onKey); }
    else { document.addEventListener("selectionchange", onSel); document.addEventListener("keydown", onKey); }

    /* ----- the small window for one passage ----- */
    function closeDialog() { if (dlg) { dlg.remove(); dlg = null; } }
    function openDialog(sel) {
      closeDialog();
      const talk = [];
      const el = document.createElement("div"); el.className = "cs-dlg"; el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); el.setAttribute("aria-label", "针对这段修改");
      const draw = () => {
        el.innerHTML = `<div class="cs-card"><div class="cs-dh"><b>针对这段修改</b><span class="sp"></span><button class="s" type="button" data-cs-x aria-label="关闭">×</button></div>
          <blockquote class="cs-quote">${esc2(short(sel.quote, 300))}</blockquote>${label(sel.key) ? `<div class="sm mut">${esc2(label(sel.key))}</div>` : ""}
          <div class="cs-talk" id="cs-talk">${talk.map((t) => `<div class="cs-t ${t.me ? "me" : ""}${t.err ? " err" : ""}">${esc2(t.text).replace(/\n/g, "<br>")}</div>`).join("")}</div>
          <textarea id="cs-in" rows="3" maxlength="600" placeholder="写下对这一处的修改要求或问题" aria-label="针对这段的要求或问题"></textarea>
          <div class="cs-db"><button class="p" type="button" data-cs-add>加入修改清单（第 ${items.length + 1} 处）</button><button class="s" type="button" data-cs-ask>只问不改</button></div></div>`;
        const t = el.querySelector("#cs-talk"); t.scrollTop = t.scrollHeight;
        const inp = el.querySelector("#cs-in");
        el.querySelector("[data-cs-x]").onclick = closeDialog;
        el.onmousedown = (e) => { if (e.target === el) closeDialog(); };
        el.querySelector("[data-cs-add]").onclick = () => {
          const lastMe = [...talk].reverse().find((x) => x.me);
          if (add(sel, inp.value.trim() || (lastMe ? lastMe.text : ""))) closeDialog();
        };
        el.querySelector("[data-cs-ask]").onclick = async () => {
          const text = inp.value.trim(); if (!text) return inp.focus();
          talk.push({ me: true, text }); talk.push({ text: "正在回答…", wait: true }); draw();
          let reply = "";
          try { reply = await opts.ask({ quote: sel.quote, key: sel.key, text }); } catch (e) { talk.pop(); talk.push({ text: (e && e.message) || "本次未能完成回复，可重新尝试。", err: true }); return draw(); }
          talk.pop(); talk.push({ text: reply || "（没有回复）" }); draw();
        };
        inp.focus();
      };
      draw(); document.body.appendChild(el); dlg = el;
    }

    observe(); drawList();
    return {
      refresh, add: (key, quote, note) => add({ key, quote }, note), count: () => items.length,
      destroy() { document.removeEventListener("selectionchange", onSel); document.removeEventListener("keydown", onKey); if (mo) mo.disconnect(); clearTimeout(tick); hideFloat(); closeDialog(); items = []; clearMarks(); if (opts.bar) { opts.bar.innerHTML = ""; opts.bar.hidden = true; } },
    };
  }
  return { attach };
})();
