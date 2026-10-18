"""A small Word (.docx) writer on the standard library: headings, paragraphs, bullets, quotes, tables and page breaks. The package is a zip of a few XML files.

Blocks are tuples: ("title", text) ("subtitle", text) ("h1", text) ("h2", text) ("p", text) ("note", text) ("bullet", text) ("quote", text)
("table", header: list[str], rows: list[list[str]]) ("pagebreak",).
"""
import io
import re
import zipfile
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")  # control characters are not allowed in XML text

STYLES = (f'{XML}<w:styles xmlns:w="{NS}">'
          '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="Microsoft YaHei" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>'
          '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
          '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:color w:val="0B382C"/><w:sz w:val="48"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:rPr><w:i/><w:color w:val="55645C"/><w:sz w:val="24"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="160"/></w:pPr><w:rPr><w:b/><w:color w:val="0B382C"/><w:sz w:val="32"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/></w:pPr><w:rPr><w:b/><w:color w:val="B84900"/><w:sz w:val="26"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="400"/></w:pPr><w:rPr><w:color w:val="444444"/></w:rPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:rPr><w:i/><w:color w:val="55645C"/><w:sz w:val="20"/></w:rPr></w:style></w:styles>')


def _t(text: str) -> str:
    return escape(_BAD.sub("", str(text)))


def _p(text: str, style: str | None = None, bold: bool = False, indent: bool = False) -> str:
    pr = (f'<w:pStyle w:val="{style}"/>' if style else "") + ('<w:ind w:left="360" w:hanging="260"/>' if indent else "")
    return f'<w:p><w:pPr>{pr}</w:pPr><w:r><w:rPr>{"<w:b/>" if bold else ""}</w:rPr><w:t xml:space="preserve">{_t(text)}</w:t></w:r></w:p>'


def _table(header: list[str], rows: list[list[str]]) -> str:
    border = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="C9D4CD"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    def cell(text: str, head: bool) -> str:
        shade = '<w:shd w:val="clear" w:color="auto" w:fill="E9EEEB"/>' if head else ""
        return f'<w:tc><w:tcPr>{shade}</w:tcPr>{_p(text, bold=head)}</w:tc>'
    def row(cells: list[str], head: bool = False) -> str:
        return "<w:tr>" + "".join(cell(c, head) for c in cells) + "</w:tr>"
    return f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>' + row(header, True) + "".join(row(r) for r in rows) + "</w:tbl><w:p/>"


def render(blocks: list[tuple]) -> bytes:
    """The document for these blocks, as the bytes of a .docx file."""
    body = []
    for b in blocks:
        kind = b[0]
        if kind == "title":
            body.append(_p(b[1], "Title"))
        elif kind == "subtitle":
            body.append(_p(b[1], "Subtitle"))
        elif kind in ("h1", "h2"):
            body.append(_p(b[1], "Heading1" if kind == "h1" else "Heading2"))
        elif kind == "p":
            body.append(_p(b[1]))
        elif kind == "note":
            body.append(_p(b[1], "Note"))
        elif kind == "bullet":
            body.append(_p("• " + b[1], indent=True))
        elif kind == "quote":
            body.append(_p(b[1], "Quote"))
        elif kind == "table":
            body.append(_table(b[1], b[2]))
        elif kind == "pagebreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    doc = f'{XML}<w:document xmlns:w="{NS}"><w:body>' + "".join(body) + "</w:body></w:document>"
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
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/_rels/document.xml.rels", docrels)
    return out.getvalue()
