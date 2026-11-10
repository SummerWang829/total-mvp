/* 受访者详情 v2：整段录像 + 模型按题切片 + 双向联动 + 全量三语。
   lang=en 时面板内所有文字（静态文案、图标提示、状态、标签、时间、总结、弹窗、空态）
   都切到英文；动态内容由 /detail/translate 返回 {源文本: 译文} 映射，局部重绘，不刷新页面。
   时间码只有一套相对播放毫秒，全部取自 ASR 字幕。 */
async function openRespondent(iid, tab) {   // eslint-disable-line no-redeclare
  rspInstall();
  if (RSP.open && RSP.close) RSP.close(false);
  const staff = STAFF(), my = renderSeq;
  const [it, segs, recs, tl, dt] = await Promise.all([
    api(`/interviews/${iid}`),
    api(`/interviews/${iid}/transcript`).catch(() => []),
    staff ? api(`/interviews/${iid}/recordings`).catch(() => []) : [],
    api(`/interviews/${iid}/timeline/segments`).catch(() => ({ segments: [], needs_generate: false })),
    api(`/interviews/${iid}/detail`).catch(() => ({ data: {}, needs_generate: false }))
  ]);
  if (my !== renderSeq) return;
  const segList = Array.isArray(segs) ? segs : [], recList = Array.isArray(recs) ? recs : [];
  const [stLabel, stCls] = STATUS[it.status] || [it.status, ""];
  let video = null, lang = "orig", sliceSource = (tl && tl.source) || "rule";
  let detail = (dt && dt.data) || {};
  const state = { follow: true, transMsg: "", translating: false };
  const L = (zh, en) => lang === "en" ? en : zh;
  const code = it.kind === "group" ? "座谈会" : (it.participant && it.participant.code) || "受访者";
  const codeText = () => it.kind === "group" ? L("座谈会", "Group interview") : (it.participant && it.participant.code) || L("受访者", "Respondent");
  const pname = staff && it.kind !== "group" && it.participant ? it.participant.name || "" : "";
  const country = (it.participant && it.participant.country) || "—";
  const langName = (typeof LANGS !== "undefined" && LANGS[it.language]) || it.language || "—";
  const idx = RSP.list.indexOf(iid), hasNav = RSP.list.length > 1 && idx >= 0;
  const planned = it.duration_min || 45;
  const mins = (it.actual_min != null ? it.actual_min : (it.started_at && it.ended_at
    ? Math.round((new Date(it.ended_at) - new Date(it.started_at)) / 60000) : null));

  const $$ = (s) => Array.prototype.slice.call(document.querySelectorAll(s));
  const escAttr = (s) => esc(String(s == null ? "" : s));
  const clock = (ms) => { const s = Math.max(0, Math.round((ms || 0) / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };

  const STATUS_EN = { "已结束": "Completed", "已完成": "Completed", "进行中": "In progress", "未开始": "Not started",
    "已预约": "Scheduled", "已取消": "Cancelled", "中途结束": "Ended early", "待开始": "Not started" };
  const COUNTRY_EN = { "香港": "Hong Kong", "中国": "China", "新加坡": "Singapore", "英国": "United Kingdom", "美国": "United States" };
  const LANG_EN = { "中文": "Chinese", "简体中文": "Simplified Chinese", "English": "English", "zh": "Chinese", "en": "English" };
  const DIM_EN = { "相关性": "Relevance", "信息密度与独特性": "Information density & uniqueness", "深度": "Depth",
    "清晰度": "Clarity", "一致性": "Consistency", "完整性": "Completeness" };
  const DIM_DESC = {
    "相关性": ["是否回应问题核心诉求，避免无关信息。", "Answers the question without unrelated content."],
    "信息密度与独特性": ["是否提供具体经历、一手细节或区别于泛泛共识的信息。", "Offers concrete, first-hand detail beyond generalities."],
    "深度": ["是否说明背后的动机、具体场景和实际影响。", "Explains motives, real situations and actual impact."],
    "清晰度": ["是否逻辑清楚，并有案例、数据或场景支撑。", "Is coherent and backed by examples, numbers or scenes."],
    "一致性": ["前后表达是否一致，并符合受访者已知背景。", "Is consistent across answers and with known background."],
    "完整性": ["是否覆盖问题的关键诉求，没有遗漏核心信息。", "Covers the key points of the question without gaps."]
  };
  const stText = () => lang === "en" ? (STATUS_EN[stLabel] || stLabel) : stLabel;
  const placeText = () => lang === "en" ? (COUNTRY_EN[country] || country) : country;
  const langText = () => lang === "en" ? (LANG_EN[langName] || langName) : langName;
  const minText = (n) => L(`${n} 分`, `${n} min`);
  const tagsBase = () => ((detail.tags && detail.tags.length ? detail.tags : ((it.participant && it.participant.tags) || [])).slice(0, 8));
  const sumBase = () => (detail.summary || []);
  const enLookup = (prefix, text) => (detail.en && detail.en[`${prefix}:${text}`]) || "";
  const tags = () => tagsBase().map((t) => lang === "en" ? (enLookup("tag", t) || t) : t);
  const summaryLines = () => sumBase().map((s) => lang === "en" ? (enLookup("sum", s) || s) : s);
  const screenEn = (a, suffix) => enLookup(`screen:${a.q}`, suffix);

  const root = document.createElement("div");
  root.className = "rsp"; root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true"); root.setAttribute("aria-label", L("受访者详情", "Respondent detail"));
  app.appendChild(root); document.body.classList.add("rsp-open");

  root.innerHTML = `<button type="button" class="rsp-tab" id="rsp-col" aria-label="${L("收起", "Collapse")}">›</button>
    <div class="rsp-md" tabindex="-1">
      <div class="rsp-hd rd-hd">
        <span class="rd-av" id="rd-av">${esc(String(codeText()).slice(-2))}</span>
        <b id="rd-code">${esc(codeText())}</b>${pname ? `<span class="mut">${esc(pname)}</span>` : ""}
        <span class="tag ${stCls}" id="rd-status">${esc(stText())}</span><span class="sp"></span>
        <div class="rd-langs" id="rd-langs">
          <button type="button" class="on" data-rd-lang="orig">${L("原文", "Original")}</button>
          <button type="button" data-rd-lang="zh">中文</button>
          <button type="button" data-rd-lang="en">English</button>
        </div>
        <div class="rd-dd"><button type="button" class="s" id="rd-dl">${L("↓ 下载 ▾", "↓ Export ▾")}</button>
          <div class="rd-ddmenu" id="rd-dlmenu" hidden>
            <button type="button" data-rd-dl="xlsx">${L("导出为 Excel（.xlsx）", "Export as Excel (.xlsx)")}</button>
            <button type="button" data-rd-dl="docx">${L("导出为 Word（.docx）", "Export as Word (.docx)")}</button>
            <button type="button" data-rd-dl="csv">${L("导出逐字稿（.csv）", "Export transcript (.csv)")}</button>
          </div></div>
        ${hasNav ? `<button type="button" class="s" id="rsp-prev" ${idx > 0 ? "" : "disabled"}>${L("上一位", "Previous")}</button><button type="button" class="s" id="rsp-next" ${idx < RSP.list.length - 1 ? "" : "disabled"}>${L("下一位", "Next")}</button>` : ""}
        <button type="button" class="s" id="rsp-x" aria-label="${L("关闭", "Close")}">✕</button>
      </div>
      <div class="rd-body" id="rd-body"></div>
      <div class="rsp-ft" id="rd-ft"></div>
    </div>
    <div class="mask rd-mask" id="rd-score" hidden><div class="rd-modal" id="rd-modal"></div></div>`;

  RSP.open = true; RSP.iid = iid; RSP.tab = "detail"; RSP.base = location.hash;
  const prevFocus = document.activeElement;
  const dur = Math.max((recList.find((r) => r.duration_ms) || {}).duration_ms || 0,
    segList.length ? segList[segList.length - 1].end_ms : 0, 1000);
  const screen = (it.key_answers || []).filter((a) => a && (a.q || a.a));
  let qa = normalize(tl && tl.segments) || ruleGroup(segList);

  /* 英语模式下：字幕按 speaker 分流；题目用主持人段译文，回复用受访者段译文 */
  const enOf = (g, moderator) => g.parts
    .filter((p) => moderator ? p.speaker === "moderator" : p.speaker !== "moderator")
    .map((p) => (detail.en && detail.en[p.id]) || (!moderator && p.text_en) || "")
    .filter(Boolean).join(" ");
  const askOf = (g) => lang === "en" ? (enOf(g, true) || g.ask) : g.ask;
  const txtOf = (g) => lang === "zh" && g.zh ? g.zh : lang === "en" ? enOf(g, false) : g.text;
  const hasEnglish = () => {
    const capOk = !segList.length || (qa.length > 0 && qa.every((g) => g.parts
      .filter((p) => (p.text_orig || "").trim())
      .every((p) => (detail.en && detail.en[p.id]) || p.text_en)));
    const screenOk = !screen.length || screen.every((a) => screenEn(a, "q") && (screenEn(a, "a") || !a.a));
    const tagOk = tagsBase().every((t) => enLookup("tag", t));
    const sumOk = sumBase().every((s) => enLookup("sum", s));
    return capOk && screenOk && tagOk && sumOk;
  };

  function normalize(list) {
    if (!Array.isArray(list) || !list.length) return null;
    const byId = new Map(segList.map((s) => [s.id, s]));
    const out = list.map((g, i) => ({
      q: g.q || i + 1, ask: g.ask || g.question || L(`第 ${i + 1} 题`, `Question ${i + 1}`),
      at: +g.at_ms || 0, end: +g.end_ms || (+g.at_ms || 0) + 1000,
      text: g.answer || "", zh: g.text_zh || "",
      parts: (g.segment_ids || []).map((id) => byId.get(id)).filter(Boolean)
    })).filter((g) => g.text || g.parts.length);
    return out.length ? out : null;
  }
  function ruleGroup(list) {
    const out = []; let cur = null;
    for (const s of list) {
      if (s.speaker === "moderator") {
        if (cur && cur.parts.length) out.push(cur);
        cur = { q: out.length + 1, ask: s.text_orig || "", at: s.start_ms || 0, parts: [] };
      } else {
        if (!cur) cur = { q: 1, ask: L("（开场 / 自由发言）", "(Opening / free speech)"), at: s.start_ms || 0, parts: [] };
        cur.parts.push(s);
      }
    }
    if (cur && cur.parts.length) out.push(cur);
    return out.map((g) => ({ ...g, text: g.parts.map((p) => p.text_orig || "").join(" "),
      zh: g.parts.map((p) => p.text_zh || "").filter(Boolean).join(" "),
      end: g.parts[g.parts.length - 1].end_ms || g.at }));
  }

  /* 一次性创建的元素（头部 / 视频区 / 底部）在切语言时由这里更新，不重建 <video> */
  function applyI18n() {
    const set = (sel, text) => { const el = $(sel); if (el) el.textContent = text; };
    const langBtn = { orig: L("原文", "Original"), zh: "中文", en: "English" };
    $$("#rd-langs button").forEach((b) => { b.textContent = langBtn[b.dataset.rdLang] || b.textContent; });
    set("#rd-status", stText());
    set("#rd-code", codeText()); set("#rd-av", String(codeText()).slice(-2));
    set("#rd-dl", L("↓ 下载 ▾", "↓ Export ▾"));
    set("#rsp-prev", L("上一位", "Previous")); set("#rsp-next", L("下一位", "Next"));
    const menu = { xlsx: L("导出为 Excel（.xlsx）", "Export as Excel (.xlsx)"),
      docx: L("导出为 Word（.docx）", "Export as Word (.docx)"), csv: L("导出逐字稿（.csv）", "Export transcript (.csv)") };
    $$("#rd-dlmenu button").forEach((b) => { b.textContent = menu[b.dataset.rdDl] || b.textContent; });
    const col = $("#rsp-col"); if (col) col.setAttribute("aria-label", L("收起", "Collapse"));
    const x = $("#rsp-x"); if (x) x.setAttribute("aria-label", L("关闭", "Close"));
    root.setAttribute("aria-label", L("受访者详情", "Respondent detail"));
    set("#rd-video-title", L("访问录像", "Recording"));
    const hint = $("#rd-vhint");
    if (hint) hint.innerHTML = L(
      '整段录像从 0 秒开始；白点是<b>模型按题切的片段点</b>（时间码取自转写）。<b>点进度条 → 原话高亮；点原话旁 ▶ → 视频回该片段</b>。',
      'One continuous recording from second 0. White marks are <b>question slices made by the model</b> (timecodes come from the transcript). <b>Click the progress bar to highlight the quote; click ▶ beside a quote to play that segment.</b>');
    const ft = $("#rd-ft");
    if (ft) ft.textContent = L("点进度条找原话，点原话旁的 ▶ 回视频；← → 前后 5 秒，空格播放 / 暂停",
      "Click the progress bar to find a quote, click ▶ beside a quote to play it; ← → skip 5s, Space play/pause");
    const ph = $("#rd-vph");
    if (ph && ph.dataset.msg !== "none") ph.textContent = ph.dataset.msg === "novideo"
      ? L("没有可播放的录像（没有录制，或已按保留期清除）。转写与文字仍可看。", "No playable recording (not recorded, or purged by the retention policy). Transcript and text are still available.")
      : L("正在加载录像…", "Loading recording…");
    const sub = $("#rd-vsub");
    if (sub && !sub.dataset.touched) sub.textContent = L("点进度条或任意一句回答，视频与文字互相对上", "Click the progress bar or any answer to align video and text");
  }

  function draw() {
    const body = $("#rd-body");
    if (!body.querySelector("#rd-rest")) {
      body.innerHTML = `<section class="rd-sec" id="rd-video-sec">
          <div class="rd-sect"><span id="rd-video-title">${L("访问录像", "Recording")}</span> <span class="rd-cur" id="rd-cur">—</span></div>
          <div class="rd-video" id="rd-vbox"><div class="rd-vph" id="rd-vph" data-msg="load">${L("正在加载录像…", "Loading recording…")}</div>
            <div class="rd-vsub" id="rd-vsub">${L("点进度条或任意一句回答，视频与文字互相对上", "Click the progress bar or any answer to align video and text")}</div>
            <div class="rd-vctrl"><button type="button" id="rd-play" aria-label="${L("播放 / 暂停", "Play / Pause")}">▶</button>
              <span id="rd-tm">00:00 / ${clock(dur)}</span>
              <div class="rd-vbar" id="rd-vbar"><div class="rd-vfill" id="rd-vfill"></div><div class="rd-vdot" id="rd-vdot"></div><div id="rd-marks"></div></div>
              <span>1×</span></div></div>
          <div class="rd-hint" id="rd-vhint"></div>
        </section><div id="rd-rest"></div>`;
    }
    applyI18n(); drawRest(); updateMarks(); wire();
  }
  function drawRest() {
    const qaHtml = qa.length ? qa.map((g, i) => `<div class="rd-qcard">
        <div class="rd-qhd"><span class="rd-qno">Q${g.q || i + 1}</span><span class="rd-qtx">${escAttr(askOf(g))}</span></div>
        <div class="rd-turn"><div class="rd-lbl"><i></i>${L("主持人", "Moderator")}</div><div class="rd-modtxt">${escAttr(askOf(g))}</div></div>
        <div class="rd-turn part"><div class="rd-bubble" data-rd-i="${i}" data-rd-at="${g.at}">
            <div class="rd-lbl"><i></i>${L("受访者", "Participant")}</div>
            <div class="rd-acts"><span>${clock(g.at)}</span>${staff && g.parts.length ? `<button type="button" data-rd-edit="${i}" title="${L("校对转写", "Edit transcript")}">✎</button>` : ""}<button type="button" data-rd-seek="${i}" title="${L("播放这一段", "Play this segment")}">▶</button></div>
            <div class="rd-txt" data-rd-txt="${i}">${escAttr(txtOf(g) || (lang === "en" ? (state.transMsg || L("English 译文待生成", "English translation pending")) : "—"))}</div>
            <div class="rd-dur">${L("时长", "Duration")} ${clock((g.end || g.at) - g.at)} · ${L("点文字或 ▶ 定位视频", "Click text or ▶ to seek the video")}</div>
          </div></div></div>`).join("")
      : `<div class="rd-empty">${L("还没有逐题问答。访谈结束并完成转写后，模型会把字幕按大纲题切成片段。", "No question-by-question answers yet. After the interview is transcribed, the model slices the captions by outline question.")}</div>`;
    const screenHtml = screen.length ? screen.map((a, i) => `<div class="rd-qcard">
        <div class="rd-qhd"><span class="rd-qno">S${i + 1}</span><span class="rd-qtx">${escAttr(lang === "en" ? (screenEn(a, "q") || a.q) : a.q)}</span></div>
        <div class="rd-optbox"><div class="rd-lbl p"><i></i>${L("受访者", "Participant")}</div><div class="rd-ans">${escAttr(lang === "en" ? (screenEn(a, "a") || a.a) : a.a)}</div></div></div>`).join("")
      : `<div class="rd-empty">${L("没有甄别题答案。", "No screening answers.")}</div>`;
    const lines = summaryLines();
    const sum = lines.length
      ? `<ul class="rd-sum">${lines.map((s) => `<li>${escAttr(s)}</li>`).join("")}</ul><p class="rd-note">${L("按整场大纲回答归纳，可在 AI 后台调整提示词。", "Summarised from the whole outline; the prompt can be edited in the AI console.")}</p>`
      : `<div class="rd-empty">${L("待生成 · 需要逐题回答数据（模型归纳接在 /detail/generate）。", "Pending — needs question-by-question answers (model summary at /detail/generate).")}</div>`;
    const q = detail.quality;
    const scoreHtml = q && q.mean != null
      ? `<b class="small rd-scorelink" id="rd-scorebtn" title="${L("查看六维评分依据", "View six-dimension breakdown")}">${q.mean} / 5 ↗</b>`
      : `<b class="small mut">${L("待评分 · 需 ≥3 道可评分题", "Not scored · needs ≥3 scoreable answers")}</b>`;
    const tl = tags();
    $("#rd-rest").innerHTML = `<div class="rd-tr" id="rd-tr" ${state.transMsg ? "" : "hidden"}>${escAttr(state.transMsg)}</div>
      <section class="rd-sec"><div class="rd-sect">${L("访问信息", "Interview Info")}</div>
        <div class="rd-meta">
          <div><span>${L("活跃时间", "Active time")}</span><b>${mins != null ? minText(mins) : "—"}</b></div>
          <div><span>${L("状态", "Status")}</span><b>${escAttr(stText())}</b></div>
          <div><span>${L("开始时间", "Started at")}</span><b class="small">${escAttr(it.started_at ? String(it.started_at).replace("T", " ").slice(0, 19) : "—")}</b></div>
          <div><span>${L("回答质量评分", "Answer quality")}</span>${scoreHtml}</div>
        </div>
        <p class="rd-note">${escAttr(placeText())} · ${escAttr(langText())} · ${L("预计", "Planned")} ${minText(planned)} · ${L("切片来源", "Slices")} ${sliceSource === "model" ? L("模型按题归类", "model-grouped by question") : L("规则分组", "rule-based")}</p>
      </section>
      <section class="rd-sec"><div class="rd-sect">${L("整体标签", "Profile Tags")}</div>
        ${tl.length ? `<div class="rd-tags">${tl.map((t) => `<span class="rd-ptag">${escAttr(t)}</span>`).join("")}</div>` : `<div class="rd-empty">${L("待生成 · 按整场回答归纳的画像标签。", "Pending — profile tags summarised from the whole interview.")}</div>`}
      </section>
      <section class="rd-sec"><div class="rd-sect">${L("要点总结", "Key Takeaways")}</div>${sum}</section>
      <section class="rd-sec"><div class="rd-sect">${L("甄别题回答", "Screening Answers")} <span class="rd-sectnote">${L("来自问卷，无需模型", "From the questionnaire")}</span></div>${screenHtml}</section>
      <section class="rd-sec"><div class="rd-sect">${L("访谈逐题问答", "Question-by-question")} <span class="rd-sectnote">${L("主持人 / 受访者分开呈现", "Moderator and participant shown separately")}</span></div>${qaHtml}</section>`;
  }
  function updateMarks() {
    const box = $("#rd-marks"); if (!box) return;
    box.innerHTML = qa.map((g) => `<i class="rd-mark" style="left:${Math.min(98, (g.at / dur) * 100)}%" title="Q${g.q || ""} ${clock(g.at)}"></i>`).join("");
  }

  /* ---------- 双向联动 ---------- */
  function atIndex(ms) {
    let hit = -1, last = -1;
    qa.forEach((g, i) => { if (ms >= g.at && ms <= g.end) hit = i; if (g.at <= ms) last = i; });
    return hit >= 0 ? hit : last;
  }
  function follow(ms, scroll) {
    const i = atIndex(ms); if (i < 0) return;
    $$(".rd-bubble.sel").forEach((b) => b.classList.remove("sel"));
    const bubble = document.querySelector(`.rd-bubble[data-rd-i="${i}"]`);
    if (bubble) { bubble.classList.add("sel"); if (scroll) bubble.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    const sub = $("#rd-vsub");
    if (sub) { sub.textContent = txtOf(qa[i]) || (lang === "en" ? "" : qa[i].text) || "—"; sub.dataset.touched = "1"; }
    const cur = $("#rd-cur"); if (cur) cur.textContent = `Q${qa[i].q || i + 1} · ${clock(ms)}`;
  }
  function bar(ms) {
    const p = Math.max(0, Math.min(100, (ms / dur) * 100));
    const f = $("#rd-vfill"), d = $("#rd-vdot"), t = $("#rd-tm");
    if (f) f.style.width = p + "%"; if (d) d.style.left = p + "%";
    if (t) t.textContent = `${clock(ms)} / ${clock(dur)}`;
  }
  function seekTo(ms, play) {
    if (video) { try { video.currentTime = ms / 1000; } catch (_) { /* not loaded */ } if (play) video.play().catch(() => {}); }
    bar(ms); follow(ms, true);
  }
  function showScore() {
    const q = detail.quality; if (!q || !q.dims) return;
    const m = $("#rd-modal"); if (!m) return;
    const dims = Object.entries(q.dims).map(([k, v]) => {
      const label = lang === "en" ? (DIM_EN[k] || k) : k;
      const desc = (DIM_DESC[k] || ["", ""])[lang === "en" ? 1 : 0];
      return `<div class="rd-dim"><div class="dh">${escAttr(label)}<b>${v}</b></div><div class="ds">${escAttr(desc)}</div></div>`;
    }).join("");
    m.innerHTML = `<h3>${L("回答质量评分依据", "Answer quality breakdown")} <button class="x" type="button" data-rd-close="rd-score">×</button></h3>
      <p>${L("按可评分的逐题回答，六维度各 1-5 分取均值；空答、拒答、寒暄不计入。评分口径可在「管理员 → AI 后台 → 提示词」修改。", "Mean of six 1-5 dimensions over scoreable answers; empty, refused and small-talk answers are excluded. The rubric can be edited in Admin → AI Console → Prompts.")}</p>
      <div class="rd-dims">${dims}</div>
      <p><b>${L("平均", "Average")} ${q.mean} / 5</b> · ${L("基于", "based on")} ${q.scored} ${L("道可评分题", "scoreable answers")} · rubric v1</p>`;
    $("#rd-score").hidden = false;
    const b = m.querySelector("[data-rd-close]");
    if (b) b.onclick = () => { $("#rd-score").hidden = true; };
  }
  function wire() {
    $$("#rd-langs button").forEach((b) => (b.onclick = async () => {
      lang = b.dataset.rdLang;
      $$("#rd-langs button").forEach((x) => x.classList.toggle("on", x === b));
      const need = lang === "en" && !hasEnglish();
      if (need) setTransState(L("正在自动翻译题目与回答，稍等…", "Translating the whole panel, please wait…"));
      applyI18n(); drawRest(); wireRestOnly();
      follow(video ? video.currentTime * 1000 : 0, false);
      if (need) { await ensureEnglish(); return; }
      toast(lang === "en" ? L("已切换 English · 全部内容已英文化", "Switched to English · all content translated") : L("已切换到中文", "Switched to Chinese"));
    }));
    wireRestOnly();
    const vbar = $("#rd-vbar");
    if (vbar) vbar.onclick = (e) => { const r = vbar.getBoundingClientRect(); seekTo(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur, false); };
    const pb = $("#rd-play"); if (pb) pb.onclick = () => { if (video) (video.paused ? video.play().catch(() => {}) : video.pause()); };
    const dl = $("#rd-dl"); if (dl) dl.onclick = (e) => { e.stopPropagation(); const m = $("#rd-dlmenu"); m.hidden = !m.hidden; };
    $$("#rd-dlmenu button").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); $("#rd-dlmenu").hidden = true; exportDoc(b.dataset.rdDl); }));
    const sb = $("#rd-scorebtn"); if (sb) sb.onclick = showScore;
  }
  function wireRestOnly() {
    $$("[data-rd-seek]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); seekTo(+b.closest(".rd-bubble").dataset.rdAt, true); }));
    $$("[data-rd-txt]").forEach((el) => (el.onclick = () => seekTo(+el.closest(".rd-bubble").dataset.rdAt, false)));
    $$("[data-rd-edit]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); editSeg(+b.dataset.rdEdit); }));
    const sb = $("#rd-scorebtn"); if (sb) sb.onclick = showScore;
  }
  function setTransState(text) {
    state.transMsg = text || "";
    const el = $("#rd-tr");
    if (el) { el.hidden = !state.transMsg; el.textContent = state.transMsg; }
  }
  async function ensureEnglish() {
    if (state.translating) return;
    state.translating = true;
    setTransState(L("正在翻译整个面板（题目、回复、标签、总结、时间），稍等…", "Translating the whole panel (questions, answers, tags, summary, labels), please wait…"));
    try {
      const r = await api(`/interviews/${iid}/detail/translate`, { method: "POST", body: { lang: "en" } });
      if (r && r.en) detail.en = { ...(detail.en || {}), ...r.en };
      detail.en_count = Object.keys(detail.en || {}).length;
      applyI18n(); drawRest(); updateMarks(); wireRestOnly();
      follow(video ? video.currentTime * 1000 : 0, false);
      setTransState("");
      toast(r && r.count ? L(`已翻译 ${r.count} 处，全部内容自动呈现，无需刷新`, `Translated ${r.count} items — all content updated, no reload needed`) : (r && r.reason) || L("暂无可翻译内容", "Nothing to translate"), !(r && r.count));
    } catch (e) { setTransState(""); toast(L("翻译失败：", "Translation failed: ") + e.message, true); }
    finally { state.translating = false; }
  }
  function editSeg(i) {
    const g = qa[i]; if (!g || !g.parts.length) return;
    const s = g.parts[0];
    const m = modal(`<h2>${L("校对转写", "Edit transcript")}</h2><p class="sm mut">${L("改动会记入审计日志。可让模型先给一版纠错建议，人工确认后再保存。", "Changes are audited. You can ask the model for a suggestion, then confirm before saving.")}</p>
      <form id="f"><label class="f"><span>${L("原文", "Original")}</span><textarea name="text_orig" rows="3" required>${escAttr(s.text_orig)}</textarea></label>
      <label class="f"><span>${L("中文翻译", "Chinese translation")}</span><textarea name="text_zh" rows="3">${escAttr(s.text_zh || "")}</textarea></label>
      <div class="row"><button type="button" class="s" id="fx">${L("模型纠错建议", "Suggest correction")}</button><span class="sp"></span><button class="p">${L("保存", "Save")}</button><button type="button" id="c">${L("取消", "Cancel")}</button></div></form>`);
    $("#c", m).onclick = () => m.remove();
    $("#fx", m).onclick = guard(async () => {
      const t = $("[name=text_orig]", m).value;
      const r = await api(`/interviews/${iid}/detail/fix`, { method: "POST", body: { text: t } });
      if (r && r.changed) { $("[name=text_orig]", m).value = r.text; toast(L("已填入纠错建议，请人工确认后保存", "Suggestion filled in — confirm before saving")); }
      else toast((r && r.reason) || L("模型认为无需修改", "No changes suggested"), true);
    });
    $("#f", m).onsubmit = guard(async (ev) => {
      ev.preventDefault(); const fd = new FormData(ev.target);
      const no = String(fd.get("text_orig") || ""), nz = String(fd.get("text_zh") || "");
      await api(`/transcript/${s.id}`, { method: "PATCH", body: { text_orig: no, text_zh: nz } });
      s.text_orig = no; s.text_zh = nz;
      g.text = g.parts.map((p) => p.text_orig || "").join(" ");
      g.zh = g.parts.map((p) => p.text_zh || "").filter(Boolean).join(" ");
      const cell = document.querySelector(`[data-rd-txt="${i}"]`); if (cell) cell.textContent = txtOf(g) || no;
      m.remove(); toast(L("已保存（已记入审计日志）", "Saved (audit logged)"));
    });
  }
  async function exportDoc(kind) {
    try {
      const r = await fetch(`/api/interviews/${iid}/detail/export?format=${kind}`, { headers: { Authorization: "Bearer " + store.get("tt_token") } });
      if (!r.ok) throw new Error(L("导出失败，请稍后再试", "Export failed, please try again"));
      const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob());
      a.download = `${code}-${L("受访者详情", "respondent-detail")}.${kind}`; document.body.appendChild(a); a.click(); a.remove();
      toast(L("已导出（含要点总结 / 评分 / 逐题问答）", "Exported (summary / score / Q&A included)"));
    } catch (e) { toast(e.message, true); }
  }

  /* ---------- 录像 ---------- */
  async function mountVideo() {
    if (!staff || document.querySelector("#rd-v")) return;
    const play = recList.find((r) => r.playable);
    const box = $("#rd-vbox"); if (!box) return;
    if (!play) {
      const ph = box.querySelector(".rd-vph");
      if (ph) { ph.dataset.msg = "novideo"; ph.textContent = L("没有可播放的录像（没有录制，或已按保留期清除）。转写与文字仍可看。", "No playable recording (not recorded, or purged by the retention policy). Transcript and text are still available."); }
      return;
    }
    const v = document.createElement("video"); v.id = "rd-v"; v.playsInline = true; v.preload = "metadata";
    v.setAttribute("controlsList", "nodownload"); v.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#111";
    box.insertBefore(v, box.firstChild); const ph = box.querySelector(".rd-vph"); if (ph) ph.remove(); video = v;
    v.addEventListener("timeupdate", () => { const ms = v.currentTime * 1000; bar(ms); follow(ms, !v.paused && state.follow); });
    v.addEventListener("play", () => { state.follow = true; const b = $("#rd-play"); if (b) b.textContent = "❚❚"; });
    v.addEventListener("pause", () => { const b = $("#rd-play"); if (b) b.textContent = "▶"; });
    v.addEventListener("seeked", () => follow(v.currentTime * 1000, false));
    try { const r = await api(`/interviews/${iid}/recordings/${play.id}/play`, { method: "POST" }); v.src = r.url; } catch (e) { toast(L("录像加载失败：", "Recording failed to load: ") + e.message, true); }
  }

  /* ---------- 模型切片 / 生成：有数据时自动跑一次 ---------- */
  if (tl && tl.needs_generate) {
    api(`/interviews/${iid}/timeline/generate`, { method: "POST" }).then((r) => {
      if (r && r.state === "ready" && Array.isArray(r.segments) && r.segments.length) {
        const next = normalize(r.segments);
        if (next) { qa = next; sliceSource = "model"; drawRest(); updateMarks(); wireRestOnly(); toast(L(`已用模型把字幕按 ${qa.length} 道题切成片段`, `Model grouped captions into ${qa.length} question slices`)); }
      }
    }).catch(() => {});
  }
  if (dt && dt.needs_generate) {
    api(`/interviews/${iid}/detail/generate`, { method: "POST" }).then((r) => {
      if (r && r.state === "ready" && r.data) {
        detail = r.data; drawRest(); wireRestOnly();
        if (lang === "en") follow(video ? video.currentTime * 1000 : 0, false);
        toast(L("已生成 English 译文 / 要点总结 / 画像标签 / 质量评分", "Generated English / summary / profile tags / quality score"));
      }
    }).catch(() => {});
  }

  /* ---------- 生命周期 ---------- */
  const onDocClick = () => { const m = $("#rd-dlmenu"); if (m) m.hidden = true; };
  const onPop = () => { if (RSP.open) done(false); };
  const onKey = (e) => {
    if (e.key === "Escape") { if (document.querySelector(".modal")) return; e.preventDefault(); done(); return; }
    if (e.target.closest("input,textarea,select,[contenteditable]") || !video) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 5); }
    else if (e.key === "ArrowRight") { e.preventDefault(); video.currentTime = Math.min(video.duration || 9e9, video.currentTime + 5); }
    else if (e.key === " " && !e.target.closest("button,a")) { e.preventDefault(); video.paused ? video.play().catch(() => {}) : video.pause(); }
  };
  const done = (pop = true) => {
    window.removeEventListener("popstate", onPop); document.removeEventListener("keydown", onKey, true); document.removeEventListener("click", onDocClick);
    if (video) { try { video.pause(); } catch (_) { /* gone */ } }
    root.remove(); document.body.classList.remove("rsp-open", "rsp-collapsed");
    RSP.open = false; RSP.refresh = null; RSP.close = null;
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    if (pop && history.state && history.state.rsp) history.back();
  };
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("popstate", onPop);
  document.addEventListener("click", onDocClick);
  RSP.close = done;
  RSP.refresh = async () => {
    const fresh = await api(`/interviews/${iid}/transcript`).catch(() => []);
    segList.length = 0; Array.prototype.push.apply(segList, Array.isArray(fresh) ? fresh : []);
    qa = normalize((await api(`/interviews/${iid}/timeline/segments`).catch(() => ({ segments: [] }))).segments) || ruleGroup(segList);
    detail = ((await api(`/interviews/${iid}/detail`).catch(() => ({ data: {} }))).data) || {};
    applyI18n(); drawRest(); updateMarks(); wire();
  };
  $("#rsp-x").onclick = () => done();
  $("#rsp-col").onclick = () => { const c = document.body.classList.toggle("rsp-collapsed"); const b = $("#rsp-col"); b.textContent = c ? "‹" : "›"; b.setAttribute("aria-label", c ? L("展开", "Expand") : L("收起", "Collapse")); };
  if (hasNav) { $("#rsp-prev").onclick = () => openRespondent(RSP.list[idx - 1]); $("#rsp-next").onclick = () => openRespondent(RSP.list[idx + 1]); }
  history.pushState({ rsp: iid }, "");
  root.querySelector(".rsp-md").focus();
  draw(); mountVideo();
}
