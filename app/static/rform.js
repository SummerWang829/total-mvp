/* 甄别设定页的“招募问卷”：左边写研究想法（AI 生成）或贴一条在别的系统做好的 HTML 问卷链接，右边看题目并直接测试。
   先有问卷，再设置配额（配额在页面下方）。依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）。 */
const RF_TYPE = { single: "单选", multi: "多选", open: "开放题", number: "数字" };
const rfAiNotice = () => (CONFIG && CONFIG.ai_local ? "AI 运行在我们自己的服务器上，这段文字不会发给其他公司。"
  : `这段文字会发送给 AI 服务商${CONFIG && CONFIG.ai_vendor ? `“${CONFIG.ai_vendor}”` : ""}来生成内容，只用来写问卷和建议。请不要写入受访者的姓名、电话等个人信息。`);

/** 右侧：题目预览，可以直接点着测试（选到“结束项”会提示这位受访者不符合）。 */
function rfQuestions(f) {
  if (!f.questions.length) return `<div class="empty"><b>还没有题目</b><span>左边写下研究想法，点“生成问卷”；改了想法，题目会跟着更新。</span></div>`;
  return `<ol class="rf-qs">${f.questions.map((q, i) => `<li class="rf-q" data-q="${q.id}"><div class="row"><b>${i + 1}. ${esc(q.text)}</b><span class="tag">${RF_TYPE[q.type]}</span></div>
    ${q.type === "open" ? `<input aria-label="第 ${i + 1} 题的回答" placeholder="填写回答">` : q.type === "number" ? `<input type="number" aria-label="第 ${i + 1} 题的回答" placeholder="填写数字">`
      : `<div class="rf-opts">${q.options.map((o, j) => `<label class="chk"><input type="${q.type === "single" ? "radio" : "checkbox"}" name="rf_${q.id}" data-end="${o.end ? 1 : 0}" aria-label="${esc(o.text)}"><span>${esc(o.text)}${o.end ? ` <span class="tag o">选这个就结束</span>` : ""}</span></label>`).join("")}</div>`}</li>`).join("")}</ol>
    <div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>`;
}

function rfPanels(f, mode) {
  const ai = mode === "ai";
  const left = ai ? `<label class="field">研究想法<textarea id="rfidea" rows="9" placeholder="例如：想了解新加坡 18-34 岁女性如何选购防晒产品，重点是购买渠道和决策因素。">${esc(f.idea)}</textarea></label>
      <div class="note">${esc(rfAiNotice())}</div>
      <div class="row"><button class="s" id="rfimport" type="button">从立项向导导入研究背景</button><button class="p" id="rfgen" type="button">生成问卷</button></div><div class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version && f.source === "ai" ? `第 ${f.version} 版 · 由 ${esc(f.generated_by || "手动")} 生成` : "改了想法，点别处或点“生成问卷”，右边的题目会更新。"}</div>`
    : `<label class="field">问卷链接（https://）<input id="rfurl" type="url" value="${esc(f.html_url)}" placeholder="https://…（在别的系统里做好的问卷）" maxlength="500"></label>
      <div class="note">问卷在别的系统里制作和托管，这里只保存链接，点右边的按钮在新窗口里测试；不会嵌进本页面。</div>
      <div class="row"><button class="p" id="rfsave" type="button">保存链接</button></div><div class="sm mut" id="rfstat" role="status" aria-live="polite"></div>`;
  const right = ai ? rfQuestions(f)
    : f.html_url ? `<div class="empty"><b>测试链接</b><code class="rfurl">${esc(f.html_url)}</code><button class="p" id="rftest" type="button">打开测试窗口</button></div>`
      : `<div class="empty"><b>还没有链接</b><span>把问卷链接贴在左边并保存，这里就能打开测试。</span></div>`;
  return `<div class="rf-grid"><div class="rf-l">${left}</div><div class="rf-r" id="rfright">${right}</div></div>`;
}

/** 整个“招募问卷”卡片，返回 { html, wire }。 */
function rfCard(p, form) {
  let f = form, mode = form.source === "html" ? "html" : "ai";
  const draw = () => { $("#rfbody").innerHTML = rfPanels(f, mode); wire(); };
  const stat = (t) => { const s = $("#rfstat"); if (s) s.textContent = t; };
  const generate = guard(async () => {
    const idea = $("#rfidea").value.trim();
    $("#rfgen").disabled = true; stat("正在生成，大约要几秒…");
    try { f = await api(`/projects/${p.id}/recruiting/form/generate`, { method: "POST", body: { idea } }); draw(); toast("问卷已更新"); }
    finally { const b = $("#rfgen"); if (b) b.disabled = false; }
  });
  function wire() {
    document.querySelectorAll("input[name=rfmode]").forEach((r) => (r.onchange = () => { mode = r.value; draw(); }));
    const idea = $("#rfidea");
    if (idea) { idea.onchange = () => { if (idea.value.trim() !== f.idea.trim() && idea.value.trim().length >= 8) generate(); }; $("#rfgen").onclick = generate;
      $("#rfimport").onclick = () => { if (!(p.brief || "").trim()) return toast("立项向导的“研究背景”还是空的", true); idea.value = p.brief; generate(); }; }
    const sv = $("#rfsave"); if (sv) sv.onclick = guard(async () => { f = await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "html", html_url: $("#rfurl").value.trim() } }); draw(); toast("链接已保存"); });
    const tt = $("#rftest"); if (tt) tt.onclick = () => window.open(f.html_url, "_blank", "noopener,noreferrer");
    document.querySelectorAll(".rf-q input[data-end]").forEach((i) => (i.onchange = () => {  // a test run: choosing an "ends here" option says the person would be screened out
      const hit = [...document.querySelectorAll(".rf-q input[data-end='1']:checked")], box = $("#rfresult");
      box.hidden = false; box.textContent = hit.length ? `测试结果：选了“结束项”（${hit[0].getAttribute("aria-label")}），这位受访者不符合，问卷到此结束。` : "测试结果：目前没有触发结束项，这位受访者符合。";
    }));
  }
  const html = `<div class="card rf"><h3 style="margin-top:0">招募问卷</h3><p class="sm mut" style="margin:0 0 8px">先有问卷，再设置配额。问卷有两种来源：让 AI 根据研究想法生成，或导入在别的系统里做好的问卷链接。</p>
    <div class="row" role="radiogroup" aria-label="问卷来源"><label class="chk"><input type="radio" name="rfmode" value="ai" ${mode === "ai" ? "checked" : ""}>AI 生成</label><label class="chk"><input type="radio" name="rfmode" value="html" ${mode === "html" ? "checked" : ""}>导入问卷链接（HTML）</label></div>
    <div id="rfbody">${rfPanels(f, mode)}</div></div>`;
  return { html, wire };
}
