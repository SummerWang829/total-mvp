/* 三个主题：浅色（默认白底、深绿强调）、橙色（暖白底、橙色为主，左侧导航也是橙色）、深色（深绿底）。
   选择只存在这台电脑的浏览器里（localStorage），不发给服务器。没有选过时跟随系统设置，和以前一样。
   访谈室（room.html）有自己的白/深绿切换，不受这里影响。 */
(function () {
  var KEY = "tt_theme", NAMES = { light: "浅色", orange: "橙色", dark: "深色" };
  function read() {
    try { var v = localStorage.getItem(KEY); return NAMES[v] ? v : ""; } catch (e) { return ""; }
  }
  function apply(v) {
    if (NAMES[v]) document.documentElement.setAttribute("data-theme", v);
    else document.documentElement.removeAttribute("data-theme");
  }
  function set(v) {
    try { if (NAMES[v]) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (e) { /* private window: keep it for this page only */ }
    apply(v);
    markCurrent();
  }
  function markCurrent() {
    var cur = read();
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      var on = b.getAttribute("data-theme-set") === cur;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.classList.toggle("on", on);
    });
  }
  /** The three-way switch shown at the bottom of the sidebar. */
  function html() {
    return '<div class="themesw" role="group" aria-label="主题">' +
      ["light", "orange", "dark"].map(function (k) {
        return '<button type="button" data-theme-set="' + k + '">' + NAMES[k] + "</button>";
      }).join("") + "</div>";
  }
  apply(read());
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-theme-set]");
    if (b) set(b.getAttribute("data-theme-set"));
  });
  document.addEventListener("DOMContentLoaded", markCurrent);
  window.TT_THEME = { html: html, set: set, get: read, markCurrent: markCurrent };
})();
