"""Transcription of a finished recording with Tencent Cloud recorded-file speech recognition (ASR_PROVIDER=tencent).

Docs (checked 2026-10-15): CreateRecTask https://cloud.tencent.com/document/api/1093/37823, DescribeTaskStatus .../37822, signing TC3-HMAC-SHA256.
Audio goes up as base64 (the API allows 5 MB), so a long track is cut into pieces of PIECE_S seconds first. Only Chinese and English are supported by the
service: any other interview language is refused with a plain message instead of a wrong transcript. Uses the same SecretId / SecretKey as the live captions.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from .config import settings

log = logging.getLogger(__name__)

HOST = "asr.tencentcloudapi.com"
VERSION = "2019-06-14"
ENGINES = {"zh": "16k_zh", "en": "16k_en"}
PIECE_S = 10 * 60  # 32 kbit/s mono mp3 = 2.4 MB per piece, far below the 5 MB (base64) limit
POLL_S = 2.0


class ASRError(Exception):
    """The recognition service refused or failed; the message is safe to show to staff (no secrets in it)."""


class ASRUnsupported(ASRError):
    """The interview language has no recognition engine."""


def file_asr_configured() -> bool:
    return bool(settings.tencent_asr_secret_id and settings.tencent_asr_secret_key)


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def tc3_headers(action: str, payload: str, secret_id: str, secret_key: str, now: float | None = None) -> dict[str, str]:
    """Request headers incl. the TC3-HMAC-SHA256 Authorization for a POST of `payload` (JSON) to asr.tencentcloudapi.com."""
    ts = int(now if now is not None else time.time())
    date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    ctype = "application/json; charset=utf-8"
    canonical = (f"POST\n/\n\ncontent-type:{ctype}\nhost:{HOST}\nx-tc-action:{action.lower()}\n\n"
                 f"content-type;host;x-tc-action\n{hashlib.sha256(payload.encode()).hexdigest()}")
    scope = f"{date}/asr/tc3_request"
    to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = _hmac(_hmac(_hmac(("TC3" + secret_key).encode(), date), "asr"), "tc3_request")
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host;x-tc-action, Signature={sig}",
            "Content-Type": ctype, "Host": HOST, "X-TC-Action": action, "X-TC-Version": VERSION, "X-TC-Timestamp": str(ts)}


def call(action: str, body: dict, client: httpx.Client | None = None) -> dict:
    """One signed API call; returns the `Response` object.

    Raises:
        ASRError: the service answered with an error (code and message included) or could not be reached.
    """
    payload = json.dumps(body, separators=(",", ":"))
    headers = tc3_headers(action, payload, settings.tencent_asr_secret_id, settings.tencent_asr_secret_key)
    try:
        r = (client or httpx).post(f"https://{HOST}/", headers=headers, content=payload.encode(), timeout=60)
        resp = r.json()["Response"]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise ASRError(f"腾讯云语音识别没有正常响应（{type(e).__name__}）")
    if "Error" in resp:
        raise ASRError(f"腾讯云语音识别返回错误 {resp['Error'].get('Code')}：{resp['Error'].get('Message')}")
    return resp


def parse_result(data: dict) -> list[dict]:
    """Sentences [{start_ms, end_ms, text}] from a finished task: ResultDetail if present, else the timestamped lines of `Result`."""
    rows = []
    for d in data.get("ResultDetail") or []:
        text = (d.get("FinalSentence") or "").strip()
        if text:
            rows.append(dict(start_ms=int(d.get("StartMs", 0)), end_ms=max(int(d.get("EndMs", 0)), int(d.get("StartMs", 0)) + 200), text=text))
    if rows:
        return rows
    import re

    for m in re.finditer(r"\[(\d+):(\d+(?:\.\d+)?),(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", data.get("Result") or ""):  # "[0:0.020,0:2.380]  text"
        text = m.group(5).strip()
        if text:
            s, e = int((int(m.group(1)) * 60 + float(m.group(2))) * 1000), int((int(m.group(3)) * 60 + float(m.group(4))) * 1000)
            rows.append(dict(start_ms=s, end_ms=max(e, s + 200), text=text))
    return rows


class TencentFileASR:
    """ASR for `transcribe_audio(path, language, role)` (the interface app/pipeline.py uses)."""

    def _recognise(self, path: str, engine: str) -> list[dict]:
        with open(path, "rb") as f:
            raw = f.read()
        task = call("CreateRecTask", {"EngineModelType": engine, "ChannelNum": 1, "ResTextFormat": 3, "SourceType": 1, "Data": base64.b64encode(raw).decode(),
                                      "DataLen": len(raw), "FilterDirty": 0, "FilterModal": 0, "ConvertNumMode": 1})["Data"]["TaskId"]
        deadline = time.time() + settings.asr_timeout_s
        while time.time() < deadline:
            data = call("DescribeTaskStatus", {"TaskId": task})["Data"]
            if data["Status"] == 2:
                return parse_result(data)
            if data["Status"] == 3:
                raise ASRError("腾讯云语音识别失败：" + str(data.get("ErrorMsg") or "未知原因"))
            time.sleep(POLL_S)
        raise ASRError("腾讯云语音识别超时，请稍后重试")

    def transcribe_audio(self, path: str, language: str, role: str = "") -> list[dict]:
        """Sentences with start/end on this track's own timeline (the caller shifts them onto the shared one).

        Raises:
            ASRUnsupported: no engine for this language (only Chinese and English).
            ASRError: the service failed.
        """
        engine = ENGINES.get(language)
        if not engine:
            raise ASRUnsupported("腾讯云录音文件识别只支持中文和英文；这场访谈的语言（" + language + "）暂时不能转写")
        pieces = [(path, 0)]
        if os.path.getsize(path) > 3 * 1024 * 1024:  # base64 of a bigger file would break the 5 MB limit
            from .recording import run

            base = path.rsplit(".", 1)[0] + ".tc%03d.mp3"
            run([settings.ffmpeg, "-y", "-v", "error", "-i", path, "-f", "segment", "-segment_time", str(PIECE_S), "-c", "copy", base], timeout=600)
            pieces = [(base % i, i * PIECE_S * 1000) for i in range(1000) if os.path.isfile(base % i)]
        out = []
        for p, off in pieces:
            for r in self._recognise(p, engine):
                out.append(dict(start_ms=off + r["start_ms"], end_ms=off + r["end_ms"], text=r["text"], lang=language))
        log.info("tencent file asr: %s track, %d pieces, %d sentences", role or "?", len(pieces), len(out))
        return out
