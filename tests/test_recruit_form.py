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


def test_the_ai_writes_questions_from_the_idea_and_the_form_is_saved_and_versioned(org):
    pid = org.project()
    r = org.c.post(url(pid, "/generate"), headers=org.h(), json={"idea": "想了解新加坡年轻人如何选购护肤品牌"}).json()
    assert r["source"] == "ai" and 5 <= len(r["questions"]) <= 15 and r["version"] == 1 and r["generated_by"] == "mock"
    assert [q["id"] for q in r["questions"]][:2] == ["q1", "q2"] and any(o["end"] for q in r["questions"] for o in q["options"])
    assert org.c.get(url(pid), headers=org.h()).json()["questions"] == r["questions"]
    again = org.c.post(url(pid, "/generate"), headers=org.h(), json={"idea": "改成了另一个想法，关于防晒产品的购买习惯"}).json()
    assert again["version"] == 2 and again["idea"].startswith("改成了")


def test_a_too_short_idea_is_refused_and_questions_are_checked(org):
    pid = org.project()
    assert org.c.post(url(pid, "/generate"), headers=org.h(), json={"idea": "短"}).status_code == 422
    bad = lambda q: org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": [q]}).status_code
    assert bad({"type": "single", "text": "只有一个选项？", "options": [{"text": "是"}]}) == 422  # a choice needs two options
    assert bad({"type": "open", "text": "请留下您的手机号"}) == 422  # no contact details
    assert bad({"type": "open", "text": "x", "surprise": 1}) == 422  # unknown keys
    assert org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": [{"type": "open", "text": "好的问题？"}]}).status_code == 200


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


def test_the_model_is_asked_in_the_projects_language_and_a_refusal_is_not_hidden():
    chat = MockModeratorChat(script=[GOOD])
    qs = recruit_form.generate(chat, "了解护肤品购买习惯和渠道偏好", "zh")
    assert len(qs) == 2 and "zh" in chat.calls[0][0]["content"] and chat.calls[0][1]["content"].count("了解护肤品") == 1
    with pytest.raises(ChatFatalError):
        recruit_form.generate(MockModeratorChat(script=[ChatFatalError("余额不足")]), "了解护肤品购买习惯和渠道偏好", "zh")
    # a first unusable answer is asked for again once, with the problem named
    chat2 = MockModeratorChat(script=[{"questions": "nope"}, GOOD])
    assert len(recruit_form.generate(chat2, "了解护肤品购买习惯和渠道偏好", "zh")) == 2 and "rejected" in chat2.calls[1][1]["content"]
    with pytest.raises(recruit_form.FormError):
        recruit_form.generate(MockModeratorChat(script=[{"questions": []}]), "了解护肤品购买习惯和渠道偏好", "zh")


def test_a_cloud_model_without_a_named_vendor_is_not_used(org, monkeypatch):
    from app.config import settings
    pid = org.project()
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-cloud.com/v1")
    monkeypatch.setattr(settings, "moderator_vendor", "")
    r = org.c.post(url(pid, "/generate"), headers=org.h(), json={"idea": "了解护肤品购买习惯和渠道偏好"})
    assert r.status_code == 409 and "MODERATOR_VENDOR" in r.json()["detail"]


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
