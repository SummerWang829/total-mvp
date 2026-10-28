"""The questionnaire assistant's checks and suggestions: found from the data without a model, never applied by themselves, gone when the question is edited, and
the sources and the numbers it is given come from the server."""
import json

from app import recruit_review
from app.db import SessionLocal
from app.models import RecruitForm
from app.recruit_form import normalize
from conftest import Org


def _q(extra=None):
    qs = [{"id": "q1", "type": "single", "text": "Where do you live?", "required": True, "options": [{"id": "o1", "text": "Greater Bay", "tag": "GBA"}, {"id": "o2", "text": "Elsewhere", "goto": "end_out"}]},
          {"id": "q2", "type": "multi", "text": "Where do you buy?", "required": True, "options": [{"id": "o1", "text": "Online", "tag": "online"}, {"id": "o2", "text": "Shops", "tag": "shops"}, {"id": "o3", "text": "None", "goto": ""}]}]
    return normalize({"questions": qs + (extra or [])}).questions


def test_rule_suggestions_from_data_only():
    qs = _q()
    out = recruit_review.rule_suggestions(qs, [{"name": "非大湾区", "field": "tag", "value": "Non-GBA", "target": 6}], {"started": 10, "out_by": {"q1": 5}})
    kinds = {s["kind"] for s in out}
    assert {"quota", "strict", "exclusive"} <= kinds
    strict = next(s for s in out if s["kind"] == "strict")
    assert "50%" in strict["text"] and strict["qid"] == "q1"
    assert recruit_review.rule_suggestions(qs, [], {"started": 3, "out_by": {"q1": 3}}) and not any(s["kind"] == "strict" for s in recruit_review.rule_suggestions(qs, [], {"started": 3, "out_by": {"q1": 3}}))  # too few people: no claim


def test_exclusive_fix_is_deterministic_and_two_way():
    qs = _q()
    fixed = recruit_review.apply_fix(qs, {"op": "exclusive", "qid": "q2"})
    q2 = next(q for q in fixed if q.id == "q2")
    a, b = q2.options[0], q2.options[1]
    assert b.id in a.excl and a.id in b.excl
    assert not any(s["kind"] == "exclusive" for s in recruit_review.rule_suggestions(fixed, [], {}))


def test_source_labels_only_from_the_fixed_set():
    qs = _q()
    labels = recruit_review.source_labels(["brief", "stats", "question:q2", "question:nope", "quota:非大湾区", "http://evil", "library"], qs, [{"name": "非大湾区", "target": 6}], True, False)
    assert labels == ["研究背景", "作答统计（匿名）", "问卷第 2 题", "配额：非大湾区（目标 6 人）"]


def test_a_suggestion_expires_when_its_question_is_edited_and_dismiss_sticks():
    qs = _q()
    item = {"sig": "abc", "kind": "wording", "label": "措辞", "qid": "q1", "text": "带引导", "instruction": "改", "fix": None, "sources": ["question:q1"], "button": "采纳", "qhash": recruit_review.qhash(qs[0])}
    assert [s["sig"] for s in recruit_review.open_suggestions(qs, [], {}, [item], [], True, False) if s["sig"] == "abc"] == ["abc"]
    edited = _q()
    edited[0].text = "Which city do you live in?"
    assert not [s for s in recruit_review.open_suggestions(edited, [], {}, [item], [], True, False) if s["sig"] == "abc"]
    assert not [s for s in recruit_review.open_suggestions(qs, [], {}, [item], ["abc"], True, False) if s["sig"] == "abc"]


def test_the_model_context_has_counts_but_no_people():
    qs = _q()
    text = recruit_review.stats_context(qs, [{"name": "GBA", "target": 4, "have": 1}], {"started": 8, "qualified": 3, "out": 5, "out_by": {"q1": 5}}, {"q": {"q1": {"_n": 3, "o1": 3}}})
    assert "started: 8" in text and "stopped 5 people" in text and "Greater Bay 100%" in text and "Quota GBA: target 4, have 1" in text and "@" not in text


def _form(org):
    pid = org.project()
    org.c.put(f"/api/projects/{pid}/recruiting/form", headers=org.h(), json={"source": "ai", "questions": [q.model_dump() for q in _q()], "goals": [], "need": ""})
    return pid


def test_review_endpoint_lists_suggestions_and_nothing_changes(org):
    pid = _form(org)
    before = org.c.get(f"/api/projects/{pid}/recruiting/form", headers=org.h()).json()
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/review", headers=org.h())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "suggest" and d["changed"] is False and any(s["kind"] == "exclusive" for s in d["suggestions"])
    assert d["questions"] == before["questions"]  # looking is not changing
    assert d["chat"][-1]["kind"] == "suggest"


def test_adopt_applies_the_deterministic_fix_and_dismiss_hides_the_rest(org):
    pid = _form(org)
    sugs = org.c.post(f"/api/projects/{pid}/recruiting/form/review", headers=org.h()).json()["suggestions"]
    ex = next(s for s in sugs if s["kind"] == "exclusive")
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/suggestions/adopt", headers=org.h(), json={"sig": ex["sig"]})
    assert r.status_code == 200 and r.json()["changed"] is True and r.json()["chat"][-1]["kind"] == "change"
    assert not any(s["kind"] == "exclusive" for s in r.json()["suggestions"])
    assert org.c.post(f"/api/projects/{pid}/recruiting/form/suggestions/adopt", headers=org.h(), json={"sig": ex["sig"]}).status_code == 422  # gone now
    other = next((s for s in r.json()["suggestions"]), None)
    if other:
        gone = org.c.post(f"/api/projects/{pid}/recruiting/form/suggestions/dismiss", headers=org.h(), json={"sig": other["sig"]}).json()
        assert other["sig"] not in [s["sig"] for s in gone["suggestions"]]
        assert other["sig"] not in [s["sig"] for s in org.c.get(f"/api/projects/{pid}/recruiting/form", headers=org.h()).json()["suggestions"]]


def test_a_question_gets_a_labelled_answer_with_sources(org):
    pid = _form(org)
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/chat", headers=org.h(), json={"message": "为什么第 1 题要筛人？", "questions": [q.model_dump() for q in _q()]})
    assert r.status_code == 200 and r.json()["kind"] == "answer" and r.json()["changed"] is False
    assert r.json()["chat"][-1]["kind"] == "answer" and r.json()["chat"][-1]["sources"] == ["作答统计（匿名）"]


def test_only_the_project_team_can_use_it(org):
    pid = _form(org)
    other = Org(org.c, "Other", "boss@other.test")
    for path in ("review", "suggestions/dismiss", "suggestions/adopt"):
        assert org.c.post(f"/api/projects/{pid}/recruiting/form/{path}", headers=other.h(), json={"sig": "x"}).status_code in (404, 403)


# ---------- when the model does not answer ----------
import pytest

from app import recruit_form
from app.moderator.providers import ChatError, ChatFatalError, MockModeratorChat


@pytest.fixture(autouse=False)
def fresh_fatal():
    recruit_form.clear_fatal()
    yield
    recruit_form.clear_fatal()


def test_a_failure_is_named_in_plain_words():
    assert recruit_form.chat_reason(ChatError("TimeoutException")) == "服务响应超时"
    assert recruit_form.chat_reason(ChatError("HTTP 429")) == "服务繁忙（请求过于频繁）"
    assert recruit_form.chat_reason(ChatError("HTTP 503")) == "服务暂时不可用"
    assert recruit_form.chat_reason(ChatError("reply is not JSON")) == "返回的内容格式不正确"
    assert recruit_form.chat_reason(ChatError("ConnectError")) == "服务暂时无法连接"


def test_an_unusable_reply_is_asked_for_once_more_then_reported(fresh_fatal, caplog):
    good = {"reply": "已回答。", "questions": None, "goals": None, "need": None, "kind": "answer"}
    bot = MockModeratorChat(script=[ChatError("reply is not JSON"), good])
    out = recruit_form.chat_turn(bot, "第一句很私密的话", [], _q(), [], "", "zh")
    assert out.reply == "已回答。" and len(bot.calls) == 2
    bad = MockModeratorChat(script=[ChatError("reply is not JSON")])
    with pytest.raises(recruit_form.ChatUnavailable) as e:
        recruit_form.chat_turn(bad, "第一句很私密的话", [], _q(), [], "", "zh")
    assert e.value.reason == "返回的内容格式不正确" and len(bad.calls) == 2
    assert "私密" not in caplog.text  # the log names the cause, never what was said


def test_a_refusal_is_remembered_until_a_reply_comes_through(fresh_fatal):
    with pytest.raises(ChatFatalError):
        recruit_form.chat_turn(MockModeratorChat(script=[ChatFatalError(402)]), "你好", [], _q(), [], "", "zh")
    assert "余额" in recruit_form.recent_fatal()
    recruit_form.chat_turn(MockModeratorChat(script=[{"reply": "好的。"}]), "你好", [], _q(), [], "", "zh")
    assert recruit_form.recent_fatal() == ""


def test_rate_report_comes_from_the_counts():
    qs = _q()
    none = recruit_review.rate_report(qs, [], {"started": 0})
    assert "暂无作答数据" in none["lines"][0] and not none["bars"]
    rep = recruit_review.rate_report(qs, [{"name": "GBA", "target": 4, "have": 1}], {"started": 8, "qualified": 3, "out": 5, "out_by": {"q1": 4, "q2": 1}})
    text = " ".join(rep["lines"])
    assert "通过率约 38%" in text and "不足 30 人" in text and "尚缺 3 人" in text and "预计还需 8 人" in text
    assert [b["no"] for b in rep["bars"]] == [1, 2] and rep["bars"][0]["n"] == 4 and "第 1 题" in rep["next"][0]["label"]
    assert "第 1 题" in " ".join(recruit_review.rate_report(qs, [], {"started": 8, "qualified": 3, "out": 5, "out_by": {"q1": 4}}, "worst")["lines"])


def test_the_stats_button_answers_without_a_model(org, monkeypatch):
    pid = _form(org)
    monkeypatch.setattr("app.routers.recruit._ai_chat", lambda: (_ for _ in ()).throw(AssertionError("no model needed")))
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/stats", headers=org.h(), json={"kind": "rate"})
    assert r.status_code == 200, r.text
    last = r.json()["chat"][-1]
    assert last["kind"] == "stat" and "暂无作答数据" in last["text"] and r.json()["chat"][-2]["text"] == "预估通过率"
    assert org.c.post(f"/api/projects/{pid}/recruiting/form/stats", headers=org.h(), json={"kind": "x"}).status_code == 422


def test_a_passing_failure_is_502_with_the_reason_and_a_refusal_is_503(org, monkeypatch, fresh_fatal):
    pid = _form(org)
    body = {"message": "预估", "questions": [q.model_dump() for q in _q()]}
    monkeypatch.setattr("app.routers.recruit._ai_chat", lambda: MockModeratorChat(script=[ChatError("TimeoutException")]))
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/chat", headers=org.h(), json=body)
    assert r.status_code == 502 and "服务响应超时" in r.json()["detail"] and "可重新尝试" in r.json()["detail"]
    monkeypatch.setattr("app.routers.recruit._ai_chat", lambda: MockModeratorChat(script=[ChatFatalError(402)]))
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/chat", headers=org.h(), json=body)
    assert r.status_code == 503 and "余额" in r.json()["detail"] and "管理员" in r.json()["detail"]
    got = org.c.get(f"/api/projects/{pid}/recruiting/form", headers=org.h()).json()
    assert "ai" in got  # the status travels with the form so the page can grey out the buttons that need the model


def test_checking_still_lists_the_rule_suggestions_when_the_model_fails(org, monkeypatch, fresh_fatal):
    pid = _form(org)
    monkeypatch.setattr("app.routers.recruit._ai_chat", lambda: MockModeratorChat(script=[ChatError("HTTP 500")]))
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/review", headers=org.h())
    assert r.status_code == 200, r.text
    d = r.json()
    assert any(s["kind"] == "exclusive" for s in d["suggestions"]) and "未能检查" in d["reply"]
