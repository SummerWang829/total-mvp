/* 问卷运行器：一次显示一道题，答完后问服务器“下一步是什么”（逻辑只在服务器上，页面不知道哪个答案会被筛掉）。
   研究员编辑器右侧的测试和候选人的作答页（screen.html）用的是同一个。依赖 app.js 的 esc。 */
const SCR_L = {
  en: { next: "Next", back: "Back", required: "Please answer this question", number: "Please enter a number", question: "Question", of: "of", pick: "Choose one", pickMany: "Choose all that apply", type: "Type your answer", errNet: "Could not load the next question. Please try again." },
  zh: { next: "下一题", back: "上一题", required: "请先回答这道题", number: "请填写数字", question: "第", of: "题 / 共", pick: "单选", pickMany: "多选", type: "在这里填写", errNet: "没有加载成功，请再试一次。" },
};

/** Run a questionnaire inside `host`. `step(answers)` resolves to { question } or { result }; `onEnd(result, answers)` is called when it ends. Returns { reset }. */
function scrRunner(host, { step, onEnd, lang = "en", onStep }) {
  const L = SCR_L[lang] || SCR_L.en, zh = lang === "zh";
  let answers = {}, trail = [], cur = null;
  const label = (q) => (zh ? `第 ${q.index} 题 / 共 ${q.total} 题` : `Question ${q.index} of ${q.total}`);
  function field(q) {
    if (q.type === "single" || q.type === "multi") {
      const kind = q.type === "single" ? "radio" : "checkbox";
      return `<div class="rf-opts" role="${q.type === "single" ? "radiogroup" : "group"}" aria-label="${esc(q.text)}">${q.options.map((o) => `<label class="chk"><input type="${kind}" name="scr_${q.id}" value="${esc(o.id)}" ${(q.type === "single" ? answers[q.id] === o.id : (answers[q.id] || []).includes(o.id)) ? "checked" : ""}><span>${esc(o.text)}</span></label>`).join("")}</div>`;
    }
    if (q.type === "number") return `<input type="number" step="any" name="scr_${q.id}" aria-label="${esc(q.text)}" value="${answers[q.id] ?? ""}">`;
    return `<textarea rows="3" name="scr_${q.id}" aria-label="${esc(q.text)}" placeholder="${L.type}">${esc(answers[q.id] ?? "")}</textarea>`;
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
    host.innerHTML = `<div class="rf-q"><div class="sm mut">${label(q)}${q.required ? "" : zh ? "（选填）" : " (optional)"}</div><b>${esc(q.text)}</b>
      <div class="sm mut">${q.type === "single" ? L.pick : q.type === "multi" ? L.pickMany : ""}</div>${field(q)}<div class="note" id="screrr" role="alert" hidden></div>
      <div class="row">${trail.length ? `<button type="button" id="scrback">${L.back}</button>` : ""}<button class="p" type="button" id="scrnext">${L.next}</button></div></div>`;
    host.querySelector("#scrnext").onclick = go;
    const b = host.querySelector("#scrback"); if (b) b.onclick = back;
    host.querySelectorAll("input[type=radio]").forEach((r) => (r.onchange = () => (host.querySelector("#screrr").hidden = true)));
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
  return { reset() { answers = {}; trail = []; return run(); }, start() { return run(); } };
}
