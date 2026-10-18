"""Public-opinion monitoring: the safe, polite fetcher; the connectors (rss, app store, public page, mock); collection, de-duplication, tagging, take-downs;
and who may use it."""
import json

import httpx
import pytest

from app.db import SessionLocal
from app.listening import service
from app.listening.connectors import ConfigError, parse_feed, validate_config, CONNECTORS
from app.listening.safe_http import FetchFailed, Fetcher, UnsafeUrl
from app.models import ListeningSource, Mention
from conftest import H
from test_project_members import second_researcher

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731 - a fake DNS: every name is a public address

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>News</title>
<item><title>Acme delivery delays anger customers</title><link>https://news.example/a1</link><description>&lt;p&gt;Many say Acme was &lt;b&gt;slow&lt;/b&gt;.&lt;/p&gt;</description>
<pubDate>Mon, 12 Oct 2026 08:00:00 GMT</pubDate><dc:creator>Jane Reporter</dc:creator></item>
<item><title>Weather today</title><link>https://news.example/w1</link><description>Sunny</description></item>
<item><title>Acme launches new app</title><link>https://news.example/a2</link><description>Customers love it. Call 91234567 for press.</description></item></channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom post about Acme</title><link href="https://blog.example/p1"/><summary>Acme is great</summary>
<updated>2026-10-12T08:00:00Z</updated><author><name>Bob</name></author></entry></feed>"""

APPSTORE = json.dumps({"feed": {"entry": [
    {"im:name": {"label": "Acme"}, "id": {"label": "app"}},
    {"title": {"label": "Keeps crashing"}, "content": {"label": "The app crashes at checkout"}, "im:rating": {"label": "1"}, "updated": {"label": "2026-10-12T08:00:00-07:00"},
     "author": {"name": {"label": "sam123"}}, "id": {"label": "r1"}},
    {"title": {"label": "Love it"}, "content": {"label": "Easy and fast"}, "im:rating": {"label": "5"}, "author": {"name": {"label": "amy"}}, "id": {"label": "r2"}}]}})

PAGE = """<html><body><div class="post"><a class="t" href="/t/1">Slow delivery</a><p class="b">Acme delivery is <b>so slow</b> lately</p><span class="u">kev</span><time>2026-10-10</time></div>
<div class="post"><a class="t" href="/t/2">Nice app</a><p class="b">The Acme app is good value</p><span class="u">lin</span></div>
<div class="post"><p class="b">Unrelated chatter</p></div></body></html>"""


def web(routes):
    """A fetcher over a fake internet: {url or path: (status, body)} (robots.txt allows everything unless given)."""
    def handler(req: httpx.Request) -> httpx.Response:
        key = str(req.url)
        status, body = routes.get(key) or routes.get(req.url.path) or ((404, "") if req.url.path == "/robots.txt" else (404, "not found"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})
    return Fetcher(resolver=PUBLIC, transport=httpx.MockTransport(handler), min_interval_s=0)


# ---------- the safe fetcher ----------
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.example/a", "http://127.0.0.1/", "http://localhost:8000/", "http://10.0.0.5/x", "http://192.168.1.1/",
                                 "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://user:pw@x.example/", "http://x.example:22/", "javascript:alert(1)", "http:///nohost"])
def test_addresses_that_are_not_the_public_internet_are_refused(url):
    with pytest.raises(UnsafeUrl):
        Fetcher(resolver=PUBLIC).check(url)


def test_a_name_that_resolves_to_a_private_address_is_refused_even_when_one_answer_is_public():
    f = Fetcher(resolver=lambda h: ["93.184.216.34", "10.0.0.8"])
    with pytest.raises(UnsafeUrl):
        f.check("https://sneaky.example/")
    with pytest.raises(UnsafeUrl):
        Fetcher(resolver=lambda h: []).check("https://nothing.example/")


def test_a_redirect_into_the_private_network_is_refused_at_that_hop():
    def handler(req):
        if req.url.host == "start.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, text="secret")
    f = Fetcher(resolver=lambda h: ["93.184.216.34"] if h == "start.example" else ["169.254.169.254"], transport=httpx.MockTransport(handler), min_interval_s=0)
    with pytest.raises(UnsafeUrl):
        f.get("https://start.example/go")


def test_robots_txt_is_obeyed_and_a_blocked_page_is_not_fetched():
    f = web({"/robots.txt": (200, "User-agent: *\nDisallow: /private\n"), "/public": (200, "ok"), "/private": (200, "secret")})
    assert f.get("https://site.example/public").text == "ok"
    with pytest.raises(UnsafeUrl):
        f.get("https://site.example/private")


def test_an_unreadable_robots_txt_means_do_not_fetch_but_a_missing_one_means_allowed():
    assert web({"/page": (200, "x")}).get("https://a.example/page").text == "x"       # robots.txt 404: allowed
    with pytest.raises(UnsafeUrl):
        web({"/robots.txt": (500, "err"), "/page": (200, "x")}).get("https://b.example/page")


def test_a_site_that_says_no_is_not_pushed_and_big_pages_are_refused():
    with pytest.raises(FetchFailed):
        web({"/p": (429, "slow down")}).get("https://c.example/p")
    with pytest.raises(FetchFailed):
        web({"/p": (403, "no")}).get("https://c.example/p")
    with pytest.raises(FetchFailed):
        web({"/big": (200, "x" * 2_100_000)}).get("https://c.example/big")


def test_requests_to_one_site_are_spaced_out():
    import time
    f = Fetcher(resolver=PUBLIC, transport=httpx.MockTransport(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 200, text="x")), min_interval_s=0.3)
    t0 = time.monotonic()
    f.get("https://d.example/1")     # robots.txt, then the page, then the second page: three requests, each at least 0.3 s after the one before
    f.get("https://d.example/2")
    assert time.monotonic() - t0 >= 0.55


def test_our_bot_says_who_it_is(monkeypatch):
    seen = []
    f = Fetcher(resolver=PUBLIC, transport=httpx.MockTransport(lambda r: (seen.append(r.headers["user-agent"]), httpx.Response(404 if r.url.path == "/robots.txt" else 200, text="x"))[1]), min_interval_s=0)
    f.get("https://e.example/")
    assert all("XingtongResearchBot" in ua and "robots.txt" in ua for ua in seen) and seen


# ---------- connectors ----------
def test_rss_items_are_cleaned_filtered_by_keyword_and_carry_date_and_author():
    items = parse_feed(RSS, ["acme"])
    assert [i.url for i in items] == ["https://news.example/a1", "https://news.example/a2"]
    assert items[0].text.startswith("Acme delivery delays anger customers.") and "<" not in items[0].text and items[0].posted_at.year == 2026 and items[0].author == "Jane Reporter"
    assert len(parse_feed(RSS, [])) == 3


def test_atom_feeds_work_too():
    items = parse_feed(ATOM, [])
    assert items[0].url == "https://blog.example/p1" and items[0].author == "Bob" and items[0].posted_at is not None


def test_a_feed_with_entity_declarations_or_garbage_is_refused():
    with pytest.raises(FetchFailed):
        parse_feed('<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>', [])
    with pytest.raises(FetchFailed):
        parse_feed("this is not xml", [])


def test_app_store_reviews_are_read_without_the_app_entry():
    f = web({"/sg/rss/customerreviews/page=1/id=123/sortby=mostrecent/json": (200, APPSTORE)})
    items = CONNECTORS["appstore"].fetch({"app_id": "123", "country": "sg"}, f)
    assert [i.metrics["rating"] for i in items] == [1, 5] and "crashes" in items[0].text and items[0].author == "sam123"
    with pytest.raises(ConfigError):
        CONNECTORS["appstore"].fetch({"app_id": "abc", "country": "sg"}, f)


def test_a_public_page_is_read_with_the_researchers_selectors_via_scrapling_parser():
    f = web({"/forum": (200, PAGE)})
    cfg = validate_config("web", {"url": "https://forum.example/forum", "item": "div.post", "text": "p.b", "link": "a.t", "title": "a.t", "author": "span.u", "date": "time", "keywords": ["acme"]})
    items = CONNECTORS["web"].fetch(cfg, f)
    assert [i.url for i in items] == ["https://forum.example/t/1", "https://forum.example/t/2"]
    assert items[0].text == "Acme delivery is so slow lately"
    assert items[0].title == "Slow delivery" and items[0].author == "kev" and items[0].posted_at is not None


def test_settings_are_checked_when_a_source_is_made():
    for kind, cfg in (("web", {"url": "ftp://x", "item": "div"}), ("web", {"url": "https://x.example", "item": ""}), ("web", {"url": "https://x.example", "item": "div[["}),
                      ("rss", {"url": "not a url"}), ("appstore", {"app_id": "x", "country": "sg"}), ("nope", {})):
        with pytest.raises(ConfigError):
            validate_config(kind, cfg)
    assert validate_config("rss", {"url": "https://x.example/feed", "keywords": [" a ", "", "b"], "extra": 1}) == {"url": "https://x.example/feed", "keywords": ["a", "b"]}


# ---------- collection, de-duplication, tagging, take-downs ----------
def make_source(db, org_ctx, kind="mock", config=None, project_id=None):
    s = ListeningSource(org_id=org_ctx["org"], project_id=project_id or org_ctx["pid"], kind=kind, name="s", config=validate_config(kind, config or {"brand": "Acme", "lang": "en"}), created_by="u")
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def ctx(org):
    pid = org.project()
    with SessionLocal() as db:
        from app.models import Project
        return {"org": db.get(Project, pid).org_id, "pid": pid}


def test_collecting_twice_stores_each_item_once_and_masks_contact_details_and_hides_names(org, ctx):
    f = web({"/feed": (200, RSS)})
    with SessionLocal() as db:
        s = make_source(db, ctx, "rss", {"url": "https://news.example/feed", "keywords": ["acme"]})
        r1, r2 = service.run_source(db, s, f), service.run_source(db, s, f)
        db.commit()
        assert r1 == {"ok": True, "new": 2, "error": ""} and r2["new"] == 0
        rows = db.query(Mention).all()
        assert len(rows) == 2 and all(m.lang == "en" for m in rows)
        assert "91234567" not in " ".join(m.text for m in rows)             # a phone number in a public post is masked
        reporter = next(m for m in rows if m.url.endswith("a1"))
        assert reporter.author_hash and "Jane" not in reporter.author_hash and len(reporter.author_hash) == 16
        assert s.last_status == "ok" and s.last_count == 0


def test_a_failing_source_records_why_in_plain_words_and_the_others_are_unaffected(org, ctx):
    with SessionLocal() as db:
        bad = make_source(db, ctx, "rss", {"url": "http://127.0.0.1/feed"})
        r = service.run_source(db, bad, web({}))
        assert r["ok"] is False and "公开" in r["error"] and bad.last_status == "error" and bad.last_run_at is not None
        blocked = make_source(db, ctx, "rss", {"url": "https://site.example/feed"})
        assert service.run_source(db, blocked, web({"/robots.txt": (200, "User-agent: *\nDisallow: /\n")}))["error"].endswith("不允许抓取")
        assert "403" in service.run_source(db, blocked, web({"/feed": (403, "no")}))["error"]


def test_tagging_gives_sentiment_and_topics_and_a_failed_batch_is_retried_then_given_up(org, ctx):
    with SessionLocal() as db:
        s = make_source(db, ctx)
        service.run_source(db, s)
        db.commit()
        assert db.query(Mention).filter(Mention.enriched_at.is_(None)).count() == 6
        assert service.enrich_pending(db) == 6
        db.commit()
        got = {m.sentiment for m in db.query(Mention)}
        assert got <= {"neg", "neu", "pos"} and "neg" in got and "pos" in got
        assert any("配送" in (m.topics or []) or "价格" in (m.topics or []) for m in db.query(Mention))

        class Broken:
            def classify_mentions(self, items):
                raise ValueError("boom")
        s2 = make_source(db, ctx, "mock", {"brand": "Other", "lang": "zh"})
        service.run_source(db, s2)
        db.commit()
        for _ in range(service.MAX_ENRICH_ATTEMPTS + 2):
            service.enrich_pending(db, llm=Broken())
        pending = db.query(Mention).filter(Mention.enriched_at.is_(None)).all()
        assert pending and all(m.enrich_attempts == service.MAX_ENRICH_ATTEMPTS for m in pending)


def test_odd_model_answers_are_tolerated(org, ctx):
    class Odd:
        def classify_mentions(self, items):
            return [{"n": 0, "sentiment": "angry", "topics": "not a list"}, {"n": "x"}, {"n": 99, "sentiment": "pos"}, {"n": 1, "sentiment": "pos", "topics": ["  价格  ", "", "a" * 80, "b", "c"]}]
    with SessionLocal() as db:
        service.run_source(db, make_source(db, ctx))
        db.commit()
        assert service.enrich_pending(db, llm=Odd(), limit=2) == 2
        db.commit()
        done = {m.sentiment: m for m in db.query(Mention).filter(Mention.enriched_at.isnot(None))}
        assert set(done) == {"neu", "pos"}                                  # "angry" is not a sentiment: neutral
        assert done["neu"].topics == []                                     # topics that are not a list: none
        assert done["pos"].topics == ["价格", "a" * 30, "b"]                # trimmed, cut to 30 characters, at most three


def test_language_detection_tells_chinese_malay_tamil_and_english_apart():
    assert service.detect_lang("这家的配送太慢了") == "zh" and service.detect_lang("Saya tidak suka dengan perkhidmatan ini, tapi yang lain okay") == "ms"
    assert service.detect_lang("இந்த சேவை மிகவும் மோசம்") == "ta" and service.detect_lang("Great service, thanks") == "en" and service.detect_lang("") == "en"


def test_take_down_by_link_or_author_removes_and_is_remembered(org, ctx):
    f = web({"/feed": (200, RSS)})
    with SessionLocal() as db:
        s = make_source(db, ctx, "rss", {"url": "https://news.example/feed", "keywords": ["acme"]})
        service.run_source(db, s, f)
        db.commit()
        assert service.take_down(db, ctx["org"], ctx["pid"], "u", url="https://news.example/a1/#frag") == 1
        assert service.take_down(db, ctx["org"], ctx["pid"], "u", author="Jane Reporter") == 0
        db.commit()
        assert service.run_source(db, s, f)["new"] == 0    # neither comes back
        assert db.query(Mention).count() == 1
        ah = db.query(Mention).one().author_hash
        assert ah == "" and service.take_down(db, ctx["org"], ctx["pid"], "u", author="nobody") == 0


def test_author_takedown_removes_all_posts_of_that_hash_and_blocks_new_ones(org, ctx):
    f = web({"/feed": (200, ATOM)})
    with SessionLocal() as db:
        s = make_source(db, ctx, "rss", {"url": "https://blog.example/feed"})
        service.run_source(db, s, f)
        db.commit()
        ah = db.query(Mention).one().author_hash
        assert ah and service.take_down(db, ctx["org"], ctx["pid"], "u", author=ah) == 1
        db.commit()
        assert service.run_source(db, s, f)["new"] == 0 and db.query(Mention).count() == 0


def test_the_background_step_runs_only_what_is_due_and_waits_longer_after_a_failure(org, ctx):
    from datetime import timedelta
    from app.models import utcnow
    with SessionLocal() as db:
        ok = make_source(db, ctx)
        bad = make_source(db, ctx, "rss", {"url": "http://10.0.0.1/feed"})
        bad.interval_min = 15
        db.commit()
        assert service.run_due(db, fetcher=web({})) == 2 and service.run_due(db, fetcher=web({})) == 0
        assert service.run_due(db, now=utcnow() + timedelta(minutes=16), fetcher=web({})) == 0   # after an error it waits at least an hour
        assert service.run_due(db, now=utcnow() + timedelta(minutes=61), fetcher=web({})) == 1
        assert service.run_due(db, now=utcnow() + timedelta(minutes=361), fetcher=web({})) == 2
        ok.enabled = False
        db.commit()
        assert service.run_due(db, now=utcnow() + timedelta(days=3), fetcher=web({})) == 1


# ---------- the API ----------
def api_source(org, pid, **over):
    body = {"kind": "mock", "name": "模拟来源", "config": {"brand": "Acme", "lang": "en", "n": 6}, **over}
    return org.c.post(f"/api/projects/{pid}/listening/sources", headers=org.h(), json=body)


def test_make_a_source_run_it_and_read_the_summary_and_mentions(org):
    pid = org.project()
    r = api_source(org, pid)
    assert r.status_code == 201
    sid = r.json()["id"]
    run = org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h()).json()
    assert run["ok"] is True and run["new"] == 6 and run["source"]["last_status"] == "ok"
    ov = org.c.get(f"/api/projects/{pid}/listening", headers=org.h()).json()
    assert ov["sources"][0]["mentions"] == 6 and ov["summary"]["total"] == 6 and sum(ov["summary"]["sentiment"].values()) == 6 and ov["summary"]["sentiment"]["none"] == 0
    assert ov["summary"]["per_day"] and ov["summary"]["topics"] and ov["summary"]["languages"] == {"en": 6}
    neg = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h(), params={"sentiment": "neg"}).json()
    assert neg["total"] >= 1 and all(m["sentiment"] == "neg" for m in neg["mentions"])
    q = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h(), params={"q": "crashing"}).json()
    assert q["total"] == 1 and "crashing" in q["mentions"][0]["text"]
    topic = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h(), params={"topic": "App 体验"}).json()
    assert topic["total"] >= 1


def test_sources_can_be_edited_switched_off_and_removed_with_their_mentions(org):
    pid = org.project()
    sid = api_source(org, pid).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    p = org.c.patch(f"/api/listening/sources/{sid}", headers=org.h(), json={"enabled": False, "name": "改名", "interval_min": 60}).json()
    assert p["enabled"] is False and p["name"] == "改名" and p["interval_min"] == 60
    assert org.c.patch(f"/api/listening/sources/{sid}", headers=org.h(), json={"config": {"brand": "B", "lang": "zh"}}).json()["config"]["lang"] == "zh"
    assert org.c.delete(f"/api/listening/sources/{sid}", headers=org.h()).status_code == 204
    assert org.c.get(f"/api/projects/{pid}/listening", headers=org.h()).json() == {"kinds": ["mock", "rss", "appstore", "web"], "sources": [], "summary": org.c.get(f"/api/projects/{pid}/listening", headers=org.h()).json()["summary"]}
    with SessionLocal() as db:
        assert db.query(Mention).count() == 0


def test_bad_sources_are_refused(org):
    pid = org.project()
    assert api_source(org, pid, kind="rss", config={"url": "nope"}).status_code == 422
    assert api_source(org, pid, kind="nope").status_code == 422
    assert api_source(org, pid, interval_min=1).status_code == 422
    for _ in range(30):
        assert api_source(org, pid).status_code == 201
    assert api_source(org, pid).status_code == 409


def test_a_source_that_points_inside_the_network_is_refused_when_it_runs_not_stored_as_working(org):
    pid = org.project()
    sid = api_source(org, pid, kind="rss", config={"url": "http://169.254.169.254/latest/"}).json()["id"]
    run = org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h()).json()
    assert run["ok"] is False and "公开" in run["error"] and run["source"]["last_status"] == "error"


def test_take_down_through_the_api_and_deleting_one_mention(org):
    pid = org.project()
    sid = api_source(org, pid).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    first = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h()).json()["mentions"][0]
    assert org.c.delete(f"/api/listening/mentions/{first['id']}", headers=org.h()).status_code == 204
    assert org.c.post(f"/api/projects/{pid}/listening/takedown", headers=org.h(), json={}).status_code == 422
    r = org.c.post(f"/api/projects/{pid}/listening/takedown", headers=org.h(), json={"author": first["author"] or "x"})
    assert r.status_code == 200
    again = org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h()).json()
    assert first["url"] not in [m["url"] for m in org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h()).json()["mentions"]]
    assert again["ok"] is True


def test_only_the_projects_researchers_and_admins_can_use_it(org, client):
    pid = org.project()
    sid = api_source(org, pid).json()["id"]
    org.c.post(f"/api/listening/sources/{sid}/run", headers=org.h())
    mid = org.c.get(f"/api/projects/{pid}/listening/mentions", headers=org.h()).json()["mentions"][0]["id"]
    other = second_researcher(org)
    for method, url, body in [("get", f"/api/projects/{pid}/listening", None), ("post", f"/api/projects/{pid}/listening/sources", {"kind": "mock", "name": "x", "config": {}}),
                              ("get", f"/api/projects/{pid}/listening/mentions", None), ("post", f"/api/projects/{pid}/listening/takedown", {"url": "https://x.example"}),
                              ("patch", f"/api/listening/sources/{sid}", {"enabled": False}), ("delete", f"/api/listening/sources/{sid}", None),
                              ("post", f"/api/listening/sources/{sid}/run", None), ("delete", f"/api/listening/mentions/{mid}", None)]:
        r = getattr(org.c, method)(url, headers=other["h"], **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/projects/{pid}/listening", headers=org.h(role)).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/projects/{pid}/listening", headers=foreign.h("admin")).status_code == 404
    assert org.c.get(f"/api/projects/{pid}/listening", headers=org.h("admin")).status_code == 200
