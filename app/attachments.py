"""Read a document the team attaches to an AI chat (PDF, Word, plain text) into plain text.

Nothing is stored: the text goes back to the page, which sends it with the next chat message. Images and video are not read yet
(they need an image-recognition or a speech model, which the AI console's model routing will bring). File names and contents are never logged.
"""
import html
import io
import logging
import re
import zipfile

log = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024          # one file
MAX_CHARS = 30_000                    # what is handed to the AI: the rest is cut, and the page says so
MAX_PDF_PAGES = 100
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024  # the unpacked size of word/document.xml (a zip-bomb guard)
TEXT_SUFFIXES = (".txt", ".md", ".csv")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")   # read by OCR: only the words in the image (app/ocr.py)
UNREADABLE = (".gif", ".heic", ".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4a", ".mp3", ".wav")   # audio / video go to /api/attachments/transcribe
XLSX_ROWS = 200          # 修正方案第 1 轮第 2 条: the first 200 rows of each sheet
XLSX_SHEETS = 10
MAX_XLSX_XML_BYTES = 40 * 1024 * 1024


class AttachmentError(ValueError):
    """The file cannot be read; the message is plain Chinese for the page. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _suffix(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", name or "")
    return m.group(1).lower() if m else ""


def _from_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise AttachmentError("文字文件的编码读不出来，请另存为 UTF-8 再上传")


def _from_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise AttachmentError("Word 文件太大，读不了")
            xml = z.read(info).decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise AttachmentError("这不是一个能读的 Word（.docx）文件") from e
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AttachmentError("这个 PDF 有密码，读不了")
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n".join((p.extract_text() or "") for p in pages)
    except AttachmentError:
        raise
    except (PyPdfError, ValueError, OSError, KeyError) as e:
        log.warning("pdf could not be read: %s", type(e).__name__)
        raise AttachmentError("这个 PDF 读不出来") from e


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(0, n - 1)


def _xml_text(fragment: str) -> str:
    return html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", fragment)))


def _from_xlsx(data: bytes) -> str:
    """Every sheet's first rows as a plain table (cells joined by " | "), read with the standard library (no spreadsheet package needed)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_XLSX_XML_BYTES:
                raise AttachmentError("Excel 文件太大，读不了")
            names = set(z.namelist())
            shared = [_xml_text(si) for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8", "replace"), re.S)] if "xl/sharedStrings.xml" in names else []
            book = z.read("xl/workbook.xml").decode("utf-8", "replace")
            rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace") if "xl/_rels/workbook.xml.rels" in names else ""
            targets = dict(re.findall(r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels)) | {k: v for v, k in re.findall(r'<Relationship[^>]*Target="([^"]+)"[^>]*Id="([^"]+)"', rels)}
            out = []
            for name, rid in re.findall(r'<sheet[^>]*name="([^"]*)"[^>]*r:id="([^"]+)"', book)[:XLSX_SHEETS]:
                path = targets.get(rid, "").lstrip("/")
                path = path if path.startswith("xl/") else "xl/" + path
                if path not in names:
                    continue
                xml = z.read(path).decode("utf-8", "replace")
                lines = []
                for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S)[:XLSX_ROWS]:
                    cells: dict[int, str] = {}
                    for attrs, body in re.findall(r"<c([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                        ref = re.search(r'r="([A-Z]+)', attrs)
                        typ = re.search(r't="([^"]+)"', attrs)
                        v = re.search(r"<v>([^<]*)</v>", body or "")
                        if typ and typ.group(1) == "s" and v and v.group(1).isdigit() and int(v.group(1)) < len(shared):
                            val = shared[int(v.group(1))]
                        elif typ and typ.group(1) == "inlineStr":
                            val = _xml_text(body or "")
                        else:
                            val = html.unescape(v.group(1)) if v else ""
                        if val.strip():
                            cells[_col(ref.group(1) if ref else "")] = " ".join(val.split())
                    if cells:
                        lines.append(" | ".join(cells.get(i, "") for i in range(max(cells) + 1)))
                if lines:
                    out.append(f"表：{html.unescape(name)}\n" + "\n".join(lines))
            return "\n\n".join(out)
    except (zipfile.BadZipFile, KeyError) as e:
        raise AttachmentError("这不是一个能读的 Excel（.xlsx）文件") from e


def extract(filename: str, data: bytes) -> dict:
    """The plain text of an attached file.

    Args:
        filename: the name the browser sent (only its ending is used).
        data: the file's bytes.

    Returns:
        {"kind", "text", "chars" (before cutting), "truncated"}.

    Raises:
        AttachmentError: too big, an image or video, a type we do not read, or a file that cannot be read or has no text.
    """
    if len(data) > MAX_BYTES:
        raise AttachmentError("文件太大，请控制在 10MB 以内", 413)
    suffix = _suffix(filename)
    if suffix in UNREADABLE:
        raise AttachmentError("这种文件不能在这里读：录音和视频请用 .mp3 / .m4a / .wav / .mp4 等格式，会转成文字", 415)
    if suffix in IMAGE_SUFFIXES:
        from . import ocr
        try:
            kind, text = "image", ocr.read_image(data)
        except ocr.OCRError as e:
            raise AttachmentError(str(e), e.status) from e
    elif suffix == ".xlsx":
        kind, text = "excel", _from_xlsx(data)
    elif suffix == ".pdf":
        kind, text = "pdf", _from_pdf(data)
    elif suffix == ".docx":
        kind, text = "word", _from_docx(data)
    elif suffix in TEXT_SUFFIXES:
        kind, text = "text", _from_text(data)
    else:
        raise AttachmentError("只能上传 PDF、Word（.docx）、Excel（.xlsx）、文字文件（.txt、.md、.csv）、图片或录音视频", 415)
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not text:
        raise AttachmentError("没有读到文字（扫描件或图片组成的文件读不了）")
    return {"kind": kind, "text": text[:MAX_CHARS], "chars": len(text), "truncated": len(text) > MAX_CHARS}
