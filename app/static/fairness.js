/* Speaking-fairness panel of a focus group. Shared by the room page (live, for the moderator and hidden observers) and the interview page
   (review afterwards). It draws what GET /api/interviews/{iid}/group/metrics returns, with textContent only (nothing from the data is parsed as HTML).
   Each seat is a number ("参与者 ·03"), never a name. State is shown by a symbol AND a word, never by colour alone. */
(function () {
  const STATE = { ok: ["●", "正常"], quiet: ["◐", "偏少"], high: ["▲", "偏多"], silent: ["○", "沉默"] };
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function ago(s) {
    if (s == null) return "还没发过言";
    if (s < 60) return "刚刚说过";
    return `${Math.floor(s / 60)} 分钟前说过`;
  }
  function minutes(s) {
    return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  }

  function spark(values) {
    const box = el("span", "fspark");
    box.setAttribute("aria-hidden", "true");
    const top = Math.max(60, ...values);  // a full bar is a full minute of talking
    values.forEach((v) => { const b = el("i"); b.style.height = Math.max(2, Math.round((v / top) * 18)) + "px"; box.appendChild(b); });
    return box;
  }

  function row(p, expected) {
    const [icon, word] = STATE[p.state] || STATE.ok;
    const li = el("li", "frow s-" + p.state);
    const head = el("div", "fh");
    head.appendChild(el("b", null, p.label));
    head.appendChild(el("span", "fst", `${icon} ${word}`));
    li.appendChild(head);
    const bar = el("div", "fbar");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", `${p.label}：占发言时间的 ${Math.round(p.share * 100)}%，大家平均应占 ${Math.round(expected * 100)}%`);
    const fill = el("i");
    fill.style.width = Math.min(100, Math.round(p.share * 100)) + "%";
    const tick = el("u");
    tick.style.left = Math.min(100, Math.round(expected * 100)) + "%";
    bar.append(fill, tick);
    li.appendChild(bar);
    const meta = el("div", "fm");
    meta.appendChild(el("span", null, `占 ${Math.round(p.share * 100)}% · 共 ${minutes(p.talk_s)} · ${p.turns} 次`));
    meta.appendChild(el("span", null, ago(p.silent_s)));
    li.appendChild(meta);
    if (p.interrupts || p.interrupted) li.appendChild(el("div", "fm", `打断别人 ${p.interrupts} 次 · 被打断 ${p.interrupted} 次`));
    li.appendChild(spark(p.spark));
    return li;
  }

  function curve(series) {
    const w = 300, h = 64, pad = 6;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("class", "fcurve");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "公平指数随时间的变化：" + series.map(([m, v]) => `第 ${m} 分钟 ${v.toFixed(2)}`).join("，"));
    const last = series[series.length - 1][0] || 1;
    const x = (m) => pad + ((w - 2 * pad) * m) / last;
    const y = (v) => h - pad - (h - 2 * pad) * v;
    const guide = document.createElementNS(NS, "line");  // the "balanced" line (0.75)
    guide.setAttribute("x1", pad); guide.setAttribute("x2", w - pad); guide.setAttribute("y1", y(0.75)); guide.setAttribute("y2", y(0.75)); guide.setAttribute("class", "guide");
    svg.appendChild(guide);
    const line = document.createElementNS(NS, "polyline");
    line.setAttribute("points", series.map(([m, v]) => `${x(m).toFixed(1)},${y(v).toFixed(1)}`).join(" "));
    line.setAttribute("class", "line");
    svg.appendChild(line);
    return svg;
  }

  /* host: an element to draw into (its content is replaced). d: the metrics. opts.series: also draw the curve (review page). */
  window.renderFairness = function (host, d, opts) {
    opts = opts || {};
    const box = el("div", "fair");
    const top = el("div", "ftop");
    top.appendChild(el("span", null, "公平指数"));
    top.appendChild(el("b", null, d.fairness == null ? "—" : d.fairness.toFixed(2)));
    top.appendChild(el("span", "fword", d.fairness_word));
    box.appendChild(top);
    if (d.hints && d.hints.length) {
      const ul = el("ul", "fhints");
      ul.setAttribute("aria-label", "需要留意");
      d.hints.forEach((h) => ul.appendChild(el("li", null, h)));
      box.appendChild(ul);
    }
    const rows = el("ul", "frows");
    d.participants.forEach((p) => rows.appendChild(row(p, d.expected_share)));
    box.appendChild(rows);
    const mod = d.moderator;
    box.appendChild(el("p", "fnote", `主持人说话 ${minutes(mod.talk_s)}，占全场 ${Math.round(mod.share_of_all * 100)}%` + (mod.interrupts ? `；打断参与者 ${mod.interrupts} 次` : "")));
    if (opts.series) {
      if (d.fairness_series.length > 1) {
        box.appendChild(el("h4", null, "公平指数的变化（虚线 = 0.75，达到算均衡）"));
        box.appendChild(curve(d.fairness_series));
      } else {
        box.appendChild(el("p", "fnote", "发言时间还不够长，画不出变化曲线。"));
      }
    }
    box.appendChild(el("p", "fnote", "时长依据实时字幕估算，可能有几秒误差；条上的竖线是“大家平均应占”的位置。"));
    host.textContent = "";
    host.appendChild(box);
  };
})();
