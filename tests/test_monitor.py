"""The health monitor: each check, announcing only changes, the webhook, and the exit status."""
import json
import os
import time

import httpx

from app import monitor
from app.config import settings
from app.db import SessionLocal
from app.models import EmailOutbox, ListeningSource, utcnow


def test_a_working_database_is_fine_and_a_broken_one_is_bad(monkeypatch):
    assert monitor.check_database().level == "ok"

    class Broken:
        def __enter__(self):
            raise RuntimeError("down")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(monitor, "SessionLocal", lambda: Broken())
    c = monitor.check_database()
    assert c.level == "bad" and "RuntimeError" in c.detail


def test_disk_space_levels(monkeypatch, tmp_path):
    from collections import namedtuple
    U = namedtuple("U", "total used free")
    for free, level in ((50, "ok"), (10, "warn"), (3, "bad")):
        monkeypatch.setattr(monitor.shutil, "disk_usage", lambda p, f=free: U(100, 100 - f, f))
        assert monitor.check_disk(str(tmp_path)).level == level


def test_backup_age_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    assert monitor.check_backup().level in ("ok", "warn")          # nothing yet: only a warning inside the deployed image
    f = tmp_path / "total-20261018-020440.db.gz"
    f.write_bytes(b"x")
    now = time.time()
    for hours, level in ((2, "ok"), (40, "warn"), (80, "bad")):
        os.utime(f, (now - hours * 3600, now - hours * 3600))
        assert monitor.check_backup(now).level == level


def test_failed_account_emails_are_flagged(org):
    assert monitor.check_mail().level == "ok"
    with SessionLocal() as db:
        db.add(EmailOutbox(kind="forgot", to_email="x@example.com", subject="s", body="", status="failed"))
        db.commit()
    c = monitor.check_mail()
    assert c.level == "warn" and "失败 1" in c.detail


def test_the_video_server_check(monkeypatch):
    monkeypatch.setattr(settings, "livekit_url", "")
    assert monitor.check_video().level == "ok"
    monkeypatch.setattr(settings, "livekit_url", "wss://video.example.com")
    seen = []

    class Sock:
        def close(self):
            pass
    assert monitor.check_video(lambda addr, timeout: (seen.append(addr), Sock())[1]).level == "ok" and seen == [("video.example.com", 443)]

    def refuse(addr, timeout):
        raise ConnectionRefusedError()
    c = monitor.check_video(refuse)
    assert c.level == "bad" and "ConnectionRefusedError" in c.detail


def test_listening_sources_all_failing_is_flagged(org):
    pid = org.project()
    with SessionLocal() as db:
        from app.models import Project
        oid = db.get(Project, pid).org_id
        assert monitor.check_listening().level == "ok"
        for i in range(2):
            db.add(ListeningSource(org_id=oid, project_id=pid, kind="rss", name=f"s{i}", config={}, last_status="error", created_by="u"))
        db.commit()
    assert monitor.check_listening().level == "warn"
    with SessionLocal() as db:
        db.query(ListeningSource).first().last_status = "ok"
        db.commit()
    assert monitor.check_listening().level == "ok"


def C(name, level, detail="d"):
    return monitor.Check(name, level, detail)


def test_only_changes_are_announced_so_a_long_outage_is_one_message():
    msgs, state = monitor.changes([C("磁盘", "bad", "剩 3%"), C("备份", "ok")], {})
    assert len(msgs) == 1 and "🔴 磁盘" in msgs[0] and state == {"磁盘": "bad", "备份": "ok"}
    msgs, state = monitor.changes([C("磁盘", "bad", "剩 3%"), C("备份", "ok")], state)
    assert msgs == []                                            # still bad: no repeat
    msgs, state = monitor.changes([C("磁盘", "ok", "剩 40%"), C("备份", "ok")], state)
    assert len(msgs) == 1 and "🟢 磁盘恢复了" in msgs[0]
    msgs, _ = monitor.changes([C("磁盘", "warn")], {"磁盘": "ok"})
    assert "🟠" in msgs[0]
    msgs, _ = monitor.changes([C("磁盘", "bad")], {"磁盘": "warn"})
    assert "变严重了" in msgs[0]


def test_the_webhook_gets_one_message_and_a_failure_is_not_fatal(monkeypatch):
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (sent.append(json.loads(r.content)), httpx.Response(200))[1]))
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert monitor.notify(["🔴 磁盘：剩 3%"], client) is False and sent == []        # no webhook: only logged
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    assert monitor.notify(["🔴 磁盘：剩 3%", "🟠 备份：旧"], client) is True
    assert len(sent) == 1 and "磁盘" in sent[0]["text"] and "备份" in sent[0]["text"] and sent[0]["text"].startswith("星桐监控")
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert monitor.notify(["x"], bad) is False


def test_main_exits_zero_when_fine_one_when_not_and_remembers_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(monitor, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(monitor, "run_checks", lambda: [C("磁盘", "ok", "fine")])
    assert monitor.main() == 0
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (sent.append(1), httpx.Response(200))[1]))
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.setattr(monitor, "run_checks", lambda: [C("磁盘", "bad", "剩 3%")])
    assert monitor.main(client) == 1 and len(sent) == 1
    assert monitor.main(client) == 1 and len(sent) == 1       # same problem: no second message
    out = capsys.readouterr().out
    assert "✗ 磁盘：剩 3%" in out and json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {"磁盘": "bad"}
