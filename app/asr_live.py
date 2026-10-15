"""Live captions: the signed WebSocket address for Tencent Cloud real-time speech recognition.

Doc: https://cloud.tencent.com/document/api/1093/48982  (checked 2026-10-15)
  wss://asr.cloud.tencent.com/asr/v2/<appid>?<params>&signature=<sig>
  string to sign = "asr.cloud.tencent.com/asr/v2/<appid>?" + all params except signature, sorted by name, joined with "&"
  signature      = urlencode(base64(HMAC-SHA1(SecretKey, string to sign)))
The browser streams 16 kHz 16-bit mono PCM (200 ms = 6400 bytes per message) and receives JSON; this server never sees the audio.
"""
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from urllib.parse import quote

from .config import settings

SIGN_HOST = "asr.cloud.tencent.com"
TTL_S = 600  # the address is only needed to open the connection; a new one is signed for every (re)connect
ENGINES = {"zh": "16k_zh", "en": "16k_en"}  # Malay and Tamil have no engine: captions are off for those interviews
SAMPLE_RATE = 16000
FRAME_MS = 200


def live_captions_configured() -> bool:
    """Whether the Tencent Cloud credentials are all present."""
    return bool(settings.tencent_asr_appid and settings.tencent_asr_secret_id and settings.tencent_asr_secret_key)


def sign(appid: str, secret_key: str, params: dict[str, str]) -> str:
    """The URL-encoded signature for these query parameters (the doc's algorithm, step by step)."""
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    raw = f"{SIGN_HOST}/asr/v2/{appid}?{query}"
    digest = hmac.new(secret_key.encode(), raw.encode(), hashlib.sha1).digest()
    return quote(base64.b64encode(digest).decode(), safe="")


def signed_url(language: str, now: float | None = None) -> str | None:
    """A fresh signed address for one recognition session, or None when captions are off for this language / not configured."""
    engine = ENGINES.get(language)
    if not engine or not live_captions_configured():
        return None
    ts = int(now if now is not None else time.time())
    params = {
        "secretid": settings.tencent_asr_secret_id, "timestamp": str(ts), "expired": str(ts + TTL_S), "nonce": str(secrets.randbelow(10**9) + 1),
        "engine_model_type": engine, "voice_id": uuid.uuid4().hex[:24], "voice_format": "1",
        "needvad": "1", "vad_silence_time": "700", "filter_modal": "1", "convert_num_mode": "1", "word_info": "0",
    }
    sig = sign(settings.tencent_asr_appid, settings.tencent_asr_secret_key, params)
    query = "&".join(f"{k}={quote(params[k], safe='')}" for k in sorted(params))
    base = settings.asr_live_ws_base.rstrip("/")
    return f"{base}/asr/v2/{settings.tencent_asr_appid}?{query}&signature={sig}"


def token_out(language: str) -> dict:
    """What the browser needs to start captioning, or why it cannot."""
    if language not in ENGINES:
        return {"enabled": False, "reason": "这场访谈的语言暂不支持实时字幕（目前支持中文、英文）"}
    if not live_captions_configured():
        return {"enabled": False, "reason": "实时字幕未开启：服务器还没有配置腾讯云语音识别"}
    return {"enabled": True, "url": signed_url(language), "sample_rate": SAMPLE_RATE, "frame_ms": FRAME_MS}
