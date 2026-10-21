"""Sprint 0 of the AI moderator: consent discloses AI moderation (AI interviews only), the model is switched by settings only."""
import json

import httpx
import pytest

from app import consent as consent_mod, doctor
from app.config import settings
from app.moderator import providers
from app.moderator.compliance import ai_moderation_blocker, moderator_runs_locally
from app.moderator.constants import CHAT_RETRY_ATTEMPTS
from app.moderator.providers import ChatError, MockModeratorChat, OpenAICompatModeratorChat, get_moderator_chat


def patch_http(monkeypatch, post) -> list[dict]:
    """Replace the provider's HTTP client: `post(url, **kw)` answers every request. Returns what the client was built with (timeout, trust_env, ...)."""
    made: list[dict] = []

    class FakeClient:
        def __init__(self, **kw):
            made.append(kw)

        def post(self, url, **kw):
            return post(url, **kw)

    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    return made

LANGS = ("en", "zh", "ms", "ta")
AI_WORD = {"en": "AI interviewer", "zh": "AI 主持人", "ms": "pewawancara AI", "ta": "AI நேர்காணலாளர்"}


# ---- consent ----
def test_consent_version_bumped():
    assert consent_mod.VERSION == "2026-10-draft-8"


@pytest.mark.parametrize("lang", LANGS)
def test_ai_interview_consent_discloses_ai_in_every_language(lang):
    c = consent_mod.consent_for(lang, "Acme", ai=True)
    assert c["lang"] == lang and c["ai"] is True
    assert c["text"].startswith(consent_mod.AI_TEXTS[lang])  # disclosed first, before anything else
    assert AI_WORD[lang] in c["text"]
    assert list(c["labels"])[0] == "ai" and c["labels"]["ai"] == consent_mod.AI_LABELS[lang]
    assert consent_mod.AI_LOCAL[lang] in c["text"]  # mock / local model: nothing leaves this computer


@pytest.mark.parametrize("lang", LANGS)
def test_human_interview_consent_has_no_ai_clause(lang):
    c = consent_mod.consent_for(lang, "Acme")
    assert "ai" not in c["labels"] and c["ai"] is False
    assert consent_mod.AI_TEXTS[lang] not in c["text"] and AI_WORD[lang] not in c["text"]


def test_cloud_model_names_the_vendor_in_every_language(monkeypatch):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    monkeypatch.setattr(settings, "moderator_vendor", "ExampleAI")
    assert ai_moderation_blocker() is None
    for lang in LANGS:
        text = consent_mod.consent_for(lang, "Acme", ai=True)["text"]
        assert "ExampleAI" in text and consent_mod.AI_LOCAL[lang] not in text


def test_local_model_server_counts_as_local(monkeypatch):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    for url, local in (("http://127.0.0.1:11434/v1", True), ("http://localhost:11434/v1", True), ("https://api.deepseek.com", False)):
        monkeypatch.setattr(settings, "moderator_base_url", url)
        assert moderator_runs_locally() is local


def _join(org, itv):
    return org.c.get(f"/api/public/join/{itv['invite_token']}").json()


def _consent(org, itv, **kw):
    body = {"country": "SG", "recording": True, "observers": True, "cross_border": True, **kw}
    return org.c.post(f"/api/public/join/{itv['invite_token']}/consent", json=body)


def test_ai_interview_needs_the_ai_checkbox(org):
    itv = org.interview(moderation="ai")
    assert itv["moderation"] == "ai"
    c = _join(org, itv)["consent"]
    assert c["ai"] is True and "ai" in c["labels"]
    assert _consent(org, itv).status_code == 422  # the three usual boxes are not enough
    assert _consent(org, itv, ai=False).status_code == 422
    assert _consent(org, itv, ai=True).status_code == 201


def test_human_interview_unchanged(org):
    itv = org.interview()
    assert itv["moderation"] == "human" and "ai" not in _join(org, itv)["consent"]["labels"]
    assert _consent(org, itv).status_code == 201


def test_consent_record_keeps_the_ai_flag_and_text_hash(org):
    from app.db import SessionLocal
    from app.models import Consent

    itv = org.interview(moderation="ai")
    _consent(org, itv, ai=True)
    with SessionLocal() as db:
        row = db.query(Consent).filter_by(interview_id=itv["id"]).one()
        assert row.ai_moderation is True and row.version == consent_mod.VERSION
        assert row.text_sha256 == consent_mod.consent_for("en", "Acme", ai=True)["sha256"]


def test_cloud_model_without_a_vendor_blocks_ai_interviews(org, monkeypatch):
    ready = org.interview(moderation="ai")  # created while the model was local
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    r = org.c.post(f"/api/projects/{ready['project_id']}/interviews", headers=org.h(),
                   json={"participant_id": org.participant(ready["project_id"])["id"], "title": "AI", "moderation": "ai"})
    assert r.status_code == 422 and "MODERATOR_VENDOR" in r.json()["detail"]
    assert _consent(org, ready, ai=True).status_code == 409  # never consent to a text that cannot name the receiver
    assert org.interview().get("moderation") == "human"  # human interviews are unaffected


def test_moderation_must_be_human_or_ai(org):
    pid = org.project()
    r = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": org.participant(pid)["id"], "title": "x", "moderation": "robot"})
    assert r.status_code == 422


# ---- model is switched by settings only ----
def test_mock_by_default_in_tests():
    assert isinstance(get_moderator_chat(), MockModeratorChat)


def test_openai_compatible_chat_is_chosen_by_settings(monkeypatch):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "moderator_model", "qwen2.5:7b")
    sent = {}

    def fake_post(url, headers, json):
        sent.update(url=url, body=json)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"spoken_response": "What happened next?"}'}}]}, request=httpx.Request("POST", url))

    made = patch_http(monkeypatch, fake_post)
    chat = get_moderator_chat()
    assert isinstance(chat, OpenAICompatModeratorChat) and chat.name == "qwen2.5:7b@local"
    assert chat.complete([{"role": "user", "content": "hi"}], seed=7) == {"spoken_response": "What happened next?"}
    assert sent["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert sent["body"]["model"] == "qwen2.5:7b" and sent["body"]["seed"] == 7 and sent["body"]["response_format"] == {"type": "json_object"}
    assert made[0]["timeout"] == settings.moderator_timeout_s and len(made) == 1  # one client for the whole interview, not one per call
    assert made[0]["trust_env"] is False  # the model host is a fixed vendor: never through the computer's system proxy (5-30x slower)
    assert "thinking" not in sent["body"]  # only vendors that need it get extra fields


@pytest.mark.parametrize("content", ["not json", "[1, 2]", "", None])
def test_bad_model_output_is_a_chat_error_without_the_text(monkeypatch, content):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    resp = {"choices": [{"message": {"content": content}}]}
    patch_http(monkeypatch, lambda *a, **k: httpx.Response(200, json=resp, request=httpx.Request("POST", "http://x")))
    with pytest.raises(ChatError) as e:
        get_moderator_chat().complete([{"role": "user", "content": "my phone is 9123 4567"}])
    assert "9123" not in str(e.value) and (content or "x") not in str(e.value)


def _fake_server(monkeypatch, answers):
    """Each request takes the next answer: an Exception (raised) or an HTTP status (200 carries a valid reply)."""
    calls = []

    def post(url, **kw):
        calls.append(url)
        a = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(a, Exception):
            raise a
        body = {"choices": [{"message": {"content": '{"ok": true}'}}]} if a == 200 else {"error": "x"}
        return httpx.Response(a, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "moderator_provider", "openai")
    patch_http(monkeypatch, post)
    return calls


def test_network_error_is_retried_then_a_chat_error(monkeypatch):
    calls = _fake_server(monkeypatch, [httpx.ConnectError("refused")])
    with pytest.raises(ChatError):
        get_moderator_chat().complete([])
    assert len(calls) == CHAT_RETRY_ATTEMPTS  # exponential backoff, bounded


def test_server_error_then_success_is_recovered(monkeypatch):
    calls = _fake_server(monkeypatch, [503, httpx.ReadTimeout("slow"), 200])
    assert get_moderator_chat().complete([]) == {"ok": True} and len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 404])
def test_client_errors_are_not_retried(monkeypatch, status):
    calls = _fake_server(monkeypatch, [status])
    with pytest.raises(ChatError):
        get_moderator_chat().complete([])
    assert len(calls) == 1  # a wrong key or model name does not get better by asking again


def test_failures_are_logged_not_swallowed(monkeypatch, caplog):
    _fake_server(monkeypatch, [httpx.ConnectError("refused")])
    with pytest.raises(ChatError):
        get_moderator_chat().complete([{"role": "user", "content": "my NRIC is S1234567A"}])
    assert "unreachable" in caplog.text and "S1234567A" not in caplog.text
    assert any(r.exc_info for r in caplog.records)  # traceback kept for debugging


# ---- Quality Charter checks on the AI moderator package (zero-tolerance items that a machine can check) ----
def _moderator_functions():
    import ast
    import pathlib

    for path in sorted([*pathlib.Path("app/moderator").rglob("*.py"), *pathlib.Path("eval").rglob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            yield path.name, node


def test_moderator_code_has_full_type_hints_and_docstrings():
    import ast

    for fname, node in _moderator_functions():
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
            assert all(a.annotation is not None for a in args), f"{fname}:{node.name} has an argument without a type"
            assert node.returns is not None, f"{fname}:{node.name} has no return type"
        if isinstance(node, ast.ClassDef):
            assert ast.get_docstring(node), f"{fname}:{node.name} class has no docstring"
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("__") and not node.name.startswith("_"):
            assert ast.get_docstring(node), f"{fname}:{node.name} public function has no docstring"


def test_moderator_code_has_no_silent_failures_or_giant_functions():
    import ast

    for fname, node in _moderator_functions():
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, f"{fname}: bare except"
            assert not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)), f"{fname}: except ... pass"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno < 150, f"{fname}:{node.name} is longer than 150 lines"
        if isinstance(node, ast.ClassDef):
            assert node.end_lineno - node.lineno < 500, f"{fname}:{node.name} is longer than 500 lines"


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "moderator_provider", "gpt4o-hardcoded")
    with pytest.raises(NotImplementedError):
        get_moderator_chat()


def test_mock_script_replays_in_order():
    m = MockModeratorChat([{"a": 1}, {"a": 2}])
    assert [m.complete([])["a"] for _ in range(3)] == [1, 2, 2] and len(m.calls) == 3


def test_moderator_falls_back_to_llm_settings(tmp_path):
    """Empty MODERATOR_* lines fall back to LLM_* (checked in a fresh Python so the app's own settings are untouched)."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "MODERATOR_PROVIDER": "", "MODERATOR_BASE_URL": "", "MODERATOR_MODEL": "",
           "LLM_PROVIDER": "openai", "LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_MODEL": "qwen2.5:7b"}
    code = ("from app.config import settings as s; from app.moderator.compliance import moderator_runs_locally as local; "
            "print(s.moderator_provider, s.moderator_base_url, s.moderator_model, local(s))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=os.getcwd(), check=True).stdout.split()
    assert out == ["openai", "http://127.0.0.1:11434/v1", "qwen2.5:7b", "True"]


# ---- doctor ----
def test_doctor_warns_about_a_cloud_model_without_vendor(monkeypatch, capsys):
    monkeypatch.setattr(settings, "moderator_provider", "openai")
    monkeypatch.setattr(settings, "moderator_base_url", "https://api.example-ai.com/v1")
    monkeypatch.setattr(settings, "moderator_api_key", "k")
    monkeypatch.setattr(doctor.httpx, "get", lambda *a, **k: httpx.Response(200))
    monkeypatch.setattr(doctor, "bad", 0)
    doctor.check_moderator()
    out = capsys.readouterr().out
    assert "云端模型" in out and "MODERATOR_VENDOR" in out and doctor.bad == 0


# ---- vendor request fields and the proxy switch ----
def _config(**kw):
    import copy
    c = copy.copy(settings)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_deepseek_gets_thinking_switched_off_by_default():
    from app.moderator.providers import request_extras
    assert request_extras(_config(moderator_base_url="https://api.deepseek.com", moderator_extra_body="")) == {"thinking": {"type": "disabled"}}
    assert request_extras(_config(moderator_base_url="http://127.0.0.1:11434/v1", moderator_extra_body="")) == {}


def test_extra_body_setting_overrides_the_vendor_default_and_is_validated():
    from app.moderator.providers import request_extras
    mine = _config(moderator_base_url="https://api.deepseek.com", moderator_extra_body='{"reasoning_effort": "low"}')
    assert request_extras(mine) == {"reasoning_effort": "low"}
    for bad in ("not json", "[1]", '"x"'):
        with pytest.raises(ValueError):
            request_extras(_config(moderator_extra_body=bad))


def test_extra_fields_reach_the_request_and_the_proxy_switch_is_honoured(monkeypatch):
    sent = {}

    def fake_post(url, headers, json):
        sent.update(body=json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}, request=httpx.Request("POST", url))

    made = patch_http(monkeypatch, fake_post)
    cfg = _config(moderator_provider="openai", moderator_base_url="https://api.deepseek.com", moderator_extra_body="", moderator_use_system_proxy=True)
    OpenAICompatModeratorChat(cfg).complete([])
    assert sent["body"]["thinking"] == {"type": "disabled"} and made[0]["trust_env"] is True
