"""Export the interview outline as a finished document (任务包 11).

Markdown and a printable HTML (open it in a browser and print to PDF; Word opens the HTML too). The document holds the full outline:
title and the participant-facing title, research background and goals, the recruiting need, the length/language/question count, then every part with its questions —
their depth, guidance, and the ★ focus marks with their reasons — and the version and export time at the end. Research team only (the router checks that).

The recruiting questionnaire is the outline's first part in the merged-create design, so a question with choices (甄别问卷) is written with its options;
interview questions show their depth and guidance instead. This module only formats text it is given — it never reads the database itself.
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timezone

from . import formats as formats_mod, guide_text
from . import methods as methods_mod
from . import outline as outline_mod

DEPTH_NAME = {"none": "不追问", "shallow": "浅度追问", "deep": "深度追问", "free": "自由讨论"}


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _time() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _focus_lines(q: dict) -> list[str]:
    """The ★ mark and its reason, or nothing."""
    if not q.get("mark"):
        return []
    out = ["★ 重点关注"]
    if (q.get("reason") or "").strip():
        out.append(f"关注原因：{q['reason'].strip()}")
    return out


def _md_question(q: dict, idx: int) -> list[str]:
    """One question in Markdown: its text, then depth/guidance/focus as indented notes."""
    lines = [f"{idx}. {q['text']}"]
    depth = q.get("depth", guide_text.DEFAULT_DEPTH)
    if depth != guide_text.DEFAULT_DEPTH:
        lines.append(f"   - 追问深度：{DEPTH_NAME.get(depth, depth)}")
    if (q.get("guide") or "").strip():
        lines.append(f"   - 访谈指引：{q['guide'].strip()}")
    for note in _focus_lines(q):
        lines.append(f"   - {note}")
    return lines


def _html_question(q: dict, idx: int) -> str:
    """One question as a list item in the printable HTML."""
    notes = []
    depth = q.get("depth", guide_text.DEFAULT_DEPTH)
    if depth != guide_text.DEFAULT_DEPTH:
        notes.append(f'<span class="d">追问深度：{_esc(DEPTH_NAME.get(depth, depth))}</span>')
    if (q.get("guide") or "").strip():
        notes.append(f'<span class="g">访谈指引：{_esc(q["guide"].strip())}</span>')
    if q.get("mark"):
        notes.append(f'<span class="mk">★ 重点关注</span>')
        if (q.get("reason") or "").strip():
            notes.append(f'<span class="rs">关注原因：{_esc(q["reason"].strip())}</span>')
    notes_html = "".join(notes)
    return f'<li><span class="q">{_esc(q["text"])}</span>{notes_html}</li>'


def _parts(summary: dict) -> list[dict]:
    """The parts in reading order, with the focus-only count and a running question number."""
    parts, n = [], 0
    for p in summary.get("parts", []):
        qs, out = [], []
        for q in p.get("questions", []):
            if q.get("cover"):
                out.append({"text": q["text"], "cover": True, "depth": "shallow", "guide": "", "mark": False, "reason": ""})
                continue
            n += 1
            item = {"text": q["text"], "cover": False, "depth": q.get("depth"), "guide": q.get("guide", ""), "mark": q.get("mark", False), "reason": q.get("reason", "")}
            out.append(item)
            qs.append(item)
        parts.append({"title": p.get("title", ""), "kind": p.get("kind", "topic"), "questions": out, "focus": sum(1 for x in qs if x.get("mark"))})
    return parts


def _meta_block(summary: dict, proj, db, form) -> dict:
    """The header facts shared by both formats."""
    fmts = formats_mod.of(proj.formats, methods_mod.of(proj)).get("depth", []) or list(formats_mod.DEFAULT)
    return {
        "title": proj.name or "",
        "topic": summary.get("topic") or "",
        "minutes": summary.get("minutes", outline_mod.DEFAULT_MINUTES),
        "language": summary.get("language", "zh"),
        "count": summary.get("question_count", 0),
        "brief": (proj.brief or "").strip(),
        "goals": list(form.goals or []) if form else [],
        "need": (form.need or "").strip() if form else "",
        "formats": fmts,
    }


def build_markdown(summary: dict, proj, db, form, version: str) -> str:
    """The whole outline as Markdown text."""
    m = _meta_block(summary, proj, db, form)
    parts = _parts(summary)
    L: list[str] = []
    L.append(f"# {m['title']}")
    if m["topic"]:
        L.append(f"对外标题：{m['topic']}")
    L.append("")
    L.append(f"- 时长：约 {m['minutes']} 分钟　·　问题数：{m['count']}　·　访谈形式：{formats_mod.FORMATS.get(m['formats'][0], m['formats'][0]) if m['formats'] else '—'}")
    L.append("")
    if m["brief"]:
        L.append("## 研究背景")
        L.append(m["brief"])
        L.append("")
    if m["goals"]:
        L.append("## 研究目标")
        L += [f"- {g}" for g in m["goals"]]
        L.append("")
    if m["need"]:
        L.append("## 招募需求")
        L.append(m["need"])
        L.append("")
    L.append("## 访谈流程")
    L.append("")
    for p in parts:
        L.append(f"### {p['title']}")
        if p["focus"]:
            L.append(f"_含 {p['focus']} 处重点关注_")
        for i, q in enumerate(p["questions"], 1):
            L += _md_question(q, i)
        L.append("")
    L.append("---")
    L.append(f"版本：{version}　·　导出时间：{_time()}")
    return "\n".join(L).rstrip() + "\n"


_HTML_SHELL = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
  body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#1a1a1a;line-height:1.7;max-width:760px;margin:40px auto;padding:0 24px}}
  h1{{font-size:26px;margin-bottom:4px}} .sub{{color:#777;margin-bottom:20px}}
  .tags{{color:#6b6b6b;font-size:14px;margin-bottom:8px}}
  h2{{font-size:18px;border-bottom:1px solid #eee;padding-bottom:6px;margin:28px 0 10px}}
  h3{{font-size:16px;margin:20px 0 8px}} .fc{{color:#b45309;font-weight:600;font-size:12px;margin-left:6px}}
  ol,ul{{padding-left:22px}} li{{margin:6px 0}}
  .q{{font-weight:500}} .d,.g,.mk,.rs{{display:block;font-size:13px;color:#6b6b6b;margin:2px 0 0 4px}}
  .mk{{color:#b45309;font-weight:600}} .rs{{color:#9a3412}}
  .foot{{color:#999;font-size:12px;border-top:1px solid #eee;margin-top:32px;padding-top:8px}}
  @media print{{body{{margin:0;max-width:none}}}}
</style></head><body>
{body}
<div class="foot">版本：{version}　·　导出时间：{time}</div></body></html>"""


def build_html(summary: dict, proj, db, form, version: str) -> str:
    """The whole outline as a printable HTML document."""
    m = _meta_block(summary, proj, db, form)
    parts = _parts(summary)
    b: list[str] = []
    b.append(f"<h1>{_esc(m['title'])}</h1>")
    if m["topic"]:
        b.append(f'<div class="sub">对外标题：{_esc(m["topic"])}</div>')
    fmt = formats_mod.FORMATS.get(m["formats"][0], m["formats"][0]) if m["formats"] else "—"
    b.append(f'<div class="tags">时长：约 {m["minutes"]} 分钟　·　问题数：{m["count"]}　·　访谈形式：{_esc(fmt)}</div>')
    if m["brief"]:
        b.append(f"<h2>研究背景</h2><p>{_esc(m['brief'])}</p>")
    if m["goals"]:
        b.append("<h2>研究目标</h2><ul>" + "".join(f"<li>{_esc(g)}</li>" for g in m["goals"]) + "</ul>")
    if m["need"]:
        b.append(f"<h2>招募需求</h2><p>{_esc(m['need'])}</p>")
    b.append("<h2>访谈流程</h2>")
    for p in parts:
        fc = f'<span class="fc">含 {p["focus"]} 处重点关注</span>' if p["focus"] else ""
        b.append(f"<h3>{_esc(p['title'])}{fc}</h3><ol>")
        for i, q in enumerate(p["questions"], 1):
            b.append(_html_question(q, i))
        b.append("</ol>")
    body = "".join(b)
    return _HTML_SHELL.format(title=_esc(m["title"]), body=body, version=_esc(version), time=_esc(_time()))
