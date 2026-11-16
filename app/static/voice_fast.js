/* 快速语音输入（微信式）：按住说话，边说边出字，松开即定稿。
   - 主通道：浏览器实时语音识别，说话过程中文字就进输入框（零等待）。
   - 兜底：同时录一份 16 kHz 单声道 WAV；实时识别不可用/没出字时，松开后只发一次请求，
     后端跳过转码直接送腾讯云一句话识别，通常 0.5~1 秒出字。
   - 只挂在创建访谈对话框 #ccmsg 上，其它输入框不出现（全局浮层已按要求删除）。
   依赖 app.js 的 store / toast 与 index.html 的全局 ME。 */
(() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const ROLES = ["admin", "researcher", "support", "recruiter"];
  const getField = () => document.querySelector("#ccmsg");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fail = (m) => { if (window.toast) toast(m, true); };
  const allowed = () => {
    const me = typeof ME !== "undefined" ? ME : null;
    return !!me && ROLES.includes(me.role) && !!getField();
  };

  let btn = null, raf = 0;
  let running = false, busy = false, finalizing = false, capturing = false, pressing = false, downAt = 0, mode = null, ignoreClick = false;
  let base = "", caret = 0, fin = "", itm = "", live = false, secs = 0, tick = 0, maxTimer = 0;
  let srvLang = "zh";
  let stream = null, ctx = null, srcNode = null, proc = null, pcm = [], srcRate = 48000, mime = "";
  let sr = null, rec = null, chunks = [];

  function build() {
    if (btn) return;
    btn = document.createElement("button");
    btn.type = "button"; btn.id = "vfmic"; btn.hidden = true;
    btn.title = "按住说话，松开就转成文字";
    btn.setAttribute("aria-label", "语音输入：按住说话");
    btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>`;
    document.body.appendChild(btn);
    // 长按＝按住说话（松手即结束）；短按＝点一下开始、再点一下结束
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (running && mode === "toggle") return;
      pressing = true; downAt = Date.now(); mode = "hold";
      start();
    });
    window.addEventListener("pointerup", () => {
      if (!pressing) return;
      pressing = false;
      if (mode !== "hold") return;
      if (Date.now() - downAt < 400) { mode = "toggle"; ignoreClick = true; return; }
      stop();
    });
    window.addEventListener("pointercancel", () => { if (pressing && mode === "hold") { pressing = false; stop(); } });
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      if (ignoreClick) { ignoreClick = false; return; }
      if (running && mode === "toggle") stop();
    });
  }

  function paint() {
    if (!btn) return;
    btn.classList.toggle("rec", running);
    btn.classList.toggle("busy", busy);
    btn.title = running ? (mode === "toggle" ? "正在听… 点一下结束" : "正在听… 松开即可") : busy ? "正在转成文字…" : "按住说话，松开就转成文字";
  }

  function place() {
    cancelAnimationFrame(raf);
    const f = getField();
    if (!f || !btn) { raf = requestAnimationFrame(place); return; }
    const r = f.getBoundingClientRect();
    const show = allowed() && r.width > 0 && r.height > 0 && r.bottom > 0 && r.bottom < innerHeight + 40;
    btn.hidden = !show;
    document.body.classList.toggle("vf-on", show);
    if (show) {
      // 贴在输入框右下角内侧、回形针左边（与截图红框位置一致）
      btn.style.top = (r.bottom - 36) + "px";
      btn.style.left = (r.right - 118) + "px";
    }
    raf = requestAnimationFrame(place);
  }

  function render(text) {
    const f = getField(); if (!f) return;
    const head = base.slice(0, caret), tail = base.slice(caret);
    let value = head + text + tail;
    if (f.maxLength > 0) value = value.slice(0, f.maxLength);
    f.value = value;
    const pos = Math.min(value.length, (head + text).length);
    try { f.setSelectionRange(pos, pos); } catch { /* not focusable yet */ }
    f.dispatchEvent(new Event("input", { bubbles: true }));
  }

  /* ---------- 录音：直接采 16 kHz PCM，结束时打包成 WAV（后端无需转码） ---------- */
  function wavBlob(samples) {
    const n = samples.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const s = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
    s(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); s(8, "WAVE"); s(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); s(36, "data"); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      let x = samples[i];
      x = x < -1 ? -1 : x > 1 ? 1 : x;
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function flatten() {
    let n = 0;
    for (const c of pcm) n += c.length;
    const all = new Float32Array(n);
    let o = 0;
    for (const c of pcm) { all.set(c, o); o += c.length; }
    // 线性重采样到 16 kHz（微信式短句，插值足够，比让服务器转码快得多）
    const ratio = srcRate / 16000, out = new Float32Array(Math.floor(n / ratio));
    for (let i = 0; i < out.length; i++) {
      const p = i * ratio, a = Math.floor(p), b = Math.min(a + 1, n - 1), t = p - a;
      out[i] = all[a] * (1 - t) + all[b] * t;
    }
    return out;
  }

  async function openMic() {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    pcm = []; capturing = true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      ctx = new AC(); srcRate = ctx.sampleRate || 48000;
      try { await ctx.resume(); } catch { /* still usable */ }
      srcNode = ctx.createMediaStreamSource(stream);
      proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => { if (capturing) pcm.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
      // 接一个 0 增益节点：ScriptProcessor 必须连到 destination 才会出数据，但不能把麦克风回放出来（啸叫）
      const mute = ctx.createGain(); mute.gain.value = 0;
      srcNode.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
      mime = "audio/wav";
    } else {
      // 极老浏览器：退回 MediaRecorder，后端照旧转码
      rec = new MediaRecorder(stream); chunks = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.start(); mime = rec.mimeType || "audio/webm";
    }
  }

  function closeMic() {
    capturing = false;
    try { if (proc) { proc.onaudioprocess = null; proc.disconnect(); } } catch { /* gone */ }
    try { if (srcNode) srcNode.disconnect(); } catch { /* gone */ }
    try { if (ctx) ctx.close(); } catch { /* gone */ }
    try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch { /* gone */ }
    proc = srcNode = ctx = stream = null;
  }

  async function start() {
    if (running || busy || !allowed()) return;
    const f = getField();
    base = f.value; caret = f.selectionStart ?? base.length;
    fin = ""; itm = ""; live = false; finalizing = false; secs = 0; running = true;
    srvLang = (document.documentElement.lang || "zh").toLowerCase().startsWith("en") ? "en" : "zh";
    paint();
    // 麦克风：一次授权，实时识别和兜底录音共用
    try { await openMic(); }
    catch {
      try { closeMic(); } catch { /* ignore */ }
      if (!SR) { running = false; paint(); return fail("没有拿到麦克风：请在浏览器地址栏允许使用麦克风"); }
    }
    if (!running) return;   // 用户在授权弹窗期间已经松开
    // 实时识别：说话时文字就出来，这是「微信速度」的关键
    if (SR) {
      try {
        sr = new SR();
        sr.lang = srvLang === "en" ? "en-US" : "zh-CN";
        sr.continuous = true; sr.interimResults = true; sr.maxAlternatives = 1;
        sr.onresult = (e) => {
          let a = "", b = "";
          for (let i = 0; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) a += r[0].transcript; else b += r[0].transcript;
          }
          fin = a; itm = b;
          // 松开后 sr.stop() 还会补一次「定稿」结果，这时也要收进来，否则会少最后一句
          if ((a || b) && (running || finalizing)) { live = true; render(a + b); }
        };
        sr.onerror = () => { /* 兜底录音还在，松手后照常出字 */ };
        sr.onend = () => { sr = null; };
        sr.start();
      } catch { sr = null; }
    }
    tick = setInterval(() => { secs++; paint(); }, 1000);
    clearTimeout(maxTimer);
    maxTimer = setTimeout(() => { if (running) stop(); }, 60000);
  }

  async function stop() {
    if (!running) return;
    running = false; finalizing = true; pressing = false; mode = null;
    clearInterval(tick); clearTimeout(maxTimer);
    try { if (sr) sr.stop(); } catch { sr = null; }
    let blob = null;
    if (mime === "audio/wav" && pcm.length) {
      blob = wavBlob(flatten());
      closeMic();
    } else if (rec && rec.state !== "inactive") {
      await new Promise((resolve) => { rec.onstop = resolve; try { rec.stop(); } catch { resolve(); } });
      blob = chunks.length ? new Blob(chunks, { type: mime }) : null;
      closeMic();
    } else {
      closeMic();
    }
    busy = true; paint();
    await finish(blob);
  }

  async function finish(blob) {
    if (live) {
      // 实时通道已经出字：等最后一次定稿结果，把未确认的半句去掉
      await sleep(350);
      render(fin || itm);
      finalizing = false; busy = false; paint(); return;
    }
    finalizing = false;
    if (!blob || blob.size < 1000) { fail("没有录到声音，请按住麦克风说完再松开"); busy = false; paint(); return; }
    try {
      const fd = new FormData();
      fd.append("file", blob, blob.type === "audio/wav" ? "clip.wav" : "clip.webm");
      fd.append("language", srvLang);
      const r = await fetch("/api/speech/transcribe", {
        method: "POST",
        headers: { Authorization: "Bearer " + (typeof store !== "undefined" ? store.get("tt_token") : "") },
        body: fd
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "语音识别没有成功，请再试一次");
      if (!d.text) throw new Error("没有听清，请再说一次");
      render(d.text);
    } catch (e) { fail(e.message); }
    busy = false; paint();
  }

  build();
  place();
})();
