/* 玻璃胶囊（2026-11-21 用户确认效果图 qa_shots/glass_nav_mockup.html，“所有页面 UI 一致”）：
   外壳里每一处导航（第二层菜单、它下面的页面小页签、最左窄栏、顶部项目条）的当前项下面垫一块半透明玻璃胶囊；
   当前项一换，胶囊滑过去，途中沿移动方向拉长，到位轻轻回弹。
   外壳换页会重画菜单（胶囊跟着被清掉），所以每一处记住上一次的位置，重画后从旧位置滑到新位置。
   只管样式：不碰点击、跳转和菜单内容。脚本没跑时，当前项保留原来的白底（glassnav.css 只在挂上胶囊后才去掉它）。 */
(function glassNav() {
  const SPOTS = [
    { key: "sub", box: () => document.querySelector(".sub2 .mn"), on: ".l1.on", icon: true },
    { key: "l2", box: () => document.querySelector(".sub2 .l2slot:not([hidden])"), on: "a.on, button.on, a[aria-selected='true'], button[aria-selected='true'], a[aria-current='page']", noFold: true },
    { key: "rail", box: railBox, on: ".rk.on" },
    { key: "top", box: () => document.querySelector(".top2 .mods"), on: ".mod.on" },
  ];
  const last = {};   // key -> {x, y, w, h, sig}: where the capsule was, kept across re-renders
  const reduce = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  let queued = false;

  function railBox() {   // .rin is display:contents normally (items sit in .rail2), and a real box in the thin 12px state
    const rin = document.querySelector(".rail2 .rin");
    if (!rin) return null;
    return getComputedStyle(rin).display === "contents" ? rin.parentElement : rin;
  }
  const folded = () => { const a = document.querySelector(".v11app"); return !!a && a.classList.contains("sub-off"); };
  const visible = (el) => !!el && el.getClientRects().length > 0 && el.offsetWidth > 0;

  function targetOf(spot, box) {
    const all = [...box.querySelectorAll(spot.on)].filter((el) => !el.closest(".gcap") && visible(el) && (spot.key !== "sub" || !el.closest(".l2slot")));
    return all[0] || null;
  }
  function rectOf(spot, box, el) {
    const useIcon = spot.icon && folded() && el.querySelector(".it"), r = (useIcon ? el.querySelector(".it") : el).getBoundingClientRect();
    const b = box.getBoundingClientRect(), pad = useIcon ? 5 : 0;
    return { x: r.left - b.left - box.clientLeft + box.scrollLeft - pad, y: r.top - b.top - box.clientTop + box.scrollTop - pad, w: r.width + pad * 2, h: r.height + pad * 2 };
  }
  const sigOf = (el) => el.getAttribute("href") || el.getAttribute("data-step") || el.getAttribute("data-k") || (el.textContent || "").trim().slice(0, 40);
  const put = (cap, p) => { cap.style.transform = `translate(${p.x}px,${p.y}px)`; cap.style.width = p.w + "px"; cap.style.height = p.h + "px"; };
  const flag = (el, cls, on) => { if (el.classList.contains(cls) !== on) el.classList.toggle(cls, on); };   // touch the class only when it really changes (a no-op write still notifies the observer)
  const liveOf = (cap) => { const s = getComputedStyle(cap), m = new DOMMatrix(s.transform === "none" ? undefined : s.transform); return { x: m.m41, y: m.m42, w: parseFloat(s.width), h: parseFloat(s.height) }; };
  const same = (a, b) => a && b && Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5 && Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5;

  /** Slide from `from` to `to`: the leading edge runs ahead and the trailing edge lags (the capsule stretches), then a small overshoot settles back. */
  function slide(cap, from, to) {
    if (cap._anim) cap._anim.cancel();
    const horiz = Math.abs(to.x - from.x) > Math.abs(to.y - from.y);
    const P = horiz ? "x" : "y", S = horiz ? "w" : "h", Q = horiz ? "y" : "x", T = horiz ? "h" : "w";
    const d = to[P] - from[P], dist = Math.abs(d), dir = Math.sign(d) || 1;
    if (dist < 1 || reduce()) { put(cap, to); return; }
    let a, b;
    if (dir > 0) { a = from[P] + dist * 0.28; b = from[P] + from[S] + dist * 0.78; } else { a = from[P] - dist * 0.78; b = from[P] + from[S] - dist * 0.28; }
    const most = to[S] * 2.6;
    if (b - a > most) { if (dir > 0) a = b - most; else b = a + most; }
    const mid = { [P]: a, [S]: b - a, [Q]: (from[Q] + to[Q]) / 2 + 1, [T]: Math.min(from[T], to[T]) - 2 };
    const ov = Math.min(7, 3 + dist * 0.04) * dir, squash = { [P]: to[P] + ov + to[S] * 0.04, [S]: to[S] * 0.92, [Q]: to[Q] - 1, [T]: to[T] + 2 };
    const kf = (p, offset, easing) => ({ transform: `translate(${p.x}px,${p.y}px)`, width: p.w + "px", height: p.h + "px", offset, easing });
    put(cap, to);   // the resting place; the animation plays on top of it
    const anim = cap.animate([kf(from, 0, "cubic-bezier(.4,0,.6,1)"), kf(mid, 0.42, "cubic-bezier(.2,.7,.3,1)"), kf(squash, 0.76, "cubic-bezier(.3,0,.3,1)"), kf(to, 1)], { duration: Math.min(640, 380 + dist * 0.55) });
    cap._anim = anim;
    anim.onfinish = anim.oncancel = () => { if (cap._anim === anim) cap._anim = null; };
  }

  function sync() {
    queued = false;
    for (const spot of SPOTS) {
      const box = spot.box();
      if (!box) continue;
      if (spot.key === "rail") [document.querySelector(".rail2"), document.querySelector(".rail2 .rin")].forEach((o) => {   // the rail box switches between .rail2 and .rin (thin state): clear the other one
        if (!o || o === box) return; const c = o.querySelector(":scope > .gcap"); if (c) c.remove(); flag(o, "gl-on", false); });
      let cap = box.querySelector(":scope > .gcap");
      const el = targetOf(spot, box);
      if (!el || (spot.noFold && folded())) { if (cap) flag(cap, "gcap-off", true); flag(box, "gl-on", false); continue; }
      const fresh = !cap;
      if (fresh) { cap = document.createElement("span"); cap.className = "gcap"; cap.setAttribute("aria-hidden", "true"); box.insertBefore(cap, box.firstChild); }
      flag(cap, "gcap-off", false);
      flag(box, "gl-on", true);
      const to = rectOf(spot, box, el), sig = sigOf(el), prev = last[spot.key], now = cap._anim ? liveOf(cap) : cap._at;
      if (prev && prev.sig !== sig) slide(cap, fresh || !now ? prev : now, to);   // the current item changed (a click, or a new page): glide over
      else if (fresh || !same(now, to)) { if (cap._anim) cap._anim.cancel(); put(cap, to); }   // same item, layout moved (fold, resize, group opened): just follow
      cap._at = to; last[spot.key] = Object.assign({ sig }, to);
    }
  }
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(sync); } };

  function watch(app) {
    const mo = new MutationObserver((list) => { if (list.some((m) => !(m.target.classList && m.target.classList.contains("gcap")) && ![...m.addedNodes].every((n) => n.classList && n.classList.contains("gcap")))) later(); });
    const ro = new ResizeObserver(later);
    [".rail2", ".top2", ".sub2"].forEach((s) => { const n = app.querySelector(s); if (n) { mo.observe(n, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "aria-selected", "aria-current", "hidden"] }); ro.observe(n); } });
    new MutationObserver(later).observe(app, { attributes: true, attributeFilter: ["class"] });   // .sub-off / .thin
    app.addEventListener("transitionend", later);
    later();
  }
  function start() {
    const root = document.getElementById("app") || document.body;
    let seen = null;
    const look = () => { const a = root.querySelector(".v11app"); if (a && a !== seen) { seen = a; watch(a); } };
    new MutationObserver(look).observe(root, { childList: true });
    window.addEventListener("resize", later);
    window.addEventListener("hashchange", later);
    look();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
