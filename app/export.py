"""Insight export: a Word report and a CSV for a project. Built with the standard library only (a .docx is a zip of small XML files).

Participants appear as P-001 codes, never by name. Evidence quotes are the verbatim transcript sentences; the timestamp is the one stored with the quote.
"""
import csv
import io
import zipfile
from xml.sax.saxutils import escape

CSV_HEADER = ["受访者", "场次", "主题", "洞察", "状态", "来源", "引文", "发言人", "时间(秒)"]
STATUS_ZH = {"approved": "已通过", "draft": "待审核", "needs_review": "需复核"}
KIND_ZH = {"ai": "AI 生成", "manual": "人工"}
NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _csv_cell(v) -> str:
    """Spreadsheet programs run text that starts with = + - @ as a formula; a leading quote keeps it text."""
    s = str(v if v is not None else "")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_csv(rows: list[dict]) -> bytes:
    """One line per quote (an insight without quotes still gets one line). UTF-8 with a BOM so Excel reads Chinese correctly."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for r in rows:
        for e in r["evidence"] or [None]:
            w.writerow([_csv_cell(x) for x in (r["pcode"], r["interview"], r["theme"], r["statement"], STATUS_ZH.get(r["status"], r["status"]), KIND_ZH.get(r["kind"], r["kind"]),
                                              e["quote"] if e else "", (e["speaker"] or "") if e else "", round(e["start_ms"] / 1000, 1) if e else "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _p(text: str, style: str | None = None, bold: bool = False, italic: bool = False) -> str:
    pr = f'<w:pStyle w:val="{style}"/>' if style else ""
    rpr = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
    return f'<w:p><w:pPr>{pr}</w:pPr><w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def to_docx(project_name: str, rows: list[dict], generated: str) -> bytes:
    """A report grouped by theme: statement, status, then each verbatim quote with who said it and when."""
    body = [_p(project_name, "Title"), _p(f"洞察导出 · {generated} · 共 {len(rows)} 条", italic=True)]
    themes: dict[str, list[dict]] = {}
    for r in rows:
        themes.setdefault(r["theme"], []).append(r)
    for theme, items in themes.items():
        body.append(_p(theme, "Heading1"))
        for r in items:
            body.append(_p(r["statement"], bold=True))
            body.append(_p(f'{r["pcode"]} · {r["interview"]} · {STATUS_ZH.get(r["status"], r["status"])} · {KIND_ZH.get(r["kind"], r["kind"])}', italic=True))
            for e in r["evidence"]:
                body.append(_p(f'“{e["quote"]}” —— {e["speaker"] or "?"} {_clock(e["start_ms"])}', "Quote"))
    doc = f'{XML}<w:document xmlns:w="{NS}"><w:body>' + "".join(body) + "</w:body></w:document>"
    styles = (f'{XML}<w:styles xmlns:w="{NS}">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="Microsoft YaHei" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:color w:val="0B382C"/><w:sz w:val="44"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:spacing w:before="320"/></w:pPr><w:rPr><w:b/><w:color w:val="0B382C"/><w:sz w:val="30"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="400"/></w:pPr><w:rPr><w:color w:val="444444"/></w:rPr></w:style></w:styles>')
    types = (f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = f'{XML}<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OFFICE}/officeDocument" Target="word/document.xml"/></Relationships>'
    docrels = f'{XML}<Relationships xmlns="{REL}"><Relationship Id="rId1" Type="{OFFICE}/styles" Target="styles.xml"/></Relationships>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", docrels)
    return out.getvalue()
