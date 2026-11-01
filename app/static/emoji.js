/* 表情面板：像手机输入法那样，搜索框在上、表情网格居中、分类栏在底部；最近使用、长按换肤色。
   数据来自本站 /vendor/emoji/data/*.json（读不到时用内置的常用表情，面板从不空白）。
   用法：EmojiPanel.open({ anchor, onPick, lang, quick, stay })；EmojiPanel.close()。 */
(function () {
  const FONT = '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji","Twemoji Mozilla",sans-serif';
  const CATS = [
    { k: "recent", icon: "🕘" }, { k: "face", icon: "😀", g: [0, 1] }, { k: "animal", icon: "🐻", g: [3] }, { k: "food", icon: "🍔", g: [4] },
    { k: "activity", icon: "⚽", g: [6] }, { k: "travel", icon: "🚗", g: [5] }, { k: "object", icon: "💡", g: [7] }, { k: "symbol", icon: "🔣", g: [8] }, { k: "flag", icon: "🏁", g: [9] },
  ];
  const TXT = {
    zh: { search: "搜索表情", none: "没有找到", recent: "最近使用", face: "表情与人物", animal: "动物与自然", food: "食物与饮料", activity: "活动", travel: "旅行与地点", object: "物品", symbol: "符号", flag: "旗帜", close: "关闭表情面板" },
    en: { search: "Search emoji", none: "Nothing found", recent: "Recently used", face: "Smileys & People", animal: "Animals & Nature", food: "Food & Drink", activity: "Activity", travel: "Travel & Places", object: "Objects", symbol: "Symbols", flag: "Flags", close: "Close emoji panel" },
    ms: { search: "Cari emoji", none: "Tiada hasil", recent: "Terkini", face: "Emoji & Orang", animal: "Haiwan & Alam", food: "Makanan & Minuman", activity: "Aktiviti", travel: "Perjalanan", object: "Objek", symbol: "Simbol", flag: "Bendera", close: "Tutup panel emoji" },
  };
  const COMMON = "😀😃😄😁😆😅😂🤣😊😇🙂😉😍🥰😘😋😎🤔😐😮😢😭😡👍👎👏🙏💪🤝❤️💔🔥✨🎉💯✅❌⭐🌟☀️🌙🍎🍔☕🎂🎁📌💡📷🎵🚗✈️🏠".match(/\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*/gu) || [];
  const RECENT_KEY = "tt_emoji_recent";
  const S = { data: null, loading: null, lang: "en", el: null, onPick: null, stay: false, anchor: null };

  const t = (k) => (TXT[S.lang] || TXT.en)[k];
  const recent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").slice(0, 24); } catch { return []; } };
  const remember = (g) => { try { localStorage.setItem(RECENT_KEY, JSON.stringify([g, ...recent().filter((x) => x !== g)].slice(0, 24))); } catch { /* private mode */ } };
  const lang2 = (l) => (l === "zh" ? "zh" : l === "ms" ? "ms" : "en");

  function load() {
    if (S.data) return Promise.resolve(S.data);
    if (!S.loading) {
      S.loading = fetch(`/vendor/emoji/data/${S.lang === "zh" ? "zh" : "en"}.json`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then((d) => (S.data = d.filter((e) => e.version <= 15)))
        .catch(() => { S.loading = null; return (S.data = COMMON.map((g, i) => ({ emoji: g, annotation: g, tags: [], group: 0, order: i }))); });
    }
    return S.loading;
  }
  const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

  function cell(e, onPick) {
    const b = mk("button", "emc", e.emoji); b.type = "button"; b.setAttribute("aria-label", e.annotation || e.emoji); b.title = e.annotation || "";
    let timer = null, longDone = false;
    const skins = e.skins || [];
    if (skins.length) {
      const showSkins = () => { longDone = true; skinRow(b, [{ emoji: e.emoji }, ...skins], onPick); };
      b.addEventListener("pointerdown", () => { longDone = false; timer = setTimeout(showSkins, 450); });
      ["pointerup", "pointerleave", "pointercancel"].forEach((n) => b.addEventListener(n, () => clearTimeout(timer)));
      b.addEventListener("contextmenu", (ev) => { ev.preventDefault(); showSkins(); });
    }
    b.addEventListener("click", () => { if (longDone) { longDone = false; return; } onPick(e.emoji); });
    return b;
  }
  function skinRow(near, list, onPick) {
    S.el.querySelectorAll(".emskin").forEach((x) => x.remove());
    const row = mk("div", "emskin"); const box = S.el.getBoundingClientRect(), r = near.getBoundingClientRect();
    list.forEach((s) => { const b = mk("button", "emc", s.emoji); b.type = "button"; b.onclick = () => { row.remove(); onPick(s.emoji); }; row.appendChild(b); });
    row.style.left = Math.max(4, Math.min(r.left - box.left - 20, box.width - list.length * 44 - 8)) + "px"; row.style.top = Math.max(4, r.top - box.top - 52) + "px";
    S.el.appendChild(row);
  }

  function build(opts) {
    const el = mk("div", "empanel"); el.setAttribute("role", "dialog"); el.setAttribute("aria-label", t("search"));
    const pick = (g) => { remember(g); if (S.onPick) S.onPick(g); if (!S.stay) close(); else renderRecent(); };
    if (opts.quick && opts.quick.length) {
      const q = mk("div", "emq");
      opts.quick.forEach(([label, g]) => { const b = mk("button", "emc big", g); b.type = "button"; b.setAttribute("aria-label", label); b.title = label; b.onclick = () => pick(g); q.appendChild(b); });
      el.appendChild(q);
    }
    const sr = mk("div", "emsearch"); const inp = mk("input"); inp.type = "search"; inp.placeholder = t("search"); inp.setAttribute("aria-label", t("search")); inp.autocomplete = "off"; sr.appendChild(inp);
    const grid = mk("div", "emg"); grid.tabIndex = 0;
    const tabs = mk("div", "emt"); tabs.setAttribute("role", "tablist");
    el.append(sr, grid, tabs);
    const secs = {};
    CATS.forEach((c) => {
      const b = mk("button", "emtab", c.icon); b.type = "button"; b.dataset.k = c.k; b.setAttribute("role", "tab"); b.setAttribute("aria-label", t(c.k)); b.title = t(c.k);
      b.onclick = () => { inp.value = ""; drawAll(); const s = secs[c.k]; if (s) grid.scrollTo({ top: s.offsetTop - grid.offsetTop, behavior: "smooth" }); };
      tabs.appendChild(b);
    });
    function section(k, list) {
      const sec = mk("section", "emsec"); sec.dataset.k = k; sec.appendChild(mk("h4", "", t(k)));
      const g = mk("div", "emcells"); list.forEach((e) => g.appendChild(cell(e, pick))); sec.appendChild(g); return sec;
    }
    function drawAll() {
      grid.textContent = ""; Object.keys(secs).forEach((k) => delete secs[k]);
      const data = S.data || [], byOrder = (a, b) => a.order - b.order, rec = recent().map((g) => data.find((e) => e.emoji === g) || { emoji: g, annotation: g }).filter(Boolean);
      CATS.forEach((c) => {
        const list = c.k === "recent" ? rec : data.filter((e) => c.g.includes(e.group)).sort(byOrder);
        if (list.length) grid.appendChild((secs[c.k] = section(c.k, list)));
      });
      tabs.querySelectorAll(".emtab").forEach((b) => (b.hidden = !secs[b.dataset.k]));
      mark();
    }
    function mark() {
      let cur = null; const top = grid.scrollTop + 8;
      Object.entries(secs).forEach(([k, s]) => { if (s.offsetTop - grid.offsetTop <= top) cur = k; });
      tabs.querySelectorAll(".emtab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.k === (cur || Object.keys(secs)[0]))));
    }
    function find(q) {
      q = q.trim().toLowerCase(); if (!q) return drawAll();
      const hit = (S.data || []).filter((e) => (e.annotation || "").toLowerCase().includes(q) || (e.tags || []).some((x) => x.toLowerCase().includes(q)) || e.emoji === q).slice(0, 120);
      grid.textContent = ""; Object.keys(secs).forEach((k) => delete secs[k]);
      if (!hit.length) { grid.appendChild(mk("p", "emnone", t("none"))); return; }
      const g = mk("div", "emcells"); hit.forEach((e) => g.appendChild(cell(e, pick))); grid.appendChild(g);
      tabs.querySelectorAll(".emtab").forEach((b) => b.setAttribute("aria-selected", "false"));
    }
    inp.addEventListener("input", () => find(inp.value));
    grid.addEventListener("scroll", mark, { passive: true });
    el.renderRecent = () => { if (!inp.value) { const keep = grid.scrollTop; drawAll(); grid.scrollTop = keep; } };
    el.search = inp;
    el.ready = load().then(() => { drawAll(); if (secs.recent) grid.scrollTop = 0; else grid.scrollTop = 0; });
    return el;
  }
  function renderRecent() { if (S.el && S.el.renderRecent) S.el.renderRecent(); }

  function place(anchor) {
    const el = S.el, phone = innerWidth <= 600;
    el.classList.toggle("sheet", phone);
    if (phone) { el.style.left = el.style.width = el.style.bottom = ""; return; }
    const r = anchor ? anchor.getBoundingClientRect() : { left: innerWidth / 2 - 220, width: 440, top: innerHeight - 70 };
    const w = Math.max(340, Math.min(r.width, 600)); el.style.width = w + "px";
    el.style.left = Math.max(8, Math.min(r.left + (r.width - w) / 2, innerWidth - w - 8)) + "px";
    el.style.bottom = Math.max(8, innerHeight - r.top + 8) + "px";
    el.style.maxHeight = Math.max(240, r.top - 16) + "px";
  }
  function onDoc(e) { if (S.el && !S.el.contains(e.target) && !(S.toggle && S.toggle.contains(e.target))) close(); }
  function onKey(e) { if (e.key === "Escape" && S.el) { const t0 = S.toggle; close(); if (t0) t0.focus(); } }
  function onSize() { if (S.el) place(S.anchor); }

  function open(opts) {
    close();
    S.lang = lang2(opts.lang); S.onPick = opts.onPick; S.stay = !!opts.stay; S.anchor = opts.anchor || null; S.toggle = opts.toggle || null;
    S.el = build(opts); document.body.appendChild(S.el); place(S.anchor);
    document.addEventListener("pointerdown", onDoc, true); document.addEventListener("keydown", onKey); addEventListener("resize", onSize);
    if (S.toggle) S.toggle.setAttribute("aria-expanded", "true");
    if (innerWidth > 600) S.el.search.focus({ preventScroll: true });   // on a phone the keyboard stays away: the panel is the keyboard
    return S.el.ready;
  }
  function close() {
    if (!S.el) return;
    S.el.remove(); S.el = null; document.removeEventListener("pointerdown", onDoc, true); document.removeEventListener("keydown", onKey); removeEventListener("resize", onSize);
    if (S.toggle) { S.toggle.setAttribute("aria-expanded", "false"); S.toggle = null; }
  }
  window.EmojiPanel = { open, close, isOpen: () => !!S.el, FONT };
})();
