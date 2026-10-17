"""Database backups: consistent snapshot, restorable, old ones expire, and a failure is reported rather than hidden."""
import gzip
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import backup


def _db(tmp_path, rows=50):
    path = tmp_path / "live.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes (body) VALUES (?)", [(f"row {i}",) for i in range(rows)])
    conn.commit()
    return path, conn


def test_a_backup_can_be_restored_with_every_row(tmp_path):
    path, _ = _db(tmp_path)
    made = backup.make_backup(f"sqlite:///{path}", tmp_path / "out")
    assert made.name.startswith("total-") and made.name.endswith(".db.gz")
    assert backup.verify(made) == {"notes": 50}


def test_a_backup_made_while_the_site_is_writing_is_still_consistent(tmp_path):
    path, conn = _db(tmp_path, rows=10)
    stop = threading.Event()

    def writer():
        w = sqlite3.connect(path, timeout=30)
        while not stop.is_set():
            w.execute("INSERT INTO notes (body) VALUES ('more')")
            w.commit()
        w.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        time.sleep(0.2)
        made = backup.make_backup(f"sqlite:///{path}", tmp_path / "out")
    finally:
        stop.set()
        t.join()
    assert backup.verify(made)["notes"] >= 10  # the integrity check inside verify() would raise on a torn copy


def test_old_backups_expire_and_foreign_files_are_left_alone(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    for days in (1, 13, 15, 40):
        name = f"total-{(now - timedelta(days=days)).strftime('%Y%m%d-%H%M%S')}.db.gz"
        (out / name).write_bytes(b"x")
    (out / "notes.txt").write_text("keep me")
    (out / "total-garbage.db.gz").write_bytes(b"x")
    gone = backup.prune(out, keep_days=14, now=now.timestamp())
    assert len(gone) == 2 and (out / "notes.txt").exists() and (out / "total-garbage.db.gz").exists()
    assert len(list(out.glob("total-2026*.db.gz"))) == 2


def test_failures_are_reported_not_hidden(tmp_path):
    with pytest.raises(backup.BackupError, match="Postgres"):
        backup.make_backup("postgresql://u:p@h/db", tmp_path)
    with pytest.raises(backup.BackupError, match="找不到"):
        backup.make_backup(f"sqlite:///{tmp_path / 'missing.db'}", tmp_path)
    bad = tmp_path / "bad.db.gz"
    bad.write_bytes(gzip.compress(b"this is not a database"))
    with pytest.raises(backup.BackupError):
        backup.verify(bad)


def test_the_command_line_makes_verifies_and_prunes(tmp_path, monkeypatch, capsys):
    path, _ = _db(tmp_path)
    monkeypatch.setattr(backup.settings, "database_url", f"sqlite:///{path}")
    monkeypatch.setattr(backup.settings, "backup_dir", str(tmp_path / "out"))
    assert backup.main([]) == 0
    assert "备份完成" in capsys.readouterr().out
    newest = sorted((tmp_path / "out").glob("total-*.db.gz"))[-1]
    assert backup.main(["--verify", str(newest)]) == 0
    monkeypatch.setattr(backup.settings, "database_url", "postgresql://u:p@h/db")
    assert backup.main([]) == 1
