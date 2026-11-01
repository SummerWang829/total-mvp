/* 三个主题：浅色（默认，白底、深绿强调）、橙色（暖白底、橙色为主，导航也是橙色）、深色（深绿底）。
   研究员端右上角固定一个小胶囊（太阳=浅色、叶子=橙色、月亮=深色），点哪个切到哪个；右下角的圆形按钮“回到顶部”滚动后才出现。
   选择只存在这台电脑的浏览器里（localStorage），不发给服务器。没有选过时是默认的浅色。访谈室也跟随这里的选择。 */
(function () {
  var KEY = "tt_theme", ORDER = ["light", "orange", "dark"];
  var NAMES = { light: "浅色", orange: "橙色", dark: "深色" };
  var ICON = {
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    orange: '<path d="M5 19c0-9 5-14 15-14 0 10-5 15-14 15"/><path d="M5 19c3-5 6-8 10-10"/>',
    dark: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>'
  };
  var svg = function (inner, n) { return '<svg width="' + n + '" height="' + n + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + "</svg>"; };
  function read() {
    try { var v = localStorage.getItem(KEY); return ORDER.indexOf(v) >= 0 ? v : "light"; } catch (e) { return "light"; }
  }
  function apply(v) {
    document.documentElement.setAttribute("data-theme", v);
    document.querySelectorAll(".tsw button").forEach(function (b) { var on = b.dataset.theme === v; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
  }
  function set(v) {
    try { localStorage.setItem(KEY, v); } catch (e) { /* private window: this page only */ }
    apply(v);
  }
  function next() { set(ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length]); }
  function html() { return ""; }   // the old sidebar button is gone: see mount()
  /** The switch (top right) and the back-to-top button (bottom right), added once to the researcher app. */
  function mount() {
    if (document.querySelector(".tsw")) return;
    var sw = document.createElement("div");
    sw.className = "tsw"; sw.setAttribute("role", "radiogroup"); sw.setAttribute("aria-label", "主题");
    sw.innerHTML = ORDER.map(function (k) { return '<button type="button" role="radio" data-theme="' + k + '" aria-label="' + NAMES[k] + '" title="' + NAMES[k] + '">' + svg(ICON[k], 16) + "</button>"; }).join("");
    sw.addEventListener("click", function (e) { var b = e.target.closest && e.target.closest("button[data-theme]"); if (b) set(b.dataset.theme); });
    document.body.appendChild(sw);
    var up = document.createElement("button");
    up.type = "button"; up.className = "totop"; up.hidden = true; up.setAttribute("aria-label", "回到顶部"); up.title = "回到顶部";
    up.innerHTML = svg('<path d="M12 19V5M5 12l7-7 7 7"/>', 20);
    document.body.appendChild(up);
    var last = null;   // the thing that scrolled most recently (the page, or a box inside it)
    document.addEventListener("scroll", function (e) {
      var t = e.target === document ? document.scrollingElement : e.target;
      if (!t || t.nodeType !== 1 || (t.closest && t.closest(".empanel"))) return;
      if (t.scrollTop > 300) { last = t; up.hidden = false; } else if (t === last) { up.hidden = true; }
    }, true);
    up.addEventListener("click", function () {
      if (last) last.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion:reduce)").matches ? "auto" : "smooth" });
      up.hidden = true;
    });
    window.addEventListener("hashchange", function () { up.hidden = true; last = null; });
    apply(read());
  }
  apply(read());
  window.TT_THEME = { html: html, next: next, get: read, set: set, mount: mount };
})();
