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

/* ---------- 直播间页本身 ---------- */
const RM_TABS = [["all", "全部"], ["scheduled", "待开始"], ["live", "进行中"], ["ended", "已结束"], ["absent", "未到场"]];
const rmEmptySel = () => ({ who: [], moderator: [], time: [], tag: [] });
// every tab keeps its own filters (the "全部" page's choices do not follow you to the other pages)
const RM = { tab: "all", wall: false, sel: { all: rmEmptySel(), scheduled: rmEmptySel(), live: rmEmptySel(), ended: rmEmptySel(), absent: rmEmptySel() } };
const rmCategory = (i) => i.status === "ended" && i.invite_state === "ended_absent" ? "absent" : i.status;

/** The length column of one row: what the tab is about (planned / running / really lasted), with the gap to the plan marked. */
function rmLength(i, tab) {
  const plan = i.duration_min || 45, real = i.actual_min;
  if (tab === "scheduled") return `${plan} 分`;
  if (tab === "live") { const run = i.started_at ? Math.max(0, Math.round((Date.now() - new Date(i.started_at + "Z").getTime()) / 60000)) : 0; return `${run} / ${plan} 分`; }
  if (tab === "ended" || tab === "absent") return real == null ? "—" : `${real} 分`;
  return i.status === "ended" && real != null ? `${plan} / ${real} 分` : `${plan} / —`;
}
const rmLengthHead = (tab) => ({ scheduled: "预计时长", live: "已进行 / 预计", ended: "访谈时长" })[tab] || "预计 / 访谈时长";
const rmKeyLine = (i) => (i.key_answers && i.key_answers.length ? i.key_answers.slice(0, 3).map((a) => a.a).join("；") : "");

async function roomsView(p) {
  let its = shFilterInterviews(p, await api(`/projects/${p.id}/interviews`));
  const staff = STAFF(), obs = ME.role === "observer";
  const canEdit = (i) => staff && rmOpen(i) && i.kind !== "async";   // the moderator and the time can be changed here until the interview has ended
  let users = []; if (staff) { try { users = await api("/users"); } catch { /* no list: the cells just stay read-only */ } }
  const mods = users.filter((u) => ["moderator", "researcher", "admin"].includes(u.role)).map((u) => ({ id: u.id, name: u.name }));
  const go = (i, as, label, cls) => `<a class="btn s ${cls}" href="/room.html?i=${i.id}&as=${as}" target="_blank" rel="noopener">${label}</a>`;
  const mod = (i) => (obs ? "—" : i.moderator_name || "未指定");
  const tagsOf = (i) => i.participant.tags || [];
  const FIELDS = [["who", "受访者", (i) => [i.participant.name]], ["moderator", "主持人", (i) => [mod(i)]], ["time", "开始时间", (i) => [timeBucket(i)]], ["tag", "标签", tagsOf]];
  const sel = () => RM.sel[RM.tab];
  const inTab = (i) => RM.tab === "all" || rmCategory(i) === RM.tab;
  const passes = (i) => inTab(i) && FIELDS.every(([k, , get]) => !sel()[k].length || get(i).some((v) => sel()[k].includes(v)));
  const filtered = () => FIELDS.some(([k]) => sel()[k].length);
  const options = (k, get) => { const seen = new Set(); its.filter(inTab).forEach((i) => get(i).forEach((v) => seen.add(v))); return k === "time" ? TIME_ORDER.filter((v) => seen.has(v)) : [...seen].sort(); };
  const tagBox = (i) => (tagsOf(i).length ? tagsOf(i).map((t) => `<span class="lch ${lcolor(t)}">${esc(t)}</span>`).join("") : `<span class="sm mut">—</span>`);
  const actions = (i) => { const open = i.status !== "ended" && !i.purged; return open ? (obs ? go(i, "observer", "隐身观察", "") : go(i, "moderator", "进入 TA 的房间", "p") + (staff ? " " + go(i, "observer", "隐身观察", "") : "")) : `<a class="btn s" href="#/i/${i.id}">转写和洞察</a>`; };
  const mins = (i) => (i.started_at ? Math.max(0, Math.round((Date.now() - new Date(i.started_at + "Z").getTime()) / 60000)) : 0);
  const card = (i) => `<div class="rmc"><div class="rmc-h"><span class="tag live">进行中</span><span class="sm mut">已进行 ${mins(i)} 分钟</span></div>
    <div class="rmc-r"><span class="sm mut">受访者</span><b>${esc(i.participant.name)}</b></div><div class="rmc-r"><span class="sm mut">主持人</span><span>${esc(mod(i))}</span></div>
    <div class="rmc-r"><span class="sm mut">开始时间</span><span>${startOf(i) ? when(startOf(i)) : "随时可进入"}</span></div><div class="rmc-t">${tagBox(i)}</div><div class="rmc-a">${actions(i)}</div></div>`;
  shell(its.length ? `<div class="rmws${staff ? " side" : ""}" id="rmws"><div class="rmcol"><div class="rmtabs" id="rmtabs" role="tablist" aria-label="按状态筛选场次"></div><div id="rmban"></div>
    <div class="rmscroll" id="rmscroll"><div class="card scroll rmbox" id="rmbox" style="padding:6px 10px"><table class="tbl rc" id="rmtable"><thead><tr id="rmh"></tr></thead><tbody id="rmb"></tbody></table><div class="rmwall" id="rmwall" hidden></div></div>
    <div class="card empty rmempty" id="rmempty" hidden></div></div></div>${staff ? `<aside class="rmside" id="rmside" aria-label="提醒和安排"></aside>` : ""}</div>`
    : `<div class="card empty">${staff ? `<b>还没有场次。</b><span>到“甄别设定”的名单里，点受访者那一行的“邀请到场次”新建场次，把邀请链接发给受访者。</span><a class="btn p" href="#/p/${p.id}/people">去甄别设定</a>` : `<b>还没有分配给你的场次。</b><span>研究员把你加入某场访谈后，它会出现在这里。</span>`}</div>`, { active: "rooms", p });
  if (!its.length) return;
  const showStatus = () => RM.tab === "all";
  const showKey = !obs;   // the questionnaire answers are for the team, not the client
  const emptyHtml = () => {
    if (filtered()) return `<b>没有符合筛选条件的场次</b><span>这一页的筛选条件太严了。</span><button type="button" class="p" id="rmclr2">清除筛选</button>`;
    if (RM.tab === "live") {
      const sched = its.filter((i) => i.status === "scheduled"), next = sched.sort((a, b) => startOf(a).localeCompare(startOf(b)))[0];
      return `<b>现在没有进行中的访谈</b>${next ? `<span>有 ${sched.length} 场待开始，下一场：${startOf(next) ? when(startOf(next)) : "随时可进入"} · ${esc(next.participant.name)} · 主持 ${esc(mod(next))}</span><div class="row" style="justify-content:center"><button type="button" class="p" data-goto="scheduled">看待开始</button><button type="button" data-goto="all">回到全部</button></div>` : `<button type="button" data-goto="all">回到全部</button>`}`;
    }
    return `<b>${RM.tab === "scheduled" ? "没有待开始的场次" : RM.tab === "absent" ? "没有未到场的场次" : "还没有结束的场次"}</b><button type="button" data-goto="all">回到全部</button>`;
  };
  const drawBody = () => {
    const rows = its.filter(passes).sort((a, b) => startOf(b).localeCompare(startOf(a)));
    const wall = RM.wall && RM.tab === "live" && rows.length > 0;
    $("#rmbox").hidden = rows.length === 0; $("#rmtable").hidden = wall; $("#rmwall").hidden = !wall;
    $("#rmwall").innerHTML = wall ? rows.map(card).join("") : "";
    const names = ["受访者", "主持人", "开始时间", "标签", ...(showKey ? ["关键回答"] : []), ...(staff ? ["邀请进度", rmLengthHead(RM.tab)] : []), ...(showStatus() ? ["状态"] : [])];
    $("#rmb").innerHTML = rows.map((i, ri) => { const [t, c] = roomState(i);
      const [pt, pc] = rmProgress(i), ed = canEdit(i);
      const modCell = ed ? `<button type="button" class="ed${i.moderator_name ? "" : " none"}" data-edm="${i.id}" aria-label="修改主持人，现在是${esc(mod(i))}">${esc(mod(i))}</button>` : `<span class="${i.moderator_name || obs ? "" : "mut"}">${esc(mod(i))}</span>`;
      const timeCell = ed ? `<button type="button" class="ed" data-edt="${i.id}" aria-label="修改开始时间">${startOf(i) ? when(startOf(i)) : "随时可进入"}</button>` : `<span class="mut">${startOf(i) ? when(startOf(i)) : "随时可进入"}</span>`;
      const cells = [`<b>${esc(i.participant.name)}</b>`, modCell, timeCell, `<div class="lbox" style="border:0;background:none;padding:0">${tagBox(i)}</div>`,
        ...(showKey ? [`<span class="rmkey" title="${esc(rmKeyLine(i))}">${esc(rmKeyLine(i)) || `<span class="mut">—</span>`}</span>`] : []),
        ...(staff ? [`<span class="tag ${pc}">${pt}</span>`, `<span class="rmlen">${rmLength(i, RM.tab)}</span>`] : []), ...(showStatus() ? [`<span class="tag ${c}">${t}</span>`] : [])];
      return `<tr data-ri="${ri}" tabindex="0">${cells.map((h, n) => `<td data-l="${names[n]}">${h}</td>`).join("")}<td class="acts">${actions(i)}</td></tr>`; }).join("");
    const em = $("#rmempty"); em.hidden = rows.length > 0; em.innerHTML = rows.length ? "" : emptyHtml();
    const clr = $("#rmclr2"); if (clr) clr.onclick = () => { RM.sel[RM.tab] = rmEmptySel(); drawHead(); drawBody(); drawTabs(); };
    em.querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => { RM.tab = b.dataset.goto; RM.wall = false; drawAll(); }));
    rspSetList(rows.filter((r) => r.status === "ended").map((r) => r.id)); wireEdits(); wireCard(rows);
  };
  /* the card (资料卡) of the row the mouse is on: the questionnaire answers and the two lengths */
  function wireCard(rows) {
    if (obs) return;
    let hideTimer = null;
    const hide = () => { clearTimeout(hideTimer); const c = $("#rmcard"); if (c) c.remove(); document.querySelectorAll("#rmb tr.hv").forEach((x) => x.classList.remove("hv")); };
    const hideSoon = () => { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 150); };   // the mouse may move from the row into the card to scroll it
    const show = (tr) => {
      const i = rows[+tr.dataset.ri]; if (!i) return; hide(); tr.classList.add("hv");
      const c = document.createElement("div"); c.id = "rmcard"; c.className = "rmcard";
      const ans = (i.key_answers || []).map((a, n) => `<div class="rmq"><small><em>${n + 1}.</em>${esc(a.q)}</small><span>${esc(a.a)}</span></div>`).join("");
      const tags = (i.participant.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
      c.innerHTML = `<h5><b>${esc(i.participant.name)}</b><span class="tag">${roomState(i)[0]}</span>${tags}<span class="sp"></span><span class="mut sm">${esc(i.participant.country || "")} · ${esc(LANGS[i.language] || i.language)}</span></h5>${ans || `<div class="mut sm">没有问卷答案（手动添加或外部名单的人没有）。</div>`}
        <div class="rmft"><span>预计 ${i.duration_min || 45} 分</span>${i.actual_min != null ? `<span>访谈 ${i.actual_min} 分</span>` : ""}${i.moderator_name ? `<span>主持 ${esc(i.moderator_name)}</span>` : ""}</div>`;
      c.onmouseenter = () => clearTimeout(hideTimer); c.onmouseleave = hideSoon;
      document.body.appendChild(c);
      const a = tr.getBoundingClientRect(), w = c.offsetWidth, h = c.offsetHeight;
      c.style.left = Math.max(8, Math.min(innerWidth - w - 8, a.left + a.width * 0.45)) + "px"; c.style.top = (a.bottom + h + 8 > innerHeight ? Math.max(8, a.top - h - 4) : a.bottom + 4) + "px";
    };
    app.querySelectorAll("#rmb tr[data-ri]").forEach((tr) => {
      tr.onmouseenter = () => { clearTimeout(hideTimer); if (matchMedia("(hover:hover)").matches && !document.querySelector(".rmpop")) show(tr); };
      tr.onmouseleave = () => { if (matchMedia("(hover:hover)").matches) hideSoon(); };
      tr.onfocus = () => { if (tr.matches(":focus-visible")) show(tr); };
      tr.onblur = hide;
      tr.onclick = (e) => { if (e.target.closest("a,button") || matchMedia("(hover:hover)").matches) return; tr.classList.contains("hv") ? hide() : show(tr); };
    });
    $("#rmscroll").onscroll = hide;
  }
  /* a column filter opens as a small panel floating over the table, with search, 全选 / 清除 and a tick list; it only applies to the page (tab) you are on */
  function openFilter(btn, k, label, get) {
    const pop = rmPop(btn, `<input class="mss" placeholder="搜索" aria-label="搜索${esc(label)}"><div class="rmfa"><button type="button" class="lnk" data-all>全选</button><button type="button" class="lnk" data-none>清除</button></div><div class="rmopts rmfl"></div>`);
    const list = pop.querySelector(".rmfl"), inp = pop.querySelector("input");
    const apply = () => { drawBody(); drawTabs(); markHeads(); };
    const fill = () => { const q = inp.value.trim(), os = options(k, get).filter((v) => !q || v.includes(q));
      list.innerHTML = os.length ? os.map((v) => `<label class="rmfo"><input type="checkbox" value="${esc(v)}"${sel()[k].includes(v) ? " checked" : ""}><span>${esc(v)}</span></label>`).join("") : `<div class="sm mut" style="padding:6px 8px">没有匹配的</div>`;
      list.querySelectorAll("input").forEach((c) => (c.onchange = () => { sel()[k] = [...list.querySelectorAll("input:checked")].map((x) => x.value).concat(sel()[k].filter((v) => !os.includes(v))); apply(); })); };
    pop.querySelector("[data-all]").onclick = () => { const q = inp.value.trim(); sel()[k] = [...new Set([...sel()[k], ...options(k, get).filter((v) => !q || v.includes(q))])]; fill(); apply(); };
    pop.querySelector("[data-none]").onclick = () => { sel()[k] = []; fill(); apply(); };
    inp.oninput = fill; fill(); inp.focus();
  }
  const markHeads = () => app.querySelectorAll("#rmh .rmhb").forEach((b) => b.classList.toggle("on", sel()[b.dataset.k].length > 0));
  const drawHead = () => {
    $("#rmh").innerHTML = FIELDS.map(([k, label]) => `<th><button type="button" class="msb hb rmhb" data-k="${k}" aria-haspopup="true" aria-label="筛选${esc(label)}">${label}</button></th>`).join("") + (showKey ? `<th>关键回答</th>` : "") + (staff ? `<th>邀请进度</th><th>${rmLengthHead(RM.tab)}</th>` : "") + (showStatus() ? `<th>状态</th>` : "") + `<th></th>`;
    FIELDS.forEach(([k, label, get]) => { const b = $(`#rmh .rmhb[data-k=${k}]`); b.onclick = (e) => { e.stopPropagation(); openFilter(b, k, label, get); }; });
    markHeads();
  };
  const drawTabs = () => {
    $("#rmtabs").innerHTML = RM_TABS.map(([k, label]) => `<button type="button" role="tab" class="rmt${RM.tab === k ? " on" : ""}" data-t="${k}" aria-selected="${RM.tab === k}" data-n="${its.filter((i) => k === "all" || rmCategory(i) === k).length}">${label}</button>`).join("")
      + `<span class="sp"></span>${filtered() ? `<button type="button" class="lnk" id="rmclr">清除这一页的筛选</button>` : ""}` + (RM.tab === "live" ? `<button type="button" id="rmwallbtn">${RM.wall ? "返回列表" : "全部预览"}</button>` : "");
    app.querySelectorAll("#rmtabs .rmt").forEach((b) => (b.onclick = () => { RM.tab = b.dataset.t; if (RM.tab !== "live") RM.wall = false; drawAll(); }));
    const w = $("#rmwallbtn"); if (w) w.onclick = () => { RM.wall = !RM.wall; drawTabs(); drawBody(); };
    const c = $("#rmclr"); if (c) c.onclick = () => { RM.sel[RM.tab] = rmEmptySel(); drawHead(); drawBody(); drawTabs(); };
  };
  const drawAll = () => { document.querySelectorAll(".rmpop,#rmcard").forEach((x) => x.remove()); drawTabs(); drawHead(); drawBody(); };
  const drawSide = () => { const s = $("#rmside"); if (s) { s.innerHTML = rmAsideHtml(its, mods); wireSide(); } };
  const banner = (i, what) => {   // the change is saved and the invitation already shows it; a mail already sent can be followed by an update mail
    const mail = CONFIG && CONFIG.mail_ready && i.mail_status === "sent" && i.kind !== "group";
    $("#rmban").innerHTML = `<div class="rmban" role="status"><span>${esc(what)}，邀请函已同步。</span><span class="sp"></span>${mail ? `<label class="chk"><input type="checkbox" id="rmmail" checked><span>同时给受访者发更新邮件</span></label><button type="button" class="p" id="rmsend">发送</button>` : ""}<button type="button" class="s" id="rmbx" aria-label="关闭">×</button></div>`;
    $("#rmbx").onclick = () => { $("#rmban").innerHTML = ""; };
    if (mail) $("#rmsend").onclick = guard(async () => { const send = $("#rmmail").checked; $("#rmban").innerHTML = ""; if (!send) return; const r = await api(`/interviews/${i.id}`, { method: "PUT", body: { send_email: true } }); mailNote(i.participant.name, r.mail); });
    else setTimeout(() => { const b = $("#rmban"); if (b && b.firstChild && !b.querySelector("#rmsend")) b.innerHTML = ""; }, 6000);
  };
  const saveOne = (i, what) => guard(async (body, label) => {
    const r = await api(`/interviews/${i.id}`, { method: "PUT", body });
    const at = its.findIndex((x) => x.id === i.id); if (at >= 0) its[at] = { ...its[at], ...r, participant: { ...its[at].participant, ...r.participant } };
    drawBody(); drawSide(); banner(its[at] || i, what(body, label));
  });
  const itemOf = (id) => its.find((x) => x.id === id);
  function wireEdits() {
    app.querySelectorAll("[data-edm]").forEach((b) => (b.onclick = () => { const i = itemOf(b.dataset.edm); rmPickModerator(b, i, its, mods, saveOne(i, (_, n) => `主持人已改为 ${n}`)); }));
    app.querySelectorAll("[data-edt]").forEach((b) => (b.onclick = () => { const i = itemOf(b.dataset.edt); rmPickTime(b, i, saveOne(i, (body) => (body.scheduled_at ? "开始时间已更新" : "开始时间已清除"))); }));
  }
  function wireSide() {
    app.querySelectorAll("#rmside [data-asg]").forEach((b) => (b.onclick = () => { const i = itemOf(b.dataset.asg); rmPickModerator(b, i, its, mods, saveOne(i, (_, n) => `主持人已改为 ${n}`)); }));
    app.querySelectorAll("#rmside [data-resend]").forEach((b) => (b.onclick = guard(async () => { const i = itemOf(b.dataset.resend), r = await api(`/interviews/${i.id}`, { method: "PUT", body: { send_email: true } }); mailNote(i.participant.name, r.mail); })));
  }
  document.body.classList.add("rfmode", "pgw");   // the page does not scroll: the table and the side cards scroll inside their own boxes
  const ws = $("#rmws"), fit = () => { if (ws && ws.offsetParent) ws.style.height = Math.max(380, innerHeight - ws.getBoundingClientRect().top - 18) + "px"; };
  fit(); addEventListener("resize", fit);
  drawAll(); drawSide();
  if (window._rmPoll) clearInterval(window._rmPoll);
  window._rmPoll = setInterval(async () => {   // a moderator or a time changed elsewhere: update the table, keep the filters and anything open
    if (document.hidden || !location.hash.endsWith("/rooms") || document.querySelector(".rmpop")) return;
    try { its = shFilterInterviews(p, await api(`/projects/${p.id}/interviews`)); drawTabs(); drawBody(); drawSide(); } catch { /* the next tick tries again */ }
  }, 10000);
}
