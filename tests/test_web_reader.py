"""Reading a pasted web address: only public pages, never our own network."""
import socket

import httpx
import pytest

from app import web_reader
from app.web_reader import WebError

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(web_reader.socket, "getaddrinfo", lambda host, *a, **k: PUBLIC)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


PAGE = "<html><head><title>香港保险 · 介绍</title><style>p{}</style></head><body><nav>菜单</nav><h1>香港保险产品</h1><p>香港保险的特点是保障型产品居多，监管由保险业监管局负责。</p><script>x()</script></body></html>"


def test_addresses_are_found_in_a_message():
    assert web_reader.urls_in("看看这个 https://a.com/x?y=1，还有 http://b.org/p。以及 https://a.com/x?y=1") == ["https://a.com/x?y=1", "http://b.org/p"]
    assert web_reader.urls_in("没有链接") == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "http://127.0.0.1:8000/api", "http://localhost/x", "http://10.0.0.5/", "http://192.168.1.1/",
                                 "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "https://user:pw@example.com/"])
def test_internal_and_odd_addresses_are_refused(url):
    with pytest.raises(WebError):
        web_reader.check_url(url)


def test_a_public_page_becomes_plain_text(public_dns):
    r = web_reader.read("https://example.com/a", client(lambda req: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=PAGE.encode("utf-8"))))
    assert r["title"] == "香港保险 · 介绍" and "保障型产品居多" in r["text"] and "菜单" not in r["text"] and "x()" not in r["text"] and r["host"] == "example.com"


def test_a_redirect_into_the_internal_network_is_refused(monkeypatch):
    seen = []

    def dns(host, *a, **k):
        seen.append(host)
        return PUBLIC if host == "example.com" else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]

    monkeypatch.setattr(web_reader.socket, "getaddrinfo", dns)
    c = client(lambda req: httpx.Response(302, headers={"location": "http://internal.example.net/admin"}))
    with pytest.raises(WebError) as e:
        web_reader.read("https://example.com/go", c)
    assert "内部" in str(e.value) and "internal.example.net" in seen


@pytest.mark.parametrize("response,why", [
    (httpx.Response(404, headers={"content-type": "text/html"}, content=b"x"), "404"),
    (httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"), "不是普通网页"),
    (httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body>hi</body></html>"), "读不出文字"),
    (httpx.Response(200, headers={"content-type": "text/html"}, content=b"a" * (web_reader.MAX_BYTES + 10)), "太大"),
])
def test_pages_that_cannot_be_read_say_why(public_dns, response, why):
    with pytest.raises(WebError) as e:
        web_reader.read("https://example.com/p", client(lambda req: response))
    assert why in str(e.value)


def test_a_slow_site_gives_up(public_dns):
    def slow(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(WebError) as e:
        web_reader.read("https://example.com/p", client(slow))
    assert "太慢" in str(e.value)


def test_a_link_in_a_chat_message_reaches_the_model_and_only_the_host_is_audited(org, monkeypatch):
    from app.db import SessionLocal
    from app.models import AuditLog
    from app.routers import creation as creation_router
    calls = []

    class Chat:
        name = "fake"

        def complete(self, messages, temperature=0.0):
            import json
            calls.append(json.loads(messages[1]["content"]))
            return {"reply": "读到了。", "patch": {}, "suggestions": [], "needs": [], "ready": False}

    monkeypatch.setattr(creation_router, "_chat", lambda: Chat())

    def fake_read(url, client=None):
        if "bad" in url:
            raise WebError("这个网页打不开")
        return {"host": "example.com", "title": "介绍", "text": "香港保险监管由保险业监管局负责", "chars": 15, "truncated": False}

    monkeypatch.setattr(web_reader, "read", fake_read)
    pid = org.project()
    org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h(), json={"message": "参考 https://example.com/a?token=SECRET123 和 https://bad.example.org/x"})
    material = calls[-1]["MATERIAL"]
    assert "保险业监管局" in material and "读取失败" in material and "这个网页打不开" in material
    with SessionLocal() as db:
        meta = str([(r.meta, r.target) for r in db.query(AuditLog).filter(AuditLog.action == "creation.web").all()])
    assert "example.com" in meta and "SECRET123" not in meta
