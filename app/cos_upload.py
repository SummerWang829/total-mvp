"""Upload one file to a Tencent Cloud COS bucket (the off-site copy of the database backups).

Only PUT Object is used, signed with the COS v5 request signature (HMAC-SHA1). The sub-account that owns the key is allowed to write to the one
bucket and nothing else (no read, no list, no delete), so a leaked key cannot expose or destroy the backups; expiry of old copies is done by a
lifecycle rule on the bucket, which this code never touches.
"""
import hashlib
import hmac
import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

SIGN_VALID_S = 600
UPLOAD_TIMEOUT_S = 120


class CosError(Exception):
    """An upload that did not succeed (the message says why, in Chinese; never contains the key)."""


def authorization(secret_id: str, secret_key: str, method: str, path: str, host: str, now: int | None = None) -> str:
    """The Authorization header value for one request (COS v5 signature, signing only the Host header)."""
    start = int(now if now is not None else time.time())
    key_time = f"{start};{start + SIGN_VALID_S}"
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    http_string = f"{method.lower()}\n{path}\n\nhost={host.lower()}\n"
    string_to_sign = f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}"
            f"&q-header-list=host&q-url-param-list=&q-signature={signature}")


def endpoint(bucket: str, region: str) -> str:
    """The bucket's host name, e.g. ``xingtong-backup-1250000000.cos.ap-singapore.myqcloud.com``."""
    return f"{bucket}.cos.{region}.myqcloud.com"


def put_file(file: Path, key: str, bucket: str, region: str, secret_id: str, secret_key: str,
             client: httpx.Client | None = None, now: int | None = None) -> None:
    """Upload ``file`` as object ``key``.

    Raises:
        CosError: the file cannot be read, the service refused it (the reason from COS is included), or it could not be reached.
    """
    host = endpoint(bucket, region)
    path = "/" + quote(key.lstrip("/"), safe="/-_.~")
    headers = {"Authorization": authorization(secret_id, secret_key, "put", path, host, now), "Host": host}
    owns = client is None
    client = client or httpx.Client(timeout=UPLOAD_TIMEOUT_S)
    try:
        with open(file, "rb") as body:
            resp = client.put(f"https://{host}{path}", content=body.read(), headers=headers)
    except OSError as e:
        raise CosError(f"读不到要上传的文件：{type(e).__name__}") from e
    except httpx.HTTPError as e:
        raise CosError(f"连不上腾讯云存储：{type(e).__name__}") from e
    finally:
        if owns:
            client.close()
    if resp.status_code not in (200, 201):
        code = _error_code(resp.text)
        raise CosError(f"腾讯云存储拒绝了上传（HTTP {resp.status_code}{'，' + code if code else ''}）：" + _hint(resp.status_code, code))


def _error_code(xml: str) -> str:
    """The <Code> of a COS error answer, or ''."""
    start, end = xml.find("<Code>"), xml.find("</Code>")
    return xml[start + 6:end] if 0 <= start < end else ""


def _hint(status: int, code: str) -> str:
    """What to check, in plain words."""
    if status == 403 and code in {"SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied"}:
        return {"SignatureDoesNotMatch": "SecretKey 不对或有多余字符",
                "InvalidAccessKeyId": "SecretId 不对",
                "AccessDenied": "这个子账号没有写入这个存储桶的权限（去存储桶的“权限管理”里给它加上 PutObject）"}[code]
    if status == 404:
        return "存储桶名称或地域填错了"
    return "请把这条错误发给开发"
