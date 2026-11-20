/* 统一输入组件 TInput（修正方案第 1 轮第 2 条，效果图 qa_shots/fix1_batch4_mockup.html 第 ③④⑥ 屏）。
   不重写页面：页面里所有文字输入框都会被自动认出来，原来的 value、事件、提交都不变。
   - chat（对话框，页面写 data-ti="chat"）：被包进一个框，带附件、贴链接、麦克风、圆形发送键；Enter 发送，Shift+Enter 换行，正在用输入法拼字时 Enter 不发送。
     附件和链接显示成卡片，交给 AI 的只是这一次（页面调用 TInput.takeRefs(el) 取走）；链接可以“存为项目资料”。
     data-ti-send="选择器" 指定页面原来的发送按钮（我们的发送键点它）；没有的话提交所在的表单。data-ti-attach="no" 时只有麦克风（这个对话不交给 AI 读资料）。
   - field（多行框，所有其他 textarea）：获得焦点时右下角出现附件、链接、麦克风，读到的文字插到光标处。
   - line（单行框）：获得焦点时右侧出现麦克风；点“展开”也能加附件和链接。
   - plain（邮箱、数字、网址、日期、密码、搜索、只读……）：只统一外观，不加附件和语音。
   语音：说话时每 2–3 秒切一段送服务器识别（做法 A），或用实时识别边说边出字（做法 B）；两种都能试，研究团队在录音条上切换（只存在本浏览器）。
   识别出的字插到光标处，不自动发送；录音不保存。受访者页面要先设置 window.TI_PUBLIC = {kind: "join"|"screen", token, lang}；没有附件和链接。 */
(() => {
  const IC = {
    clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    stop: '<rect width="14" height="14" x="5" y="5" rx="2"/>',
    send: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    sheet: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M8 13h8"/><path d="M8 17h8"/><path d="M12 11v8"/>',
    more: '<path d="m6 9 6 6 6-6"/>',
  };
  const svg = (k) => `<svg class="ti-i" viewBox="0 0 24 24" aria-hidden="true">${IC[k]}</svg>`;
  const E = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const PLAIN_TYPES = ["email", "number", "url", "date", "datetime-local", "time", "password", "search", "tel", "month", "week"];
  const SKIP_TYPES = ["checkbox", "radio", "file", "hidden", "range", "color", "submit", "button", "reset", "image"];
  const MAX_FILES = 5, MAX_FILE = 10 * 1024 * 1024, MAX_AV = 12 * 1024 * 1024;
  const AV = /\.(mp3|m4a|wav|aac|flac|ogg|oga|webm|mp4|mov)$/i;
  const LANGS = [["zh", "中文"], ["en", "English"], ["ms", "马来语"], ["ta", "泰米尔语"]];
  const NOTE = { zh: "录音只用来转成文字，不会保存。", en: "Your voice is only turned into text; the recording is not kept.", ms: "Suara anda hanya ditukar menjadi teks; rakaman tidak disimpan.", ta: "உங்கள் குரல் உரையாக மட்டுமே மாற்றப்படும்; பதிவு சேமிக்கப்படாது." };
  const SEG_MS = 2500;
  const state = new Map();   // chat key -> {refs: [...]}: survives a page that redraws its chat box

  const pub = () => window.TI_PUBLIC || null;
  const me = () => (typeof ME !== "undefined" ? ME : null);
  const role = () => {   // the account role (the room page has no ME: it is read from the sign-in token, only to decide which buttons to show; the server checks again)
    if (me() && me().role) return me().role;
    try { const t = (window.store && store.get("tt_token")) || localStorage.getItem("tt_token") || ""; return JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).role || ""; } catch { return ""; }
  };
  const canVoice = () => (pub() ? ["zh", "en"].includes(pub().lang || "zh") : ["admin", "researcher", "support", "recruiter"].includes(role()));
  const canAttach = (el) => !pub() && ["admin", "researcher", "support"].includes(role()) && el.dataset.tiAttach !== "no";
  const token = () => { try { return (window.store && store.get("tt_token")) || localStorage.getItem("tt_token") || ""; } catch { return ""; } };
  const auth = () => (token() ? { Authorization: "Bearer " + token() } : {});
  const projectId = () => (location.hash.match(/^#\/p\/([^/?]+)/) || [])[1] || null;
  const say = (m, bad) => { if (window.toast) toast(m, bad); };

  /** Which form an element takes. */
  function kindOf(el) {
    if (el.dataset.ti) return el.dataset.ti;
    if (el.tagName === "TEXTAREA") return el.readOnly ? "plain" : "field";
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (PLAIN_TYPES.includes(t) || el.readOnly) return "plain";
    const hint = `${el.placeholder || ""} ${el.getAttribute("aria-label") || ""}`;
    if (/搜索|筛选|查找|search|filter/i.test(hint) || el.closest("thead,[role=search],.flt,.tf")) return "plain";
    return "line";
  }
  const isText = (el) => (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && !SKIP_TYPES.includes((el.getAttribute("type") || "text").toLowerCase())));

  // ---- the look: every text input gets the same frame; a chat box is wrapped ------------------------------------------------------------------------
  function setup(el) {
    if (el.dataset.tiOn || !isText(el)) return;
    const k = kindOf(el);
    el.dataset.tiOn = k;
    el.classList.add("ti-core", "ti-" + k);
    if (k === "chat") wrapChat(el);
  }
  function scan(root) {
    (root.querySelectorAll ? root.querySelectorAll("input,textarea") : []).forEach(setup); if (root.matches && root.matches("input,textarea")) setup(root);
    (root.querySelectorAll ? root.querySelectorAll("[data-ti-mic]") : []).forEach((b) => { b.hidden = !canVoice(); });   // a page's own mic button (room composers): only who may speak sees it
  }

  function chatKey(el) { return el.dataset.tiKey || el.id || "chat"; }
  function wrapChat(el) {
    const box = document.createElement("div");
    box.className = "ti ti-box";
    el.parentNode.insertBefore(box, el);
    const chips = document.createElement("div"); chips.className = "ti-chips";
    const bar = document.createElement("div"); bar.className = "ti-bar";
    const err = document.createElement("div"); err.className = "ti-err"; err.setAttribute("role", "alert");
    box.append(chips, el, bar);
    box.after(err);
    const orig = el.dataset.tiSend ? document.querySelector(el.dataset.tiSend) : null;
    if (orig) orig.classList.add("ti-orig");
    const attach = canAttach(el);
    bar.innerHTML = `${attach ? `<button type="button" class="ti-b" data-tia="file" aria-label="添加附件" title="添加附件（PDF、Word、Excel、图片、录音视频）">${svg("clip")}</button><button type="button" class="ti-b" data-tia="link" aria-label="贴链接" title="贴链接">${svg("link")}</button>` : ""}`
      + `${canVoice() ? `<button type="button" class="ti-b" data-tia="mic" aria-label="语音输入" title="语音输入">${svg("mic")}</button>` : ""}<span class="ti-rec" hidden></span><span class="ti-sp"></span>`
      + `<span class="ti-hint">Enter 发送 · Shift+Enter 换行</span><button type="button" class="ti-send" data-tia="send" aria-label="发送">${svg("send")}</button>`;
    const sync = () => bar.querySelector(".ti-send").classList.toggle("off", !el.value.trim() && !(state.get(chatKey(el))?.refs || []).length);
    el.addEventListener("input", sync); sync();
    el.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.keyCode === 229) return;
      e.preventDefault(); e.stopImmediatePropagation(); send(el);
    }, true);
    box.addEventListener("mousedown", (e) => { if (e.target.closest(".ti-b,.ti-send")) e.preventDefault(); });
    box.addEventListener("click", (e) => onAction(e, el, box));
    drawChips(el, box);
  }
  function send(el) {
    if (!el.value.trim() && !(state.get(chatKey(el))?.refs || []).length) return;
    const orig = el.dataset.tiSend ? document.querySelector(el.dataset.tiSend) : null;
    if (orig) orig.click();
    else if (el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
  }

  // ---- attachments and links ---------------------------------------------------------------------------------------------------------------------------
  function drawChips(el, box) {
    const st = state.get(chatKey(el)) || { refs: [] };
    const chips = box.querySelector(".ti-chips"); if (!chips) return;
    chips.innerHTML = st.refs.map((r, i) => r.kind === "pending" ? `<div class="ti-att"><span class="ic">${svg("file")}</span><span class="t"><b>${E(r.title)}</b><small>正在读取……</small></span></div>`
      : r.ui === "link" ? `<div class="ti-link"><div class="row1">${svg("globe")}<b>${E(r.title)}</b><small>· ${E(r.host)} · 已读取 ${r.chars} 字</small><span class="ti-sp"></span><button type="button" class="ti-b" data-tirm="${i}" aria-label="删除这条链接">${svg("x")}</button></div>
          <div class="row2"><label><input type="radio" name="tikeep${i}" data-tikeep="${i}" value="once"${r.kind !== "repo" ? " checked" : ""}> 只用于这次</label><label><input type="radio" name="tikeep${i}" data-tikeep="${i}" value="save"${r.kind === "repo" ? " checked" : ""}> 存为项目资料</label></div></div>`
      : `<div class="ti-att"><span class="ic">${svg(r.icon || "file")}</span><span class="t"><b>${E(r.title)}</b><small>${E(r.note)}</small></span><button type="button" class="ti-b" data-tirm="${i}" aria-label="删除这个附件">${svg("x")}</button></div>`).join("")
      + (st.linkOpen ? `<div class="ti-linkin"><input type="url" class="ti-core ti-plain" data-ti="plain" data-ti-on="plain" placeholder="粘贴网址 https://…" aria-label="网址"><button type="button" class="ti-mini" data-tia="link-read">读取</button><button type="button" class="ti-mini" data-tia="link-cancel">取消</button></div>` : "");
    chips.querySelectorAll("[data-tikeep]").forEach((r) => (r.onchange = () => keepLink(el, box, +r.dataset.tikeep, r.value)));
    const lin = chips.querySelector(".ti-linkin input"); if (lin && st.linkOpen === "new") { st.linkOpen = true; lin.focus(); lin.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); readLink(el, box, lin.value); } }; }
    const s = box.querySelector(".ti-send"); if (s) s.classList.toggle("off", !el.value.trim() && !st.refs.length);
  }
  function errOf(el) { const box = el.closest(".ti-box"); return box ? box.nextElementSibling : null; }
  function showErr(el, m) { const e = errOf(el); if (e && e.classList.contains("ti-err")) { e.textContent = m; clearTimeout(e._t); e._t = setTimeout(() => (e.textContent = ""), 9000); } else say(m, true); }
  function stOf(el) { const k = chatKey(el); if (!state.has(k)) state.set(k, { refs: [] }); return state.get(k); }

  async function readFile(file) {
    const av = AV.test(file.name);
    if (file.size > (av ? MAX_AV : MAX_FILE)) throw new Error(`“${file.name}”超过 ${av ? 12 : 10}MB，没有加上`);
    const fd = new FormData(); fd.append("file", file);
    const url = av ? `/api/attachments/transcribe?lang=${encodeURIComponent((document.documentElement.lang || "zh").startsWith("en") ? "en" : "zh")}` : "/api/attachments/extract";
    const r = await fetch(url, { method: "POST", headers: auth(), body: fd });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(`“${file.name}”没有读到：${d.detail || "读取失败"}`);
    const text = d.text || "";
    const kind = av ? "av" : d.kind;
    const note = kind === "image" ? `只读取了图中的文字 · ${text.length} 字` : kind === "excel" ? `每个表前 200 行 · ${text.length} 字` : kind === "av" ? `转写文字 · ${text.length} 字` : `已读取文字 · ${(d.chars || text.length).toLocaleString()} 字${d.truncated ? "（只读了前面一部分）" : ""}`;
    return { kind: "file", title: file.name, text, note, icon: kind === "image" ? "image" : kind === "excel" ? "sheet" : "file" };
  }
  async function addFiles(el, box, files) {
    const st = stOf(el);
    for (const f of files) {
      if (st.refs.length >= MAX_FILES) { showErr(el, `一次最多 ${MAX_FILES} 个附件，“${f.name}”没有加上`); break; }
      const tmp = { kind: "pending", title: f.name }; st.refs.push(tmp); drawChips(el, box);
      try { st.refs.splice(st.refs.indexOf(tmp), 1, await readFile(f)); } catch (e) { st.refs.splice(st.refs.indexOf(tmp), 1); showErr(el, e.message); }
      drawChips(el, box);
    }
  }
  async function readLink(el, box, url) {
    url = (url || "").trim(); if (!/^https?:\/\//i.test(url)) return showErr(el, "请贴以 http:// 或 https:// 开头的网址");
    const st = stOf(el);
    if (st.refs.length >= MAX_FILES) return showErr(el, `一次最多 ${MAX_FILES} 个附件和链接`);
    st.linkOpen = false; const tmp = { kind: "pending", title: "正在读取链接" }; st.refs.push(tmp); drawChips(el, box);
    try {
      const r = await fetch("/api/links/read", { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ url, project_id: projectId(), save: false }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "链接没读到");
      st.refs.splice(st.refs.indexOf(tmp), 1, { ui: "link", kind: "link", url, title: d.title, host: d.host, chars: d.chars, text: d.text });
    } catch (e) { st.refs.splice(st.refs.indexOf(tmp), 1); showErr(el, e.message); }
    drawChips(el, box);
  }
  async function keepLink(el, box, i, how) {
    const st = stOf(el), r = st.refs[i]; if (!r) return;
    if (how === "once") { if (r.kind === "repo") { r.kind = "link"; } return drawChips(el, box); }
    if (!projectId()) { showErr(el, "存为项目资料要在项目里操作"); return drawChips(el, box); }
    try {
      const x = await fetch("/api/links/read", { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ url: r.url, project_id: projectId(), save: true }) });
      const d = await x.json().catch(() => ({}));
      if (!x.ok) throw new Error(d.detail || "没有存上");
      Object.assign(r, { kind: "repo", repo_id: d.repo_id }); say("已存为项目资料（研究库里只存标题和网站名）");
    } catch (e) { showErr(el, e.message); }
    drawChips(el, box);
  }
  function pickFiles(cb) {
    const f = document.createElement("input"); f.type = "file"; f.multiple = true; f.hidden = true;
    f.accept = ".pdf,.docx,.txt,.md,.csv,.xlsx,.png,.jpg,.jpeg,.webp,.bmp,.mp3,.m4a,.wav,.aac,.flac,.ogg,.webm,.mp4,.mov";
    f.onchange = () => { cb([...f.files]); f.remove(); }; document.body.appendChild(f); f.click();
  }

  // ---- the floating tools of a field / line box -----------------------------------------------------------------------------------------------------------
  let float = null, cur = null;
  function floatEl() {
    if (float) return float;
    float = document.createElement("div"); float.className = "ti-float"; float.hidden = true;
    float.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
    float.addEventListener("click", (e) => cur && onAction(e, cur, null));
    document.body.appendChild(float);
    return float;
  }
  function showFloat(el) {
    const k = el.dataset.tiOn; if (k !== "field" && k !== "line") return;
    const voice = canVoice() && el.dataset.tiVoice !== "no", attach = canAttach(el) && el.dataset.tiAttach !== "no";
    if (!voice && !attach) return;
    cur = el; const f = floatEl();
    const open = k === "field" || el.dataset.tiOpen === "1";
    f.innerHTML = `${attach && open ? `<button type="button" class="ti-b" data-tia="file" aria-label="添加附件，读到的文字放进框里" title="添加附件（读到的文字放进框里）">${svg("clip")}</button><button type="button" class="ti-b" data-tia="link" aria-label="贴链接，读到的文字放进框里" title="贴链接（读到的文字放进框里）">${svg("link")}</button>` : ""}`
      + `${voice ? `<button type="button" class="ti-b" data-tia="mic" aria-label="语音输入" title="语音输入">${svg("mic")}</button>` : ""}${attach && !open ? `<button type="button" class="ti-b" data-tia="open" aria-label="展开：附件和链接" title="展开：附件和链接">${svg("more")}</button>` : ""}<span class="ti-rec" hidden></span>`;
    f.hidden = false; place();
  }
  function place() {
    if (!cur || !float || float.hidden) return;
    const r = cur.getBoundingClientRect();
    if (!r.width || r.bottom < 0 || r.top > innerHeight) { float.style.visibility = "hidden"; return; }
    float.style.visibility = "";
    const fw = float.offsetWidth, fh = float.offsetHeight;   // just below the box's right edge: it never covers what is being typed
    float.style.left = Math.max(4, Math.min(innerWidth - fw - 4, r.right - fw)) + "px";
    float.style.top = (r.bottom + fh + 6 <= innerHeight ? r.bottom + 4 : Math.max(4, r.top - fh - 4)) + "px";
  }
  function hideFloat() { if (REC.on) return; if (float) float.hidden = true; cur = null; }

  function insert(el, text) {
    if (!text) return;
    const max = el.maxLength > 0 ? el.maxLength : Infinity;
    const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? el.value.length;
    const room = max - (el.value.length - (b - a));
    if (room <= 0) return say("这个框已经写满了", true);
    const t = text.slice(0, room);
    el.setRangeText(t, a, b, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    if (t.length < text.length) say("框里放不下全部文字，只放了前面一部分");
  }

  async function onAction(e, el, box) {
    const b = e.target.closest("[data-tia],[data-tirm]"); if (!b) return;
    e.preventDefault();
    if (b.dataset.tirm !== undefined) { stOf(el).refs.splice(+b.dataset.tirm, 1); return drawChips(el, box); }
    const a = b.dataset.tia;
    if (a === "send") return send(el);
    if (a === "mic") return REC.on ? REC.stop() : REC.start(el, box ? box.querySelector(".ti-rec") : float.querySelector(".ti-rec"), box ? box.querySelector(".ti-bar") : float);
    if (a === "open") { el.dataset.tiOpen = "1"; return showFloat(el); }
    if (box) {
      if (a === "file") return pickFiles((fs) => addFiles(el, box, fs));
      if (a === "link") { stOf(el).linkOpen = "new"; return drawChips(el, box); }
      if (a === "link-cancel") { stOf(el).linkOpen = false; return drawChips(el, box); }
      if (a === "link-read") return readLink(el, box, box.querySelector(".ti-linkin input").value);
      return;
    }
    if (a === "file") return pickFiles(async (fs) => { for (const f of fs.slice(0, MAX_FILES)) { try { const r = await readFile(f); insert(el, r.text); say(`已把“${f.name}”的文字放进框里`); } catch (er) { say(er.message, true); } } });
    if (a === "link") {
      const url = prompt("粘贴网址（https://…）");
      if (!url) return;
      try {
        const r = await fetch("/api/links/read", { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ url, project_id: projectId(), save: false }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || "链接没读到");
        insert(el, `${d.title}（${d.host}）：${d.text}`);
      } catch (er) { say(er.message, true); }
    }
  }

  // ---- voice: A = a piece every 2.5 seconds to the one-sentence recogniser; B = the live recogniser -------------------------------------------------------------------
  const PCM = ["class P extends AudioWorkletProcessor{constructor(){super();this.o=[];this.acc=0;this.cnt=0;this.pos=0;this.ratio=sampleRate/16000;}",
    "process(inp){const c=inp[0]&&inp[0][0];if(!c)return true;for(let k=0;k<c.length;k++){this.acc+=c[k];this.cnt++;this.pos+=1;",
    "if(this.pos>=this.ratio){this.pos-=this.ratio;this.o.push(this.acc/this.cnt);this.acc=0;this.cnt=0;",
    "if(this.o.length>=3200){const b=new Int16Array(3200);for(let j=0;j<3200;j++){const s=Math.max(-1,Math.min(1,this.o[j]));b[j]=s<0?s*32768:s*32767;}this.o=[];this.port.postMessage(b.buffer,[b.buffer]);}}}return true;}}",
    "registerProcessor('pcm16',P);"].join("\n");
  const mode = () => { try { return localStorage.getItem("ti_voice") === "b" ? "b" : "a"; } catch { return "a"; } };
  const langNow = () => { if (pub()) return pub().lang || "zh"; try { return localStorage.getItem("ti_lang") || "zh"; } catch { return "zh"; } };
  function wav(chunks) {
    const n = chunks.reduce((a, c) => a + c.length, 0), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const w = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
    w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
    let o = 44; for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, c[i], true);
    return new Blob([buf], { type: "audio/wav" });
  }
  const loud = (c) => { let s = 0; for (let i = 0; i < c.length; i++) s += c[i] * c[i]; return 20 * Math.log10(Math.sqrt(s / c.length) / 32768 + 1e-9) > -50; };
  const REC = {
    on: false, el: null, ui: null, bar: null, ctx: null, stream: null, ws: null, pcs: [], heard: false, timer: 0, clock: 0, t0: 0, queue: Promise.resolve(), partial: "",
    async start(el, ui, bar) {
      if (!canVoice()) return;
      if (!navigator.mediaDevices || !window.AudioWorkletNode) return say("这个浏览器不能录音，请换 Chrome、Edge 或 Safari", true);
      this.el = el; this.ui = ui; this.bar = bar; this.on = true; this.pcs = []; this.heard = false; this.partial = ""; this.t0 = Date.now();
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        try { this.ctx = new AudioContext({ sampleRate: 16000 }); } catch { this.ctx = new AudioContext(); }
        await this.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([PCM], { type: "text/javascript" })));
        const node = new AudioWorkletNode(this.ctx, "pcm16");
        node.port.onmessage = (ev) => this.frame(new Int16Array(ev.data));
        this.ctx.createMediaStreamSource(this.stream).connect(node);
      } catch { this.cleanup(); return say(pub() ? "Microphone not available / 麦克风无法使用" : "麦克风无法使用：请在浏览器里允许使用麦克风", true); }
      if (mode() === "b") await this.openLive(); else this.timer = setInterval(() => this.cut(), SEG_MS);
      this.clock = setInterval(() => this.draw(), 500); this.draw();
      bar.classList.add("rec");
    },
    frame(c) {
      if (this.ws) { if (this.ws.readyState === 1) this.ws.send(c.buffer); return; }
      this.pcs.push(c); if (loud(c)) this.heard = true;
    },
    cut(last) {
      const pcs = this.pcs, heard = this.heard; this.pcs = []; this.heard = false;
      if (!pcs.length || !heard) return;
      const el = this.el, blob = wav(pcs), lang = langNow();
      this.queue = this.queue.then(async () => {
        const fd = new FormData(); fd.append("file", blob, "clip.wav");
        const p = pub();
        if (p) { fd.append("token", p.token); fd.append("kind", p.kind || "join"); fd.append("language", lang); } else fd.append("language", lang);
        try {
          const r = await fetch(p ? "/api/public/speech" : "/api/speech/transcribe", { method: "POST", headers: p ? {} : auth(), body: fd });
          const d = await r.json().catch(() => ({}));
          if (r.ok && d.text) insert(el, d.text); else if (!r.ok && r.status !== 422) say(d.detail || "没有识别出来", true);
        } catch { say("网络不稳定，这一段没有识别", true); }
        if (last) el.dispatchEvent(new Event("change", { bubbles: true }));
      });
    },
    async openLive() {
      const p = pub(), lang = langNow();
      let cfg;
      try {
        if (p) { const fd = new FormData(); fd.append("token", p.token); fd.append("kind", p.kind || "join"); fd.append("language", lang); cfg = await (await fetch("/api/public/speech/live", { method: "POST", body: fd })).json(); }
        else cfg = await (await fetch(`/api/speech/live?language=${lang}`, { headers: auth() })).json();
      } catch { cfg = null; }
      if (!cfg || !cfg.enabled) { say((cfg && cfg.reason ? cfg.reason + "，" : "") + "改用分段识别", true); this.timer = setInterval(() => this.cut(), SEG_MS); return; }
      const ws = new WebSocket(cfg.url); ws.binaryType = "arraybuffer"; this.ws = ws;
      ws.onmessage = (ev) => {
        let m; try { m = JSON.parse(ev.data); } catch { return; }
        if (m.code !== 0) { say("实时识别出错，改用分段识别", true); this.ws = null; try { ws.close(); } catch {} this.timer = setInterval(() => this.cut(), SEG_MS); return; }
        const r = m.result; if (!r || !r.voice_text_str) return;
        if (r.slice_type === 2) { this.partial = ""; insert(this.el, r.voice_text_str); } else this.partial = r.voice_text_str;
        this.draw();
      };
      ws.onclose = () => { if (this.ws === ws) this.ws = null; };
    },
    draw() {
      if (!this.ui) return;
      const s = Math.floor((Date.now() - this.t0) / 1000), staff = !pub();
      this.ui.hidden = false;
      this.ui.innerHTML = `<span class="dot" aria-hidden="true"></span><span>${pub() && (pub().lang || "zh") !== "zh" ? "Listening" : "正在听"} ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}</span>`
        + (this.partial ? `<span class="ti-ghost">${E(this.partial)}</span>` : "")
        + (staff ? `<span class="ti-langs" role="group" aria-label="识别语言">${LANGS.map(([k, t]) => `<button type="button" class="${langNow() === k ? "on" : ""}${["ms", "ta"].includes(k) ? " off" : ""}" data-til="${k}" ${["ms", "ta"].includes(k) ? 'disabled title="暂不支持"' : ""}>${t}</button>`).join("")}</span>`
          + `<span class="ti-langs" role="group" aria-label="识别方式"><button type="button" class="${mode() === "a" ? "on" : ""}" data-tim="a" title="每 2–3 秒识别一段">分段</button><button type="button" class="${mode() === "b" ? "on" : ""}" data-tim="b" title="边说边出字">实时</button></span>`
          : `<span class="ti-note">${E(NOTE[pub().lang || "zh"] || NOTE.en)}</span>`)
        + `<button type="button" class="ti-b stop" data-tia="mic" aria-label="停止">${svg("stop")}</button>`;
      this.ui.querySelectorAll("[data-til]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); try { localStorage.setItem("ti_lang", b.dataset.til); } catch {} this.restart(); }));
      this.ui.querySelectorAll("[data-tim]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); try { localStorage.setItem("ti_voice", b.dataset.tim); } catch {} this.restart(); }));
      this.ui.onmousedown = (e) => e.preventDefault();
      place();
    },
    restart() { const el = this.el, ui = this.ui, bar = this.bar; this.stop(); this.start(el, ui, bar); },
    stop() {
      if (!this.on) return;
      if (this.ws) { try { if (this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: "end" })); } catch {} const w = this.ws; setTimeout(() => { try { w.close(); } catch {} }, 1500); this.ws = null; }
      else this.cut(true);
      this.cleanup();
    },
    cleanup() {
      clearInterval(this.timer); clearInterval(this.clock); this.on = false;
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) this.ctx.close().catch(() => {});
      this.stream = this.ctx = null;
      if (this.ui) { this.ui.hidden = true; this.ui.innerHTML = ""; }
      if (this.bar) this.bar.classList.remove("rec");
      if (this.el && document.activeElement !== this.el && !this.el.closest(".ti-box")) hideFloat();
    },
  };

  // ---- wiring ---------------------------------------------------------------------------------------------------------------------------------------------------------
  function boot() {
    scan(document);
    new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && scan(n)))).observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener("click", (e) => {   // a mic button a page placed in its own tool row: voice into that box
      const b = e.target.closest("[data-ti-mic]"); if (!b) return;
      e.preventDefault();
      if (REC.on) return REC.stop();
      const el = document.querySelector(b.dataset.tiMic); if (!el || el.disabled) return;
      let ui = b.parentNode.querySelector(".ti-rec"); if (!ui) { ui = document.createElement("span"); ui.className = "ti-rec"; ui.hidden = true; b.after(ui); }
      el.focus(); REC.start(el, ui, b.parentNode);
    });
    document.addEventListener("focusin", (e) => { const el = e.target; if (el.dataset && el.dataset.tiOn && !el.closest(".ti-box")) showFloat(el); else if (!(float && float.contains(el))) hideFloat(); });
    document.addEventListener("focusout", (e) => { if (e.target === cur) setTimeout(() => { if (document.activeElement !== cur && !(float && float.contains(document.activeElement))) hideFloat(); }, 150); });
    addEventListener("scroll", place, true); addEventListener("resize", place);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

  window.TInput = {
    /** The sources added to a chat box, for the request (files and links for this time, kept project material by id); the cards are cleared. */
    takeRefs(el) {
      const k = typeof el === "string" ? el : chatKey(el), st = state.get(k); if (!st) return [];
      const out = st.refs.filter((r) => r.kind !== "pending").map((r) => (r.kind === "repo" ? { kind: "repo", repo_id: r.repo_id, title: r.title } : { kind: r.kind === "link" ? "link" : "file", title: r.title, text: r.text, host: r.host || "" }));
      st.refs = []; st.linkOpen = false;
      document.querySelectorAll(".ti-box").forEach((b) => { const t = b.querySelector("textarea.ti-chat,input.ti-chat"); if (t && chatKey(t) === k) drawChips(t, b); });
      return out;
    },
    /** Cards for the sources an answer used ([{n, title, kind, host}] from the server). */
    cites(list) {
      if (!list || !list.length) return "";
      return `<div class="ti-cites">${list.map((c) => `<span class="ti-att sm"><span class="ic">${svg(c.kind === "file" ? "file" : "globe")}</span><span class="t"><b>资料 ${c.n} · ${E(c.title)}</b><small>${c.kind === "repo" ? "项目资料" : c.kind === "link" ? "本次链接" : "本次附件"}${c.host ? " · " + E(c.host) : ""}</small></span></span>`).join("")}</div>`;
    },
    scan,
  };
})();
