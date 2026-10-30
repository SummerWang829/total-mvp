/* 邀请函：名单里“发送邀请函 / 更新邀请函”的窗口，和“批量邀请”。邮件由服务器发出；发不出去时页面右上角弹一条提醒，不挡其他操作。
   依赖 index.html 里的 api / esc / modal / toast / guard / langOpts / ROLE / CONFIG / showInvite / showGroupInvites。 */
const EMAIL_OK = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const z2 = (n) => String(n).padStart(2, "0");
const toLocalInput = (iso) => { if (!iso) return ""; const d = new Date(iso + "Z"); return `${d.getFullYear()}-${z2(d.getMonth() + 1)}-${z2(d.getDate())}T${z2(d.getHours())}:${z2(d.getMinutes())}`; };
const toIsoUtc = (local) => (local ? new Date(local).toISOString().replace("Z", "") : null);
const timeLabel = (iso) => { const d = new Date(iso + "Z"); return `${d.getMonth() + 1}/${d.getDate()} ${WEEK[d.getDay()]} ${z2(d.getHours())}:${z2(d.getMinutes())}`; };

/** A notice in the top right corner that blocks nothing; it closes itself after a while. kind: ok | bad */
function notice(html, kind = "ok", ms = 10000) {
  document.querySelectorAll(".ntc").forEach((n) => n.remove());
  const n = document.createElement("div"); n.className = `ntc ${kind}`; n.setAttribute("role", kind === "bad" ? "alert" : "status");
  n.innerHTML = `<span class="x" role="button" aria-label="关闭">×</span>${html}`;
  document.body.appendChild(n); n.querySelector(".x").onclick = () => n.remove(); setTimeout(() => n.remove(), ms);
}
/** Say how sending the email went. */
function mailNote(name, mail) {
  if (!mail) return;
  if (mail.status === "queued") notice(`邮件服务已接受发送给 ${esc(name)} 的邀请函。`, "ok");
  else notice(`<b>邀请函已创建，但邮件没有发出去</b><br>${esc(mail.reason)}。其他操作不受影响。`, "bad");
}

/** The invitation window. `itv` = an existing session (update it); otherwise a new one for `preselect`. `onDone` runs after a save or a send. */
async function inviteModal(p, parts, { preselect = "", itv = null, onDone = () => {} } = {}) {
  const users = await api("/users");
  const mods = users.filter((u) => ["moderator", "researcher", "admin"].includes(u.role)), obs = users.filter((u) => u.role === "observer");
  const el = parts.filter((x) => x.screening === "eligible" && !x.anonymized);
  const editing = !!itv, who = (editing ? parts.find((x) => x.id === itv.participant.id) : parts.find((x) => x.id === preselect)) || null;
  const times = who && who.availability ? who.availability : [];
  let main = editing ? itv.scheduled_at || "" : times[0] || "";
  let backups = times.filter((a) => a !== main);
  const mailReady = !!(window.CONFIG && CONFIG.mail_ready);
  // the interview language and format: only what the project allows; what the candidate chose in the questionnaire fills them in (the first is the one to book, the rest are backups)
  const info = await api(`/projects/${p.id}/languages`).catch(() => null);
  const FMT = (info && info.format_names) || { video: "视频" }, projLangs = info ? info.languages : Object.keys(LANGS);
  const fmtFor = (kind) => ((info && info.formats && info.formats[kind === "group" ? "group" : "depth"]) || Object.keys(FMT)).filter((k) => FMT[k]);
  const fmtOpts = (kind, cur) => fmtFor(kind).map((k) => `<option value="${k}"${k === cur ? " selected" : ""}>${esc(FMT[k])}</option>`).join("");
  const lgOpts = (cur) => (editing ? "" : `<option value="">同受访者</option>`) + projLangs.map((k) => `<option value="${k}"${k === cur ? " selected" : ""}>${esc(LANGS[k] || k)}</option>`).join("");
  const wantFmt = editing ? itv.format || "" : (who && who.pref_formats && who.pref_formats[0]) || "";
  const wantLg = editing ? itv.language : (who && who.pref_langs && who.pref_langs[0]) || "";
  const m = modal(`<div class="invdlg"><h2>邀请函</h2><form id="f">
    ${editing ? "" : `<label class="f"><span>场次类型</span><select name="kind" id="kind"><option value="depth">深访（一对一）</option><option value="group">座谈会（最多 10 人，人工主持）</option></select></label>`}
    <div id="depthpick"><div class="grid2"><label class="f"><span>受访者</span><select name="participant_id"${editing ? " disabled" : ""}>${(editing ? [who].filter(Boolean) : el).map((x) => `<option value="${x.id}"${x.id === (who && who.id) ? " selected" : ""}>${esc(x.code)} · ${esc(x.name)} (${esc(x.country)})</option>`).join("")}</select></label>
      <label class="f"><span>访谈语言</span><select name="language" id="lg">${lgOpts(wantLg)}</select></label></div></div>
    <div class="grid2"><label class="f"><span>访谈形式</span><select name="format" id="fm">${fmtOpts(editing ? itv.kind || "depth" : "depth", wantFmt)}</select></label></div><div class="tmchips" id="pfc"></div>
    <fieldset id="grouppick" hidden style="border:1px solid var(--line);border-radius:10px;margin:0 0 14px;padding:6px 14px"><legend class="sm mut">座谈会受访者（勾选 1 到 10 位）</legend>${el.map((x) => `<label class="chk"><input type="checkbox" name="pids" value="${x.id}"${x.id === preselect ? " checked" : ""}><span>${esc(x.code)} · ${esc(x.name)}</span></label>`).join("")}</fieldset>
    <div class="tmbox"><label class="f" style="margin:0"><span>时间</span><input name="scheduled_at" id="tm" type="datetime-local" value="${toLocalInput(main)}"></label><div class="tmchips" id="tmc"></div></div>
    <label class="f" id="emailbox"><span>受访者邮箱</span><input name="email" id="em" type="text" maxlength="200" autocomplete="off" value="${esc(who ? who.email || "" : "")}"><div class="emsg" id="emsg" role="status"></div></label>
    <div class="grid2"><label class="f"><span>谁来主持</span><select name="moderation" id="how"><option value="human"${editing && itv.moderation !== "ai" ? " selected" : ""}>真人主持人</option><option value="ai"${editing && itv.moderation === "ai" ? " selected" : ""}>AI 主持人</option></select></label>
      <label class="f"><span>主持人</span><select name="moderator_id"><option value="">稍后指定</option>${mods.map((u) => `<option value="${u.id}"${editing && itv.moderator_id === u.id ? " selected" : ""}>${esc(u.name)}（${ROLE[u.role]}）</option>`).join("")}</select></label></div>
    <label class="f"><span>预计访谈时长</span><div class="dur"><input name="duration_min" id="du" type="number" min="5" max="240" value="${editing ? itv.duration_min || 45 : 45}" aria-label="预计访谈时长（分钟）"><span>分钟</span><span class="durq" id="dq"></span></div></label>
    ${editing ? "" : `<fieldset style="border:1px solid var(--line);border-radius:10px;margin:0 0 12px;padding:6px 14px"><legend class="sm mut">观察员（隐身旁听）</legend>${obs.length ? obs.map((u) => `<label class="chk"><input type="checkbox" name="obs" value="${u.id}"><span>${esc(u.name)}</span></label>`).join("") : `<span class="sm mut">还没有观察员账号</span>`}</fieldset>`}
    ${mailReady ? "" : `<div class="rem" id="nomail">还没有配置发件邮箱：仍会生成邀请函，链接会显示给你复制。</div>`}
    <div class="btns"><button class="p" type="submit" id="mk">${editing ? "保存并发送更新" : "生成并发送邀请函"}</button><span class="sp"></span><button type="button" id="cp"${editing && !(itv.status === "live" || itv.invite_state === "entered") ? "" : " disabled"} title="${!editing ? "创建之后才有链接可以复制" : itv.status === "live" || itv.invite_state === "entered" ? "访谈进行中，不能换链接" : "复制受访者的进入链接"}">复制链接</button><button type="button" id="c">取消</button></div>
    <div class="lkbox" id="lkbox" hidden></div><div class="rem okn" id="savedmsg" role="status" hidden></div></form></div>`);
  const f = $("#f", m), tm = $("#tm", m), tmc = $("#tmc", m), em = $("#em", m), msg = $("#emsg", m), mk = $("#mk", m), du = $("#du", m), dq = $("#dq", m);
  const cp = $("#cp", m), lkbox = $("#lkbox", m), savedmsg = $("#savedmsg", m);
  let linkUrl = "", dirty = false;   // the entry link once there is one in this window; dirty: something was saved, so the list behind needs a refresh when the window closes
  $("#c", m).onclick = () => { m.remove(); if (dirty) onDone(); };
  const saidSaved = (text) => { savedmsg.hidden = false; savedmsg.textContent = text; $("#c", m).textContent = "关闭"; };
  const copyLink = async (url) => {   // copy it; when the browser does not allow that, show it in a box to copy by hand
    try { await navigator.clipboard.writeText(url); notice("链接已复制，可以直接粘贴发给受访者。", "ok", 6000); lkbox.hidden = true; }
    catch { lkbox.hidden = false; lkbox.innerHTML = `<span class="sm mut">这个浏览器不让自动复制，请手动复制：</span><input readonly value="${esc(url)}" aria-label="进入链接">`; lkbox.querySelector("input").select(); }
  };
  cp.onclick = guard(async () => {
    if (!linkUrl) {   // an existing session: its link was only shown when it was made, so this makes a new one and the old one stops working
      if (!confirm("会生成一条新链接，之前发给受访者的旧链接会失效。继续吗？")) return;
      linkUrl = location.origin + (await api(`/interviews/${itv.id}/invite/rotate`, { method: "POST" })).invite_path;
    }
    await copyLink(linkUrl);
  });
  // the other times the candidate chose: click one to make it the booked time (the booked one steps back into the list); × drops it
  const drawChips = () => {
    tmc.innerHTML = backups.length ? `<span class="sm mut">受访者还选了：</span>` + backups.map((b, i) => `<span class="tmc" data-i="${i}" role="button" tabindex="0">${esc(timeLabel(b))}<button type="button" data-x="${i}" aria-label="去掉这个备选时间">×</button></span>`).join("") : "";
    tmc.querySelectorAll(".tmc").forEach((c) => (c.onclick = (e) => { const i = +c.dataset.i; if (e.target.dataset.x !== undefined) { backups.splice(i, 1); drawChips(); return; } const cur = toIsoUtc(tm.value); tm.value = toLocalInput(backups[i]); backups[i] = cur || backups[i]; if (!cur) backups.splice(i, 1); drawChips(); }));
  };
  drawChips();
  const fm = $("#fm", m), lg = $("#lg", m), pfc = $("#pfc", m);
  const drawPrefs = () => {   // what else the candidate chose: click one to make it the booked one
    const fs = ((who && who.pref_formats) || []).filter((k) => FMT[k] && k !== fm.value), ls = ((who && who.pref_langs) || []).filter((k) => projLangs.includes(k) && k !== lg.value);
    pfc.innerHTML = fs.length || ls.length ? `<span class="sm mut">受访者还选了：</span>` + fs.map((k) => `<span class="tmc" data-f="${k}" role="button" tabindex="0">${esc(FMT[k])}</span>`).join("") + ls.map((k) => `<span class="tmc" data-l="${k}" role="button" tabindex="0">${esc(LANGS[k] || k)}</span>`).join("") : "";
    pfc.querySelectorAll("[data-f]").forEach((c) => (c.onclick = () => { fm.value = c.dataset.f; drawPrefs(); }));
    pfc.querySelectorAll("[data-l]").forEach((c) => (c.onclick = () => { lg.value = c.dataset.l; drawPrefs(); }));
  };
  fm.onchange = lg.onchange = drawPrefs; drawPrefs();
  [30, 45, 60, 90].forEach((v) => { const b = document.createElement("button"); b.type = "button"; b.textContent = v; b.onclick = () => { du.value = v; syncDur(); }; dq.appendChild(b); });
  const syncDur = () => dq.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.textContent === +du.value));
  du.oninput = syncDur; syncDur();
  const grouped = () => !editing && $("#kind", m).value === "group";
  const checkEmail = () => {
    if (grouped()) { mk.disabled = false; return true; }
    const v = em.value.trim();
    if (!v) { em.className = ""; msg.className = "emsg bad"; msg.textContent = "没有邮箱"; mk.disabled = true; return false; }
    const ok = EMAIL_OK.test(v);
    em.className = ok ? "" : "bad"; msg.className = "emsg " + (ok ? "good" : "bad");
    msg.textContent = ok ? "格式正确" : v.indexOf("@") < 0 ? "缺少 @，邮箱应该像 name@example.com" : "@ 后面的域名不完整，比如缺少 .com";
    mk.disabled = !ok; return ok;
  };
  em.oninput = checkEmail; checkEmail();
  if (!editing) $("#kind", m).onchange = () => {
    const g = grouped(); fm.innerHTML = fmtOpts(g ? "group" : "depth", ""); drawPrefs(); $("#grouppick", m).hidden = !g; $("#depthpick", m).hidden = g; $("#emailbox", m).hidden = g; $(".tmbox", m).hidden = false;
    const how = $("#how", m); if (g) how.value = "human"; how.querySelector("option[value=ai]").disabled = g; checkEmail();
  };
  const go = guard(async (send) => {
    const fd = new FormData(f), kind = editing ? itv.kind || "depth" : fd.get("kind") || "depth";
    const base = { moderator_id: fd.get("moderator_id") || null, duration_min: Number(fd.get("duration_min")) || 45, scheduled_at: toIsoUtc(fd.get("scheduled_at")), format: fd.get("format") || "" };
    if (kind !== "group" && who) {
      const addr = em.value.trim().toLowerCase();
      if (addr && addr !== (who.email || "").toLowerCase()) { await api(`/participants/${who.id}/email`, { method: "PUT", body: { email: addr } }); who.email = addr; }
    }
    if (editing) {
      const body = { ...base, send_email: send };
      if (fd.get("moderation") && fd.get("moderation") !== itv.moderation) body.moderation = fd.get("moderation");
      if (fd.get("language") && fd.get("language") !== itv.language) body.language = fd.get("language");
      const r = await api(`/interviews/${itv.id}`, { method: "PUT", body });
      itv = { ...itv, ...r }; dirty = true; if (r.mail) mailNote(who ? who.name : "", r.mail); else toast("已保存");
      saidSaved(send && r.mail && r.mail.status === "queued" ? "已保存，更新邮件已发出。" : "已保存。需要的话，可以点“复制链接”把进入链接发给受访者。"); return;
    }
    const body = { kind, ...base, language: fd.get("language") || null, observer_ids: fd.getAll("obs"), moderation: kind === "group" ? "human" : fd.get("moderation") || "human", send_email: send && kind !== "group" };
    if (kind === "group") { body.participant_ids = fd.getAll("pids"); if (!body.participant_ids.length || body.participant_ids.length > 10) return toast("座谈会请勾选 1 到 10 位受访者", true); }
    else body.participant_id = fd.get("participant_id") || (who && who.id);
    const r = await api(`/projects/${p.id}/interviews`, { method: "POST", body });
    if (kind === "group") { m.remove(); showGroupInvites(r); onDone(r); return; }
    if (send) mailNote(who ? who.name : "", r.mail);
    linkUrl = location.origin + r.invite_path;   // known only now: the window stays open so it can be copied (or sent by other means when the mail did not go)
    cp.disabled = false; cp.title = "复制受访者的进入链接"; mk.disabled = true;
    saidSaved(send && r.mail && r.mail.status === "queued" ? "邀请函已生成，邮件服务已接受发送请求。链接也可以复制。" : "邀请函已生成，但邮件没有进入发送队列；可以复制链接或稍后重试发送。");
    $("#c", m).onclick = () => { m.remove(); onDone(r); };   // closing hands the new session to the page behind
  });
  f.onsubmit = (e) => { e.preventDefault(); if (checkEmail()) go(true); };
}

/** Invite several people at once: the time and the email come from what each person gave, the way is a human moderator, one moderator for the batch (or none yet). */
async function batchInviteModal(p, parts, ids, onDone = () => {}) {
  const chosen = ids.map((id) => parts.find((x) => x.id === id)).filter(Boolean);
  const todo = chosen.filter((x) => x.screening === "eligible" && !x.anonymized && x.screen_result !== "disqualified" && !x.interview_kind);
  if (!todo.length) { notice("勾选的人都已经邀请过了（或不能邀请）。", "bad", 6000); return; }
  const users = await api("/users"), mods = users.filter((u) => ["moderator", "researcher", "admin"].includes(u.role));
  const skipped = chosen.length - todo.length;
  const m = modal(`<div class="invdlg"><h2>批量邀请</h2><p class="sm mut">将给 ${todo.length} 位受访者发送邀请函${skipped ? `（另有 ${skipped} 位已邀请过，跳过）` : ""}。</p>
    <label class="f"><span>主持人（这一批都用同一位）</span><select id="bmod"><option value="">未选择</option>${mods.map((u) => `<option value="${u.id}">${esc(u.name)}（${ROLE[u.role]}）</option>`).join("")}</select></label>
    <div class="rem" id="brem" role="alert">还没有选主持人：邀请函里会写“主持人稍后通知”，之后可以在每个人的“更新邀请函”里补上。不选也可以直接发送。</div>
    ${CONFIG && CONFIG.mail_ready ? "" : `<div class="rem">还没有配置发件邮箱：只会创建邀请函，链接会列出来给你复制。</div>`}
    <div class="scroll"><table class="tbl"><thead><tr><th>受访者</th><th>时间</th><th>邮箱</th><th>方式</th></tr></thead><tbody>${todo.map((x) => { const ok = EMAIL_OK.test((x.email || "").trim());
      return `<tr><td>${esc(x.name)}</td><td>${x.availability && x.availability[0] ? esc(timeLabel(x.availability[0])) : "随时可进入"}</td><td class="${ok ? "" : "tgwarn"}">${ok ? esc(x.email) : esc(x.email || "没有邮箱") + "（格式不对）"}</td><td>真人访谈</td></tr>`; }).join("")}</tbody></table></div>
    <div class="btns"><button class="p" id="bgo" type="button">发送 ${todo.length} 封邀请函</button><span class="sp"></span><button type="button" id="bc">取消</button></div></div>`);
  $("#bc", m).onclick = () => m.remove();
  const mod = $("#bmod", m); mod.onchange = () => { $("#brem", m).hidden = !!mod.value; };
  $("#bgo", m).onclick = guard(async () => {
    $("#bgo", m).disabled = true; const out = [];
    for (const x of todo) {
      try { const r = await api(`/projects/${p.id}/interviews`, { method: "POST", body: { participant_id: x.id, moderation: "human", moderator_id: mod.value || null, duration_min: 45, scheduled_at: x.availability && x.availability[0] ? x.availability[0] : null, send_email: true } }); out.push({ x, r }); }
      catch (e) { out.push({ x, err: e.message }); }
    }
    m.remove();
    const sent = out.filter((o) => o.r && o.r.mail && o.r.mail.status === "queued").length, noMail = out.filter((o) => o.r && !(o.r.mail && o.r.mail.status === "queued")), failed = out.filter((o) => o.err);
    notice(`<b>已处理 ${out.length} 位</b>：${sent} 封邀请函已发出${noMail.length ? `；<b>${noMail.length} 封邮件没发出去</b>（${esc(noMail[0].r.mail ? noMail[0].r.mail.reason : "")}）` : ""}${failed.length ? `；${failed.length} 位没能创建（${esc(failed[0].err)}）` : ""}。其他操作不受影响。`, noMail.length || failed.length ? "bad" : "ok");
    if (noMail.length) {   // those links are shown only now
      const mm = modal(`<div class="invdlg"><h2>这几位的邀请链接</h2><p class="sm mut">邮件没发出去，请自己复制链接发给他们。只显示这一次。</p><ul style="padding-left:18px">${noMail.map((o) => `<li style="margin:8px 0"><b>${esc(o.x.name)}</b><code class="copy">${esc(location.origin + o.r.invite_path)}</code></li>`).join("")}</ul><div class="row"><button class="p" id="bx">完成</button></div></div>`);
      $("#bx", mm).onclick = () => mm.remove();
    }
    onDone(out);
  });
}
