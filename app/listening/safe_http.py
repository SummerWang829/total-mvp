"""Fetching public web pages for monitoring, safely and politely.

Researchers type the addresses, and the server fetches them, so this layer is a server-side-request-forgery boundary: only http(s) on normal ports,
the address must resolve to public internet addresses only (no private, loopback, link-local or cloud-metadata ranges), every redirect is checked again,
and size and time are capped. It is also polite: an identifiable User-Agent, robots.txt is obeyed, and a site is not hit more than once every few seconds.
No cookies, no logins, no tricks to get past blocks: when a site says no (robots.txt, 403, 429) we stop.
"""
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "XingtongResearchBot/1.0 (+https://deepresearch.morningstarsystem.tech/bot; public pages only; honours robots.txt)"
ALLOWED_PORTS = (None, 80, 443, 8080, 8443)
MAX_BYTES = 2_000_000
TIMEOUT_S = 15.0
MAX_REDIRECTS = 4
MIN_INTERVAL_S = 2.0       # between two requests to the same host
ROBOTS_TTL_S = 3600.0


class UnsafeUrl(Exception):
    """The address must not be fetched (wrong scheme or port, not a public internet address, or refused by robots.txt)."""


class FetchFailed(Exception):
    """The page could not be fetched (network, status, size); the message says why without leaking content."""


@dataclass
class Fetched:
    url: str
    status: int
    text: str
    content_type: str


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def default_resolver(host: str) -> list[str]:
    """All addresses a name resolves to."""
    return list({info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


class Fetcher:
    """A polite, safe page fetcher. `resolver` and `transport` can be replaced (tests use a fake DNS and a fake web)."""

    def __init__(self, resolver=default_resolver, transport: httpx.BaseTransport | None = None, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.resolver, self.transport, self.min_interval_s = resolver, transport, min_interval_s
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> str:
        """Raise UnsafeUrl unless this address may be fetched. Returns the host."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise UnsafeUrl("只支持 http / https 网址")
        if parts.username or parts.password:
            raise UnsafeUrl("网址里不能带账号密码")
        try:
            port = parts.port
        except ValueError as e:
            raise UnsafeUrl("网址端口不对") from e
        if port not in ALLOWED_PORTS:
            raise UnsafeUrl("不支持这个端口")
        host = parts.hostname
        try:
            addrs = [host] if _literal_ip(host) else self.resolver(host)
        except OSError as e:
            raise UnsafeUrl("网址解析不了") from e
        if not addrs or not all(_is_public(a) for a in addrs):
            raise UnsafeUrl("这个地址不是公开的互联网地址")
        return host

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, 0.0) + self.min_interval_s - now
            self._last[host] = now + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _client(self) -> httpx.Client:
        return httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,application/json;q=0.9,*/*;q=0.5"},
                            follow_redirects=False, timeout=TIMEOUT_S, transport=self.transport)

    def robots_ok(self, url: str) -> bool:
        """Whether robots.txt lets our bot fetch this page (an unreachable robots.txt counts as 'allowed' only when the site says it has none: 404)."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if not cached or time.monotonic() - cached[0] > ROBOTS_TTL_S:
            parser: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                got = self._get(origin + "/robots.txt", robots=False)
                if got.status == 404:
                    parser.parse([])
                elif got.status >= 400:
                    parser = None  # robots.txt exists but cannot be read: be careful, do not fetch
                else:
                    parser.parse(got.text.splitlines())
            except (FetchFailed, UnsafeUrl):
                parser = None
            cached = self._robots[origin] = (time.monotonic(), parser)
        return bool(cached[1] and cached[1].can_fetch(USER_AGENT, url))

    def _get(self, url: str, robots: bool) -> Fetched:
        current = url
        with self._client() as client:
            for _ in range(MAX_REDIRECTS + 1):
                host = self.check(current)
                if robots and not self.robots_ok(current):
                    raise UnsafeUrl("这个网站的 robots.txt 不允许抓取")
                self._wait_turn(host)
                try:
                    with client.stream("GET", current) as r:
                        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                            current = urljoin(current, r.headers["location"])
                            continue
                        chunks, size = [], 0
                        for chunk in r.iter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise FetchFailed("页面太大")
                            chunks.append(chunk)
                        text = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
                        return Fetched(current, r.status_code, text, r.headers.get("content-type", ""))
                except httpx.HTTPError as e:
                    raise FetchFailed(f"网络错误：{type(e).__name__}") from e
        raise FetchFailed("跳转太多次")

    def get(self, url: str) -> Fetched:
        """Fetch a page.

        Raises:
            UnsafeUrl: the address is not allowed (not public, robots.txt says no).
            FetchFailed: network error, too big, too many redirects, or the site answered with an error status.
        """
        got = self._get(url, robots=True)
        if got.status == 429 or got.status >= 400:
            raise FetchFailed(f"网站返回 {got.status}，已停止（不会绕过限制）")
        return got


def _literal_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
