"""The recruiting questionnaire (甄别问卷) and the AI suggestions from the research background."""
import pytest

from app import plan_suggest, recruit_form
from app.moderator.providers import ChatFatalError, MockModeratorChat
from conftest import Org
from test_plans import draft

GOOD = {"questions": [{"type": "single", "text": "您的年龄？", "options": [{"text": "18 岁以下", "end": True}, {"text": "18 岁及以上", "end": False}]}, {"type": "open", "text": "最近一次购买时看重什么？"}]}


def url(pid, tail=""):
    return f"/api/projects/{pid}/recruiting/form{tail}"


def test_an_empty_project_has_an_empty_form(org):
    pid = org.project()
    f = org.c.get(url(pid), headers=org.h()).json()
    assert f["questions"] == [] and f["source"] == "ai" and f["version"] == 0


def test_an_html_questionnaire_is_only_kept_as_an_https_link(org):
    pid = org.project()
    for bad in ("http://x.com/q", "javascript:alert(1)", "https://user:pw@x.com/q", "ftp://x.com", "不是链接"):
        assert org.c.put(url(pid), headers=org.h(), json={"source": "html", "html_url": bad}).status_code == 422, bad
    assert org.c.put(url(pid), headers=org.h(), json={"source": "html", "html_url": ""}).status_code == 422  # a link is needed
    ok = org.c.put(url(pid), headers=org.h(), json={"source": "html", "html_url": "https://survey.example.com/s/abc?lang=en"}).json()
    assert ok["source"] == "html" and ok["html_url"] == "https://survey.example.com/s/abc?lang=en" and ok["version"] == 1


def test_only_the_project_team_can_use_it_and_another_company_cannot_see_it(client, org):
    pid = org.project()
    assert org.c.get(url(pid), headers=org.h("observer")).status_code == 403
    assert org.c.get(url(pid), headers=org.h("moderator")).status_code == 403
    other = Org(client, "Other Co", "other@x.com")
    assert other.c.get(url(pid), headers=other.h()).status_code == 404


def test_the_public_config_says_where_the_ai_runs(client):
    c = client.get("/api/public/config").json()
    assert c["ai_local"] is True and c["ai_vendor"] == ""


# ---------- suggestions from the research background ----------
def test_suggestions_come_from_the_background_and_are_kept_with_the_plan(org):
    pid = draft(org)["id"]
    assert org.c.post(f"/api/projects/{pid}/plan/suggest", headers=org.h()).status_code == 422  # no background yet
    org.c.put(f"/api/projects/{pid}/plan", headers=org.h(), json={"brief": "新品防晒霜进入新加坡市场，想了解年轻人的购买决策", "config": org.c.get(f"/api/projects/{pid}/plan", headers=org.h()).json()["config"]})
    s = org.c.post(f"/api/projects/{pid}/plan/suggest", headers=org.h()).json()
    assert len(s["depth_topics"]) >= 3 and len(s["group_topics"]) >= 3 and s["quota_hint"] and s["listening_keywords"]
    assert org.c.get(f"/api/projects/{pid}/plan", headers=org.h()).json()["suggest"] == s
    assert org.c.post(f"/api/projects/{pid}/plan/suggest", headers=org.h("observer")).status_code == 403


def test_the_suggestion_text_is_trimmed_and_checked():
    out = plan_suggest.suggest(MockModeratorChat(script=[{"depth_topics": [{"title": "T" * 500, "questions": ["问题一？", "问题二？", "问题三？", "问题四？"]}], "group_topics": [{"title": "G", "questions": ["问？"]}],
                                                          "quota_hint": "好", "listening_keywords": ["词"] * 30}]), "新品防晒霜进入新加坡市场", "zh")
    assert len(out["depth_topics"][0]["title"]) == plan_suggest.MAX_TEXT and len(out["depth_topics"][0]["questions"]) == plan_suggest.MAX_QUESTIONS and len(out["listening_keywords"]) == plan_suggest.MAX_KEYWORDS
    with pytest.raises(recruit_form.FormError):
        plan_suggest.suggest(None, "短", "zh")
