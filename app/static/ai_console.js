/* AI 后台（仅管理员）：功能总览 + 调用监控。只显示次数、耗时、错误类型，不显示任何人说的话。数据来自 GET /api/ai-console/overview。 */
async function aiConsoleView() {
  if (ME.role !== "admin") { location.hash = "#/projects"; return; }
  let tab = sessionStorage.getItem("ai_tab") === "log" ? "log" : "over";
  let hours = Number(sessionStorage.getItem("ai_hours")) || 24;
  let data = null;
  const TABS = [["over", "功能总览"], ["log", "调用监控"]], WINDOWS = [[24, "24 小时"], [168, "7 天"]];
  const secs = (ms) => (ms ? (ms / 1000).toFixed(1) + " 秒" : "—");
  const rate = (f) => (f.calls ? Math.round(((f.calls - f.failed) / f.calls) * 1000) / 10 : null);
  const winLabel = () => (WINDOWS.find((w) => w[0] === hours) || WINDOWS[0])[1];
  const winPick = () => `<select id="aiwin" aria-label="统计时间">${WINDOWS.map(([h, n]) => `<option value="${h}"${h === hours ? " selected" : ""}>${n}</option>`).join("")}</select>`;

  function overHtml() {
    const fs = data.features, calls = fs.reduce((n, f) => n + f.calls, 0), failed = fs.reduce((n, f) => n + f.failed, 0);
    const m = data.model;
    const kpi = `<div class="ai-kpi"><div><b>${fs.length}</b><span>个 AI 功能</span></div><div><b>${calls}</b><span>${winLabel()}调用</span></div>
      <div><b>${calls ? Math.round((failed / calls) * 1000) / 10 + "%" : "—"}</b><span>${winLabel()}失败率</span></div><div><b>${esc(m.alias)}</b><span>${m.ready ? "可用" : "缺少数据接收方，不能使用"}</span></div></div>`;
    const rows = fs.map((f) => `<tr><td>${esc(f.domain)}</td><td><b>${esc(f.name)}</b></td><td class="mut">${esc(f.page)}</td><td>${f.prompt ? `<span class="tag">${esc(f.prompt)}</span>` : "—"}</td><td>${esc(f.model)}</td>
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

  function draw() {
    $("#aitabs").innerHTML = TABS.map(([k, n]) => `<a href="#" role="tab" data-k="${k}" class="${tab === k ? "on" : ""}" aria-selected="${tab === k}">${n}</a>`).join("") + `<span class="sp"></span>${winPick()}`;
    $("#aipanel").innerHTML = data ? (tab === "over" ? overHtml() : logHtml()) : `<div class="card empty"><b>加载中…</b></div>`;
    document.querySelectorAll("#aitabs [data-k]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); tab = a.dataset.k; sessionStorage.setItem("ai_tab", tab); draw(); }));
    $("#aiwin").onchange = (e) => { hours = Number(e.target.value); sessionStorage.setItem("ai_hours", hours); load(); };
  }

  const load = guard(async () => { data = await api(`/ai-console/overview?hours=${hours}`); draw(); });
  shell(`${head("AI 后台", "")}<div class="hubtabs sub ai-tabs" id="aitabs" role="tablist" aria-label="AI 后台"></div><div id="aipanel"></div>`, { active: "ai", title: "AI 后台" });
  draw();
  load();
}
