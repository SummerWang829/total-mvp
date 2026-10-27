/* 问卷运行器：一次显示一道题，答完后问服务器“下一步是什么”（逻辑只在服务器上，页面不知道哪个答案会被筛掉）。
   研究员编辑器右侧的测试和候选人的作答页（screen.html）用的是同一个。依赖 app.js 的 esc。 */
const SCR_L = {
  ms: { next: "Seterusnya", back: "Kembali", required: "Sila jawab soalan ini", number: "Sila masukkan nombor", pick: "Pilih satu", pickMany: "Pilih semua yang berkenaan", type: "Taip jawapan anda", errNet: "Tidak dapat memuatkan soalan seterusnya. Sila cuba lagi.", label: (i, n) => `Soalan ${i} daripada ${n}`, optional: " (pilihan)" },
  ta: { next: "அடுத்தது", back: "பின்", required: "இந்தக் கேள்விக்குப் பதில் அளிக்கவும்", number: "ஓர் எண்ணை உள்ளிடவும்", pick: "ஒன்றைத் தேர்ந்தெடுக்கவும்", pickMany: "பொருந்தும் அனைத்தையும் தேர்ந்தெடுக்கவும்", type: "உங்கள் பதிலை எழுதுங்கள்", errNet: "அடுத்த கேள்வியை ஏற்ற முடியவில்லை. மீண்டும் முயலவும்.", label: (i, n) => `கேள்வி ${i} / ${n}`, optional: " (விருப்பம்)" },
  en: { next: "Next", back: "Back", required: "Please answer this question", number: "Please enter a number", question: "Question", of: "of", pick: "Choose one", pickMany: "Choose all that apply", type: "Type your answer", errNet: "Could not load the next question. Please try again." },
  zh: { next: "下一题", back: "上一题", required: "请先回答这道题", number: "请填写数字", question: "第", of: "题 / 共", pick: "单选", pickMany: "多选", type: "在这里填写", errNet: "没有加载成功，请再试一次。" },
};

/** Run a questionnaire inside `host`. `step(answers)` resolves to { question } or { result }; `onEnd(result, answers)` is called when it ends. Returns { reset }. */
function scrRunner(host, { step, onEnd, lang = "en", onStep }) {
  let L = SCR_L[lang] || SCR_L.en, zh = lang === "zh";
  let answers = {}, trail = [], cur = null, draft = { id: "", v: undefined };   // draft: the choice on screen that was not sent yet, kept when the language changes
  const pre = (q) => (answers[q.id] !== undefined ? answers[q.id] : draft.id === q.id ? draft.v : undefined);
  const label = (q) => (L.label ? L.label(q.index, q.total) : zh ? `第 ${q.index} 题 / 共 ${q.total} 题` : `Question ${q.index} of ${q.total}`);
  function field(q) {
    if (q.type === "single" || q.type === "multi") {
      const kind = q.type === "single" ? "radio" : "checkbox";
      return `<div class="rf-opts" role="${q.type === "single" ? "radiogroup" : "group"}" aria-label="${esc(q.text)}">${q.options.map((o) => `<label class="chk"><input type="${kind}" name="scr_${q.id}" value="${esc(o.id)}" ${(q.type === "single" ? pre(q) === o.id : (pre(q) || []).includes(o.id)) ? "checked" : ""}><span>${esc(o.text)}</span></label>`).join("")}</div>`;
    }
    if (q.type === "number") return `<input type="number" step="any" name="scr_${q.id}" aria-label="${esc(q.text)}" value="${pre(q) ?? ""}">`;
    return `<textarea rows="3" name="scr_${q.id}" aria-label="${esc(q.text)}" placeholder="${L.type}">${esc(pre(q) ?? "")}</textarea>`;
  }
  function read(q) {
    const els = [...host.querySelectorAll(`[name=scr_${q.id}]`)];
    if (q.type === "single") return (els.find((e) => e.checked) || {}).value ?? null;
    if (q.type === "multi") return els.filter((e) => e.checked).map((e) => e.value);
    const v = (els[0] || {}).value ?? "";
    return q.type === "number" ? (v === "" ? null : Number(v)) : v;
  }
  function draw() {
    const q = cur;
    host.innerHTML = `<div class="rf-q"><div class="sm mut">${label(q)}${q.required ? "" : L.optional || (zh ? "（选填）" : " (optional)")}</div><b>${esc(q.text)}</b>
      <div class="sm mut">${q.type === "single" ? L.pick : q.type === "multi" ? L.pickMany : ""}</div>${field(q)}<div class="note" id="screrr" role="alert" hidden></div>
      <div class="row">${trail.length ? `<button type="button" id="scrback">${L.back}</button>` : ""}<button class="p" type="button" id="scrnext">${L.next}</button></div></div>`;
    host.querySelector("#scrnext").onclick = go;
    const b = host.querySelector("#scrback"); if (b) b.onclick = back;
    host.querySelectorAll("input[type=radio]").forEach((r) => (r.onchange = () => (host.querySelector("#screrr").hidden = true)));
    if (q.type === "multi") host.querySelectorAll(`input[name=scr_${q.id}]`).forEach((c) => (c.onchange = () => {  // choosing one option unticks the ones it cannot go with
      if (!c.checked) return; const o = q.options.find((x) => x.id === c.value) || { excl: [] };
      host.querySelectorAll(`input[name=scr_${q.id}]`).forEach((d) => { if (d !== c && (o.excl || []).includes(d.value)) d.checked = false; });
    }));
  }
  async function run() {
    let r;
    try { r = await step(answers); } catch (e) { host.innerHTML = `<div class="note" role="alert">${esc(e.message || L.errNet)}</div>`; return; }
    if (r.result) { host.innerHTML = ""; return onEnd(r.result, answers); }
    cur = r.question; if (onStep) onStep(r.question); draw();
  }
  async function go() {
    const q = cur, v = read(q), err = host.querySelector("#screrr");
    const empty = v === null || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "number" && !Number.isFinite(v));
    if (empty && q.required) { err.textContent = q.type === "number" ? L.number : L.required; err.hidden = false; return; }
    answers[q.id] = empty ? null : v; trail.push(q.id);
    await run();
  }
  async function back() { const id = trail.pop(); delete answers[id]; await run(); }
  // a language change keeps the answers and shows the same question again in the new language (the server translates it; the logic is the same)
  return { reset() { answers = {}; trail = []; return run(); }, start() { return run(); }, redraw() { if (!cur) return undefined; draft = { id: cur.id, v: read(cur) }; return run(); }, setLang(l) { lang = l; L = SCR_L[l] || SCR_L.en; zh = l === "zh"; } };
}
