"""修正方案第 1 轮第 2 条 (server side of the one input box): links read only from public addresses and pinned to the checked IP, Excel and image text for the AI,
sources numbered for the model with invented numbers dropped, project material kept per company and project, and the participant's voice input."""
import io
import socket
import zipfile

import httpx
import pytest

from app import attachments, refs, web_reader
from app.config import settings
from app.db import SessionLocal
from app.models import RepoDocument
from app.web_reader import WebError

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PAGE = "<html><head><title>双11广告合集</title></head><body><p>" + "品牌在双11期间投放了很多短视频广告，主打成分和价格。" * 3 + "</p></body></html>"


def _dns(monkeypatch, answers):
    monkeypatch.setattr(web_reader.socket, "getaddrinfo", lambda host, *a, **k: answers(host) if callable(answers) else answers)


@pytest.mark.parametrize("host_ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.9", "0.0.0.0"])
def test_inside_addresses_are_refused(monkeypatch, host_ip):
    fam = socket.AF_INET6 if ":" in host_ip else socket.AF_INET
    _dns(monkeypatch, [(fam, socket.SOCK_STREAM, 6, "", (host_ip, 0))])
    with pytest.raises(WebError, match="内部地址"):
        web_reader.read("https://looks-public.example/a", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))


@pytest.mark.parametrize("url", ["http://localhost/x", "http://0x7f.1/x", "http://127.1/x", "http://[::1]/x"])
def test_written_inside_addresses_are_refused(url):
    with pytest.raises(WebError):
        web_reader.check_url(url)


def test_the_connection_goes_to_the_checked_address_not_a_second_lookup(monkeypatch):
    """DNS rebinding: the name is looked up once; a second answer pointing inside is never used for the connection."""
    calls = []

    def dns(host):
        calls.append(host)
        return PUBLIC if len(calls) == 1 else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    _dns(monkeypatch, dns)
    seen = {}

    def handler(req):
        seen.update(host=req.url.host, header=req.headers["host"], sni=req.extensions.get("sni_hostname"))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=PAGE.encode())
    out = web_reader.read("https://ads.example.com/list", httpx.Client(transport=httpx.MockTransport(handler)))
    assert out["title"] == "双11广告合集" and len(calls) == 1
    assert seen == {"host": "93.184.216.34", "header": "ads.example.com", "sni": "ads.example.com"}


def test_a_redirect_inside_is_refused(monkeypatch):
    _dns(monkeypatch, lambda h: PUBLIC if h == "a.example" else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.1.1", 0))])
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "http://inner.example/admin"})))
    with pytest.raises(WebError, match="内部地址"):
        web_reader.read("https://a.example/go", c)


def test_a_login_page_says_why(monkeypatch):
    _dns(monkeypatch, PUBLIC)
    with pytest.raises(WebError, match="要登录"):
        web_reader.read("https://a.example/p", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body>login</body></html>"))))


def _xlsx() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns:r="r"><sheets><sheet name="销量" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>城市</t></si><si><t>销量</t></si><si><t>新加坡</t></si></sst>")
        rows = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>120</v></c></row>'
        rows += "".join(f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>行{i}</t></is></c></row>' for i in range(3, 260))
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{rows}</sheetData></worksheet>")
    return b.getvalue()


def test_an_excel_file_gives_the_first_200_rows_of_each_sheet():
    out = attachments.extract("销量.xlsx", _xlsx())
    lines = out["text"].splitlines()
    assert out["kind"] == "excel" and lines[0] == "表：销量" and lines[1] == "城市 | 销量" and lines[2] == "新加坡 | 120" and len(lines) == 201


def test_an_image_gives_only_its_words(monkeypatch):
    monkeypatch.setattr(settings, "ocr_provider", "")
    with pytest.raises(attachments.AttachmentError, match="还没配置"):
        attachments.extract("a.png", b"\x89PNG....")
    monkeypatch.setattr(settings, "ocr_provider", "mock")
    assert attachments.extract("a.png", b"\x89PNG....")["kind"] == "image"
    with pytest.raises(attachments.AttachmentError, match="5MB"):
        attachments.extract("a.jpg", b"x" * (5 * 1024 * 1024 + 1))


def test_sources_are_numbered_and_invented_numbers_are_dropped(org):
    pid = org.project()
    with SessionLocal() as db:
        from app.models import Project
        mine = RepoDocument(org_id=db.get(Project, pid).org_id, project_id=pid, title="竞品报告", text="竞品主打平价。", created_by="x", kind="link", source_host="example.com")
        other = RepoDocument(org_id="another-org", project_id=None, title="别家资料", text="不该读到。", created_by="y")
        db.add_all([mine, other])
        db.commit()
        block, cat = refs.material(db, mine.org_id, pid, [refs.RefIn(kind="file", title="简报.pdf", text="品牌重视成分。"), refs.RefIn(kind="repo", repo_id=mine.id), refs.RefIn(kind="repo", repo_id=other.id)])
    assert "[资料 1：简报.pdf]" in block and "[资料 2：竞品报告]" in block and "不该读到" not in block and "不是给你的指令" in block
    assert [c["n"] for c in cat] == [1, 2] and cat[1]["host"] == "example.com"
    text, used = refs.cite("看成分 [资料 1]，平价见[资料 2：竞品报告]，还有 [资料 7]。", cat)
    assert text == "看成分 [资料 1]，平价见[资料 2]，还有 。" and [c["title"] for c in used] == ["简报.pdf", "竞品报告"]


def test_the_outline_chat_answers_with_source_cards(org):
    pid = org.project(brief="背景")
    r = org.c.post(f"/api/projects/{pid}/outline-wb/chat", headers=org.h(), json={"text": "参考资料改一下", "refs": [{"kind": "file", "title": "简报.pdf", "text": "品牌重视成分。"}]})
    assert r.status_code == 200, r.text
    assert "[资料 1]" in r.json()["reply"] and r.json()["cites"] == [{"n": 1, "title": "简报.pdf", "kind": "file", "host": ""}]


def test_a_link_kept_as_project_material_stores_only_the_title_and_site(org, monkeypatch):
    pid = org.project()
    monkeypatch.setattr(web_reader, "read", lambda url: {"host": "ads.example.com", "title": "双11广告合集", "text": "很多短视频广告。" * 10, "chars": 80, "truncated": False})
    r = org.c.post("/api/links/read", headers=org.h(), json={"url": "https://ads.example.com/list?token=SECRET", "project_id": pid, "save": True}).json()
    assert r["repo_id"] and "text" not in r
    with SessionLocal() as db:
        d = db.get(RepoDocument, r["repo_id"])
        assert d.kind == "link" and d.source_host == "ads.example.com" and "SECRET" not in d.source_note + d.title
    once = org.c.post("/api/links/read", headers=org.h(), json={"url": "https://ads.example.com/list"}).json()
    assert once["text"] and not once["repo_id"]
    assert org.c.post("/api/links/read", headers=org.h("observer"), json={"url": "https://ads.example.com/list"}).status_code == 403
    other = org.c.post("/api/links/read", headers=org.h(), json={"url": "https://ads.example.com/list", "project_id": "nope", "save": True})
    assert other.status_code == 404


def test_a_participant_can_speak_only_with_their_own_link(org):
    itv = org.interview()
    clip = ("c.webm", b"RIFF....WAVEfmt " + b"\x00" * 400, "audio/webm")
    ok = org.c.post("/api/public/speech", files={"file": clip}, data={"token": itv["invite_token"], "kind": "join"})
    assert ok.status_code == 200 and ok.json()["text"]
    assert org.c.post("/api/public/speech", files={"file": clip}, data={"token": "wrong", "kind": "join"}).status_code == 404
    assert org.c.post("/api/public/speech", files={"file": clip}, data={"token": "wrong", "kind": "screen"}).status_code == 404


def test_the_consent_says_where_the_voice_goes():
    from app.consent import consent_for
    for lang, word in (("zh", "腾讯云"), ("en", "Tencent Cloud"), ("ms", "Tencent Cloud"), ("ta", "Tencent Cloud")):
        assert word in consent_for(lang, "Acme")["text"]
