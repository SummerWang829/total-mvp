/* 甄别设定页的“招募问卷”：左边编写（单选、多选、文字填空、数字填空，带逻辑：下一题 / 跳题 / 结束·不符合 / 结束·符合），右边直接填写测试，
   写好后发布成链接和二维码；候选人在 screen.html 作答，合格的人自动进受访者名单。AI 可以根据研究想法（和研究库）起草。
   也可以改成导入在别的系统里做好的 HTML 问卷链接。依赖 index.html 里的 api / esc / toast / guard / $ / CONFIG（app.js）和 screen_run.js。 */
const RF_TYPE = { single: "单选", multi: "多选", text: "文字填空", number: "数字填空" };
const RF_OP = { lt: "小于", lte: "不大于", gt: "大于", gte: "不小于", eq: "等于" };
const rfAiNotice = () => (CONFIG && CONFIG.ai_local ? "AI 运行在我们自己的服务器上，这段文字不会发给其他公司。"
  : `这段文字会发送给 AI 服务商${CONFIG && CONFIG.ai_vendor ? `“${CONFIG.ai_vendor}”` : ""}来生成内容，只用来写问卷和建议。勾选“参考研究库”时，本项目研究库里的资料也会一起发送。请不要写入受访者的姓名、电话等个人信息。`);

/** 整个“招募问卷”卡片，返回 { html, wire }。 */
function rfCard(p, form) {
  let f = form, mode = form.source === "html" ? "html" : "ai", Q = JSON.parse(JSON.stringify(form.questions || [])), timer = null;
  const nextId = () => { let n = 0; Q.forEach((q) => { const m = /^q(\d+)$/.exec(q.id); if (m) n = Math.max(n, +m[1]); }); return "q" + (n + 1); };
  const optId = (q) => { let n = 0; q.options.forEach((o) => { const m = /^o(\d+)$/.exec(o.id); if (m) n = Math.max(n, +m[1]); }); return "o" + (n + 1); };
  const blank = () => [{ id: "o1", text: "", goto: "", tag: "" }, { id: "o2", text: "", goto: "", tag: "" }];
  const fix = () => {  // a jump may only go forward to a question that exists: drop the ones that no longer do
    Q.forEach((q, i) => {
      const ok = (g) => !g || g === "end_out" || g === "end_ok" || Q.findIndex((x) => x.id === g) > i;
      q.options.forEach((o) => { if (!ok(o.goto)) o.goto = ""; }); q.rules = (q.rules || []).filter((r) => ok(r.goto) && r.goto);
    });
  };
  const incomplete = () => Q.some((q) => !q.text.trim() || q.options.some((o) => !o.text.trim()) || (q.rules || []).some((r) => !Number.isFinite(+r.value)));  // an empty question or option is not sent anywhere yet
  const body = () => ({ source: "ai", idea: ($("#rfidea") || {}).value ?? f.idea, questions: Q });
  const stat = (t) => { const s = $("#rfstat"); if (s) s.textContent = t; };
  // an unfinished option or question is only reported in the status line; publishing checks it again
  const save = () => { clearTimeout(timer); if (incomplete()) return stat("有题目或选项还没写完，写完会自动保存"); timer = setTimeout(async () => { try { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: body() })) }; stat("已保存"); } catch (e) { stat("还没保存：" + e.message); } }, 700); stat("保存中…"); };
  const actions = (i, cur, allowNext) => `<option value="" ${cur === "" ? "selected" : ""}>${allowNext ? "下一题" : "选择处理方式"}</option>${Q.slice(i + 1).map((x, k) => `<option value="${x.id}" ${cur === x.id ? "selected" : ""}>跳到第 ${i + k + 2} 题</option>`).join("")}<option value="end_out" ${cur === "end_out" ? "selected" : ""}>结束 · 不符合（筛掉）</option><option value="end_ok" ${cur === "end_ok" ? "selected" : ""}>结束 · 符合</option>`;

  function qCard(q, i) {
    const opts = q.type === "single" || q.type === "multi";
    return `<div class="rf-edit" data-qi="${i}"><div class="row"><b>${i + 1}.</b><select data-f="type" aria-label="第 ${i + 1} 题题型">${Object.entries(RF_TYPE).map(([k, n]) => `<option value="${k}" ${q.type === k ? "selected" : ""}>${n}</option>`).join("")}</select>
      <label class="chk"><input type="checkbox" data-f="required" ${q.required ? "checked" : ""}>必答</label><span class="sp"></span>
      <button class="s" type="button" data-mv="-1" ${i ? "" : "disabled"} aria-label="上移">↑</button><button class="s" type="button" data-mv="1" ${i < Q.length - 1 ? "" : "disabled"} aria-label="下移">↓</button><button class="s warn" type="button" data-del="1">删除</button></div>
      <input data-f="text" value="${esc(q.text)}" maxlength="300" placeholder="题目" aria-label="第 ${i + 1} 题题目">
      ${opts ? `<div class="rf-optl">${q.options.map((o, j) => `<div class="rf-optrow" data-oj="${j}"><input data-o="text" value="${esc(o.text)}" maxlength="120" placeholder="选项" aria-label="选项 ${j + 1}">
        <select data-o="goto" aria-label="选了之后">${actions(i, o.goto, true)}</select><input data-o="tag" value="${esc(o.tag || "")}" maxlength="30" placeholder="标签（选填）" aria-label="选了就打上的标签" style="max-width:140px">
        <button class="s" type="button" data-odel="${j}" aria-label="删除选项" ${q.options.length > 2 ? "" : "disabled"}>×</button></div>`).join("")}<button class="s" type="button" data-oadd="1" ${q.options.length >= 12 ? "disabled" : ""}>+ 添加选项</button></div>` : ""}
      ${q.type === "number" ? `<div class="rf-optl">${(q.rules || []).map((r, j) => `<div class="rf-optrow" data-rj="${j}"><span>如果数字</span><select data-r="op" aria-label="条件">${Object.entries(RF_OP).map(([k, n]) => `<option value="${k}" ${r.op === k ? "selected" : ""}>${n}</option>`).join("")}</select>
        <input data-r="value" type="number" step="any" value="${r.value}" aria-label="数值" style="max-width:110px"><select data-r="goto" aria-label="就">${actions(i, r.goto, false)}</select><button class="s" type="button" data-rdel="${j}" aria-label="删除规则">×</button></div>`).join("")}
        <button class="s" type="button" data-radd="1" ${(q.rules || []).length >= 4 ? "disabled" : ""}>+ 添加规则（例如 小于 18 → 结束 · 不符合）</button></div>` : ""}
      ${q.type === "text" ? `<p class="sm mut" style="margin:0">文字填空：答什么都进入下一题（答案会记在受访者资料里）。</p>` : ""}</div>`;
  }
  const editor = () => `<div id="rfqs">${Q.length ? Q.map(qCard).join("") : `<div class="empty"><b>还没有题目</b><span>点下面的按钮添加，或让 AI 根据研究想法起草。</span></div>`}</div>
    <div class="row"><button class="s" type="button" data-add="single">+ 单选</button><button class="s" type="button" data-add="multi">+ 多选</button><button class="s" type="button" data-add="text">+ 文字填空</button><button class="s" type="button" data-add="number">+ 数字填空</button></div>`;

  const publishBox = () => {
    const live = f.status === "published", n = f.stats || { qualified: 0, out: 0, out_by: {} };
    const outBy = Object.entries(n.out_by || {}).map(([id, c]) => `第 ${Q.findIndex((q) => q.id === id) + 1} 题 ${c} 人`).join("、");
    return `<div class="rf-pub"><div class="row"><b>发布</b><span class="pill ${live ? "" : "o"}">${live ? "发布中" : f.status === "closed" ? "已停止" : "草稿"}</span><span class="sp"></span>
      ${live ? `<button class="s" type="button" id="rfclose">停止收集</button>` : `<button class="p" type="button" id="rfpub" ${Q.length ? "" : "disabled"}>${f.status === "closed" ? "重新发布" : "发布成链接和二维码"}</button>`}</div>
      ${f.link ? `<div class="rf-link"><div><input id="rflink" readonly value="${esc(location.origin + f.link)}" aria-label="问卷链接"><div class="row" style="margin-top:6px"><button class="s" type="button" id="rfcopy">复制链接</button><a class="btn s" href="${esc(f.link)}" target="_blank" rel="noopener">打开答题页</a></div>
        <p class="sm mut" style="margin:8px 0 0">合格的人填完联系方式后，自动进下面的受访者名单（已标记“符合”，带上答案和标签）；被筛掉的人只计数，不保存任何信息。</p></div><img id="rfqr" alt="问卷二维码" width="132" height="132"></div>
        <div class="sm">已合格 <b>${n.qualified}</b> 人 · 被筛掉 <b>${n.out}</b> 人${outBy ? `（${esc(outBy)}）` : ""}</div>` : `<p class="sm mut" style="margin:0">发布后会得到一个链接和二维码，发给候选人就能作答。</p>`}</div>`;
  };

  function panels() {
    if (mode === "html") return `<div class="rf-grid"><div class="rf-l"><label class="field">问卷链接（https://）<input id="rfurl" type="url" value="${esc(f.html_url)}" placeholder="https://…（在别的系统里做好的问卷）" maxlength="500"></label>
      <div class="note">问卷在别的系统里制作和托管，这里只保存链接，点右边的按钮在新窗口里测试；不会嵌进本页面。</div><div class="row"><button class="p" id="rfsave" type="button">保存链接</button></div></div>
      <div class="rf-r">${f.html_url ? `<div class="empty"><b>测试链接</b><code class="rfurl">${esc(f.html_url)}</code><button class="p" id="rftest" type="button">打开测试窗口</button></div>` : `<div class="empty"><b>还没有链接</b><span>把问卷链接贴在左边并保存，这里就能打开测试。</span></div>`}</div></div>`;
    return `<div class="rf-grid"><div class="rf-l">
      <details class="rf-ai" ${Q.length ? "" : "open"}><summary>让 AI 起草（可选）</summary><label class="field">研究想法<textarea id="rfidea" rows="5" placeholder="例如：想了解新加坡 18-34 岁女性如何选购防晒产品，重点是购买渠道和决策因素。">${esc(f.idea)}</textarea></label>
        <div class="note">${esc(rfAiNotice())}</div><label class="chk"><input type="checkbox" id="rflib" ${f.library_docs ? "checked" : "disabled"}><span>参考研究库里本项目的资料（${f.library_docs || 0} 份）</span></label>
        <div class="row"><button class="s" id="rfimport" type="button">从立项向导导入研究背景</button><button class="p" id="rfgen" type="button">AI 起草问卷</button></div></details>
      <div class="sm mut" id="rfstat" role="status" aria-live="polite">${f.version ? `第 ${f.version} 版` : "改动会自动保存。"}</div>${editor()}</div>
      <div class="rf-r"><div class="row"><b>预览与测试</b><span class="sp"></span><button class="s" type="button" id="rfretest">重新测试</button></div><p class="sm mut" style="margin:0">就是候选人看到的样子：直接填写，看会走到哪里。</p>
        <div id="rfrun"></div><div class="note" id="rfresult" role="status" aria-live="polite" hidden></div>${publishBox()}</div></div>`;
  }

  const draw = () => { $("#rfbody").innerHTML = panels(); wire(); };
  const retest = () => {
    const host = $("#rfrun"); if (!host) return; const res = $("#rfresult"); res.hidden = true;
    if (!Q.length) { host.innerHTML = `<div class="empty"><b>还没有题目</b><span>左边添加题目后，这里直接测试。</span></div>`; return; }
    if (incomplete()) { host.innerHTML = `<div class="empty"><b>有题目还没写完</b><span>题目和选项都写好后，这里就能测试。</span></div>`; return; }
    scrRunner(host, {
      lang: "zh",
      step: async (answers) => { const r = await api(`/projects/${p.id}/recruiting/form/preview`, { method: "POST", body: { questions: Q, answers } }); return r.result ? { result: r.result } : { question: r.question }; },
      onEnd: (result) => { res.hidden = false; res.textContent = result === "out" ? "测试结果：这位候选人会被筛掉（不符合），问卷到此结束。" : "测试结果：这位候选人符合，会进入留联系方式的步骤。"; },
    }).start();
  };
  const loadQr = async () => {
    const img = $("#rfqr"); if (!img || !f.link) return;
    try { const r = await fetch(`/api/projects/${p.id}/recruiting/form/qr.svg`, { headers: { Authorization: "Bearer " + store.get("tt_token") } }); if (r.ok) img.src = URL.createObjectURL(await r.blob()); } catch { /* the link still works without the picture */ }
  };
  const structure = () => { fix(); save(); draw(); };

  function wire() {
    document.querySelectorAll("input[name=rfmode]").forEach((r) => (r.onchange = () => { mode = r.value; draw(); }));
    const sv = $("#rfsave"); if (sv) sv.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: { source: "html", html_url: $("#rfurl").value.trim() } })) }; draw(); toast("链接已保存"); });
    const tt = $("#rftest"); if (tt) tt.onclick = () => window.open(f.html_url, "_blank", "noopener,noreferrer");
    if (mode !== "ai") return;
    retest(); loadQr();
    $("#rfretest").onclick = retest;
    const gen = $("#rfgen");
    gen.onclick = guard(async () => {
      const idea = $("#rfidea").value.trim();
      if (Q.length && !confirm("AI 起草会替换现在所有的题目，确定吗？")) return;
      gen.disabled = true; stat("正在生成，大约要几秒…");
      try { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/generate`, { method: "POST", body: { idea, use_library: !!($("#rflib") && $("#rflib").checked) } })) }; Q = JSON.parse(JSON.stringify(f.questions)); draw(); toast("已起草，可以继续修改"); }
      finally { const b = $("#rfgen"); if (b) b.disabled = false; }
    });
    $("#rfimport").onclick = () => { if (!(p.brief || "").trim()) return toast("立项向导的“研究背景”还是空的", true); $("#rfidea").value = p.brief; };
    document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
      const type = b.dataset.add, q = { id: nextId(), type, text: "", required: true, options: type === "single" || type === "multi" ? blank() : [], rules: [] };
      Q.push(q); structure();
    }));
    document.querySelectorAll(".rf-edit").forEach((card) => {
      const i = +card.dataset.qi, q = Q[i];
      card.querySelectorAll("[data-f]").forEach((el) => (el.onchange = () => {
        const k = el.dataset.f;
        if (k === "type") { q.type = el.value; if ((q.type === "single" || q.type === "multi") && q.options.length < 2) q.options = blank(); if (q.type !== "number") q.rules = []; return structure(); }
        q[k] = k === "required" ? el.checked : el.value; save(); retest();
      }));
      card.querySelectorAll("[data-mv]").forEach((b) => (b.onclick = () => { const j = i + +b.dataset.mv; [Q[i], Q[j]] = [Q[j], Q[i]]; structure(); }));
      card.querySelector("[data-del]").onclick = () => { Q.splice(i, 1); structure(); };
      card.querySelectorAll(".rf-optrow[data-oj]").forEach((row) => { const o = q.options[+row.dataset.oj]; row.querySelectorAll("[data-o]").forEach((el) => (el.onchange = () => { o[el.dataset.o] = el.value; save(); retest(); })); });
      card.querySelectorAll("[data-odel]").forEach((b) => (b.onclick = () => { q.options.splice(+b.dataset.odel, 1); structure(); }));
      const oa = card.querySelector("[data-oadd]"); if (oa) oa.onclick = () => { q.options.push({ id: optId(q), text: "", goto: "", tag: "" }); structure(); };
      card.querySelectorAll(".rf-optrow[data-rj]").forEach((row) => { const r = q.rules[+row.dataset.rj]; row.querySelectorAll("[data-r]").forEach((el) => (el.onchange = () => { r[el.dataset.r] = el.dataset.r === "value" ? Number(el.value) : el.value; save(); retest(); })); });
      card.querySelectorAll("[data-rdel]").forEach((b) => (b.onclick = () => { q.rules.splice(+b.dataset.rdel, 1); structure(); }));
      const ra = card.querySelector("[data-radd]"); if (ra) ra.onclick = () => { (q.rules = q.rules || []).push({ op: "lt", value: 18, goto: "end_out" }); structure(); };
    });
    const pub = $("#rfpub"); if (pub) pub.onclick = guard(async () => {
      clearTimeout(timer); if (incomplete()) throw new Error("有题目或选项还没写完，写完再发布");
      f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form`, { method: "PUT", body: body() })) };
      f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/publish`, { method: "POST" })) }; draw(); toast("已发布，可以把链接或二维码发给候选人");
    });
    const cl = $("#rfclose"); if (cl) cl.onclick = guard(async () => { f = { ...f, ...(await api(`/projects/${p.id}/recruiting/form/close`, { method: "POST" })) }; draw(); toast("已停止收集"); });
    const cp = $("#rfcopy"); if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText($("#rflink").value); toast("链接已复制"); } catch { $("#rflink").select(); toast("请按 Ctrl+C 复制", true); } };
  }
  const html = `<div class="card rf"><h3 style="margin-top:0">招募问卷</h3><p class="sm mut" style="margin:0 0 8px">先有问卷，再设置配额。在这里编写（可让 AI 起草），右边直接测试，写好发布成链接或二维码；也可以导入在别的系统里做好的问卷链接。</p>
    <div class="row" role="radiogroup" aria-label="问卷来源"><label class="chk"><input type="radio" name="rfmode" value="ai" ${mode === "ai" ? "checked" : ""}>在这里编写</label><label class="chk"><input type="radio" name="rfmode" value="html" ${mode === "html" ? "checked" : ""}>导入问卷链接（HTML）</label></div>
    <div id="rfbody">${panels()}</div></div>`;
  return { html, wire };
}
