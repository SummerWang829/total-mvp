"""The recruiting questionnaire in several languages: every project language gets its version automatically (no tag, candidates read it), a team-only version never reaches candidates,
an edited original makes the other versions stale at once (and they are not offered until updated), and the logic is the same in every language."""
from conftest import Org
from test_screen_form import published, qs, url


def _multi(org):
    pid, token = published(org)
    assert org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"languages": ["zh", "en", "ms"], "default": "zh"}).status_code == 200
    return pid, token


def _codes(client, token):
    return [x["code"] for x in client.get(f"/api/public/screen/{token}").json()["languages"]]


def test_every_project_language_gets_its_version_and_candidates_can_read_it(client, org):
    pid, token = _multi(org)
    t = f"/api/projects/{pid}/recruiting/form"
    d = org.c.get(f"{t}/translations", headers=org.h()).json()
    assert d["source"] == "zh" and {x["code"]: x["state"] for x in d["languages"]} == {"zh": "source", "en": "machine", "ms": "missing"}  # English was a project language when it was published, so it already has its version
    assert _codes(client, token) == ["zh", "en"]  # Malay is not offered until it has one
    d = org.c.post(f"{t}/translate-all", headers=org.h()).json()
    assert {x["code"]: x["state"] for x in d["languages"]} == {"zh": "source", "en": "machine", "ms": "machine"} and all(not x["team_only"] for x in d["languages"])
    assert _codes(client, token) == ["zh", "en", "ms"]  # no confirmation step: the project languages are what candidates see
    q = client.post(f"/api/public/screen/{token}/step", json={"answers": {"age": 30}, "lang": "en"}).json()["question"]
    assert q["text"].startswith("[EN] ") and all(o["text"].startswith("[EN] ") for o in q["options"]) and [o["id"] for o in q["options"]] == ["never", "daily", "some"]
    assert org.c.post(f"{t}/translate-all", headers=org.h()).json()["languages"] == d["languages"]  # nothing is made twice


def test_publishing_makes_the_versions_before_candidates_arrive(client, org):
    pid, token = _multi(org)
    org.c.post(f"/api/projects/{pid}/recruiting/form/publish", headers=org.h())
    assert _codes(client, token) == ["zh", "en", "ms"]


def test_a_team_only_version_never_reaches_candidates(client, org):
    pid, token = _multi(org)
    t = f"/api/projects/{pid}/recruiting/form"
    org.c.post(f"{t}/translate-all", headers=org.h())
    d = org.c.post(f"{t}/translate", headers=org.h(), json={"lang": "ta"}).json()  # Tamil is not a project language
    ta = next(x for x in d["languages"] if x["code"] == "ta")
    assert ta["team_only"] is True and ta["state"] == "machine" and "ta" in d["items"]
    assert _codes(client, token) == ["zh", "en", "ms"]
    asked = client.post(f"/api/public/screen/{token}/step", json={"answers": {"age": 30}, "lang": "ta"}).json()["question"]
    assert asked["text"] == "您用过这类产品吗？"  # a candidate cannot ask for it
    pv = org.c.post(f"{t}/preview", headers=org.h(), json={"questions": qs(), "answers": {"age": 30}, "lang": "ta"}).json()["question"]
    assert pv["text"].startswith("[TA] ")  # the team can read and test it


def test_the_same_answers_screen_the_same_way_in_every_language(client, org):
    pid, token = _multi(org)
    org.c.post(f"/api/projects/{pid}/recruiting/form/translate-all", headers=org.h())
    for lang in ("zh", "en", "ms"):
        assert client.post(f"/api/public/screen/{token}/step", json={"answers": {"age": 12}, "lang": lang}).json()["result"] == "out"  # under 18 screens out whatever the language


def test_editing_the_original_makes_the_other_versions_stale_until_updated(client, org):
    pid, token = _multi(org)
    t = f"/api/projects/{pid}/recruiting/form"
    org.c.post(f"{t}/translate-all", headers=org.h())
    edited = qs()
    edited[4]["text"] = "购买时最看重什么？"
    assert org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": edited}).status_code == 200
    d = org.c.get(f"{t}/translations", headers=org.h()).json()
    en = next(x for x in d["languages"] if x["code"] == "en")
    assert en["state"] == "stale" and en["stale"] == 1 and d["items"]["en"]["why"]["state"] == "stale"
    assert _codes(client, token) == ["zh"]  # an out-of-date version is not offered: a candidate never reads a stale translation
    assert next(x for x in org.c.post(f"{t}/translate-all", headers=org.h()).json()["languages"] if x["code"] == "en")["state"] == "stale"  # opening the page does not refresh it: the notice stays
    d = org.c.post(f"{t}/translate-all", headers=org.h(), json={"stale": True}).json()  # 更新译文: only the stale question is made again
    assert next(x for x in d["languages"] if x["code"] == "en")["state"] == "machine" and _codes(client, token) == ["zh", "en", "ms"]
    r = org.c.put(f"{t}/translations/en/why", headers=org.h(), json={"text": "What matters most when you buy?", "options": {}})
    assert r.status_code == 200 and r.json()["items"]["en"]["why"]["text"].startswith("What matters")  # a hand edit is kept


def test_refusals_and_isolation(client, org):
    pid, token = _multi(org)
    t = f"/api/projects/{pid}/recruiting/form"
    assert org.c.post(f"{t}/translate", headers=org.h(), json={"lang": "zh"}).status_code == 422  # the original language is not translated
    assert org.c.post(f"{t}/translate", headers=org.h(), json={"lang": "xx"}).status_code == 422
    assert org.c.put(f"{t}/translations/en/age", headers=org.h(), json={"text": "x", "options": {"nope": "y"}}).status_code in (404, 422)
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.get(f"{t}/translations", headers=other.h()).status_code == 404
    empty = org.project()
    assert org.c.get(f"/api/projects/{empty}/recruiting/form/translations", headers=org.h()).status_code == 409
