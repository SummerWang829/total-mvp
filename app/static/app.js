const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (ms) => { const s = Math.floor(ms / 1000); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} }, del: (k) => { try { localStorage.removeItem(k); } catch {} } };

async function api(path, { method = "GET", body, auth = true, signal } = {}) {
  const h = {};
  if (body !== undefined) h["Content-Type"] = "application/json";
  const t = store.get("tt_token");
  if (auth && t) h.Authorization = "Bearer " + t;
  // reads (GET) never hang for ever on a bad network: they give up after 45 s with a message, and a dropped connection is tried once more.
  // Writes and AI requests are not cut off (some take long on purpose) and are never sent twice.
  const get = method === "GET" && !signal, ctl = get ? new AbortController() : null, timer = get ? setTimeout(() => ctl.abort(), 45000) : null;
  const send = () => fetch("/api" + path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined, signal: get ? ctl.signal : signal });
  let r, data = null;
  try {
    try { r = await send(); }
    catch (e) { if (!get || ctl.signal.aborted) throw e; await new Promise((ok) => setTimeout(ok, 800)); r = await send(); }
    try { data = await r.json(); } catch {}
  } catch (e) {
    if (get && ctl.signal.aborted) { const er = new Error("网络太慢，没有等到服务器回复，请稍后再试。"); er.status = 0; throw er; }
    throw e;
  } finally { if (timer) clearTimeout(timer); }
  if (!r.ok) {
    if (r.status === 401 && auth) { store.del("tt_token"); if (!location.hash.startsWith("#/login") && location.pathname === "/") location.hash = "#/login"; }
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : d && d.message ? d.message + (d.errors ? "：" + d.errors.join("；") : "") : Array.isArray(d) ? "填写有误：" + d.map((x) => (x.loc || []).slice(-1) + " " + x.msg).join("；") : "请求失败 (" + r.status + ")";
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  document.querySelectorAll(".toast").forEach((n) => n.remove());
  const n = document.createElement("div"); n.className = "toast" + (err ? " err" : ""); n.setAttribute("role", "status"); n.textContent = msg;
  document.body.appendChild(n); clearTimeout(toastTimer); toastTimer = setTimeout(() => n.remove(), err ? 6000 : 2600);
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
const COUNTRIES = [["SG", "新加坡"], ["ID", "印度尼西亚"], ["VN", "越南"], ["TH", "泰国"], ["MY", "马来西亚"], ["SA", "沙特阿拉伯"], ["AE", "阿联酋"], ["PH", "菲律宾"], ["MX", "墨西哥"], ["BR", "巴西"], ["US", "美国"], ["JP", "日本"], ["DE", "德国"], ["FR", "法国"], ["GB", "英国"]];
const LANGS = { en: "English", zh: "中文", ms: "Bahasa Melayu", ta: "தமிழ் (Tamil)" };
const langOpts = (sel, blank) => (blank ? `<option value="">${blank}</option>` : "") + Object.entries(LANGS).map(([k, n]) => `<option value="${k}"${k === sel ? " selected" : ""}>${n}</option>`).join("");
let CONFIG = null;
async function loadConfig() { if (!CONFIG) { try { CONFIG = await api("/public/config", { auth: false }); } catch { CONFIG = { allowed_countries: ["SG"], languages: Object.keys(LANGS) }; } } return CONFIG; }
const openCountryOpts = (sel) => COUNTRIES.filter(([c]) => (CONFIG ? CONFIG.allowed_countries : ["SG"]).includes(c)).map(([c, n]) => `<option value="${c}"${c === sel ? " selected" : ""}>${n} (${c})</option>`).join("");
const countryOpts = (sel) => COUNTRIES.map(([c, n]) => `<option value="${c}"${c === sel ? " selected" : ""}>${n} (${c})</option>`).join("");
const STATUS = { scheduled: ["待开始", ""], live: ["进行中", "live"], ended: ["已结束", "ok"] };
const ROLE = { admin: "管理员", researcher: "研究员", moderator: "主持人", observer: "观察员", recruiter: "项目经理", viewer: "客户（只读）", support: "技术支持" };
// the participant's journey as the research team sees it (design spec 3.5): label + tag colour
// Transcript / caption speaker keys. A focus-group seat is "p1"…"p10" and is shown as 「参与者 ·01」, never the raw key.
function speakerName(key) {
  const m = /^p([1-9]|10)$/.exec(String(key || ""));
  if (m) return "参与者 ·" + String(+m[1]).padStart(2, "0");
  return { moderator: "主持人", ai_moderator: "AI 主持人", participant: "受访者", observer: "观察员" }[key] || key || "";
}
function speakerClass(key) {
  if (/^p([1-9]|10)$/.test(String(key || ""))) return "participant";
  if (key === "moderator" || key === "ai_moderator" || key === "participant") return key;
  return "participant";
}
const INVITE = { sent: ["已发送", ""], opened: ["已打开", "o"], device_ok: ["检测通过", "o"], consented: ["已同意", "o"], entered: ["已进入", "ok"], done: ["已完成", "ok"], ended_absent: ["未到场", "bad"] };
