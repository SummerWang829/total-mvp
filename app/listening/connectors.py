"""Where public opinion comes from. A connector turns one source's settings into a list of raw items; everything after that (cleaning, de-duplication,
tagging) is the same for all. New channels are new connectors, nothing else changes.

Built in: `mock` (made-up items for trying the page out and for tests), `rss` (news sites, blogs and official accounts that publish a feed),
`appstore` (Apple App Store customer reviews, Apple's public review feed), `web` (a public listing page read with CSS selectors the researcher
 gives; the page is fetched by our own polite fetcher and only *parsed* with Scrapling's parser: its stealth / anti-bot browsers are deliberately not used). Planned: YouTube (official API key), Reddit (after Reddit's approval),
vendors (the team's own account at a data provider), polite public-page reader.
"""
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol

from .safe_http import Fetcher, FetchFailed, UnsafeUrl

MAX_ITEMS = 200
MAX_TEXT = 2000
KINDS = ("mock", "rss", "appstore", "web")


class ConfigError(ValueError):
    """The source's settings are not usable; the message is plain Chinese for the researcher."""


@dataclass
class RawItem:
    """One public post / review / article as collected."""
    url: str
    text: str
    title: str = ""
    posted_at: datetime | None = None
    author: str = ""            # only ever hashed by the service; never stored as is
    metrics: dict = field(default_factory=dict)


class Connector(Protocol):
    def fetch(self, config: dict, fetcher: Fetcher) -> list[RawItem]:
        """Collect items. Raises FetchFailed / UnsafeUrl on trouble."""
        ...


class _Strip(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(raw: str) -> str:
    """Plain text from a snippet that may contain HTML, whitespace squeezed, cut to MAX_TEXT."""
    p = _Strip()
    p.feed(raw or "")
    p.close()
    return re.sub(r"\s+", " ", html.unescape("".join(p.parts))).strip()[:MAX_TEXT]


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    for parse in (lambda v: parsedate_to_datetime(v), lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            dt = parse(value.strip())
        except (TypeError, ValueError):
            continue
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    return None


# ---------- mock ----------
_MOCK = {
    "en": ["{b} delivery was slow again, waited over an hour and the food was cold", "Love the new {b} app, ordering is so easy and the price is fair",
           "{b} customer service never replied to my refund request, very disappointing", "Honestly {b} is expensive compared to others but quality is good",
           "The {b} app keeps crashing when I try to pay, please fix this bug", "Great promotion from {b} this week, saved a lot on my order"],
    "zh": ["{b} 的配送太慢了，等了一个多小时，送到都凉了", "很喜欢 {b} 的新版 App，下单特别方便，价格也合理", "{b} 的客服一直没回复我的退款申请，太让人失望了",
           "说实话 {b} 价格偏贵，不过品质确实不错", "{b} 的 App 付款时总是闪退，希望尽快修复", "{b} 这周的优惠力度很大，省了不少钱"],
}


class MockConnector:
    """Made-up, repeatable items about a brand (so the page and the tests have something to show; clearly not real people)."""

    def fetch(self, config: dict, fetcher: Fetcher) -> list[RawItem]:
        brand, lang = str(config.get("brand") or "示例品牌")[:60], config.get("lang") if config.get("lang") in _MOCK else "zh"
        n = max(1, min(int(config.get("n", 6)), 50))
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = []
        for i in range(n):
            tpl = _MOCK[lang][i % len(_MOCK[lang])]
            out.append(RawItem(url=f"https://example.invalid/mock/{hashlib.sha1(f'{brand}{lang}{day}{i}'.encode()).hexdigest()[:12]}", text=tpl.format(b=brand),
                               posted_at=datetime.now(timezone.utc).replace(tzinfo=None), author=f"mock-user-{i % 4}", metrics={"mock": True}))
        return out


# ---------- rss / atom ----------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml: str, keywords: list[str]) -> list[RawItem]:
    """Items of an RSS 2.0 or Atom feed, optionally only those that mention one of the keywords.

    Raises:
        FetchFailed: not a feed, or it declares entities / a doctype (refused: such documents can blow up the parser).
    """
    if re.search(r"<!(DOCTYPE|ENTITY)", xml, re.IGNORECASE):
        raise FetchFailed("订阅源含有不安全的声明，已拒绝")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise FetchFailed("不是有效的订阅源（RSS/Atom）") from e
    kw = [k.lower() for k in keywords if k.strip()]
    out: list[RawItem] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f = {_local(c.tag): c for c in el}
        title = clean_text((f["title"].text or "") if "title" in f else "")
        body = clean_text(" ".join((f[k].text or "") for k in ("description", "summary", "content", "encoded") if k in f))
        link = ""
        if "link" in f:
            link = (f["link"].attrib.get("href") or f["link"].text or "").strip()
        when = _when(next((f[k].text for k in ("pubDate", "published", "updated", "date") if k in f), None))
        author = ""
        if "creator" in f:
            author = (f["creator"].text or "").strip()
        elif "author" in f:
            author = clean_text(" ".join(t.strip() for t in f["author"].itertext()))
        text = f"{title}. {body}".strip(". ") if body else title
        if not link or not text or (kw and not any(k in text.lower() for k in kw)):
            continue
        out.append(RawItem(url=link, text=text[:MAX_TEXT], title=title, posted_at=when, author=author))
        if len(out) >= MAX_ITEMS:
            break
    return out


class RssConnector:
    def fetch(self, config: dict, fetcher: Fetcher) -> list[RawItem]:
        url = str(config.get("url", "")).strip()
        if not url:
            raise ConfigError("请填写订阅源网址")
        return parse_feed(fetcher.get(url).text, list(config.get("keywords") or []))


# ---------- apple app store reviews ----------
class AppStoreConnector:
    def fetch(self, config: dict, fetcher: Fetcher) -> list[RawItem]:
        app_id, country = str(config.get("app_id", "")).strip(), str(config.get("country", "sg")).strip().lower()
        pages = max(1, min(int(config.get("pages", 1)), 5))
        if not app_id.isdigit() or len(country) != 2 or not country.isalpha():
            raise ConfigError("App Store 来源需要数字的 App 编号和两位国家代码")
        out: list[RawItem] = []
        for page in range(1, pages + 1):
            got = fetcher.get(f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json")
            try:
                entries = json.loads(got.text)["feed"].get("entry", [])
            except (ValueError, KeyError, AttributeError) as e:
                raise FetchFailed("App Store 返回的内容看不懂") from e
            for e in entries if isinstance(entries, list) else [entries]:
                if "im:rating" not in e:  # the first entry is the app itself
                    continue
                text = clean_text(f'{e.get("title", {}).get("label", "")}. {e.get("content", {}).get("label", "")}')
                link = (e.get("link", {}) or {}).get("attributes", {}).get("href", "") if isinstance(e.get("link"), dict) else ""
                out.append(RawItem(url=link or f"https://apps.apple.com/{country}/app/id{app_id}#{e.get('id', {}).get('label', '')}", text=text,
                                   posted_at=_when(e.get("updated", {}).get("label")), author=e.get("author", {}).get("name", {}).get("label", ""),
                                   metrics={"rating": int(e["im:rating"]["label"])} if str(e["im:rating"].get("label", "")).isdigit() else {}))
        return out[:MAX_ITEMS]


# ---------- a public listing page, read with CSS selectors ----------
def _selector_ok(sel: str) -> bool:
    from scrapling.parser import Selector
    try:
        Selector("<div><a href='/x'>x</a></div>").css(sel)
    except Exception:  # noqa: BLE001 - any parser error means the selector is not usable
        return False
    return True


class WebPageConnector:
    """Read items off one public page: `item` selects each post, `text` / `link` / `title` / `date` / `author` are selectors inside it (all optional but `item`)."""

    def fetch(self, config: dict, fetcher: Fetcher) -> list[RawItem]:
        from scrapling.parser import Selector
        got = fetcher.get(str(config.get("url", "")).strip())
        page = Selector(got.text, url=got.url)
        kw = [k.lower() for k in config.get("keywords") or [] if str(k).strip()]

        def pick(el, sel):
            found = el.css(sel) if sel else []
            return found[0] if len(found) else None

        out: list[RawItem] = []
        for el in page.css(config["item"])[:MAX_ITEMS]:
            node = pick(el, config.get("text")) or el
            text = clean_text(node.get_all_text())
            if not text or (kw and not any(k in text.lower() for k in kw)):
                continue
            link_el = pick(el, config.get("link"))
            href = (link_el.attrib.get("href") if link_el is not None else "") or ""
            url = page.urljoin(href) if href else f"{got.url}#{hashlib.sha1(text.encode()).hexdigest()[:12]}"
            title_el, date_el, author_el = pick(el, config.get("title")), pick(el, config.get("date")), pick(el, config.get("author"))
            out.append(RawItem(url=url, text=text, title=clean_text(title_el.get_all_text()) if title_el is not None else "",
                               posted_at=_when(clean_text(date_el.get_all_text())) if date_el is not None else None,
                               author=clean_text(author_el.get_all_text()) if author_el is not None else ""))
        return out


CONNECTORS: dict[str, Connector] = {"mock": MockConnector(), "rss": RssConnector(), "appstore": AppStoreConnector(), "web": WebPageConnector()}


def validate_config(kind: str, config: dict) -> dict:
    """The settings a source of this kind keeps (anything else is dropped).

    Raises:
        ConfigError: unknown kind or unusable settings.
    """
    if kind not in CONNECTORS:
        raise ConfigError("不支持的来源类型")
    if kind == "mock":
        return {"brand": str(config.get("brand") or "示例品牌")[:60], "lang": "en" if config.get("lang") == "en" else "zh", "n": max(1, min(int(config.get("n", 6) or 6), 50))}
    if kind == "rss":
        url = str(config.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ConfigError("请填写订阅源网址（以 http:// 或 https:// 开头）")
        kws = [str(k).strip()[:60] for k in (config.get("keywords") or []) if str(k).strip()][:20]
        return {"url": url[:500], "keywords": kws}
    if kind == "web":
        url, item = str(config.get("url", "")).strip(), str(config.get("item", "")).strip()
        if not url.startswith(("http://", "https://")) or not item:
            raise ConfigError("网页来源需要网址，以及“每条内容”的 CSS 选择器")
        out = {"url": url[:500], "item": item[:200], "keywords": [str(k).strip()[:60] for k in (config.get("keywords") or []) if str(k).strip()][:20]}
        for key in ("text", "link", "title", "date", "author"):
            if str(config.get(key, "")).strip():
                out[key] = str(config[key]).strip()[:200]
        if not all(_selector_ok(v) for k, v in out.items() if k in ("item", "text", "link", "title", "date", "author")):
            raise ConfigError("有选择器的写法不对（要用 CSS 选择器，例如 div.post 或 a.title）")
        return out
    app_id, country = str(config.get("app_id", "")).strip(), str(config.get("country", "sg")).strip().lower()
    if not app_id.isdigit() or len(country) != 2 or not country.isalpha():
        raise ConfigError("App Store 来源需要数字的 App 编号和两位国家代码")
    return {"app_id": app_id, "country": country, "pages": max(1, min(int(config.get("pages", 1) or 1), 5))}


__all__ = ["CONNECTORS", "KINDS", "ConfigError", "RawItem", "UnsafeUrl", "clean_text", "parse_feed", "validate_config"]
