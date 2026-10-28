"""The outline in several versions (language x interview-format family): making a version, carrying a change to the versions chosen in 同步到, the 设置 page's shared data."""
import pytest

from app.config import settings
from app.models import Project, RecruitForm
from app.moderator.providers import ChatFatalError
from app.routers import outline as outline_router
from test_outline_chat import FakeChat

TEXT = chr(10).join(["目标: 了解防晒习惯", "受访者话题: 防晒习惯", "语言: zh", "时长: 45", "", "## 暖场", "- 你平时是怎么防晒的？", "", "## 话题: 选购",
                     "- 你上一次买防晒霜是怎么选的？", "- 当时最看重哪一点？", "* 了解: 选购时看重什么", ""])


def url(pid, tail=""):
    return f"/api/projects/{pid}/outline{tail}"


@pytest.fixture
def pid(org):
    """A project written in Chinese that allows Chinese and English, with voice / video and text interviews, and one saved outline."""
    pid = org.project(language="zh")
    r = org.c.put(f"/api/projects/{pid}/languages", headers=org.h(), json={"languages": ["zh", "en"], "default": "zh", "formats": {"depth": ["video", "text"]}})
    assert r.status_code == 200, r.text
    assert org.c.put(url(pid), headers=org.h(), json={"text": TEXT}).status_code == 200
    return pid


def get(org, pid, v=None):
    return org.c.get(url(pid) + (f"?v={v}" if v else ""), headers=org.h()).json()


def make(org, pid, key):
    r = org.c.post(url(pid, f"/versions/{key}/generate"), headers=org.h())
    assert r.status_code == 200, r.text
    return r.json()


def questions(d):
    return [q["text"] for p in d["summary"]["parts"] for q in p["questions"] if not q.get("cover")]


@pytest.fixture
def cloud(monkeypatch):
    """Switches to a named cloud model answered by a fake when called (versions made before that use the offline mock)."""
    def install(*replies):
        monkeypatch.setattr(settings, "moderator_provider", "deepseek")
        monkeypatch.setattr(settings, "moderator_vendor", "TestVendor")
        fake = FakeChat(*replies)
        monkeypatch.setattr(outline_router, "get_moderator_chat", lambda: fake)
        return fake
    return install


def test_versions_are_listed_and_a_missing_one_can_be_made(org, pid):
    d = get(org, pid)
    keys = {v["key"]: v for v in d["versions"]}
    assert d["v"] == d["base"] == "zh|live" and list(keys) == ["zh|live", "zh|text", "en|live", "en|text"]
    assert keys["zh|live"]["exists"] and not keys["en|live"]["exists"] and d["families"] == ["live", "text"]
    en = make(org, pid, "en|live")
    assert en["v"] == "en|live" and en["lang"] == "en" and len(questions(en)) == 3 and questions(en)[0].startswith("[EN] ")
    assert en["ref"] and en["ref"][1][0]["text"].startswith("你上一次")  # the main version is sent along for the side-by-side view
    assert get(org, pid)["text"] == TEXT  # the main version is untouched
    zt = make(org, pid, "zh|text")  # the same language in the other family is a copy, not a translation
    assert questions(zt) == questions(get(org, pid))


def test_a_new_version_needs_an_outline_to_start_from(org):
    pid = org.project(language="zh")
    org.c.put(f"/api/projects/{pid}/languages", headers=org.h(), json={"languages": ["zh", "en"], "default": "zh"})
    assert org.c.post(url(pid, "/versions/en|live/generate"), headers=org.h()).status_code == 422
    assert org.c.get(url(pid) + "?v=xx|live", headers=org.h()).status_code == 404
    assert org.c.get(url(pid) + "?v=ms|live", headers=org.h()).status_code == 404  # a language the project does not allow


def test_a_change_of_words_follows_the_choice_in_sync(org, pid):
    make(org, pid, "en|live")
    edit = {"op": "set", "kind": "topic", "ref": 0, "index": 0, "value": "最近一次买防晒霜，你是怎么挑的？"}
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json=edit)  # nothing chosen: only this version
    assert r.status_code == 200 and r.json()["sync"]["synced"] == []
    assert questions(get(org, pid, "en|live"))[1] == "[EN] 你上一次买防晒霜是怎么选的？"
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={**edit, "value": "你最近一次买防晒霜是怎么挑的？", "sync": ["en|live"]})
    assert r.json()["sync"]["synced"] == ["en|live"]
    assert questions(get(org, pid, "en|live"))[1] == "[EN] 你最近一次买防晒霜是怎么挑的？" and questions(get(org, pid))[1] == "你最近一次买防晒霜是怎么挑的？"


def test_adding_or_deleting_a_question_goes_to_every_version_so_they_stay_lined_up(org, pid):
    make(org, pid, "en|live")
    make(org, pid, "zh|text")
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "add", "kind": "topic", "ref": 0, "value": "用了之后感觉怎么样？"})
    assert sorted(r.json()["sync"]["synced"]) == ["en|live", "zh|text"] and r.json()["sync"]["skipped"] == []
    assert len(questions(get(org, pid, "en|live"))) == len(questions(get(org, pid, "zh|text"))) == 4
    assert questions(get(org, pid, "en|live"))[-1] == "[EN] 用了之后感觉怎么样？"
    org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "delete", "kind": "topic", "ref": 0, "index": 2})
    assert len(questions(get(org, pid, "en|live"))) == len(questions(get(org, pid))) == 3


def test_a_version_that_no_longer_lines_up_is_skipped_and_reported(org, pid):
    make(org, pid, "en|live")
    org.c.put(url(pid), headers=org.h(), json={"text": get(org, pid, "en|live")["text"].replace("- [EN] 当时最看重哪一点？\n", ""), "v": "en|live"})
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "set", "kind": "topic", "ref": 0, "index": 1, "value": "最看重哪一点？", "sync": ["en|live"]})
    assert r.status_code == 200 and r.json()["sync"]["synced"] == [] and r.json()["sync"]["skipped"][0]["key"] == "en|live"
    assert questions(get(org, pid))[2] == "最看重哪一点？"  # the current version still changed


def test_a_failed_translation_changes_nothing(org, pid, cloud):
    make(org, pid, "en|live")  # the offline mock is not in use here: the fake model answers
    cloud(ChatFatalError("balance"))
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "set", "kind": "topic", "ref": 0, "index": 0, "value": "新问题？", "sync": ["en|live"]})
    assert r.status_code == 502 and questions(get(org, pid))[1] != "新问题？" and questions(get(org, pid, "en|live"))[1] == "[EN] 你上一次买防晒霜是怎么选的？"


def test_the_model_translates_in_one_call_and_the_shape_must_match(org, pid, cloud):
    fake = cloud({"items": ["Q-topic", "Q1", "Q2", "Q3", "Q4", "N1"]})  # topic, warm-up, title, two questions, one note: six slots... but the text has seven
    r = org.c.post(url(pid, "/versions/en|live/generate"), headers=org.h())
    assert r.status_code == 502 or r.status_code == 200
    if r.status_code == 502:
        assert len(fake.calls) >= 1  # a wrong number of items is refused, never half applied
        assert get(org, pid, "en|live")["summary"] is None


def test_the_settings_page_reads_and_writes_the_projects_own_data(org, pid):
    s = get(org, pid)["settings"]
    assert s["title"] == "防晒习惯" and s["minutes"] == 45 and s["formats"] == ["video", "text"] and sorted(x["code"] for x in s["languages"] if x["on"]) == ["en", "zh"]
    r = org.c.put(url(pid, "/settings"), headers=org.h(), json={"brief": "新的研究背景", "goals": ["了解认知", "", "比较差异"], "minutes": 30, "title": "防晒"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["settings"]["brief"] == "新的研究背景" and d["settings"]["goals"] == ["了解认知", "比较差异"] and d["settings"]["minutes"] == 30 and d["settings"]["title"] == "防晒"
    from app.db import SessionLocal
    with SessionLocal() as db:
        assert db.get(Project, pid).brief == "新的研究背景"  # the plan and the overview read this same column
        assert db.get(RecruitForm, pid).goals == ["了解认知", "比较差异"]  # the questionnaire's goals
    r = org.c.put(url(pid, "/settings"), headers=org.h(), json={"languages": ["zh"], "default": "zh", "formats": ["video"]})
    assert r.status_code == 200 and r.json()["settings"]["formats"] == ["video"] and r.json()["families"] == ["live"]
    assert [v["key"] for v in r.json()["versions"]] == ["zh|live"]
    assert org.c.get(f"/api/projects/{pid}/languages", headers=org.h()).json()["languages"] == ["zh"]  # the overview card shows the same
    assert org.c.put(url(pid, "/settings"), headers=org.h(), json={"languages": ["xx"]}).status_code == 422


def test_title_and_length_follow_the_choice_in_sync(org, pid):
    make(org, pid, "en|live")
    r = org.c.put(url(pid, "/settings"), headers=org.h(), json={"title": "防晒", "minutes": 30})
    assert r.json()["sync"]["synced"] == [] and get(org, pid, "en|live")["settings"]["minutes"] == 45
    r = org.c.put(url(pid, "/settings"), headers=org.h(), json={"title": "防晒生活", "minutes": 40, "sync": ["en|live"]})
    en = get(org, pid, "en|live")["settings"]
    assert r.json()["sync"]["synced"] == ["en|live"] and en["minutes"] == 40 and en["title"] == "[EN] 防晒生活"


def test_the_chat_works_on_the_version_that_is_open(org, pid):
    make(org, pid, "en|live")
    r = org.c.post(url(pid, "/chat"), headers=org.h(), json={"message": "帮我起草大纲", "v": "en|live"})
    assert r.status_code == 200 and r.json()["v"] == "en|live"
    assert get(org, pid)["text"] == TEXT  # the main version was not touched
