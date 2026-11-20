"""修正方案第 3 轮 (用户 2026-11-20): the AI moderator speaks in AI-hosted audio / video interviews — only its own stored lines are read aloud (never text from the
browser, never the participant's words), long lines are cut for the service, a refusal answers 503 with the text staying on screen, and it is off unless set up."""
import httpx
import pytest
from test_ai_room import _say, _start, _walk_into_the_room

from app import tts
from app.config import settings


@pytest.fixture
def voiced(org, monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "mock")
    tts._cache.clear()
    itv = _walk_into_the_room(org)
    assert _start(org, itv).status_code == 200
    return org, itv


def _lines(org, itv):
    return org.c.get(f"/api/public/join/{itv['invite_token']}/chat").json()


def test_an_ai_line_is_read_aloud_and_nothing_else(voiced):
    org, itv = voiced
    _say(org, itv, "Yes, ready.")
    st = _lines(org, itv)
    assert st["voice"] is True
    ai = next(m for m in st["messages"] if m["sender"] == "ai")
    me = next(m for m in st["messages"] if m["sender"] == "participant")
    r = org.c.get(f"/api/public/join/{itv['invite_token']}/tts/{ai['seq']}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content.startswith(b"\xff\xfb")
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/tts/{me['seq']}").status_code == 404   # the participant's own words are never sent


def test_no_voice_unless_set_up_or_for_a_text_interview(org, monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "")
    itv = _walk_into_the_room(org)
    _start(org, itv)
    assert _lines(org, itv)["voice"] is False
    assert org.c.get(f"/api/public/join/{itv['invite_token']}/tts/1").status_code == 404


def test_a_long_line_is_cut_for_the_service():
    parts = tts.pieces("今天想请您聊聊最近一次保养的经历。" * 20, "zh")
    assert all(len(p) <= tts.MAX_ZH for p in parts) and "".join(parts) == "今天想请您聊聊最近一次保养的经历。" * 20


def test_a_refusal_is_not_retried_and_says_so(monkeypatch):
    monkeypatch.setattr(settings, "tencent_tts_secret_id", "id")
    monkeypatch.setattr(settings, "tencent_tts_secret_key", "key")
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["X-TC-Action"] == "TextToVoice" and req.headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=id/")
        return httpx.Response(200, json={"Response": {"Error": {"Code": "AuthFailure.SecretIdNotFound", "Message": "x"}}})
    p = tts.TencentTTS(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(tts.TTSError):
        p.synth("你好", "zh")
    assert len(calls) == 1


def test_audio_comes_back_decoded(monkeypatch):
    import base64
    monkeypatch.setattr(settings, "tencent_tts_secret_id", "id")
    monkeypatch.setattr(settings, "tencent_tts_secret_key", "key")
    p = tts.TencentTTS(httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"Response": {"Audio": base64.b64encode(b"MP3").decode()}}))))
    assert p.synth("Hello", "en") == b"MP3"
