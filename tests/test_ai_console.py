"""AI 后台 step 1: the call log (what is written, what never is), the feature list, and the admin-only overview."""
import dataclasses
import json

import httpx
import pytest

from app import ai_calls, ai_registry
from app.ai_calls import AiCallLog, error_category
from app.config import settings
from app.db import SessionLocal
from app.models import utcnow
from app.moderator.providers import ChatError, ChatFatalError, ChatFormatError, OpenAICompatModeratorChat, _TransientChatError
from app.routers import creation as creation_router

SECRET = "我的电话是 91234567，我讨厌这个牌子"


def converse(chat, messages):
    """Stands in for a registered feature function: the log finds the feature by the (module, function) that called complete()."""
    return chat.complete(messages)


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setitem(ai_registry._BY_SITE, (__name__, "converse"), "creation.chat")


def provider(handler):
    cfg = dataclasses.replace(settings, moderator_provider="openai", moderator_vendor="TestVendor", moderator_base_url="https://llm.example/v1")
    chat = OpenAICompatModeratorChat(cfg)
    chat._client = httpx.Client(transport=httpx.MockTransport(handler))
    return chat


def reply(content, status=200, finish="stop"):
    return lambda request: httpx.Response(status, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def rows():
    with SessionLocal() as db:
        return db.query(AiCallLog).order_by(AiCallLog.at).all()


def test_a_good_call_is_logged_under_the_feature_that_asked(registered):
    chat = provider(reply('{"ok": true}'))
    with ai_calls.org_scope("org-1"):
        assert converse(chat, [{"role": "user", "content": SECRET}]) == {"ok": True}
    (r,) = rows()
    assert (r.feature, r.status, r.error_type, r.org_id, r.model) == ("creation.chat", "ok", "", "org-1", "云端模型")
    assert r.prompt_version == "内置" and r.duration_ms >= 0


def test_a_call_from_somewhere_unlisted_counts_as_other():
    provider(reply('{"ok": true}')).complete([{"role": "user", "content": "x"}])
    assert [r.feature for r in rows()] == ["other"]


def test_failures_are_logged_with_a_category_and_the_error_still_reaches_the_caller(registered):
    for handler, error, category in ((reply("", 401), ChatFatalError, "服务拒绝"), (reply("this is not json"), ChatFormatError, "格式错误"),
                                     (reply('{"a": 1', finish="length"), ChatError, "被截断")):
        with pytest.raises(error):
            converse(provider(handler), [{"role": "user", "content": SECRET}])
        assert rows()[-1].error_type == category and rows()[-1].status == "error"


def test_error_categories_come_from_the_error_type_not_from_any_text():
    assert error_category(_TransientChatError("ReadTimeout")) == "超时"
    assert error_category(_TransientChatError("HTTP 503")) == "网络或服务繁忙"
    assert error_category(ValueError("boom")) == "其他"


def test_nothing_anyone_said_is_stored(registered):
    chat = provider(reply('{"say": "' + SECRET + '"}'))
    converse(chat, [{"role": "system", "content": SECRET}, {"role": "user", "content": SECRET}])
    with pytest.raises(ChatFormatError):
        converse(provider(reply("没法解析 " + SECRET)), [{"role": "user", "content": SECRET}])
    stored = json.dumps([[getattr(r, c.name) for c in AiCallLog.__table__.columns] for r in rows()], ensure_ascii=False, default=str)
    assert "91234567" not in stored and "讨厌" not in stored and "没法解析" not in stored


def test_a_broken_log_never_breaks_the_call(registered, monkeypatch):
    def broken():
        raise RuntimeError("database is down")
    monkeypatch.setattr(ai_calls, "SessionLocal", broken)
    assert converse(provider(reply('{"ok": true}')), [{"role": "user", "content": "x"}]) == {"ok": True}
    with pytest.raises(ChatFormatError):
        converse(provider(reply("nope")), [{"role": "user", "content": "x"}])


def test_a_request_to_a_real_endpoint_is_logged_for_the_requesters_company(org, monkeypatch):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_vendor", "TestVendor")
    monkeypatch.setattr(creation_router, "_chat", lambda: provider(reply("not json at all")))
    pid = org.project()
    r = org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h("admin"), json={"message": "帮我整理目标"})
    assert r.status_code in (502, 409, 422, 400), r.text
    mine = rows()
    assert mine and {x.feature for x in mine} == {"creation.chat"} and all(x.org_id and x.error_type == "格式错误" for x in mine)
    other = org.c.post("/api/auth/register", json={"org_name": "Other", "name": "A", "email": "a@other.com", "password": "password123"}).json()["token"]
    seen = org.c.get("/api/ai-console/overview", headers={"Authorization": f"Bearer {other}"}).json()
    assert all(f["calls"] == 0 for f in seen["features"])
    mine_view = org.c.get("/api/ai-console/overview", headers=org.h("admin")).json()
    assert {f["key"]: f["failed"] for f in mine_view["features"]}["creation.chat"] == len(mine)


def test_only_admins_see_the_overview(org):
    for role in ("researcher", "moderator", "observer"):
        assert org.c.get("/api/ai-console/overview", headers=org.h(role)).status_code == 403
    assert org.c.get("/api/ai-console/overview").status_code == 401
    assert org.c.get("/api/ai-console/overview", headers=org.h("admin")).status_code == 200


def test_the_overview_lists_every_feature_with_numbers_and_never_a_key_or_a_vendor_model(org, monkeypatch):
    monkeypatch.setattr(settings, "moderator_api_key", "sk-THE-SECRET-KEY")
    monkeypatch.setattr(settings, "moderator_model", "vendor-model-9000")
    with SessionLocal() as db:
        oid = db.execute(__import__("sqlalchemy").text("select id from orgs limit 1")).scalar()
        for ms, status, err in ((1000, "ok", ""), (3000, "ok", ""), (500, "error", "格式错误")):
            db.add(AiCallLog(org_id=oid, feature="recruit.insights", prompt_version="内置", model="云端模型", duration_ms=ms, status=status, error_type=err, at=utcnow()))
        db.commit()
    body = org.c.get("/api/ai-console/overview", headers=org.h("admin")).json()
    by = {f["key"]: f for f in body["features"]}
    assert len(by) == len(ai_registry.FEATURES)
    assert (by["recruit.insights"]["calls"], by["recruit.insights"]["failed"], by["recruit.insights"]["errors"]) == (3, 1, {"格式错误": 1})
    assert by["moderator.speak"]["prompt"] and by["asr"]["model"] == "语音识别" and by["asr"]["tracked"] is False
    assert body["recent_failures"][0]["error"] == "格式错误"
    text = json.dumps(body, ensure_ascii=False)
    assert "THE-SECRET-KEY" not in text and "vendor-model-9000" not in text and "TestVendor" not in text


def test_every_registered_call_site_really_exists():
    """A renamed function would silently turn its calls into 'other': each (module, function) in the list must still be there."""
    import importlib
    for f in ai_registry.FEATURES:
        for module, function in f.sites:
            owner = importlib.import_module(module)
            found = hasattr(owner, function) or any(hasattr(getattr(owner, name), function) for name in dir(owner) if isinstance(getattr(owner, name), type))
            assert found, f"{f.key}: {module}.{function} not found"
