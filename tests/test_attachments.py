"""Reading an attached document into text: PDF, Word, text; images and video are refused; nothing is stored or logged."""
import io
import zipfile

import pytest

from app import attachments
from conftest import Org

NL = b"\n"


def make_pdf(text: str) -> bytes:
    """A one-page PDF with a proper cross-reference table (what real files have)."""
    stream = f"BT /F1 18 Tf 20 50 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>" % len(stream) + NL + b"stream" + NL + stream + NL + b"endstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = b"%PDF-1.4" + NL, []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + NL + o + NL + b"endobj" + NL
    xref = len(out)
    out += b"xref" + NL + b"0 %d" % (len(objs) + 1) + NL + b"0000000000 65535 f " + NL
    out += b"".join(b"%010d 00000 n " % o + NL for o in offsets)
    return out + b"trailer<</Root 1 0 R/Size %d>>" % (len(objs) + 1) + NL + b"startxref" + NL + b"%d" % xref + NL + b"%%EOF"


def docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_a_text_file_is_read_in_utf8_or_gbk():
    assert attachments.extract("a.txt", "研究背景：保险".encode("utf-8"))["text"] == "研究背景：保险"
    assert attachments.extract("a.md", "研究背景".encode("gb18030"))["text"] == "研究背景"


def test_a_word_file_gives_its_paragraphs():
    out = attachments.extract("b.docx", docx_bytes(["第一段 &amp; 要点", "第二段"]))
    assert out["kind"] == "word" and out["text"].splitlines() == ["第一段 & 要点", "第二段"]


def test_a_pdf_gives_its_text():
    out = attachments.extract("c.pdf", make_pdf("Hello brief"))
    assert out["kind"] == "pdf" and "Hello brief" in out["text"]


def test_images_and_video_are_refused_with_a_clear_message():
    for name in ("x.png", "y.JPG", "z.mp4"):
        with pytest.raises(attachments.AttachmentError) as e:
            attachments.extract(name, b"\x00\x01")
        assert e.value.status == 415 and "暂不支持" in str(e.value)


def test_unknown_types_broken_files_and_empty_files_are_refused():
    for name, data in (("a.exe", b"MZ"), ("a.docx", b"not a zip"), ("a.pdf", b"nope"), ("a.txt", b"   " + NL)):
        with pytest.raises(attachments.AttachmentError):
            attachments.extract(name, data)


def test_a_long_text_is_cut_and_says_so_and_a_huge_file_is_refused():
    out = attachments.extract("a.txt", ("字" * (attachments.MAX_CHARS + 5)).encode())
    assert out["truncated"] and len(out["text"]) == attachments.MAX_CHARS and out["chars"] == attachments.MAX_CHARS + 5
    with pytest.raises(attachments.AttachmentError) as e:
        attachments.extract("a.txt", b"x" * (attachments.MAX_BYTES + 1))
    assert e.value.status == 413


def test_the_endpoint_is_for_staff_only_and_stores_nothing(org: Org):
    files = {"file": ("note.txt", "访谈资料".encode(), "text/plain")}
    assert org.c.post("/api/attachments/extract", files=files).status_code in (401, 403)
    r = org.c.post("/api/attachments/extract", headers=org.h("researcher"), files=files)
    assert r.status_code == 200 and r.json()["text"] == "访谈资料"
    assert org.c.post("/api/attachments/extract", headers=org.h("observer"), files={"file": ("n.txt", b"x", "text/plain")}).status_code == 403
    bad = org.c.post("/api/attachments/extract", headers=org.h("researcher"), files={"file": ("p.png", b"x", "image/png")})
    assert bad.status_code == 415
