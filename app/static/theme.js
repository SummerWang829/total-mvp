/* 三个主题：浅色（默认，白底、深绿强调）、橙色（暖白底、橙色为主，导航也是橙色）、深色（深绿底）。
   左下角一个小按钮，每点一下切换到下一个主题。选择只存在这台电脑的浏览器里（localStorage），不发给服务器。
   没有选过时是默认的浅色（深绿强调）。访谈室也跟随这里的选择（它原来的白/深绿按钮仍然保留）。 */
(function () {
  var KEY = "tt_theme", ORDER = ["light", "orange", "dark"];
  var NAMES = { light: "浅色", orange: "橙色", dark: "深色" };
  function read() {
    try { var v = localStorage.getItem(KEY); return ORDER.indexOf(v) >= 0 ? v : "light"; } catch (e) { return "light"; }
  }
  function apply(v) {
    document.documentElement.setAttribute("data-theme", v);
    var b = document.querySelector("[data-theme-cycle]");
    if (b) b.textContent = "主题：" + NAMES[v];
  }
  function next() {
    var cur = read(), v = ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length];
    try { localStorage.setItem(KEY, v); } catch (e) { /* private window: this page only */ }
    apply(v);
  }
  /** The small button at the bottom-left of the sidebar. */
  function html() {
    return '<button type="button" class="themebtn" data-theme-cycle title="点击切换：浅色、橙色、深色">主题：' + NAMES[read()] + "</button>";
  }
  apply(read());
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-theme-cycle]")) next();
  });
  window.TT_THEME = { html: html, next: next, get: read };
})();
