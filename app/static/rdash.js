/* “配额与受访者名单”页顶部的看板：问卷发布（链接和二维码）、一眼看数据、漏斗、人从哪里来、人在哪一题被筛掉、配额进度、合格的人的共同点、
   合格和被筛掉的每题对比。数字来自 GET /projects/{id}/recruiting/dashboard；被筛掉的人只有匿名的选项人数，没有任何个人信息。
   依赖 index.html 里的 api / esc / toast / guard / $ / store / render。 */
const rdPct = (a, b) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0);
const rdCell = (n, cls) => `<span class="rd-t" role="img" aria-label="${n}%"><i class="${cls}" style="width:${rdPct(n, 100)}%"></i></span><b class="rd-p">${n}%</b>`;
const rdBar = (label, n, max, cls = "", suffix = "") => `<div class="rd-bar"><span title="${esc(label)}">${esc(label)}</span><span class="rd-t" role="img" aria-label="${esc(label)} ${n}"><i class="${cls}" style="width:${rdPct(n, max)}%"></i></span><b>${n}${suffix}</b></div>`;

/** 一条配额：已有 = 正式 + 备份，超出目标的部分单独标出来；进度条绿色是正式，橙黄色是备份。 */
const rdQuota = (q) => {
  const denom = Math.max(q.target, q.have, 1);
  return `<div class="rd-q2"><div class="rd-ql"><b>${esc(q.name)}</b><span class="sm mut">已有 ${q.have} / 目标 ${q.target}${q.backup ? ` · 正式 ${q.formal} · 备份 ${q.backup}` : ""}</span>${q.over ? `<span class="rd-over">溢出 ${q.over}</span>` : q.full ? `<span class="rd-full">已满额</span>` : ""}</div>
    <div class="rd-t rd-stack" role="img" aria-label="${esc(q.name)}：已有 ${q.have}，目标 ${q.target}，溢出 ${q.over}"><i class="g" style="width:${rdPct(q.formal, denom)}%"></i><i class="y2" style="width:${rdPct(q.backup, denom)}%"></i></div></div>`;
};

/** 可折叠的一块：标题 + 收起时的一句摘要 + 固定在最右边的箭头；展开时摘要隐藏、箭头翻转，箭头位置不动。 */
const rdSection = (id, title, summary, body, open) => `<section class="rd-sec" data-sec="${id}"><div class="rd-sh"><b>${title}</b><span class="rd-sum" ${open ? 'style="visibility:hidden"' : ""}>${summary}</span>
  <button class="rd-arr" type="button" aria-expanded="${open}" aria-controls="rdb-${id}" aria-label="展开或收起：${title}"></button></div><div class="rd-sb" id="rdb-${id}" ${open ? "" : "hidden"}>${body}</div></section>`;
const rdOpenSet = () => { try { return new Set(JSON.parse(sessionStorage.getItem("rd_open") || "[]")); } catch { return new Set(); } };

/** 看板的 HTML 和事件；返回 { html, wire }。`rec` 是 /recruiting 的返回（配额编辑还用它）。 */
async function rdBuild(p, rec) {
  const d = await api(`/projects/${p.id}/recruiting/dashboard`), k = d.kpi, f = d.form || {}, live = f.status === "published", n = f.stats || { qualified: 0, out: 0 };
  const maxF = Math.max(1, ...d.funnel.map((x) => x.n)), maxS = Math.max(1, ...d.sources.map((x) => x.n)), maxO = Math.max(1, ...d.out_by.map((x) => x.n));
  const publish = f.link
    ? `<div class="rd-pub"><div><div class="sm mut">${live ? "候选人通过这个链接或二维码作答" : "已停止收集（到“招募问卷”页点“重新发布”，链接不变）"}</div><input id="rdlink" readonly value="${esc(location.origin + f.link)}" aria-label="问卷链接">
        <div class="row" style="margin-top:6px"><button class="s" type="button" id="rdcopy">复制链接</button><a class="btn s" href="${esc(f.link)}" target="_blank" rel="noopener">打开答题页</a>${live ? `<button class="s" type="button" id="rdclose">停止收集</button>` : ""}</div></div><div class="rd-qrc"><img id="rdqr" alt="问卷二维码" width="108" height="108"><button class="s" type="button" id="rdcopyqr">复制二维码</button><span class="sm rd-ok" id="rdqrok" role="status" aria-live="polite"></span></div></div>`
    : `<div class="empty" style="padding:14px"><b>${d.has_form ? "问卷还没有发布" : "还没有招募问卷"}</b><span>到“招募问卷”页对话编写、测试，点“发布成链接和二维码”，链接和二维码就出现在这里。</span><button class="s" type="button" id="rdgo">去招募问卷</button></div>`;
  const kpi = (v, l, cls = "") => `<div class="rd-k ${cls}"><b>${v}</b><span>${l}</span></div>`;
  const quotas = rec.quotas.length ? rec.quotas.map(rdQuota).join("") : `<p class="sm mut" style="margin:0">还没有配额。例如“新加坡 10 人”“女性 5 人”。</p>`;
  const rules = d.rules.count ? `<div class="sm" style="margin-top:6px"><button class="lnk" type="button" id="rules">旧版资格规则（${d.rules.count} 条）· 编辑或清空</button> <button class="lnk" type="button" id="reapply">重新筛一遍</button></div>` : "";
  const traits = (xs, cls, none) => (xs.length ? xs.map((t) => `<span class="rd-chip ${cls}">${esc(t)}</span>`).join("") : `<span class="sm mut">${none}</span>`);
  const TYPE = { single: "单选", multi: "多选" };
  const cmp = d.compare.length ? `<table class="rd-cmp"><colgroup><col style="width:55%"><col style="width:15%"><col style="width:15%"><col style="width:15%"></colgroup><thead><tr><th>题目 / 选项</th><th>合格</th><th>被筛掉</th><th>差异</th></tr></thead><tbody>${d.compare.map((r) => `<tr><td><div class="rd-q" tabindex="0" data-full="${esc(r.question)}" data-type="${TYPE[r.type] || ""}" data-opt="${esc(r.option)}"><div class="rd-qa"><span class="rd-qt">${esc(r.question)}</span>${TYPE[r.type] ? `<span class="rd-ty">${TYPE[r.type]}</span>` : ""}</div><div class="rd-qo">${esc(r.option)}</div></div></td>
      <td>${rdCell(r.qualified, "g")}</td><td>${rdCell(r.out, "r")}</td><td><span class="rd-chip">差 ${r.gap} 点</span></td></tr>`).join("")}</tbody></table><div class="rd-tip" id="rdtip" role="tooltip" hidden></div>
      <div class="sm mut" style="margin-top:6px">差异越大，说明这个条件越能区分“合格”和“不合格”；也能看出投放该往哪类人群去。鼠标移到题目上（手机上点一下）可以看完整内容。被筛掉的人只统计选了哪个选项的人数，不保存个人信息。</div>`
    : `<p class="sm mut" style="margin:0">还没有足够的数据：有人答完问卷后，这里会对比合格和被筛掉的人在每题上的答案。</p>`;
  const traitSum = () => `合格者共同点：${d.traits.qualified.length ? esc(d.traits.qualified[0]) : "暂无"}`;
  const updateQuotas = (qs) => { const box = $("#rdquotas"); if (box) box.innerHTML = qs.length ? qs.map(rdQuota).join("") : `<p class="sm mut" style="margin:0">还没有配额。例如“新加坡 10 人”“女性 5 人”。</p>`; };
  const opened = rdOpenSet();
  const html = `<div class="rd">
    <div class="rd-row rd-top"><div class="card"><h3>问卷发布 ${live ? `<span class="tag ok">发布中</span>` : f.link ? `<span class="tag">已停止</span>` : ""}</h3>${publish}</div>
      <div class="card"><h3>一眼看数据</h3><div class="rd-kpis">${kpi(k.started, "开始作答")}${kpi(k.finished, "完成甄别（甄别人数）")}${kpi(k.qualified, "合格（问卷）", "ok")}${kpi(k.pass_rate + "%", "通过率（合格 ÷ 完成）", "warn")}${kpi(k.external, "外部名单导入（直接可邀请）")}${kpi(k.usable, `可邀请总数 · 已邀请 ${k.invited} · 完成访谈 ${k.done}`)}</div></div>
      <div class="card"><h3>配额进度 <button class="lnk" id="quotas" type="button">编辑配额</button></h3><div id="rdquotas">${quotas}</div><div class="rd-legend"><span class="a">正式</span><span class="b">备份（超出目标的部分）</span></div>${rules}</div></div>
    <div class="rd-all"><button type="button" class="s" id="rdall"></button></div>
    ${rdSection("funnel", "漏斗与来源", `漏斗 开始 ${k.started} → 完成 ${k.finished} → 合格 ${k.qualified} → 已邀请 ${k.invited} · 来源 ${d.sources.length ? d.sources.map((x) => `${esc(x.name)} ${x.n}`).join("、") : "暂无"} · 被筛掉 ${d.out_by.reduce((s, x) => s + x.n, 0)} 人`,
      `<div class="rd-row rd-three"><div class="card"><h3>漏斗：从开始到访谈</h3>${d.funnel.map((x, i) => rdBar(x.label, x.n, maxF, i === 3 ? "s" : i >= 2 ? "g" : "", i === 1 && k.started ? ` · ${rdPct(x.n, k.started)}%` : "")).join("")}</div>
      <div class="card"><h3>人从哪里来（按来源名称）</h3>${d.sources.length ? d.sources.map((x, i) => rdBar(x.name, x.n, maxS, ["", "s", "y", "g", "r"][i % 5])).join("") : `<p class="sm mut" style="margin:0">还没有受访者。</p>`}</div>
      <div class="card"><h3>人在哪一题被筛掉</h3>${d.out_by.length ? d.out_by.map((x) => rdBar(x.label, x.n, maxO, "r", " 人")).join("") + `<div class="sm mut">卡得最多的一题 = 条件最严的一题，可以回去看这题是否太严。</div>` : `<p class="sm mut" style="margin:0">还没有人被筛掉。</p>`}</div></div>`, opened.has("funnel"))}
    ${rdSection("quota", "人群画像", traitSum(),
      `<div class="rd-row rd-two"><div class="card"><h3>合格的人有什么共同点</h3><div>${traits(d.traits.qualified, "g", "有 3 个以上的人答完后才能看出共同点。")}</div>
        <div class="sm" style="margin-top:8px">被筛掉的人的共同点：</div><div>${traits(d.traits.out, "r", "暂时看不出。")}</div>
        <div style="margin-top:8px"><button class="p" type="button" id="rdsum">让 AI 总结人群画像</button> <span class="sm mut">只发统计数字，不含个人信息${CONFIG && !CONFIG.ai_local ? `；会发送给 AI 服务商${CONFIG.ai_vendor ? `“${esc(CONFIG.ai_vendor)}”` : ""}` : ""}</span></div><div class="note" id="rdsumout" role="status" aria-live="polite" hidden></div></div></div>`, opened.has("quota"))}
    ${rdSection("compare", "合格 vs 被筛掉：每题的答案分布对比", d.compare.length ? `共 ${d.compare.length} 项，差异最大 ${Math.max(...d.compare.map((r) => r.gap))} 点` : "暂无数据", `<div class="card">${cmp}</div>`, opened.has("compare"))}</div>`;
  const wire = () => {
    const go = $("#rdgo"); if (go) go.onclick = () => { sessionStorage.setItem("ptab", "form"); render(); };
    const cl = $("#rdclose"); if (cl) cl.onclick = guard(async () => { await api(`/projects/${p.id}/recruiting/form/close`, { method: "POST" }); toast("已停止收集"); render(); });
    const cp = $("#rdcopy"); if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText($("#rdlink").value); toast("链接已复制"); } catch { $("#rdlink").select(); toast("请按 Ctrl+C 复制", true); } };
    const img = $("#rdqr"); if (img && f.link) fetch(`/api/projects/${p.id}/recruiting/form/qr.svg`, { headers: { Authorization: "Bearer " + store.get("tt_token") } }).then(async (r) => { if (r.ok) img.src = URL.createObjectURL(await r.blob()); }).catch(() => {});
    const arrows = [...document.querySelectorAll(".rd-arr")], all = $("#rdall");
    const sync = () => { all.textContent = arrows.every((b) => b.getAttribute("aria-expanded") === "true") ? "全部收起" : "全部展开"; };
    const setOpen = (b, open) => { b.setAttribute("aria-expanded", String(open)); const sec = b.closest(".rd-sec"); $(".rd-sb", sec).hidden = !open; $(".rd-sum", sec).style.visibility = open ? "hidden" : "visible"; };
    const save = () => sessionStorage.setItem("rd_open", JSON.stringify(arrows.filter((b) => b.getAttribute("aria-expanded") === "true").map((b) => b.closest(".rd-sec").dataset.sec)));
    arrows.forEach((b) => (b.onclick = () => { setOpen(b, b.getAttribute("aria-expanded") !== "true"); save(); sync(); }));
    all.onclick = () => { const open = all.textContent === "全部展开"; arrows.forEach((b) => setOpen(b, open)); save(); sync(); };
    sync();
    const tip = $("#rdtip");
    if (tip) document.querySelectorAll(".rd-q").forEach((q) => {
      const show = () => {
        tip.innerHTML = `${esc(q.dataset.full)}${q.dataset.type ? ` <span class="rd-ty">${q.dataset.type}</span>` : ""}<br><b>选项：${esc(q.dataset.opt)}</b>`; tip.hidden = false;
        const host = tip.offsetParent || tip.parentElement, a = q.getBoundingClientRect(), h = host.getBoundingClientRect();
        tip.style.left = Math.max(0, a.left - h.left) + "px"; tip.style.top = a.bottom - h.top + 4 + "px";
      };
      q.onmouseenter = q.onfocus = q.onclick = show; q.onmouseleave = q.onblur = () => { tip.hidden = true; };
    });
    const cq = $("#rdcopyqr"); if (cq) cq.onclick = async () => {
      const ok = $("#rdqrok"), say = (m) => { ok.textContent = m; setTimeout(() => { ok.textContent = ""; }, 3000); };
      try {
        if (!img.complete || !img.naturalWidth) throw new Error("二维码还在加载");
        const cv = document.createElement("canvas"); cv.width = cv.height = 512; const g = cv.getContext("2d");
        g.fillStyle = "#fff"; g.fillRect(0, 0, 512, 512); g.drawImage(img, 24, 24, 464, 464);   // a white margin round the code so a scanner can read it
        const blob = await new Promise((res, rej) => cv.toBlob((b) => (b ? res(b) : rej(new Error("no image"))), "image/png"));
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        say("已复制二维码，可以直接粘贴");
      } catch { say("这个浏览器不让复制图片，请右键二维码另存为"); }
    };
    const sum = $("#rdsum"); if (sum) sum.onclick = guard(async () => {
      sum.disabled = true; const out = $("#rdsumout"); out.hidden = false; out.textContent = "正在总结，大约要几秒…";
      try { out.textContent = (await api(`/projects/${p.id}/recruiting/dashboard/summary`, { method: "POST" })).summary; } catch (e) { out.hidden = true; throw e; } finally { sum.disabled = false; }
    });
  };
  return { html, wire, updateQuotas };
}
