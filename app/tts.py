"""The AI moderator's voice (修正方案第 3 轮, 用户 2026-11-20): in an AI-hosted audio or video interview each line the AI writes is also read aloud.

Tencent Cloud speech synthesis, TextToVoice (https://cloud.tencent.com/document/api/1073/37995), basic voices (TTS_VOICE_ZH / TTS_VOICE_EN). One call takes at most
150 Chinese characters (500 letters in English), so a longer line is cut at sentence ends and the MP3 pieces are joined. Only the AI's own words are sent — never
anything the participant said. Nothing is stored on disk; a few recent lines are kept in memory so a page that asks twice is not charged twice. The text is never
logged. The provider is injectable (tests use the offline one) and every call has a timeout and retries with backing off.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .config import settings

log = logging.getLogger(__name__)

HOST = "tts.tencentcloudapi.com"
VERSION = "2019-08-23"
TIMEOUT_S = 15
RETRIES = 3
BACKOFF_S = 0.6
MAX_ZH = 140          # under the service's 150 so punctuation never tips it over
MAX_EN = 480
CACHE_N = 200
LANGS = ("zh", "en")   # the AI moderator speaks Chinese and English only


class TTSError(RuntimeError):
    """The voice could not be made (not set up, refused, or the service could not be reached). The page then shows the text only."""


class TTSProvider(Protocol):
    """Makes MP3 audio of one short piece of text."""

    def synth(self, text: str, lang: str) -> bytes:
        """Raises:
            TTSError: the piece could not be made."""
        ...


def enabled() -> bool:
    """Whether AI interviews speak: TTS_PROVIDER=tencent with keys, or mock (tests, demo)."""
    return settings.tts_provider == "mock" or (settings.tts_provider == "tencent" and bool(settings.tencent_tts_secret_id and settings.tencent_tts_secret_key))


def pieces(text: str, lang: str) -> list[str]:
    """The text cut at sentence ends into pieces the service takes (a sentence longer than that is cut where it must)."""
    limit = MAX_ZH if lang == "zh" else MAX_EN
    sents = [x for x in re.split(r"(?<=[。！？!?；;.\n])", " ".join(str(text or "").split())) if x.strip()]
    out: list[str] = []
    for s in sents:
        while len(s) > limit:
            out.append(s[:limit])
            s = s[limit:]
        if out and len(out[-1]) + len(s) <= limit:
            out[-1] += s
        else:
            out.append(s)
    return [x.strip() for x in out if x.strip()]


class MockTTS:
    """Offline: a tiny valid MP3 frame per piece (silence), so the page's whole path can be tried without a key."""

    FRAME = bytes.fromhex("fffb9004") + b"\x00" * 413

    def synth(self, text: str, lang: str) -> bytes:
        return self.FRAME


class TencentTTS:
    """Tencent Cloud TextToVoice, signed with TC3-HMAC-SHA256."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client

    def _headers(self, payload: str, now: float | None = None) -> dict[str, str]:
        ts = int(now if now is not None else time.time())
        date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        ctype, action = "application/json; charset=utf-8", "TextToVoice"
        canonical = f"POST\n/\n\ncontent-type:{ctype}\nhost:{HOST}\nx-tc-action:{action.lower()}\n\ncontent-type;host;x-tc-action\n{hashlib.sha256(payload.encode()).hexdigest()}"
        scope = f"{date}/tts/tc3_request"
        to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        k = hmac.new(("TC3" + settings.tencent_tts_secret_key).encode(), date.encode(), hashlib.sha256).digest()
        for part in ("tts", "tc3_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        return {"Authorization": f"TC3-HMAC-SHA256 Credential={settings.tencent_tts_secret_id}/{scope}, SignedHeaders=content-type;host;x-tc-action, Signature={sig}",
                "Content-Type": ctype, "Host": HOST, "X-TC-Action": action, "X-TC-Version": VERSION, "X-TC-Timestamp": str(ts), "X-TC-Region": settings.tencent_tts_region}

    def synth(self, text: str, lang: str) -> bytes:
        """Raises:
            TTSError: refused (keys, balance, a voice the account does not have) or not reachable after RETRIES tries."""
        payload = json.dumps({"Text": text, "SessionId": uuid.uuid4().hex, "VoiceType": settings.tts_voice_zh if lang == "zh" else settings.tts_voice_en,
                              "PrimaryLanguage": 1 if lang == "zh" else 2, "Codec": "mp3", "SampleRate": 16000, "ModelType": 1}, ensure_ascii=False)
        last = "unknown"
        for n in range(RETRIES):
            try:
                c = self.client or httpx.Client(timeout=TIMEOUT_S)
                try:
                    r = c.post(f"https://{HOST}", content=payload.encode(), headers=self._headers(payload))
                finally:
                    if self.client is None:
                        c.close()
                body = r.json().get("Response", {})
                err = body.get("Error")
                if err:
                    code = str(err.get("Code", ""))
                    if code.startswith(("AuthFailure", "UnauthorizedOperation", "UnsupportedOperation", "ResourceUnavailable", "ResourceInsufficient", "FailedOperation.ServiceIsolate", "InvalidParameter")):
                        log.error("tts refused: %s", code)   # the code only, never the words
                        raise TTSError("语音合成服务拒绝了请求")
                    last = code
                elif body.get("Audio"):
                    return base64.b64decode(body["Audio"])
                else:
                    last = "no audio"
            except TTSError:
                raise
            except (httpx.HTTPError, ValueError) as e:
                last = type(e).__name__
                log.warning("tts call failed (%s), try %d", last, n + 1, exc_info=True)
            time.sleep(BACKOFF_S * (2 ** n))
        raise TTSError(f"语音合成暂时不可用（{last}）")


_provider: TTSProvider | None = None
_cache: "OrderedDict[str, bytes]" = OrderedDict()


def provider() -> TTSProvider:
    """The configured provider (tests may replace it with set_provider)."""
    global _provider
    if _provider is None:
        _provider = MockTTS() if settings.tts_provider == "mock" else TencentTTS()
    return _provider


def set_provider(p: TTSProvider | None) -> None:
    global _provider
    _provider = p


def speak(key: str, text: str, lang: str) -> bytes:
    """MP3 audio of one AI line (cached by `key`, e.g. "<interview>:<seq>").

    Raises:
        TTSError: not set up, a language the AI does not speak, or the service failed.
    """
    if not enabled():
        raise TTSError("没有开启语音")
    if lang not in LANGS:
        raise TTSError("这个语言没有语音")
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    audio = b"".join(provider().synth(p, lang) for p in pieces(text, lang))
    if not audio:
        raise TTSError("没有可读的文字")
    _cache[key] = audio
    while len(_cache) > CACHE_N:
        _cache.popitem(last=False)
    return audio
