"""A model reply that is valid JSON but wrapped in a code fence or prose must still be read, and a reply cut off at the output limit must be named as such.

Why: on 2026-11-02 the questionnaire chat showed "返回的内容格式不正确" twice for a normal request. The reply was either wrapped or cut off,
and both looked the same to the researcher. A cut-off reply is not retried: the same request would be cut the same way."""
import httpx
import pytest

from app.config import settings
from app.moderator import providers
from app.moderator.providers import ChatError, MockModeratorChat, OpenAICompatModeratorChat
from app import recruit_form
from tests.test_moderator_setup import patch_http


def _reply_server(monkeypatch, choice: dict) -> list[int]:
    """Every POST answers 200 with this one choice. Returns the call log."""
    calls: list[int] = []

    def post(url, **kw):
        calls.append(200)
        return httpx.Response(200, json={"choices": [choice]}, request=httpx.Request("POST", url))

    patch_http(monkeypatch, post)
    return calls


def _chat(monkeypatch) -> OpenAICompatModeratorChat:
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    return OpenAICompatModeratorChat(settings)


def test_a_reply_in_a_code_fence_is_read(monkeypatch):
    _reply_server(monkeypatch, {"message": {"content": '```json\n{"reply": "好的。"}\n```'}, "finish_reason": "stop"})
    assert _chat(monkeypatch).complete([]) == {"reply": "好的。"}


def test_a_reply_with_words_around_the_object_is_read(monkeypatch):
    _reply_server(monkeypatch, {"message": {"content": '好的，结果如下：{"reply": "已修改。"} 希望有帮助。'}, "finish_reason": "stop"})
    assert _chat(monkeypatch).complete([]) == {"reply": "已修改。"}


def test_a_reply_with_no_object_is_still_not_json(monkeypatch):
    _reply_server(monkeypatch, {"message": {"content": "没有任何结构"}, "finish_reason": "stop"})
    with pytest.raises(ChatError, match="not JSON"):
        _chat(monkeypatch).complete([])


def test_a_reply_cut_off_at_the_output_limit_is_named_and_not_retried(monkeypatch):
    calls = _reply_server(monkeypatch, {"message": {"content": '{"reply": "好的", "questions": [{"id": "q1"'}, "finish_reason": "length"})
    with pytest.raises(ChatError, match="cut off") as e:
        _chat(monkeypatch).complete([])
    assert len(calls) == 1
    assert recruit_form.chat_reason(e.value) == "回复太长，被截断了（可以让它只改其中一部分）"


def test_a_cut_off_reply_is_not_asked_again_by_the_questionnaire_chat():
    recruit_form.clear_fatal()
    bot = MockModeratorChat(script=[ChatError("reply was cut off at the output limit")])
    with pytest.raises(recruit_form.ChatUnavailable) as e:
        recruit_form.chat_turn(bot, "检查问卷", [], [], [], "", "zh")
    assert e.value.reason == "回复太长，被截断了（可以让它只改其中一部分）" and len(bot.calls) == 1
