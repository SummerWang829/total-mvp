/* 招募问卷工作台的“语言版本”：右上角一个“版本”下拉，大纲和测试答题共用，切到哪个版本两边一起变。
   默认版本是原文（研究员写的语言）；项目语言的版本自动生成、不加任何标签（受访者看到的就是它们）；团队自己需要但项目没选的语言用“＋ 添加语言版本…”加，
   标“仅团队”，受访者永远收不到。改了原文某一题，其他版本顶上提示“译文待更新”，点“更新译文”重新翻译；过期的版本不会给受访者看。
   翻译只换文字，跳题、筛选、标签、配额都按选项走，和语言无关。依赖 index.html 里的 api / esc / toast / guard / $。 */
const RT_NAMES = { zh: "中文", en: "English", ms: "马来语", ta: "泰米尔语" };

/** 各版本的状态：{ source, languages: [{ code, name, source, team_only, state, ready, total, stale, missing, machine, confirmed }], items, ai_local, ai_vendor }；还没有问卷时返回 null。 */
async function rtVersions(p) { return api(`/projects/${p.id}/recruiting/form/translations`).catch(() => null); }

/** 版本下拉的菜单 HTML：原文、项目语言的版本、仅团队的版本、“＋ 添加语言版本…”。 */
function rtMenuHtml(d, cur) {
  const row = (x) => `<button type="button" class="rf-vi${x.code === cur ? " on" : ""}" role="menuitemradio" aria-checked="${x.code === cur}" data-ver="${x.code}"><span>${esc(x.name)}${x.source ? "（原文）" : ""}</span>${x.team_only ? `<span class="rt-st o">仅团队</span>` : ""}${!x.source && !x.ready ? `<span class="rt-st bad">${x.stale ? "译文待更新" : "未翻译"}</span>` : ""}</button>`;
  const have = new Set(d.languages.map((x) => x.code)), more = Object.keys(RT_NAMES).filter((c) => !have.has(c));
  return d.languages.map(row).join("") + (more.length ? `<div class="rf-vsep"></div><button type="button" class="rf-vi add" data-vadd="1" aria-expanded="false">＋ 添加语言版本…</button><div class="rf-vmore" hidden>${more.map((c) => `<button type="button" class="rf-vi" data-vnew="${c}"><span>${RT_NAMES[c]}</span><span class="rt-st o">仅团队</span></button>`).join("")}</div>` : "");
}

/** 某个非原文版本的编辑页：顶上的状态条（待更新/仅团队）+ 每道题的译文，返回 { html, wire(done) }；`done` 在有变化后被调用。 */
function rtBuild(p, questions, lang, d) {
  const v = d.languages.find((x) => x.code === lang) || { name: RT_NAMES[lang] || lang, stale: 0, missing: 0, ready: false, team_only: false };
  const items = d.items[lang] || {};
  const notice = d.ai_local ? "" : `<div class="sm mut" style="margin:6px 0">翻译会把问卷的文字发送给 AI 服务商${d.ai_vendor ? `“${esc(d.ai_vendor)}”` : ""}。请不要在问卷里写入个人信息。</div>`;
  const need = v.stale + v.missing;
  const bar = `${v.team_only ? `<div class="rf-warn" role="note" style="background:var(--wash);color:var(--ink2)">这是“仅团队”的版本，只给你们自己看和试答，受访者永远收不到。</div>` : ""}
    ${need ? `<div class="rf-warn" role="status">${v.stale ? `原文改过，${v.stale} 题译文待更新` : ""}${v.stale && v.missing ? "；" : ""}${v.missing ? `${v.missing} 题还没有译文` : ""}。${v.team_only ? "" : "更新之前，受访者暂时看不到这个语言。"}<span class="sp"></span><button type="button" class="p" id="rtupd">更新译文</button></div>` : ""}${notice}`;
  const qCard = (q, i) => {
    const e = items[q.id] || {}, st = e.state || "missing";
    const opts = (q.options || []).map((o) => `<div class="rt-o"><span class="mut">${esc(o.text)}</span><input data-ro="${q.id}|${o.id}" value="${esc(((e.options || {})[o.id]) || "")}" maxlength="120" aria-label="第 ${i + 1} 题选项“${esc(o.text)}”的译文" placeholder="译文"></div>`).join("");
    return `<div class="rf-q" data-rq="${q.id}"><div class="row"><span class="rf-n">${i + 1}</span><b>${esc(q.text)}</b><span class="sp"></span>${st === "stale" ? `<span class="rt-st bad">译文待更新</span>` : st === "missing" ? `<span class="rt-st o">未翻译</span>` : ""}</div>
      <input data-rt="${q.id}" value="${esc(e.text || "")}" maxlength="300" aria-label="第 ${i + 1} 题的译文" placeholder="译文">${opts}<div class="row"><button type="button" class="s" data-rsave="${q.id}">保存这一题</button></div></div>`;
  };
  const html = `<div class="row" style="margin:0 0 6px"><b>${esc(v.name)}</b><span class="sm mut">${questions.length} 题</span></div>${bar}${questions.map(qCard).join("")}`;
  const wire = (done) => {
    const call = (path, body, method = "POST") => api(`/projects/${p.id}/recruiting/form/${path}`, { method, body });
    const upd = $("#rtupd"); if (upd) upd.onclick = guard(async () => { upd.disabled = true; upd.textContent = "正在翻译…"; try { done(await call("translate-all", { stale: true })); } finally { upd.disabled = false; } });
    document.querySelectorAll("[data-rsave]").forEach((b) => (b.onclick = guard(async () => {
      const id = b.dataset.rsave, box = document.querySelector(`[data-rq="${id}"]`), text = box.querySelector(`[data-rt="${id}"]`).value.trim(), options = {};
      box.querySelectorAll("[data-ro]").forEach((i) => { options[i.dataset.ro.split("|")[1]] = i.value.trim(); });
      if (!text || Object.values(options).some((x) => !x)) throw new Error("题目和每个选项都要有译文");
      done(await call(`translations/${lang}/${id}`, { text, options }, "PUT")); toast("已保存");
    })));
  };
  return { html, wire };
}
