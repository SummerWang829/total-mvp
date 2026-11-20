"""The words in an image (修正方案第 1 轮第 2 条: images attached to an AI chat), read by Tencent Cloud general OCR (GeneralBasicOCR, checked 2026-11-20:
https://cloud.tencent.com/document/api/866/33526). The model the team uses reads text only, so only the text in the image reaches it; the page says so.
Nothing is stored and nothing of the image or its text is logged.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from .config import settings

log = logging.getLogger(__name__)

HOST = "ocr.tencentcloudapi.com"
VERSION = "2018-11-19"
MAX_BYTES = 5 * 1024 * 1024   # the service takes at most 7MB once base64-encoded
TIMEOUT_S = 20


class OCRError(ValueError):
    """The image cannot be read; the message is plain Chinese for the page. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return settings.ocr_provider == "mock" or (settings.ocr_provider == "tencent" and bool(settings.tencent_ocr_secret_id and settings.tencent_ocr_secret_key))


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _headers(payload: str, now: float | None = None) -> dict[str, str]:
    ts = int(now if now is not None else time.time())
    date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    ctype = "application/json; charset=utf-8"
    action = "GeneralBasicOCR"
    canonical = f"POST\n/\n\ncontent-type:{ctype}\nhost:{HOST}\nx-tc-action:{action.lower()}\n\ncontent-type;host;x-tc-action\n{hashlib.sha256(payload.encode()).hexdigest()}"
    scope = f"{date}/ocr/tc3_request"
    to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = _hmac(_hmac(_hmac(("TC3" + settings.tencent_ocr_secret_key).encode(), date), "ocr"), "tc3_request")
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"TC3-HMAC-SHA256 Credential={settings.tencent_ocr_secret_id}/{scope}, SignedHeaders=content-type;host;x-tc-action, Signature={sig}",
            "Content-Type": ctype, "Host": HOST, "X-TC-Action": action, "X-TC-Version": VERSION, "X-TC-Timestamp": str(ts), "X-TC-Region": settings.tencent_ocr_region}


def read_image(data: bytes, client: httpx.Client | None = None) -> str:
    """The text in the image, lines joined.

    Raises:
        OCRError: not set up, too big, no words found, or the service refused / could not be reached.
    """
    if not configured():
        raise OCRError("图片文字识别还没配置，请先上传 PDF、Word 或文字文件", 503)
    if len(data) > MAX_BYTES:
        raise OCRError("图片太大，请控制在 5MB 以内", 413)
    if settings.ocr_provider == "mock":
        return "（演示）图中的文字"
    payload = json.dumps({"ImageBase64": base64.b64encode(data).decode()}, separators=(",", ":"))
    try:
        r = (client or httpx).post(f"https://{HOST}/", headers=_headers(payload), content=payload.encode(), timeout=TIMEOUT_S)
        resp = r.json().get("Response", {})
    except (httpx.HTTPError, ValueError) as e:
        log.warning("ocr: request failed (%s)", type(e).__name__)
        raise OCRError("图片文字识别服务连不上，请稍后再试", 502) from e
    if "Error" in resp:
        code = str(resp["Error"].get("Code", ""))
        log.warning("ocr: service error %s", code)
        if "ImageNoText" in code or "NoText" in code:
            raise OCRError("这张图片里没有读到文字")
        raise OCRError("图片文字识别服务拒绝了请求，请联系管理员", 502)
    text = "\n".join(str(t.get("DetectedText", "")) for t in resp.get("TextDetections", []) if t.get("DetectedText"))
    if not text.strip():
        raise OCRError("这张图片里没有读到文字")
    return text
