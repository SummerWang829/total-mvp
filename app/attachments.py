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
UNREADABLE = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4a", ".mp3", ".wav")


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
        raise AttachmentError("图片和视频暂不支持（等后台接入对应的模型后再做），请先上传 PDF、Word 或文字文件", 415)
    if suffix == ".pdf":
        kind, text = "pdf", _from_pdf(data)
    elif suffix == ".docx":
        kind, text = "word", _from_docx(data)
    elif suffix in TEXT_SUFFIXES:
        kind, text = "text", _from_text(data)
    else:
        raise AttachmentError("只能上传 PDF、Word（.docx）或文字文件（.txt、.md、.csv）", 415)
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if not text:
        raise AttachmentError("没有读到文字（扫描件或图片组成的文件读不了）")
    return {"kind": kind, "text": text[:MAX_CHARS], "chars": len(text), "truncated": len(text) > MAX_CHARS}
