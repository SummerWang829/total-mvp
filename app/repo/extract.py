"""Text out of an uploaded file for the research library: plain text, Markdown, CSV and Word (.docx).

A .docx is a zip of XML. It is read with a size check first (a small zip can hold a huge file) and with regular expressions instead of an XML parser, so a
hostile file cannot use entity tricks. PDF is not supported here (it needs a different tool); the message says how to get the text in.
"""
import io
import re
import zipfile
from html import unescape

MAX_UPLOAD = 8 * 1024 * 1024          # bytes of the uploaded file
MAX_XML = 25 * 1024 * 1024            # bytes of document.xml after unzipping
TEXT_TYPES = (".txt", ".md", ".markdown", ".csv", ".tsv")


class ExtractError(ValueError):
    """The file cannot be read as text; the message says why in plain Chinese."""


def docx_text(data: bytes) -> str:
    """Paragraphs of a Word file (headings, body, table cells), one per line.

    Raises:
        ExtractError: not a Word file, damaged, or too large once unzipped.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_XML:
                raise ExtractError("这个 Word 文件太大了")
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ExtractError("这不是有效的 Word（.docx）文件；旧的 .doc 请先另存为 .docx") from e
    paragraphs = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        p = re.sub(r"<w:tab/>|<w:br[^>]*/>", " ", p)
        text = "".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, flags=re.S))
        text = unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def file_text(name: str, data: bytes) -> str:
    """The text of an uploaded file, by its extension.

    Raises:
        ExtractError: too large, a PDF or another unsupported type, empty, or unreadable.
    """
    if len(data) > MAX_UPLOAD:
        raise ExtractError("文件太大了（最多 8 MB）")
    lower = name.lower()
    if lower.endswith(".docx"):
        text = docx_text(data)
    elif lower.endswith(".pdf"):
        raise ExtractError("暂不支持 PDF：请把 PDF 另存为 Word 或文字文件，或把内容复制粘贴进来")
    elif lower.endswith(TEXT_TYPES):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        raise ExtractError("只支持 .docx、.txt、.md、.csv 文件")
    if not text.strip():
        raise ExtractError("文件里没有可用的文字")
    return text
