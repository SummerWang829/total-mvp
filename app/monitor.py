"""Health monitor: `python -m app.monitor` (cron runs it every 5 minutes, see deploy/monitor.sh) checks that the platform is fit to run and tells someone when that changes.

Checks: the database answers, free disk space, the newest database backup is recent, account emails are not piling up failed, the video server accepts
connections, and (when the background workers are on) public-opinion sources are not all failing. A problem is announced once when it starts and once when it
is over, to a webhook (ALERT_WEBHOOK_URL: a Feishu / WeCom / Slack style incoming webhook that takes {"text": "..."}); without a webhook it is written to the
log only. The last state is kept in a small file so a long outage does not send a message every five minutes.
"""
import json
import logging
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, text

from .config import settings
from .db import SessionLocal

log = logging.getLogger(__name__)

DISK_WARN_FREE = 0.15     # less than this share of the disk free: warn
DISK_BAD_FREE = 0.07
BACKUP_WARN_H = 36
BACKUP_BAD_H = 72
STATE_FILE = os.getenv("MONITOR_STATE", "/tmp/xingtong-monitor.json")
WEBHOOK_TIMEOUT_S = 10


@dataclass
class Check:
    name: str
    level: str      # ok | warn | bad
    detail: str


def check_database() -> Check:
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
        return Check("数据库", "ok", "可以连接")
    except Exception as e:  # noqa: BLE001 - any failure here is exactly what we are here to report
        return Check("数据库", "bad", f"连不上：{type(e).__name__}")


def check_disk(path: str | None = None) -> Check:
    path = path or (settings.backup_dir if Path(settings.backup_dir).exists() else ".")
    usage = shutil.disk_usage(path)
    free = usage.free / usage.total
    level = "bad" if free < DISK_BAD_FREE else "warn" if free < DISK_WARN_FREE else "ok"
    return Check("磁盘", level, f"剩余 {free * 100:.0f}%（{usage.free // 2**30} GB）")


def check_backup(now: float | None = None) -> Check:
    folder = Path(settings.backup_dir)
    newest = max(folder.glob("total-*.db.gz"), default=None, key=lambda f: f.name) if folder.exists() else None
    if not newest:
        return Check("备份", "ok" if not os.path.exists("/.dockerenv") else "warn", "还没有备份文件")
    hours = ((now or time.time()) - newest.stat().st_mtime) / 3600
    level = "bad" if hours > BACKUP_BAD_H else "warn" if hours > BACKUP_WARN_H else "ok"
    return Check("备份", level, f"最近一次 {hours:.0f} 小时前")


def check_mail() -> Check:
    from .models import EmailOutbox
    try:
        with SessionLocal() as db:
            failed = db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "failed")) or 0
            pending = db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "pending")) or 0
    except Exception as e:  # noqa: BLE001
        return Check("账号邮件", "warn", f"查不了：{type(e).__name__}")
    return Check("账号邮件", "warn" if failed or pending > 20 else "ok", f"待发 {pending} 封，失败 {failed} 封")


def check_video(connect=socket.create_connection) -> Check:
    if not settings.livekit_url:
        return Check("视频服务", "ok", "没有配置（本机开发）")
    parsed = urlparse(settings.livekit_url.replace("wss://", "https://").replace("ws://", "http://"))
    host, port = parsed.hostname or "", parsed.port or (443 if settings.livekit_url.startswith("wss") else 80)
    try:
        connect((host, port), timeout=5).close()
        return Check("视频服务", "ok", f"{host}:{port} 可以连接")
    except OSError as e:
        return Check("视频服务", "bad", f"{host}:{port} 连不上：{type(e).__name__}")


def check_listening() -> Check:
    from .models import ListeningSource
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(ListeningSource).where(ListeningSource.enabled.is_(True), ListeningSource.last_status != "")).all()
    except Exception as e:  # noqa: BLE001
        return Check("舆情来源", "warn", f"查不了：{type(e).__name__}")
    failing = [r for r in rows if r.last_status == "error"]
    if rows and len(failing) == len(rows):
        return Check("舆情来源", "warn", f"{len(rows)} 个来源最近一次运行全部出错")
    return Check("舆情来源", "ok", f"{len(rows)} 个启用的来源，{len(failing)} 个最近出错")


def run_checks() -> list[Check]:
    return [check_database(), check_disk(), check_backup(), check_mail(), check_video(), check_listening()]


def _load() -> dict:
    try:
        return json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    try:
        Path(STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        log.warning("monitor: cannot write its state file %s", STATE_FILE)


def changes(checks: list[Check], previous: dict) -> tuple[list[str], dict]:
    """Messages for every check whose state changed since last time (a check that stays bad is not announced again), and the new state."""
    now = {c.name: c.level for c in checks}
    out = []
    for c in checks:
        before = previous.get(c.name, "ok")
        if c.level != "ok" and before == "ok":
            out.append(f"{'🔴' if c.level == 'bad' else '🟠'} {c.name}：{c.detail}")
        elif c.level != "ok" and before == "warn" and c.level == "bad":
            out.append(f"🔴 {c.name}变严重了：{c.detail}")
        elif c.level == "ok" and before != "ok":
            out.append(f"🟢 {c.name}恢复了：{c.detail}")
    return out, now


def notify(messages: list[str], client: httpx.Client | None = None) -> bool:
    """Send the messages to the webhook (one message). Returns False if there is no webhook or it failed; the messages are always logged."""
    body = "星桐监控\n" + "\n".join(messages)
    for m in messages:
        log.warning("monitor: %s", m)
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        r = (client or httpx.Client(timeout=WEBHOOK_TIMEOUT_S)).post(url, json={"msg_type": "text", "content": {"text": body}, "text": body})
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("monitor: the webhook did not take the message: %s", type(e).__name__)
        return False


def main(client: httpx.Client | None = None) -> int:
    """Run the checks, announce changes, remember the state. Exit status: 0 all fine, 1 something needs attention (cron / a wrapper can use it)."""
    checks = run_checks()
    messages, state = changes(checks, _load())
    if messages:
        notify(messages, client)
    _save(state)
    for c in checks:
        print(f"{'✓' if c.level == 'ok' else '!' if c.level == 'warn' else '✗'} {c.name}：{c.detail}")
    return 0 if all(c.level == "ok" for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
