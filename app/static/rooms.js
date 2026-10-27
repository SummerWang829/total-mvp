/* 直播间页的辅助：表格里“主持人”“开始时间”两格点一下就能改（和邀请函是同一份数据，改哪边另一边都跟着变），
   右侧四张卡片（接下来的场次、待办提醒、本周日历、主持人安排），邀请进度的叫法。依赖 index.html 里的 api / esc / when / CONFIG / toast / guard / $，invite.js 里的 toLocalInput / toIsoUtc / timeLabel / notice / mailNote。 */
const RM_PROGRESS = { sent: ["未打开链接", "w"], opened: ["设备未检测", "w"], device_ok: ["设备已通过", ""], consented: ["已同意", "ok"], entered: ["已进房", "ok"], done: ["已完成", "ok"], ended_absent: ["未到场", ""] };
const rmProgress = (i) => RM_PROGRESS[i.invite_state] || ["—", ""];
const rmOpen = (i) => i.status !== "ended" && !i.purged;
const rmMinutes = (i) => (i.duration_min || 45) * 60000;
const rmAt = (i) => (i.scheduled_at ? new Date(i.scheduled_at + "Z").getTime() : null);
const rmHm = (iso) => { const d = new Date(iso + "Z"); return `${z2(d.getHours())}:${z2(d.getMinutes())}`; };

/** Interviews of the same moderator that overlap in time (both still to come): [[a, b], ...]. */
function rmConflicts(its) {
  const open = its.filter((i) => rmOpen(i) && i.moderator_id && rmAt(i) !== null), out = [];
  open.forEach((a, n) => open.slice(n + 1).forEach((b) => { if (a.moderator_id === b.moderator_id && Math.abs(rmAt(a) - rmAt(b)) < Math.max(rmMinutes(a), rmMinutes(b))) out.push([a, b]); }));
  return out;
}

/** The four cards on the right. `mods` are the people who can moderate ({id, name}). */
function rmAsideHtml(its, mods) {
  const open = its.filter(rmOpen).sort((a, b) => (rmAt(a) ?? 9e15) - (rmAt(b) ?? 9e15));
  const now = Date.now(), person = (i) => esc(i.participant.name);
  const nextCard = open.slice(0, 5).map((i) => { const [t, c] = rmProgress(i);
    return `<div class="rma-n"><div class="rma-tm">${i.scheduled_at ? when(i.scheduled_at) : "随时"}</div><div class="rma-b"><b>${person(i)}</b> · ${i.moderator_name ? "主持 " + esc(i.moderator_name) : `<span class="mut">没有主持人</span>`}<br><span class="tag ${c}">${t}</span></div>
      ${i.moderator_id ? `<a class="btn s" href="/room.html?i=${i.id}&as=moderator" target="_blank" rel="noopener">进入</a>` : `<button type="button" class="s" data-asg="${i.id}">指派</button>`}</div>`; }).join("") || `<div class="mut sm">没有待开始的场次。</div>`;
  const todos = [];
  open.forEach((i) => {
    if (!i.moderator_id) todos.push(["没主持人", `${person(i)} · ${i.scheduled_at ? when(i.scheduled_at) : "未定时间"}`, `<button type="button" class="s" data-asg="${i.id}">指派</button>`]);
    if (i.invite_state === "opened") todos.push(["设备未检测", person(i), ""]);
    if (i.invite_state === "sent" && i.scheduled_at && rmAt(i) - now < 86400000) todos.push(["还没打开链接", `${person(i)} · ${when(i.scheduled_at)}`, ""]);
    if (i.mail_status === "failed") todos.push(["邮件没发出去", person(i), `<button type="button" class="s" data-resend="${i.id}">重发</button>`]);
  });
  rmConflicts(its).forEach(([a, b]) => todos.push(["时间冲突", `${esc(a.moderator_name)}：${person(a)} 和 ${person(b)}`, `<button type="button" class="s" data-asg="${b.id}">改主持人</button>`]));
  const todoCard = todos.length ? todos.map(([tag, text, act]) => `<div class="rma-t"><span class="tag bad">${tag}</span><span class="rma-b">${text}</span>${act}</div>`).join("") : `<div class="mut sm">没有要处理的事。</div>`;
  const days = [...Array(5)].map((_, d) => { const t = new Date(); t.setHours(0, 0, 0, 0); t.setDate(t.getDate() + d); return t; });
  const cal = days.map((day) => { const nxt = day.getTime() + 86400000, here = open.filter((i) => rmAt(i) !== null && rmAt(i) >= day.getTime() && rmAt(i) < nxt);
    return `<div class="rma-d"><b>${WEEK[day.getDay()]}<small>${day.getMonth() + 1}/${day.getDate()}</small></b>${here.map((i) => `<i class="${i.moderator_id ? "" : "w"}" title="${person(i)}">${rmHm(i.scheduled_at)} ${person(i)}</i>`).join("")}</div>`; }).join("");
  const week = Date.now() + 7 * 86400000, load = (id) => open.filter((i) => (i.moderator_id || "") === id && (rmAt(i) === null || rmAt(i) < week)).length;
  const rows = [...mods.map((m) => [m.name, load(m.id)]), ["未指定", load("")]], max = Math.max(1, ...rows.map((r) => r[1]));
  const loadCard = rows.map(([name, n]) => `<div class="rma-m"><div class="rma-l"><span>${esc(name)}</span><span>${n ? n + " 场" : name === "未指定" ? "0 场" : "空闲"}</span></div><div class="rma-bar"><i class="${name === "未指定" ? "w" : ""}" style="width:${(n / max) * 100}%"></i></div></div>`).join("");
  return `<div class="card"><h4>接下来的场次</h4>${nextCard}</div><div class="card"><h4>待办提醒 <span class="sm mut">${todos.length} 件</span></h4>${todoCard}</div>
    <div class="card"><h4>本周日历 <span class="sm mut">今天起 5 天</span></h4><div class="rma-cal">${cal}</div></div><div class="card"><h4>主持人安排 <span class="sm mut">7 天内</span></h4>${loadCard}</div>`;
}

/** One small popup next to the button that was clicked; closes on a click anywhere else and on Esc. */
function rmPop(anchor, html) {
  document.querySelectorAll(".rmpop").forEach((x) => x.remove());
  const pop = document.createElement("div"); pop.className = "rmpop"; pop.innerHTML = html; document.body.appendChild(pop);
  const a = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(a.left, innerWidth - w - 8)) + "px"; pop.style.top = (a.bottom + h + 12 > innerHeight ? Math.max(8, a.top - h - 4) : a.bottom + 4) + "px";
  const off = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); }, esc_ = (e) => { if (e.key === "Escape") close(); };
  const close = () => { pop.remove(); document.removeEventListener("mousedown", off, true); document.removeEventListener("keydown", esc_); };
  setTimeout(() => { document.addEventListener("mousedown", off, true); document.addEventListener("keydown", esc_); }, 0);
  pop.close = close; return pop;
}

/** Choose a moderator for one interview. `its` are all the interviews (for the load and the clash hints); `save(body)` sends the change. */
function rmPickModerator(anchor, i, its, mods, save) {
  const mine = its.filter((x) => x.id !== i.id && rmOpen(x) && x.moderator_id), at = rmAt(i);
  const hint = (m) => { const c = at === null ? null : mine.find((x) => x.moderator_id === m.id && rmAt(x) !== null && Math.abs(rmAt(x) - at) < Math.max(rmMinutes(x), rmMinutes(i))); const n = mine.filter((x) => x.moderator_id === m.id).length;
    return c ? `<span class="tag bad">${rmHm(c.scheduled_at)} 已有一场</span>` : `<span class="tag ${n ? "" : "ok"}">${n ? "已排 " + n + " 场" : "空闲"}</span>`; };
  const pop = rmPop(anchor, `<div class="lb">选主持人</div><input class="mss" placeholder="搜索姓名" aria-label="搜索主持人"><div class="rmopts"></div>`);
  const list = pop.querySelector(".rmopts"), inp = pop.querySelector("input");
  const fill = () => { const q = inp.value.trim(); list.innerHTML = [{ id: "", name: "未指定" }, ...mods].filter((m) => !q || m.name.includes(q)).map((m) => `<button type="button" class="rmopt${(i.moderator_id || "") === m.id ? " on" : ""}" data-id="${m.id}"><span>${esc(m.name)}</span>${m.id ? hint(m) : ""}</button>`).join("");
    list.querySelectorAll("[data-id]").forEach((b) => (b.onclick = () => { pop.close(); save({ moderator_id: b.dataset.id || null }, b.querySelector("span").textContent); })); };
  inp.oninput = fill; fill(); inp.focus();
}

/** Change the start time of one interview: type it, or tap one of the times the participant offered in the questionnaire. */
function rmPickTime(anchor, i, save) {
  const offered = ((i.participant && i.participant.availability) || []).slice(0, 6);
  const pop = rmPop(anchor, `<div class="lb">改开始时间</div><input type="datetime-local" id="rmdt" value="${toLocalInput(i.scheduled_at)}" aria-label="开始时间">
    ${offered.length ? `<div class="lb" style="margin-top:8px">受访者在问卷里选过的时间</div><div class="rmchips">${offered.map((o) => `<button type="button" class="s" data-o="${o}">${timeLabel(o)}</button>`).join("")}</div>` : ""}
    <div class="row" style="margin-top:10px"><button type="button" class="p" id="rmok">保存</button>${i.scheduled_at ? `<button type="button" class="s" id="rmclear">清除时间</button>` : ""}<button type="button" class="s" id="rmno">取消</button></div>`);
  pop.querySelectorAll("[data-o]").forEach((b) => (b.onclick = () => { pop.querySelector("#rmdt").value = toLocalInput(b.dataset.o); }));
  pop.querySelector("#rmok").onclick = () => { const v = pop.querySelector("#rmdt").value; if (!v) return toast("先选一个时间，或点“清除时间”", true); pop.close(); save({ scheduled_at: toIsoUtc(v) }, ""); };
  const c = pop.querySelector("#rmclear"); if (c) c.onclick = () => { pop.close(); save({ scheduled_at: null }, ""); };
  pop.querySelector("#rmno").onclick = () => pop.close();
}
