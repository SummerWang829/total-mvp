"""Voice input for the team's text boxes: a short recording in, text out, nothing kept."""
import io
import shutil
import wave

import pytest

from app import speech
from app.db import SessionLocal
from app.models import AuditLog
from app.routers import speech as speech_router


def clip(seconds=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)
    return buf.getvalue()


def say(org, role="admin", data=None, language="zh"):
    files = {"file": ("clip.webm", clip() if data is None else data, "audio/webm")}
    return org.c.post("/api/speech/transcribe", headers=org.h(role), files=files, data={"language": language})


@pytest.fixture(autouse=True)
def fresh_limits():
    speech_router._recent.clear()


def test_a_clip_comes_back_as_text(org):
    r = say(org)
    assert r.status_code == 200 and r.json()["text"] == speech.MOCK_TEXT   # the mock recogniser: a fixed demo sentence


def test_who_may_use_it(org):
    for role in ("admin", "researcher"):
        assert say(org, role).status_code == 200
    for role in ("moderator", "observer"):
        assert say(org, role).status_code == 403
    assert org.c.post("/api/speech/transcribe", files={"file": ("c.webm", clip(), "audio/webm")}).status_code == 401


def test_bad_input_is_refused_plainly(org):
    assert say(org, language="ms").status_code == 422
    assert say(org, data=b"").status_code == 422
    assert say(org, data=b"x" * (speech.MAX_BYTES + 5)).status_code == 413


def test_it_is_not_a_bulk_service(org):
    codes = [say(org).status_code for _ in range(speech_router.PER_MINUTE + 2)]
    assert codes[:speech_router.PER_MINUTE] == [200] * speech_router.PER_MINUTE and codes[-1] == 429


def test_the_audit_trail_records_that_a_clip_was_read_and_never_what_it_said(org):
    say(org)
    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.action == "speech.read").all()
    assert len(rows) == 1 and set(rows[0].meta) == {"bytes", "language"} and speech.MOCK_TEXT not in str(rows[0].meta)


class FakeASR:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.seen = rows or [], error, []

    def transcribe_audio(self, path, language, role=""):
        self.seen.append((path.rsplit(".", 1)[-1], language))
        if self.error:
            raise self.error
        return self.rows


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_real_recording_is_converted_and_the_sentences_are_joined(monkeypatch):
    fake = FakeASR([{"text": "我平时用护手霜，"}, {"text": "主要是冬天。"}])
    monkeypatch.setattr(speech, "get_asr", lambda: fake)
    assert speech.transcribe_clip(clip(), "zh") == "我平时用护手霜，主要是冬天。"
    assert fake.seen == [("mp3", "zh")]   # converted to a small mono mp3 first


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_not_audio_and_a_failing_service_become_plain_messages(monkeypatch):
    monkeypatch.setattr(speech, "get_asr", lambda: FakeASR())
    with pytest.raises(speech.SpeechError) as e:
        speech.transcribe_clip(b"this is not audio at all", "zh")
    assert "没有录到" in str(e.value)
    monkeypatch.setattr(speech, "get_asr", lambda: FakeASR(error=RuntimeError("boom with a secret 123")))
    with pytest.raises(speech.SpeechError) as e2:
        speech.transcribe_clip(clip(), "zh")
    assert e2.value.status == 502 and "secret" not in str(e2.value)
