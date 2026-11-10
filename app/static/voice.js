/* 语音输入：研究员/管理员端所有文字输入框（textarea 和单行文字 input）获得焦点时，右上角出现一个小麦克风。
   点一下开始说话，再点一下结束：录音发给 POST /api/speech/transcribe，转成文字填进输入框光标处（不会自动发送）。录音不保存。
   不想要的输入框加 data-novoice。依赖 app.js 里的 store / toast，和 index.html 里的全局 ME。 */
(() => {
  const ROLES = ["admin", "researcher", "support", "recruiter"], MAX_S = 60, TYPES = ["", "text", "search"];
  let btn, lang, field = null, rec = null, chunks = [], timer = 0, tick = 0, secs = 0, busy = false, raf = 0;
  const getLang = () => { try { return sessionStorage.getItem("tt_voice_lang") || "zh"; } catch { return "zh"; } };
  const setLang = (l) => { try { sessionStorage.setItem("tt_voice_lang", l); } catch { /* no storage: stays Chinese */ } };
  const eligible = (el) => {
    const me = typeof ME !== "undefined" ? ME : null;
    if (!el || !me || !ROLES.includes(me.role) || el.disabled || el.readOnly || el.hasAttribute("data-novoice") || el.closest("[data-novoice]")) return false;
    return el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && TYPES.includes((el.getAttribute("type") || "").toLowerCase()));
  };
  const build = () => {
    if (btn) return;
    btn = document.createElement("div"); btn.id = "vmic"; btn.hidden = true;
    btn.innerHTML = `<button type="button" class="vm-l" aria-label="语音输入的语言（中文 / English）"></button><button type="button" class="vm-b" aria-label="语音输入：点一下开始说话，再点一下结束"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg><span class="vm-t"></span></button>`;
    document.body.appendChild(btn);
    lang = btn.querySelector(".vm-l");
    const keep = (e) => e.preventDefault();   // clicking the button must not take the focus away from the box
    btn.addEventListener("mousedown", keep);
    lang.onclick = () => { setLang(getLang() === "zh" ? "en" : "zh"); paint(); };
    btn.querySelector(".vm-b").onclick = () => (rec ? stop() : start());
  };
  const paint = () => {
    if (!btn) return;
    lang.textContent = getLang() === "zh" ? "中" : "EN";
    btn.classList.toggle("rec", !!rec); btn.classList.toggle("busy", busy);
    btn.querySelector(".vm-t").textContent = rec ? `${secs}s` : "";
  };
  const place = () => {
    cancelAnimationFrame(raf);
    if (!field || !btn || btn.hidden) return;
    const r = field.getBoundingClientRect(), w = btn.offsetWidth || 60;
    btn.style.top = Math.max(4, Math.min(innerHeight - 34, r.top + (r.height < 50 ? (r.height - 28) / 2 : 6))) + "px";
    btn.style.left = Math.max(4, Math.min(innerWidth - w - 4, r.right - w - 8)) + "px";
    raf = requestAnimationFrame(place);
  };
  const show = (el) => { build(); field = el; btn.hidden = false; paint(); place(); };
  const hide = () => { if (rec || busy) return; if (btn) btn.hidden = true; field = null; cancelAnimationFrame(raf); };
  const fail = (m) => { if (window.toast) toast(m, true); };
  async function start() {
    if (!field || busy) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) return fail("这个浏览器不能录音，请换 Chrome 或 Edge");
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { return fail("没有拿到麦克风：请在浏览器地址栏允许使用麦克风"); }
    chunks = []; secs = 0; const target = field;
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => { stream.getTracks().forEach((t) => t.stop()); clearInterval(tick); clearTimeout(timer); const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" }); rec = null; paint(); send(blob, target); };
    rec.start(); paint();
    tick = setInterval(() => { secs++; paint(); }, 1000); timer = setTimeout(stop, MAX_S * 1000);
    target.focus();
  }
  function stop() { if (rec && rec.state !== "inactive") rec.stop(); }
  async function send(blob, target) {
    if (blob.size < 1200) return fail("没有录到声音，请靠近麦克风再试一次");
    busy = true; paint();
    try {
      const fd = new FormData(); fd.append("file", blob, "clip.webm"); fd.append("language", getLang());
      const r = await fetch("/api/speech/transcribe", { method: "POST", headers: { Authorization: "Bearer " + store.get("tt_token") }, body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "语音识别没有成功，请再试一次");
      if (!d.text) throw new Error("没有听清，请再说一次");
      insert(target, d.text);
    } catch (e) { fail(e.message); }
    busy = false; paint(); if (document.activeElement !== target) hide();
  }
  function insert(el, text) {
    const room = el.maxLength > 0 ? Math.max(0, el.maxLength - (el.value.length - (el.selectionEnd - el.selectionStart))) : text.length;
    el.focus();
    const s = el.selectionStart ?? el.value.length, e = el.selectionEnd ?? s;
    el.setRangeText(text.slice(0, room), s, e, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  document.addEventListener("focusin", (e) => { if (eligible(e.target)) show(e.target); else if (!(btn && btn.contains(e.target))) hide(); });
  document.addEventListener("focusout", () => setTimeout(() => { if (!rec && !busy && !(document.activeElement && eligible(document.activeElement))) hide(); }, 150));
  window.addEventListener("hashchange", () => { if (rec) stop(); hide(); });
})();
