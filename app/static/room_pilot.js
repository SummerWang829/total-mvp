/* 直播间 · Pilot 的“标记问题”栏（修正方案第 1 轮第 9 条，效果图 qa_shots/fix1_batch2_mockup.html 第 ② 屏）。
   只在 pilot 场次、只给研究团队 / 主持人 / 观察员（受访者页面带 ?t= 邀请令牌，这里直接不启动；服务器也不给受访者这个接口）。
   只把写的那句话发给服务器：时间点和题号由服务器按“现在”和“正在问的题”记，不信浏览器。按 M 键或点按钮开始写。
   依赖 room.html 里的全局 iid / inviteT / authHdr。 */
(() => {
  if (typeof inviteT !== "undefined" && inviteT) return;
  if (typeof iid === "undefined" || !iid) return;
  const P = { on: false, marks: [], label: "", open: false, busy: false, timer: 0 };
  const E = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (ms) => (ms == null ? "—" : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`);
  const url = () => `/api/interviews/${iid}/pilot-marks`;

  function host() {
    let box = document.getElementById("pmk");
    if (box) return box;
    box = document.createElement("section");
    box.id = "pmk"; box.className = "qp pmk"; box.setAttribute("aria-label", "标记问题");
    const col = document.querySelector(".qc.qc4") || document.querySelector(".q4 > .qc:last-child");
    if (col) col.prepend(box); else { box.classList.add("float"); document.body.appendChild(box); }
    box.addEventListener("click", onClick);
    box.addEventListener("submit", onSubmit);
    return box;
  }
  function paint() {
    const box = host();
    box.innerHTML = `<h3>标记问题${P.label ? ` <span class="pmk-q">正在问 ${E(P.label)}</span>` : ""}<button type="button" class="pmk-go" data-pmk="open" title="按 M 键也可以">标记问题<span class="kbd">M</span></button></h3>
      ${P.open ? `<form class="pmk-f"><textarea id="pmk-in" rows="2" maxlength="500" aria-label="是什么问题" placeholder="写一句是什么问题（时间点和题号会自动记下）"></textarea><div class="pmk-a"><button type="submit" class="p"${P.busy ? " disabled" : ""}>记下</button><button type="button" data-pmk="close">取消</button></div></form>` : ""}
      <div class="pmk-list">${P.marks.slice().reverse().map((m) => `<div class="pmk-one${m.origin === "auto" ? " ai" : ""}"><small>${E(m.who)} · ${clock(m.at_ms)}${m.label ? ` · ${E(m.label)}` : ""}</small>${E(m.note)}</div>`).join("") || `<p class="sm">还没有标记。</p>`}</div>
      <p class="sm pmk-note">只有 pilot 场次有这一栏；受访者看不到。</p>`;
    if (P.open) { const t = document.getElementById("pmk-in"); if (t && document.activeElement !== t) t.focus(); }
  }
  async function load() {
    try {
      const r = await fetch(url(), { headers: authHdr() });
      if (r.status === 404 || r.status === 403) { stop(); return; }   // not a pilot (or not for this person): no panel
      if (!r.ok) return;
      const d = await r.json();
      P.on = true; P.marks = d.marks; P.label = d.label;
      if (!document.getElementById("pmk-in") || !document.getElementById("pmk-in").value) paint();
    } catch { /* the next poll tries again */ }
  }
  function stop() { clearInterval(P.timer); const b = document.getElementById("pmk"); if (b) b.remove(); P.on = false; }
  function onClick(e) {
    const b = e.target.closest("[data-pmk]"); if (!b) return;
    P.open = b.dataset.pmk === "open"; paint();
  }
  async function onSubmit(e) {
    e.preventDefault();
    const t = document.getElementById("pmk-in"), note = (t && t.value || "").trim();
    if (!note || P.busy) return;
    P.busy = true;
    try {
      const r = await fetch(url(), { method: "POST", headers: { ...authHdr(), "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { if (window.toast) toast(d.detail || "没有记下来", true); }
      else { P.marks = d.marks; P.label = d.label; P.open = false; if (window.toast) toast("已记下"); }
    } catch { if (window.toast) toast("网络不稳定，没有记下来", true); }
    P.busy = false; paint();
  }
  document.addEventListener("keydown", (e) => {
    if (!P.on || e.key.toLowerCase() !== "m" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest && e.target.closest("input,textarea,select,[contenteditable]")) return;
    e.preventDefault(); P.open = true; paint();
  });
  load(); P.timer = setInterval(load, 5000);
})();
