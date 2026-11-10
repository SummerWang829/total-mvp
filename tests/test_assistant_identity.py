"""The team's assistants call themselves 小星星助手 (user 2026-11-01): a question about which model or tool answers, in any language, gets a fixed
reply without any model being asked; ordinary requests that merely mention a model still go to the model."""
import pytest

from app import assistant_identity
from app.config import settings
from app.routers import creation as creation_router


@pytest.mark.parametrize("text", ["你是什么模型？", "您是谁", "你用的是哪个模型", "什么模型在回答？", "你是ChatGPT吗", "谁开发了你",
                                  "What model are you?", "who made you", "Are you ChatGPT?", "Which company built this?", "model apa ni?", "siapa anda?",
                                  "நீங்கள் யார்?"])
def test_identity_questions_are_recognised(text):
    assert assistant_identity.asks_identity(text)


@pytest.mark.parametrize("text", ["帮我起草问卷", "把第 3 题改成多选", "加一题：你最近用过哪些大模型产品？比如 ChatGPT、DeepSeek、豆包，请多选，并说明常用场景和频率。",
                                  "受访者对 AI 工具的看法有什么共同点？", "Add a question about which AI tools they use at work", ""])
def test_ordinary_requests_are_not_caught(text):
    assert not assistant_identity.asks_identity(text)


class NoCall:
    name = "should-not-be-called"

    def complete(self, messages, temperature=0.0):
        raise AssertionError("the model must not be asked who it is")


def test_the_creation_dialog_answers_identity_without_the_model(org, monkeypatch):
    monkeypatch.setattr(creation_router, "_chat", lambda: NoCall())
    pid = org.project(language="zh")
    r = org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h(), json={"message": "你是什么模型？"})
    assert r.status_code == 200
    last = r.json()["messages"][-1]
    assert last["text"] == "我是小星星助手。" and r.json()["model"] and not last["actions"]


def test_library_ask_answers_identity(org):
    r = org.c.post("/api/repo/ask", headers=org.h(), json={"question": "what model are you?"})
    assert r.status_code == 200 and r.json()["message"] == "我是小星星助手。"


def test_prompts_carry_the_identity_rule():
    from app import insight_chat
    from app.creation import specs
    for prompt in (specs.SYSTEM, insight_chat.SYSTEM):
        assert "小星星助手" in prompt
