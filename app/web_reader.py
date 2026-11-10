"""Read a web page the team pastes into the creation chat (a link), as plain text for the AI to read. Nothing is stored.

The server fetches the page, so a link must never be a way into our own network: only http / https, the host must resolve to public addresses only (loopback, private,
link-local, cloud-metadata and other reserved ranges are refused), every redirect is checked again, and size and time are limited. The address itself may carry a token, so
only the host name is ever logged or audited.
"""
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 20_000
MAX_REDIRECTS = 3
TIMEOUT_S = 10.0
URL_RE = re.compile(r"https?://[^\s<>\"'，。；、）)】]+", re.IGNORECASE)
SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}


class WebError(ValueError):
    """The page cannot be read; the message is plain Chinese for the page."""


def urls_in(text: str, limit: int = 2) -> list[str]:
    """The distinct web addresses written in a message, in order (at most `limit`)."""
    seen: list[str] = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?")
        if u not in seen:
            seen.append(u)
    return seen[:limit]


def _public(host: str) -> None:
    """Raises WebError unless every address the host resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise WebError("这个网址打不开（找不到这个网站）") from e
    if not infos:
        raise WebError("这个网址打不开")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise WebError("这个网址指向内部地址，不能读取")


def check_url(url: str) -> str:
    """The address as a clean string, or WebError (scheme, host, public address)."""
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise WebError("只能读 http / https 开头的网页地址")
    if p.username or p.password:
        raise WebError("网址里不能带账号密码")
    _public(p.hostname)
    return p.geturl()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if self._in_title:
            self.title += data
        elif not self.skip and data.strip():
            self.parts.append(data.strip() + " ")


def page_text(html: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page."""
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return " ".join(p.title.split())[:120], re.sub(r"\n\s*\n+", "\n", text).strip()


def read(url: str, client: httpx.Client | None = None) -> dict:
    """Fetch one page and return {"host", "title", "text", "chars", "truncated"}.

    Raises:
        WebError: refused or unreadable (the message is plain Chinese).
    """
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (compatible; TotalResearchBot/1.0)"})
    try:
        current = check_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = check_url(urljoin(current, r.headers["location"]))
                        continue
                    if r.status_code >= 400:
                        raise WebError(f"这个网页没有打开（对方返回 {r.status_code}）")
                    ctype = r.headers.get("content-type", "").lower()
                    if ctype and not any(k in ctype for k in ("text/html", "text/plain", "application/xhtml")):
                        raise WebError("这个链接不是普通网页（可能是文件、图片或视频），请下载后用 📎 上传")
                    data = b""
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            raise WebError("这个网页太大了，读不了")
                    enc = r.encoding or "utf-8"
                    break
            except httpx.TimeoutException as e:
                raise WebError("这个网页打开太慢了，读不了") from e
            except httpx.HTTPError as e:
                raise WebError("这个网页打不开") from e
        else:
            raise WebError("这个网址转来转去，读不了")
        try:
            html = data.decode(enc, errors="replace")
        except LookupError:
            html = data.decode("utf-8", errors="replace")
        title, text = page_text(html) if "html" in ctype or "<html" in html[:2000].lower() else ("", " ".join(html.split()))
        if len(text) < 30:
            raise WebError("这个网页里读不出文字（可能要登录，或是靠脚本生成的页面）")
        return {"host": urlparse(current).hostname or "", "title": title, "text": text[:MAX_CHARS], "chars": min(len(text), MAX_CHARS), "truncated": len(text) > MAX_CHARS}
    finally:
        if own:
            client.close()
