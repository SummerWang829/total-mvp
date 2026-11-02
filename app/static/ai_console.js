/* AI 后台（仅管理员）：功能总览 + 调用监控。只显示次数、耗时、错误类型，不显示任何人说的话。数据来自 GET /api/ai-console/overview。 */
async function aiConsoleView() {
  if (ME.role !== "admin") { location.hash = "#/projects"; return; }
  let tab = ["log", "prompt"].includes(sessionStorage.getItem("ai_tab")) ? sessionStorage.getItem("ai_tab") : "over";
  let plist = null, pkey = "", pd = null, shown = "built", draft = "", pnote = "";   // 提示词页：功能列表、当前功能、它的版本、正在看的版本（"built" = 内置）、文本框里的字、说明
  let hours = Number(sessionStorage.getItem("ai_hours")) || 24;
  let data = null;
  const TABS = [["over", "功能总览"], ["prompt", "提示词"], ["log", "调用监控"]], WINDOWS = [[24, "24 小时"], [168, "7 天"]];
  const secs = (ms) => (ms ? (ms / 1000).toFixed(1) + " 秒" : "—");
  const rate = (f) => (f.calls ? Math.round(((f.calls - f.failed) / f.calls) * 1000) / 10 : null);
  const winLabel = () => (WINDOWS.find((w) => w[0] === hours) || WINDOWS[0])[1];
  const winPick = () => `<select id="aiwin" aria-label="统计时间">${WINDOWS.map(([h, n]) => `<option value="${h}"${h === hours ? " selected" : ""}>${n}</option>`).join("")}</select>`;

  function overHtml() {
    const fs = data.features, calls = fs.reduce((n, f) => n + f.calls, 0), failed = fs.reduce((n, f) => n + f.failed, 0);
    const m = data.model;
    const kpi = `<div class="ai-kpi"><div><b>${fs.length}</b><span>个 AI 功能</span></div><div><b>${calls}</b><span>${winLabel()}调用</span></div>
      <div><b>${calls ? Math.round((failed / calls) * 1000) / 10 + "%" : "—"}</b><span>${winLabel()}失败率</span></div><div><b>${esc(m.alias)}</b><span>${m.ready ? "可用" : "缺少数据接收方，不能使用"}</span></div></div>`;
    const rows = fs.map((f) => `<tr${f.editable ? ` class="ai-go" data-go="${esc(f.key)}"` : ""}><td>${esc(f.domain)}</td><td><b>${esc(f.name)}</b></td><td class="mut">${esc(f.page)}</td><td>${f.prompt ? `<span class="tag">${esc(f.prompt)}</span>` : "—"}</td><td>${esc(f.model)}</td>
      <td>${f.tracked ? `${f.calls} / ${f.failed ? `<span class="ai-bad">${f.failed}</span>` : 0}` : '<span class="mut">未统计</span>'}</td></tr>`).join("");
    return `${kpi}<div class="card scroll" style="padding:6px 10px"><table class="tbl"><thead><tr><th>领域</th><th>功能</th><th>用在哪个页面</th><th>提示词</th><th>模型</th><th>调用 / 失败</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function logHtml() {
    const used = data.features.filter((f) => f.calls).concat(data.other ? [{ name: "未归类的调用", domain: "", calls: data.other.calls, failed: data.other.failed, errors: data.other.errors, avg_ms: data.other.avg_ms, p95_ms: data.other.p95_ms }] : []);
    const calls = used.reduce((n, f) => n + f.calls, 0), failed = used.reduce((n, f) => n + f.failed, 0);
    const avg = calls ? Math.round(used.reduce((n, f) => n + f.avg_ms * f.calls, 0) / calls) : 0, p95 = used.reduce((n, f) => Math.max(n, f.p95_ms), 0);
    const kpi = `<div class="ai-kpi"><div><b>${calls}</b><span>${winLabel()}调用</span></div><div><b>${secs(avg)}</b><span>平均耗时</span></div><div><b>${secs(p95)}</b><span>最慢的一档（p95，按功能取最大）</span></div><div><b>${failed}</b><span>失败</span></div></div>`;
    if (!used.length) return kpi + `<div class="card empty"><b>${winLabel()}内还没有 AI 调用</b></div>`;
    const rows = used.map((f) => {
      const r = rate(f), errs = Object.entries(f.errors || {}).map(([k, v]) => `${esc(k)} ${v}`).join("、");
      return `<tr><td>${esc(f.domain ? f.domain + " · " + f.name : f.name)}</td><td>${f.calls}</td><td>${f.failed ? `<span class="ai-bad">${f.failed}</span>` : 0}</td><td class="mut">${errs || "—"}</td><td>${secs(f.avg_ms)}</td><td>${secs(f.p95_ms)}</td>
        <td><div class="ai-bar" title="${r}%"><i style="width:${r}%"></i></div></td></tr>`;
    }).join("");
    const names = Object.fromEntries(data.features.map((f) => [f.key, f.domain + " · " + f.name]));
    const recent = data.recent_failures.map((x) => `<tr><td class="mut">${esc(new Date(x.at + "Z").toLocaleString())}</td><td>${esc(names[x.feature] || "未归类的调用")}</td><td>${esc(x.error)}</td><td>${secs(x.ms)}</td></tr>`).join("");
    return `${kpi}<div class="card scroll" style="padding:6px 10px"><table class="tbl"><thead><tr><th>功能</th><th>调用</th><th>失败</th><th>错误类型</th><th>平均耗时</th><th>p95</th><th>成功率</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${recent ? `<h2 class="ai-h">最近的失败</h2><div class="card scroll" style="padding:6px 10px"><table class="tbl"><thead><tr><th>时间</th><th>功能</th><th>错误类型</th><th>耗时</th></tr></thead><tbody>${recent}</tbody></table></div>` : ""}`;
  }

  const stateName = { live: "上线", draft: "草稿", retired: "已停用" };
  const when = (s) => new Date(s + "Z").toLocaleString();
  function promptHtml() {
    if (!plist) return `<div class="card empty"><b>加载中…</b></div>`;
    const doms = [...new Set(plist.map((f) => f.domain))];
    const left = doms.map((d) => `<div class="ai-dom">${esc(d)}</div>` + plist.filter((f) => f.domain === d).map((f) => `<button type="button" class="ai-pi${f.key === pkey ? " on" : ""}" data-pk="${esc(f.key)}"><span>${esc(f.name)}</span><small>${esc(f.prompt)}</small></button>`).join("")).join("");
    if (!pd) return `<div class="ai-pg"><div class="ai-pl">${left}</div><div class="card empty"><b>在左边选一个功能</b></div></div>`;
    const live = pd.live, vs = pd.versions, cur = shown === "built" ? null : vs.find((v) => v.version === shown);
    const opts = [`<option value="built"${shown === "built" ? " selected" : ""}>内置${live === null ? "（当前）" : ""}</option>`].concat(vs.map((v) => `<option value="${v.version}"${v.version === shown ? " selected" : ""}>v${v.version}${v.state === "live" ? "（当前）" : v.state === "draft" ? " · 草稿" : ""}</option>`)).join("");
    const base = cur ? cur.text : pd.built_in, changed = draft.trim() !== base.trim();
    const isLive = shown === "built" ? live === null : cur && cur.state === "live";
    const rows = vs.map((v) => `<tr><td>v${v.version}</td><td><span class="tag${v.state === "live" ? " ai-live" : ""}">${stateName[v.state]}</span></td><td class="mut">${esc(v.note || "—")}</td><td class="mut">${esc(v.by || "—")}</td><td class="mut">${esc(when(v.at))}</td><td>${v.state === "live" ? "" : `<button class="s" type="button" data-live="${v.version}">上线</button>`}</td></tr>`).join("");
    return `<div class="ai-pg"><div class="ai-pl">${left}</div><div class="ai-pr"><div class="card"><div class="row"><b>${esc(pd.domain)} · ${esc(pd.name)}</b><span class="tag${live === null ? "" : " ai-live"}">${live === null ? "内置" : "上线 v" + live}</span><span class="sp"></span><label class="mut">版本 <select id="aipv" aria-label="版本">${opts}</select></label></div>
      <div class="sm mut" style="margin:4px 0 8px">用在：${esc(pd.page)}</div>
      <textarea id="aipt" class="ai-ta" spellcheck="false" aria-label="提示词">${esc(draft)}</textarea>
      <div class="sm mut ai-vars">${pd.placeholders.length ? "可用变量：" + pd.placeholders.map((x) => `<code>${esc(x)}</code>`).join(" ") + "（程序会换成真实内容，不能删）" : ""}${pd.identity ? " 助手身份说明由系统自动追加，不用写。" : ""}</div>
      <div class="row" style="margin-top:10px"><input id="aipn" maxlength="80" placeholder="改了什么（选填）" aria-label="改了什么" value="${esc(pnote)}" style="flex:1;min-width:160px"><button class="s" type="button" id="aipsave"${changed ? "" : " disabled"}>保存为新版本（草稿）</button>${isLive ? "" : `<button class="p" type="button" id="aiplive">${shown === "built" ? "回到内置" : "上线 v" + shown}</button>`}</div></div>
      ${vs.length ? `<div class="card scroll" style="padding:6px 10px"><table class="tbl"><thead><tr><th>版本</th><th>状态</th><th>改动说明</th><th>作者</th><th>时间</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}</div></div>`;
  }
  const settle = (r, pick) => { pd = r; shown = pick || (r.live === null ? "built" : r.live); const v = r.versions.find((x) => x.version === shown); draft = v ? v.text : r.built_in; pnote = ""; };
  const loadList = guard(async () => { plist = (await api("/ai-console/prompts")).features; draw(); });
  const openPrompt = guard(async (key) => { pkey = key; settle(await api(`/ai-console/prompts/${encodeURIComponent(key)}`)); draw(); });
  const afterChange = (r, msg, pick) => { settle(r, pick); toast(msg); loadList(); draw(); };
  function wirePrompt() {
    document.querySelectorAll("[data-pk]").forEach((b) => (b.onclick = () => openPrompt(b.dataset.pk)));
    const sel = $("#aipv"); if (!sel) return;
    $("#aipt").oninput = (e) => { draft = e.target.value; const base = shown === "built" ? pd.built_in : pd.versions.find((v) => v.version === shown).text; $("#aipsave").disabled = draft.trim() === base.trim(); };
    $("#aipn").oninput = (e) => { pnote = e.target.value; };
    sel.onchange = () => { shown = sel.value === "built" ? "built" : +sel.value; const v = pd.versions.find((x) => x.version === shown); draft = v ? v.text : pd.built_in; draw(); };
    const url = `/ai-console/prompts/${encodeURIComponent(pkey)}`;
    $("#aipsave").onclick = guard(async () => { const r = await api(`${url}/versions`, { method: "POST", body: { text: draft, note: pnote } }); afterChange(r, "已保存为新版本草稿，上线后才会生效", r.versions[0].version); });
    const go = $("#aiplive"); if (go) go.onclick = guard(async () => afterChange(await api(`${url}/live`, { method: "POST", body: { version: shown === "built" ? null : shown } }), shown === "built" ? "已回到内置提示词" : `已上线 v${shown}`));
    document.querySelectorAll("[data-live]").forEach((b) => (b.onclick = guard(async () => afterChange(await api(`${url}/live`, { method: "POST", body: { version: +b.dataset.live } }), `已上线 v${b.dataset.live}`))));
  }
  function draw() {
    $("#aitabs").innerHTML = TABS.map(([k, n]) => `<a href="#" role="tab" data-k="${k}" class="${tab === k ? "on" : ""}" aria-selected="${tab === k}">${n}</a>`).join("") + `<span class="sp"></span>${winPick()}`;
    $("#aipanel").innerHTML = tab === "prompt" ? promptHtml() : data ? (tab === "over" ? overHtml() : logHtml()) : `<div class="card empty"><b>加载中…</b></div>`;
    if (tab === "prompt") wirePrompt();
    document.querySelectorAll("#aipanel [data-go]").forEach((r) => (r.onclick = () => { tab = "prompt"; sessionStorage.setItem("ai_tab", tab); if (!plist) loadList(); openPrompt(r.dataset.go); }));
    document.querySelectorAll("#aitabs [data-k]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); tab = a.dataset.k; sessionStorage.setItem("ai_tab", tab); if (tab === "prompt" && !plist) loadList(); draw(); }));
    $("#aiwin").onchange = (e) => { hours = Number(e.target.value); sessionStorage.setItem("ai_hours", hours); load(); };
  }

  const load = guard(async () => { data = await api(`/ai-console/overview?hours=${hours}`); draw(); });
  shell(`${head("AI 后台", "")}<div class="hubtabs sub ai-tabs" id="aitabs" role="tablist" aria-label="AI 后台"></div><div id="aipanel"></div>`, { active: "ai", title: "AI 后台" });
  draw();
  load();
  if (tab === "prompt") loadList();
}
