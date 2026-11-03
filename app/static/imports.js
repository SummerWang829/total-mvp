/* 导入甄别数据（客户 Confirmit 导出的 raw data）：上传 → 映射 → 预检 → 确认 → 结果；以及未匹配池。
   为什么单独一个文件：这条链路和"导入名单（外部渠道）"完全不同——那边是姓名+邮箱的名单，这边是**编码 + 没有姓名**的原始数据，
   匹配只靠邮箱，值要按映射解码，匹配不上的人进未匹配池而不是自动建档（这些决定见 docs/设计稿_招募落地_原型说明_2026-11-04.md）。
   依赖 index.html 里的 api / esc / guard / toast / modal / render / $，后端见 app/routers/recruit_import.py。 */
const IW = { p: null, m: null, dlg: null, step: 1, csv: "", filename: "", mapping: {}, preview: null, pre: null, batch: null,
             busy: false, saved: [], saveAs: "", sourceName: "", notice: "", keyCol: "" };

const iwPct = (n, d) => (d ? Math.round((n / d) * 100) : 0);
const iwParseValues = (text) => {
  const out = {};
  (text || "").split(/\n/).forEach((line) => {
    const m = line.match(/^\s*([^=＝]+?)\s*[=＝]\s*(.+?)\s*$/);
    if (m) out[m[1]] = m[2];
  });
  return out;
};
const iwValuesText = (values) => Object.entries(values || {}).map(([k, v]) => `${k}=${v}`).join("\n");

/** 打开导入向导。p 是项目（和页面里其它视图一样用 p.id 调接口）。 */
function openImportWizard(p) {
  Object.assign(IW, { p, step: 1, csv: "", filename: "", mapping: {}, preview: null, pre: null, batch: null, busy: false,
                      saved: [], saveAs: "", sourceName: "", notice: "", keyCol: "" });
  IW.m = modal(``);
  IW.dlg = IW.m.firstElementChild;
  IW.dlg.classList.add("iwdlg");
  iwRender();
}

function iwClose() { if (IW.m) IW.m.remove(); IW.m = null; IW.dlg = null; }

function iwRender() {
  if (!IW.dlg) return;
  const steps = ["上传", "映射", "预检", "确认导入", "结果"];
  const stepper = `<div class="iwstep">${steps.map((s, i) => {
    const n = i + 1, cls = n === IW.step ? " on" : n < IW.step ? " done" : "";
    return `<span class="iwno${cls}"><i>${n < IW.step ? "✓" : n}</i>${s}</span>` + (i < steps.length - 1 ? `<span class="iwarr"></span>` : "");
  }).join("")}</div>`;
  const body = [iwStep1, iwStep2, iwStep3, iwStep4, iwStep5][IW.step - 1]();
  IW.dlg.innerHTML = `
    <div class="iwh"><h2>导入甄别数据</h2><span class="sm mut">客户 Confirmit 导出的原始数据 · 匹配键：邮箱</span>
      <button type="button" class="iwx" id="iwclose" aria-label="关闭">×</button></div>
    ${stepper}${IW.notice ? `<div class="iwnote bad" role="alert">${esc(IW.notice)}</div>` : ""}
    <div class="iwb">${body}</div>
    <div class="iwf">${iwFoot()}</div>`;
  $("#iwclose", IW.dlg).onclick = iwClose;
  iwWire();
}

function iwFoot() {
  const back = IW.step > 1 && IW.step < 5 ? `<button type="button" class="btn" id="iwback">上一步</button>` : "";
  const next = {
    1: `<button type="button" class="btn p" id="iwnext"${IW.csv ? "" : " disabled"}>下一步：映射字段</button>`,
    2: `<button type="button" class="btn p" id="iwnext">下一步：预检（不写库）</button>`,
    3: `<button type="button" class="btn p" id="iwnext">确认这批数字 →</button>`,
    4: `<button type="button" class="btn p" id="iwgo">确认导入</button>`,
    5: `<button type="button" class="btn p" id="iwdone">完成，回到名单</button>`,
  }[IW.step];
  return `<span class="sm mut">${IW.busy ? "处理中…" : ""}</span><span class="sp"></span>${IW.step < 5 ? `<button type="button" class="btn" id="iwcancel">取消</button>` : ""}${back}${next}`;
}

/* ---------------- ① 上传 ---------------- */
function iwStep1() {
  return `
    <label class="iwdrop" id="iwdrop">
      <input type="file" id="iwfile" accept=".csv,.tsv,.txt" hidden>
      <b>把 Confirmit 导出的 CSV / TSV 拖到这里，或点一下选择文件</b>
      <span class="sm mut">Data Map 固定格式 · 自动识别逗号/制表符 · 单个文件建议 20MB 以内 · 一次最多 500 行</span>
    </label>
    <label class="iwta"><span class="sm mut">也可以直接粘贴</span>
      <textarea id="iwcsv" rows="6" placeholder="Q_EMAIL,Q1_REGION,Q2_AGE&#10;lin@example.com,1,3"></textarea></label>
    <div class="iwrow"><span class="sm">已读取：</span><span class="iwsrc" id="iwsrc">${IW.csv ? esc(IW.filename || "粘贴的内容") + " · " + IW.csv.split(/\n/).length - 1 + " 行" : "还没有文件"}</span></div>`;
}

/* ---------------- ② 映射 ---------------- */
function iwStep2() {
  const cols = IW.preview ? IW.preview.columns : [];
  const sample = IW.preview ? IW.preview.sample : [];
  const targets = [["question", "题目答案"], ["name", "姓名"], ["email", "邮箱（匹配键）"], ["tags", "标签"], ["country", "国家"], ["language", "语言"]];
  const uses = [["screen", "甄别判定"], ["segment", "分群"], ["quota", "配额"], ["archive", "仅存档"]];
  const rows = cols.map((c) => {
    const s = IW.mapping[c] || { target: "question", use: "screen", question: c, values: {} };
    const isKey = s.target === "email";
    return `<tr class="${isKey ? "key" : ""}">
      <td class="v"><span class="mono">${esc(c)}</span>${isKey ? `<span class="iwkey">匹配键</span>` : ""}</td>
      <td><select class="iws" data-col="${esc(c)}" data-f="target">${targets.map(([v, n]) => `<option value="${v}"${s.target === v ? " selected" : ""}>${n}</option>`).join("")}</select></td>
      <td><input class="iwi" data-col="${esc(c)}" data-f="question" value="${esc(s.question || c)}" maxlength="300"${s.target === "question" ? "" : " disabled"}></td>
      <td><textarea class="iws2" data-col="${esc(c)}" data-f="values" rows="2" placeholder="1=广深珠…&#10;2=广东其他…"${s.target === "question" ? "" : " disabled"}>${esc(iwValuesText(s.values))}</textarea></td>
      <td><select class="iws" data-col="${esc(c)}" data-f="use"${s.target === "question" ? "" : " disabled"}>${uses.map(([v, n]) => `<option value="${v}"${s.use === v ? " selected" : ""}>${n}</option>`).join("")}</select></td>
    </tr>`;
  }).join("");
  const samples = sample.map((r) => `<tr><td class="sm mut">${r.line}</td>${cols.map((c) => `<td class="sm">${esc(r.cells[c] || "")}</td>`).join("")}</tr>`).join("");
  const savedOpts = IW.saved.length ? `<label class="iwrow"><span class="sm">用已保存的映射</span>
      <select id="iwsaved"><option value="">— 选一套 —</option>${IW.saved.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join("")}</select></label>` : "";
  return `
    <div class="iwrow"><span class="iwkey">匹配键：邮箱</span><span class="sm mut">邮箱会去空格、转小写后比较；姓名只做展示，不参与匹配。</span></div>
    ${savedOpts}
    <div class="iwmap"><table><thead><tr><th>原始变量</th><th>写入目标</th><th>题干（显示给团队/报告）</th><th>值映射（1=…，一行一个）</th><th>用途</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <details class="iwdet"><summary>看前 5 行原始数据</summary>
      <div class="tw"><table class="iwsmp"><thead><tr><th>行</th>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${samples}</tbody></table></div></details>
    <div class="iwrow"><label class="sm">把这套映射存起来（下次一键复用）</label>
      <input class="iwi" id="iwsaveas" value="${esc(IW.saveAs)}" placeholder="例如：国庆访港甄别 · Confirmit v2" maxlength="120">
      <button type="button" class="btn s" id="iwsavemap">保存映射</button></div>`;
}

/* ---------------- ③ 预检 ---------------- */
function iwStep3() {
  const r = IW.pre || {};
  const imp = r.impact || {};
  const quotas = (imp.quotas || []).map((q) => `<div class="iwq${q.over ? " over" : ""}">
      <b>${esc(q.name)}</b><span class="sm">已有 ${q.have} / 目标 ${q.target}</span>
      <span class="iwplus">+${q.add}</span><span class="sm mut">导入后 ${q.after}</span>
      ${q.over ? `<span class="iwov">溢出 ${q.over}</span>` : ""}</div>`).join("");
  const list = (rows, empty) => rows.length ? rows.map((x) => `<div class="iwli"><span class="mono">${esc(x.email || "—")}</span>
      <span class="sm mut">第 ${x.line || "?"} 行</span><span class="sm">${esc(x.why || "")}</span></div>`).join("") : `<div class="sm mut">${empty}</div>`;
  return `
    <div class="iwcards">
      <div class="iwcard g"><span class="sm">命中且已在名单</span><b>${r.matched || 0}</b><span class="sm mut">会写入答案与合格状态</span></div>
      <div class="iwcard y"><span class="sm">冲突</span><b>${r.conflicted || 0}</b><span class="sm mut">同一邮箱多条 / 与已有答案不同</span></div>
      <div class="iwcard r"><span class="sm">未匹配</span><b>${r.orphaned || 0}</b><span class="sm mut">进未匹配池，不自动建档</span></div>
      <div class="iwcard"><span class="sm">无法使用</span><b>${r.failed || 0}</b><span class="sm mut">没有邮箱 / 行内容不完整</span></div>
    </div>
    <div class="iwimpact"><b>导入后会发生什么（还没写库）</b>
      <div class="iwig">
        <div><b>${imp.to_qualified || 0}</b><span class="sm">人变成合格</span></div>
        <div><b>${imp.to_disqualified || 0}</b><span class="sm">人变成不符合</span></div>
        <div><b>${imp.unchanged || 0}</b><span class="sm">人只更新字段</span></div>
      </div>
      ${quotas ? `<div class="iwquotas"><span class="sm mut">配额变化</span>${quotas}</div>` : `<div class="sm mut">这个项目还没有配额。</div>`}
    </div>
    <details class="iwdet" ${r.orphaned ? "open" : ""}><summary>未匹配的 ${r.orphaned || 0} 条（进未匹配池）</summary>${list(r.orphan_rows || [], "没有")}</details>
    <details class="iwdet"><summary>冲突 ${r.conflicted || 0} 条</summary>${list(r.conflict_rows || [], "没有")}</details>
    <details class="iwdet"><summary>无法使用 ${r.failed || 0} 条</summary>${list(r.failed_rows || [], "没有")}</details>`;
}

/* ---------------- ④ 确认 ---------------- */
function iwStep4() {
  return `
    <label class="iwchk"><input type="checkbox" id="iwok"> <span>我已核对预检数字：写入 <b>${IW.pre.matched}</b> 人、冲突 <b>${IW.pre.conflicted}</b> 条、未匹配 <b>${IW.pre.orphaned}</b> 条进池。</span></label>
    <label class="iwrow"><span class="sm">这批数据的来源名称</span>
      <input class="iwi" id="iwsrcname" value="${esc(IW.sourceName)}" placeholder="例如：客户 A · 国庆访港甄别 v2 · 11-04" maxlength="120"></label>
    <label class="iwrow"><span class="sm">顺便保存这套映射</span>
      <input class="iwi" id="iwsaveas2" value="${esc(IW.saveAs)}" placeholder="可留空" maxlength="120"></label>
    <div class="iwnote">
      <b>写入范围</b>：只写「招募 · 受访者档案」——甄别答案、合格状态、分群标签、配额占用、批次号。<br>
      <b>不会写</b>洞察回复，也不进定量结果（甄别数据只服务"谁能进访谈"）。<br>
      <b>可回滚</b>：一次事务写入；导入后仍可整批回滚，把字段和答案恢复到导入前。
    </div>`;
}

/* ---------------- ⑤ 结果 ---------------- */
function iwStep5() {
  const b = IW.batch || {};
  return `
    <div class="iwcards">
      <div class="iwcard g"><span class="sm">已写入</span><b>${b.matched || 0}</b><span class="sm mut">受访者归档完成</span></div>
      <div class="iwcard y"><span class="sm">冲突</span><b>${b.conflicted || 0}</b><span class="sm mut">已按规则写入，可人工核对</span></div>
      <div class="iwcard r"><span class="sm">进未匹配池</span><b>${b.orphaned || 0}</b><span class="sm mut">没有自动建档</span></div>
      <div class="iwcard"><span class="sm">无法使用</span><b>${b.failed || 0}</b><span class="sm mut">已进池，可核对原始行</span></div>
    </div>
    <div class="iwrow"><span class="sm">批次</span><span class="mono">${esc((b.id || "").slice(0, 8))}</span>
      <span class="sm mut">${esc(b.filename || "")} · ${esc(b.source_name || "")} · ${b.rows || 0} 行</span></div>
    <div class="iwnexts">
      <button type="button" class="btn" id="iworthan">处理未匹配（${b.orphaned || 0}）</button>
      <button type="button" class="btn" id="iwundobatch">整批回滚</button>
      <span class="sm mut">回滚会把这次写入的答案和状态全部恢复成导入前。</span>
    </div>`;
}

/* ---------------- 交互 ---------------- */
function iwWire() {
  const d = IW.dlg;
  if (IW.step === 1) {
    const file = $("#iwfile", d), ta = $("#iwcsv", d);
    const setCsv = (text, name) => {
      IW.csv = text; IW.filename = name || "";
      const src = $("#iwsrc", d);
      if (src) src.textContent = text ? `${name || "粘贴的内容"} · ${Math.max(0, text.split(/\n/).filter((x) => x.trim()).length - 1)} 行` : "还没有文件";
      const nx = $("#iwnext", d); if (nx) nx.disabled = !text.trim();
    };
    file.onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      setCsv(await f.text(), f.name);
    };
    ta.oninput = () => setCsv(ta.value, "");
    const drop = $("#iwdrop", d);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("on"); };
    drop.ondragleave = () => drop.classList.remove("on");
    drop.ondrop = async (e) => { e.preventDefault(); drop.classList.remove("on"); const f = e.dataTransfer.files[0]; if (f) setCsv(await f.text(), f.name); };
  }
  if (IW.step === 2) {
    d.querySelectorAll(".iws,.iwi,.iws2").forEach((el) => {
      el.onchange = () => {
        const col = el.dataset.col, f = el.dataset.f;
        const spec = IW.mapping[col] || (IW.mapping[col] = { target: "question", use: "screen", question: col, values: {} });
        if (f === "values") spec.values = iwParseValues(el.value);
        else if (f === "target" && el.value === "email") {
          Object.keys(IW.mapping).forEach((k) => { if (IW.mapping[k].target === "email") IW.mapping[k] = { ...IW.mapping[k], target: "question", use: "screen" }; });
          spec.target = "email"; spec.use = "archive";
        } else if (f === "target") {
          spec.target = el.value;
          if (el.value !== "question") spec.use = "archive";
        } else spec[f] = el.value;
        iwRender();
      };
    });
    const sv = $("#iwsaved", d);
    if (sv) sv.onchange = () => {
      const item = IW.saved[Number(sv.value)];
      if (item) { IW.mapping = JSON.parse(JSON.stringify(item.mapping)); iwRender(); }
    };
    $("#iwsavemap", d).onclick = guard(async () => {
      const name = ($("#iwsaveas", d).value || "").trim();
      if (!name) throw new Error("给这套映射起个名字");
      await api(`/projects/${IW.p.id}/recruiting/import/mappings`, { method: "PUT", body: { name, mapping: iwCollectMapping() } });
      toast("映射已保存，下次可以直接选");
      IW.saveAs = name;
      const r = await api(`/projects/${IW.p.id}/recruiting/import/mappings`);
      IW.saved = r.mappings || [];
      iwRender();
    });
  }
  if (IW.step === 4) {
    const ok = $("#iwok", d), go = $("#iwgo", d);
    ok.onchange = () => { go.disabled = !ok.checked; };
    go.disabled = !ok.checked;
    go.onclick = guard(iwNext);
    $("#iwsrcname", d).oninput = (e) => { IW.sourceName = e.target.value; };
    $("#iwsaveas2", d).oninput = (e) => { IW.saveAs = e.target.value; };
  }
  if (IW.step === 5) {
    $("#iworthan", d).onclick = () => { iwClose(); openOrphanPool(IW.p); };
    $("#iwundobatch", d).onclick = guard(async () => {
      await impRollback(IW.p, IW.batch.id);
      iwClose();
    });
  }
  const back = $("#iwback", d); if (back) back.onclick = () => { IW.step -= 1; IW.notice = ""; iwRender(); };
  const cancel = $("#iwcancel", d); if (cancel) cancel.onclick = iwClose;
  const done = $("#iwdone", d); if (done) done.onclick = () => { iwClose(); render(); };
  const next = $("#iwnext", d); if (next) next.onclick = guard(iwNext);
}

function iwCollectMapping() {
  const out = {};
  Object.entries(IW.mapping).forEach(([col, s]) => {
    out[col] = { target: s.target, use: s.use, question: s.question || col, values: s.values || {} };
  });
  return out;
}

async function iwNext() {
  if (IW.busy) return;
  IW.busy = true; IW.notice = "";
  try {
    if (IW.step === 1) {
      const r = await api(`/projects/${IW.p.id}/recruiting/import/preview`, { method: "POST", body: { csv: IW.csv } });
      IW.preview = r; IW.mapping = r.mapping || {}; IW.saved = r.saved_mappings || [];
      IW.step = 2;
    } else if (IW.step === 2) {
      const r = await api(`/projects/${IW.p.id}/recruiting/import/precheck`, { method: "POST", body: { csv: IW.csv, mapping: iwCollectMapping() } });
      IW.pre = r; IW.step = 3;
    } else if (IW.step === 3) {
      IW.step = 4;
    } else if (IW.step === 4) {
      const ok = $("#iwok", IW.dlg);
      if (!ok || !ok.checked) throw new Error("请先勾选确认预检数字");
      const r = await api(`/projects/${IW.p.id}/recruiting/import/commit`, { method: "POST",
        body: { csv: IW.csv, mapping: iwCollectMapping(), confirm: true, filename: IW.filename || "粘贴的内容",
                source_name: ($("#iwsrcname", IW.dlg).value || "").trim(), save_mapping_as: ($("#iwsaveas2", IW.dlg).value || "").trim() } });
      IW.batch = r.batch; IW.step = 5;
      toast(`已导入：写入 ${r.batch.matched} 人，未匹配 ${r.batch.orphaned} 条`);
    }
  } finally {
    IW.busy = false;
    iwRender();
  }
}

/* ---------------- 未匹配池 ---------------- */
async function openOrphanPool(p) {
  const [o, parts] = await Promise.all([api(`/projects/${p.id}/recruiting/import/orphans`), api(`/projects/${p.id}/participants`)]);
  const people = parts.filter((x) => !x.anonymized);
  const m = modal(``);
  const dlg = m.firstElementChild;
  dlg.classList.add("iwdlg");
  const list = o.orphans || [];
  dlg.innerHTML = `
    <div class="iwh"><h2>未匹配池</h2><span class="sm mut">${list.length} 条待处理 · 不自动建档，等人处理</span>
      <button type="button" class="iwx" id="opx" aria-label="关闭">×</button></div>
    <div class="iwb">
      ${list.length ? `<div class="iwmap"><table><thead><tr><th>邮箱</th><th>原因</th><th>收到的原始值</th><th>处理</th></tr></thead><tbody>
        ${list.map((o) => `<tr>
          <td class="mono sm">${esc(o.email || "（空）")}</td>
          <td class="sm reason">${esc(o.reason || "")}</td>
          <td class="sm mut">${esc((o.answers || []).map((a) => a[0] + "=" + a[1]).join("；").slice(0, 120) || "—")}</td>
          <td class="nowrap"><select class="iws" data-op="${o.id}"><option value="">— 挂到名单里的人 —</option>
            ${people.map((x) => `<option value="${x.id}">${esc(x.code)} ${esc(x.name)}${x.email ? " · " + esc(x.email) : ""}</option>`).join("")}</select>
            <button type="button" class="btn s warn" data-opign="${o.id}">忽略</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty"><b>没有待处理的未匹配数据。</b><span>导入时邮箱对不上的行会出现在这里。</span></div>`}
      <div class="iwnote">挂接会把这条原始数据的答案写进你选的人；原始行保留，事后仍能追溯。如果是名单里本来就没有的人，应该走「导入名单（外部渠道）」建档，再回来挂。</div>
    </div>
    <div class="iwf"><span class="sp"></span><button type="button" class="btn p" id="opdone">关闭</button></div>`;
  const closePool = () => { m.remove(); render(); };   // 关闭后刷新名单：挂接/忽略都会改数据
  $("#opx", dlg).onclick = closePool;
  $("#opdone", dlg).onclick = closePool;
  dlg.querySelectorAll("[data-op]").forEach((sel) => {
    sel.onchange = guard(async () => {
      if (!sel.value) return;
      await api(`/projects/${p.id}/recruiting/import/orphans/${sel.dataset.op}/attach`, { method: "POST", body: { participant_id: sel.value } });
      toast("已把这条数据的答案写进名单里的人");
      m.remove(); openOrphanPool(p);
    });
  });
  dlg.querySelectorAll("[data-opign]").forEach((b) => {
    b.onclick = guard(async () => {
      await api(`/projects/${p.id}/recruiting/import/orphans/${b.dataset.opign}/ignore`, { method: "POST" });
      toast("已标记忽略，原始行保留");
      m.remove(); openOrphanPool(p);
    });
  });
}

/* ---------------- 名单页上的批次条 ---------------- */
function impBatchBar(batches, orphansPending) {
  const last = (batches || []).find((b) => !b.rolled_back) || (batches || [])[0];
  if (!last && !orphansPending) return "";
  const when = (last.created_at || "").replace("T", " ").slice(5, 16);
  return `<div class="iwbar">
    ${last ? `<span class="badge ${last.rolled_back ? "grey" : "info"}">${last.rolled_back ? "已回滚" : "最近导入"}</span>
      <span class="sm">${esc(when)} · ${esc(last.filename || "未命名")} · ${last.rows} 行 → 命中 <b>${last.matched}</b> · 冲突 <b>${last.conflicted}</b> · 未匹配 <b>${last.orphaned}</b> · ${esc(last.actor || "")}</span>`
      : `<span class="sm mut">还没有导入过甄别数据。</span>`}
    <span class="sp"></span>
    ${orphansPending ? `<button type="button" class="btn s" data-opopen>未匹配池（${orphansPending}）</button>` : ""}
    ${last && !last.rolled_back ? `<button type="button" class="btn s warn" data-opundo="${last.id}">整批回滚</button>` : ""}
  </div>`;
}

async function impRollback(p, batchId) {
  const r = await api(`/projects/${p.id}/recruiting/import/batches/${batchId}/rollback`, { method: "POST" });
  toast(`已回滚：恢复 ${r.restored} 人，清掉 ${r.orphans_dropped} 条未匹配`);
  render();
  return r;
}
