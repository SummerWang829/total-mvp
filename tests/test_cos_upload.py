"""The off-site copy of the database backups: the COS request signature, the upload, and what the backup command does with it."""
import hashlib
import hmac

import httpx
import pytest

from app import backup, cos_upload
from app.config import settings


def _expected_signature(secret_key, key_time, method, path, host):
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    http_string = f"{method}\n{path}\n\nhost={host}\n"
    to_sign = "sha1\n" + key_time + "\n" + hashlib.sha1(http_string.encode()).hexdigest() + "\n"
    return hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()


def test_the_signature_follows_the_cos_v5_recipe():
    host = "bk-125.cos.ap-singapore.myqcloud.com"
    auth = cos_upload.authorization("AKIDexample", "secret", "PUT", "/db/a.db.gz", host, now=1_700_000_000)
    key_time = "1700000000;1700000600"
    assert auth == (f"q-sign-algorithm=sha1&q-ak=AKIDexample&q-sign-time={key_time}&q-key-time={key_time}&q-header-list=host&q-url-param-list="
                    f"&q-signature={_expected_signature('secret', key_time, 'put', '/db/a.db.gz', host)}")
    assert "secret" not in auth  # the key itself never travels


def test_the_endpoint_is_the_buckets_own_host():
    assert cos_upload.endpoint("bk-125", "ap-singapore") == "bk-125.cos.ap-singapore.myqcloud.com"


def _client(status=200, body=""):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, url=str(request.url), auth=request.headers.get("authorization"), body=request.content)
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_a_file_is_put_with_its_signature(tmp_path):
    f = tmp_path / "total-1.db.gz"
    f.write_bytes(b"backup bytes")
    client, seen = _client()
    cos_upload.put_file(f, "db/total-1.db.gz", "bk-125", "ap-singapore", "AKID1", "s3cr3t", client=client, now=1_700_000_000)
    assert seen["method"] == "PUT" and seen["url"] == "https://bk-125.cos.ap-singapore.myqcloud.com/db/total-1.db.gz"
    assert seen["body"] == b"backup bytes" and seen["auth"].startswith("q-sign-algorithm=sha1&q-ak=AKID1&")
    assert "s3cr3t" not in seen["auth"]


@pytest.mark.parametrize("status, code, words", [
    (403, "SignatureDoesNotMatch", "SecretKey"), (403, "InvalidAccessKeyId", "SecretId"), (403, "AccessDenied", "PutObject"),
    (404, "NoSuchBucket", "存储桶名称或地域"), (500, "", "开发")])
def test_a_refusal_says_what_to_check_and_never_shows_the_key(tmp_path, status, code, words):
    f = tmp_path / "x.gz"
    f.write_bytes(b"x")
    client, _ = _client(status, f"<Error><Code>{code}</Code></Error>")
    with pytest.raises(cos_upload.CosError) as e:
        cos_upload.put_file(f, "db/x.gz", "bk-125", "ap-singapore", "AKID1", "s3cr3t", client=client)
    assert words in str(e.value) and "s3cr3t" not in str(e.value)


def test_a_missing_file_and_an_unreachable_service_are_reported(tmp_path):
    client, _ = _client()
    with pytest.raises(cos_upload.CosError, match="读不到"):
        cos_upload.put_file(tmp_path / "missing", "k", "b", "r", "i", "s", client=client)
    f = tmp_path / "x"
    f.write_bytes(b"x")

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(cos_upload.CosError, match="连不上"):
        cos_upload.put_file(f, "k", "b", "r", "i", "s", client=httpx.Client(transport=httpx.MockTransport(boom)))


# ---------- what the backup command does with it ----------
def _live_db(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "live.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(backup.settings, "database_url", f"sqlite:///{db}")
    monkeypatch.setattr(backup.settings, "backup_dir", str(tmp_path / "out"))


def test_without_cos_settings_the_backup_stays_local_and_says_nothing_about_off_site(tmp_path, monkeypatch, capsys):
    _live_db(tmp_path, monkeypatch)
    for name in ("cos_bucket", "cos_secret_id", "cos_secret_key"):
        monkeypatch.setattr(settings, name, "")
    assert backup.main([]) == 0 and "异地" not in capsys.readouterr().out
    with pytest.raises(backup.BackupError, match="没有配置"):
        backup.upload_offsite(tmp_path / "x")


def test_with_cos_settings_the_backup_is_sent_off_site_and_the_time_is_noted(tmp_path, monkeypatch, capsys):
    _live_db(tmp_path, monkeypatch)
    for name, value in (("cos_bucket", "bk-125"), ("cos_secret_id", "AKID1"), ("cos_secret_key", "s3cr3t")):
        monkeypatch.setattr(settings, name, value)
    sent = []
    monkeypatch.setattr(cos_upload, "put_file", lambda file, key, *a, **k: sent.append((file.name, key)))
    assert backup.main([]) == 0
    assert len(sent) == 1 and sent[0][1] == f"db/{sent[0][0]}" and "异地备份完成" in capsys.readouterr().out
    assert (tmp_path / "out" / backup.OFFSITE_MARKER).exists()


def test_when_only_the_off_site_copy_fails_the_local_one_stays_and_the_exit_code_says_so(tmp_path, monkeypatch, capsys):
    _live_db(tmp_path, monkeypatch)
    for name, value in (("cos_bucket", "bk-125"), ("cos_secret_id", "AKID1"), ("cos_secret_key", "s3cr3t")):
        monkeypatch.setattr(settings, name, value)

    def refuse(*a, **k):
        raise cos_upload.CosError("腾讯云存储拒绝了上传")

    monkeypatch.setattr(cos_upload, "put_file", refuse)
    assert backup.main([]) == 3
    assert "异地备份失败" in capsys.readouterr().out and list((tmp_path / "out").glob("total-*.db.gz"))
    assert not (tmp_path / "out" / backup.OFFSITE_MARKER).exists()


def test_the_connectivity_test_uploads_a_small_probe(tmp_path, monkeypatch, capsys):
    for name, value in (("cos_bucket", "bk-125"), ("cos_secret_id", "AKID1"), ("cos_secret_key", "s3cr3t")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(backup.settings, "backup_dir", str(tmp_path))
    sent = []
    monkeypatch.setattr(cos_upload, "put_file", lambda file, key, *a, **k: sent.append((key, file.read_bytes())))
    assert backup.main(["--offsite-test"]) == 0 and sent[0][0] == "test/hello.txt" and "异地备份连通" in capsys.readouterr().out
