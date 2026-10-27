"""The research assistant of the 洞察与片段 page: private conversations, answers split into analysis / word-for-word quotes / suggestions, invented references
dropped, the model's refusal not hidden, quotes looked up fresh (a purged interview leaves no quote), staff only, other companies and other people cannot read it."""
import json

import pytest

from app import insight_chat
from app.config import settings
from app.db import SessionLocal
from app.models import Interview
from app.moderator.providers import ChatFatalError, MockModeratorChat
from conftest import Org
from test_week1 import transcribed


def _setup(org):
    itv, segs = transcribed(org)
    seg = [s for s in segs if s["speaker"] == "participant"][1]
    org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h(), json={"theme": "信任", "statement": "怕假货", "evidence": [{"segment_id": seg["id"], "quote": "clear halal certification"}]})
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    org.c.post(f"/api/insights/{org.c.get(f'/api/interviews/{itv['id']}/insights', headers=org.h()).json()[0]['id']}/approve", headers=org.h())
    return itv, pid


def _new(org, pid, role="researcher"):
    r = org.c.post(f"/api/projects/{pid}/chats", headers=org.h(role))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _use_model(monkeypatch, script, vendor="DeepSeek"):
    chat = MockModeratorChat(script)
    settings.moderator_provider, settings.moderator_vendor = "openai", vendor
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    monkeypatch.setattr("app.routers.insight_chat.get_moderator_chat", lambda: chat)
    return chat


def test_mock_mode_answers_from_the_words_and_keeps_the_conversation(org):
    itv, pid = _setup(org)
    cid = _new(org, pid)
    r = org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "受访者担心什么？ halal certification"})
    assert r.status_code == 200, r.text
    m = r.json()["message"]
    assert m["answer"] and "演示模式" in m["answer"] and m["quotes"] and m["quotes"][0]["text"]
    got = org.c.get(f"/api/chats/{cid}", headers=org.h()).json()
    assert [x["role"] for x in got["messages"]] == ["user", "ai"] and got["title"].startswith("受访者担心")
    assert org.c.get(f"/api/projects/{pid}/chats", headers=org.h()).json()[0]["id"] == cid


def test_model_answer_quotes_are_copied_by_the_server_and_invented_numbers_are_dropped(org, monkeypatch):
    itv, pid = _setup(org)
    chat = _use_model(monkeypatch, lambda msgs: {"answer": "他们担心真假，见 [1] 和 [99]。", "suggestions": ["追问认证", ""], "followups": ["还有哪些顾虑？"]})
    cid = _new(org, pid)
    r = org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "halal certification 是什么情况", "scope": ["transcripts", "insights"]})
    assert r.status_code == 200, r.text
    m = r.json()["message"]
    assert "[99]" not in m["answer"] and "[1]" in m["answer"] and m["suggestions"] == ["追问认证"] and m["followups"] == ["还有哪些顾虑？"]
    assert [q["n"] for q in m["quotes"]] == [1] and m["quotes"][0]["who"] == "M-001"
    sent = json.dumps(chat.calls[0], ensure_ascii=False)
    assert "Wei Ling" not in sent and "halal" in sent  # the passages go to the model, the participant's name does not
    r2 = org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "再说说价格"})
    assert r2.status_code == 200 and any("他们担心真假" in x["content"] for x in chat.calls[1] if x["role"] == "assistant")  # the earlier answer is sent along


def test_the_model_refusing_is_reported_not_hidden(org, monkeypatch):
    itv, pid = _setup(org)
    def boom(msgs):
        raise ChatFatalError(402)
    _use_model(monkeypatch, boom)
    cid = _new(org, pid)
    r = org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "任何问题"})
    assert r.status_code == 502 and "拒绝了请求" in r.text and "余额" in r.text
    assert org.c.get(f"/api/chats/{cid}", headers=org.h()).json()["messages"] == []  # nothing was stored for the failed turn


def test_a_cloud_model_needs_its_vendor_named(org, monkeypatch):
    itv, pid = _setup(org)
    _use_model(monkeypatch, [{"answer": "x"}], vendor="")
    assert org.c.get(f"/api/projects/{pid}/chat/status", headers=org.h()).json()["ready"] is False
    cid = _new(org, pid)
    assert org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "你好"}).status_code == 409


def test_a_purged_interview_leaves_no_quote_in_old_conversations(org):
    itv, pid = _setup(org)
    cid = _new(org, pid)
    org.c.post(f"/api/chats/{cid}/ask", headers=org.h(), json={"message": "halal certification"})
    assert org.c.get(f"/api/chats/{cid}", headers=org.h()).json()["messages"][1]["quotes"]
    with SessionLocal() as db:
        from datetime import datetime
        db.get(Interview, itv["id"]).purged_at = datetime.utcnow()
        db.commit()
    assert org.c.get(f"/api/chats/{cid}", headers=org.h()).json()["messages"][1]["quotes"] == []


def test_conversations_are_private_and_staff_only(org):
    itv, pid = _setup(org)
    cid = _new(org, pid, "admin")
    assert org.c.get(f"/api/chats/{cid}", headers=org.h("researcher")).status_code == 404  # another person's
    assert org.c.delete(f"/api/chats/{cid}", headers=org.h("researcher")).status_code == 404
    for role in ("moderator", "observer"):
        assert org.c.post(f"/api/projects/{pid}/chats", headers=org.h(role)).status_code in (401, 403, 404)
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.get(f"/api/chats/{cid}", headers=other.h()).status_code == 404
    assert org.c.post(f"/api/projects/{pid}/chats", headers=other.h()).status_code == 404
    assert org.c.delete(f"/api/chats/{cid}", headers=org.h("admin")).status_code == 204


def test_only_the_latest_conversations_are_kept(org):
    itv, pid = _setup(org)
    ids = [_new(org, pid) for _ in range(insight_chat.MAX_THREADS + 2)]
    kept = [t["id"] for t in org.c.get(f"/api/projects/{pid}/chats", headers=org.h()).json()]
    assert len(kept) == insight_chat.MAX_THREADS and ids[-1] in kept and ids[0] not in kept
