"""Database backup (python -m app.backup): a consistent snapshot of the live SQLite database, compressed, with old ones pruned.

The snapshot uses SQLite's own online-backup call, so it is consistent even while the site is writing (a plain file copy of a database in WAL
mode can be torn). Backups expire (BACKUP_KEEP_DAYS) so a participant's erased data is not kept in them for long; the retention rules in
``app/retention.py`` apply to the live data, and this expiry is what lets them apply to the copies too.

For Postgres (the plan before real participants) use ``pg_dump`` instead; this module refuses to guess and says so.
"""
import gzip
import logging
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings

log = logging.getLogger(__name__)

PREFIX = "total-"
SUFFIX = ".db.gz"
KEEP_DAYS_DEFAULT = 14


class BackupError(Exception):
    """A backup that cannot be made or checked (the message says why, in Chinese)."""


def sqlite_path(url: str) -> Path:
    """The file behind a ``sqlite:///...`` URL.

    Raises:
        BackupError: the URL is not a file-based SQLite database.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        raise BackupError("当前不是文件型 SQLite 数据库；Postgres 请用 pg_dump 备份")
    return Path(parsed.database)


def make_backup(db_url: str, out_dir: Path, now: datetime | None = None) -> Path:
    """Write a compressed consistent snapshot of the database into ``out_dir`` and return its path.

    Raises:
        BackupError: not a SQLite file, the file does not exist, or the snapshot could not be written.
    """
    source = sqlite_path(db_url)
    if not source.exists():
        raise BackupError(f"找不到数据库文件：{source.name}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PREFIX}{stamp}{SUFFIX}"
    with tempfile.TemporaryDirectory(dir=out_dir) as scratch:
        snapshot = Path(scratch) / "snapshot.db"
        try:
            src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)  # SQLite's online backup: consistent while the site keeps writing
            finally:
                dst.close()
                src.close()
            with open(snapshot, "rb") as raw, gzip.open(target, "wb", compresslevel=6) as packed:
                shutil.copyfileobj(raw, packed)
        except (sqlite3.Error, OSError) as e:
            target.unlink(missing_ok=True)
            raise BackupError(f"备份失败：{type(e).__name__}") from e
    log.info("database backup written: %s (%d bytes)", target.name, target.stat().st_size)
    return target


def prune(out_dir: Path, keep_days: int, now: float | None = None) -> list[str]:
    """Delete backups older than ``keep_days`` (by the time in the file name, not the file's own date). Returns the deleted names."""
    cutoff = (now or time.time()) - keep_days * 86400
    gone = []
    for path in sorted(out_dir.glob(f"{PREFIX}*{SUFFIX}")):
        try:
            taken = datetime.strptime(path.name[len(PREFIX):-len(SUFFIX)], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue  # not one of ours: never delete what we cannot read the date of
        if taken < cutoff:
            path.unlink()
            gone.append(path.name)
    return gone


def verify(backup: Path) -> dict[str, int]:
    """Open a backup in a scratch copy and count the rows of every table (proof it can be restored).

    Raises:
        BackupError: the file is not a readable SQLite database, or its integrity check fails.
    """
    with tempfile.TemporaryDirectory() as scratch:
        restored = Path(scratch) / "restored.db"
        try:
            with gzip.open(backup, "rb") as packed, open(restored, "wb") as raw:
                shutil.copyfileobj(packed, raw)
            conn = sqlite3.connect(restored)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("备份文件没有通过完整性检查")
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
            finally:
                conn.close()
        except (sqlite3.Error, OSError, EOFError) as e:
            raise BackupError(f"备份文件无法读取：{type(e).__name__}") from e


def main(argv: list[str]) -> int:
    """python -m app.backup [--verify FILE]: make a backup (and prune), or check an existing one. Exit code 0 on success."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if len(argv) == 2 and argv[0] == "--verify":
            counts = verify(Path(argv[1]))
            print("可以恢复：" + "，".join(f"{t} {n}" for t, n in sorted(counts.items()) if n))
            return 0
        out_dir = Path(settings.backup_dir)
        path = make_backup(settings.database_url, out_dir)
        counts = verify(path)  # a backup nobody has opened is not yet a backup
        removed = prune(out_dir, settings.backup_keep_days)
        print(f"备份完成：{path.name}（{path.stat().st_size // 1024} KB，{len(counts)} 张表）；已清理过期备份 {len(removed)} 个")
        return 0
    except BackupError as e:
        log.error("backup failed: %s", e)
        print(f"备份失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
