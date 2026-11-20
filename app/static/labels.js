/* 代码 → 中文（修正方案第 1 轮第 6 条）：界面上永远不显示程序里的代码（usage / none / skip / draft ……）。
   字典里查不到的值一律显示“其他”，并在控制台提醒一次，方便补字典；不把原值露给用户。 */
(() => {
  const warned = new Set();
  window.lbl = (text, code) => {
    if (text != null && text !== "") return text;
    if (code == null || code === "") return text ?? "";
    if (!warned.has(code)) { warned.add(code); console.warn("labels: 没有中文的代码", code); }
    return "其他";
  };
})();
