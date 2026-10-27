"""Account emails: invitation, password-reset and "forgot password" links, sent through any SMTP service (MAIL_HOST / MAIL_FROM / PUBLIC_BASE_URL).

Mail goes through an outbox table: the request only writes a row, a background thread sends it and retries a failed one with growing
pauses. A row holds a one-time link, so its text is wiped as soon as the mail is sent or given up. Without MAIL_HOST nothing is queued and
the admin hands the link over by hand, as before.
"""
import logging
import smtplib
import threading
import time
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import EmailOutbox, utcnow

log = logging.getLogger(__name__)

RETRY_PAUSES_MIN = (1, 5, 15, 60)  # after attempt 1, 2, 3, 4; the fifth failure gives up
MAX_ATTEMPTS = len(RETRY_PAUSES_MIN) + 1
POLL_S = 10
SMTP_TIMEOUT_S = 20
KEEP_DAYS = 14  # sent / failed rows (without text) are kept this long for the audit trail, then dropped

SUBJECTS = {
    "join": "邀请你加入星桐 / You are invited to 星桐 TOTAL",
    "reset": "重设你的星桐密码 / Reset your 星桐 TOTAL password",
    "forgot": "重设你的星桐密码 / Reset your 星桐 TOTAL password",
}


def configured() -> bool:
    """True when account emails can be sent (a server, a sender and the public address for links are all set)."""
    return bool(settings.mail_host and settings.mail_from and settings.public_base_url)


def link_for(token: str) -> str:
    """The full address of a one-time link (the invite page reads the token from ?t=)."""
    return f"{settings.public_base_url}/invite.html?t={token}"


def body_for(kind: str, org_name: str, link: str, days: int | None = None, hours: int | None = None) -> str:
    """The email text, Chinese then English. Never mentions anything but the company name, and says how long the link lives."""
    life_zh = f"{hours} 小时" if hours else f"{days or 7} 天"
    life_en = f"{hours} hour(s)" if hours else f"{days or 7} days"
    if kind == "join":
        zh = f"{org_name} 邀请你加入星桐。点下面的链接，设置你的姓名和密码即可加入。"
        en = f"{org_name} has invited you to 星桐 TOTAL. Open the link below to set your name and password."
    else:
        zh = "有人（希望是你）请求重设星桐账号的密码。点下面的链接设置新密码。"
        en = "Someone (hopefully you) asked to reset the password of this 星桐 TOTAL account. Open the link below to choose a new one."
    tail_zh = f"链接 {life_zh}内有效，只能用一次。如果这不是你的操作，忽略这封邮件即可，你的密码不会改变。"
    tail_en = f"The link works for {life_en} and only once. If this was not you, ignore this email: your password stays as it is."
    return f"{zh}\n\n{link}\n\n{tail_zh}\n\n----\n\n{en}\n\n{link}\n\n{tail_en}\n"


def queue(db: Session, to_email: str, kind: str, body: str, org_id: str | None = None, subject: str | None = None, ref_id: str = "") -> str:
    """Put one email in the outbox (the caller commits). Returns its id. `subject` is for kinds without a fixed one (a participant's invitation); `ref_id`
    ties the row to the interview it is about.

    Raises:
        RuntimeError: account emails are not configured.
    """
    if not configured():
        raise RuntimeError("account emails are not configured")
    row = EmailOutbox(org_id=org_id, kind=kind, to_email=to_email, subject=subject or SUBJECTS[kind], body=body, ref_id=ref_id)
    db.add(row)
    db.flush()
    return row.id


def _smtp_send(to_email: str, subject: str, body: str) -> None:
    """One delivery over SMTP. Raises smtplib.SMTPException / OSError on any failure. Tests replace this function."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, to_email, subject
    msg.set_content(body)
    if settings.mail_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.mail_host, settings.mail_port, timeout=SMTP_TIMEOUT_S)
    else:
        server = smtplib.SMTP(settings.mail_host, settings.mail_port, timeout=SMTP_TIMEOUT_S)
    with server:
        if settings.mail_security == "starttls":
            server.starttls()
        if settings.mail_user:
            server.login(settings.mail_user, settings.mail_password)
        server.send_message(msg)


def deliver_pending(db: Session, now=None) -> int:
    """Send every email that is due; a failure is logged (never the text or the address) and retried later. Returns how many were sent."""
    now = now or utcnow()
    sent = 0
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.status == "pending", EmailOutbox.next_try <= now).order_by(EmailOutbox.created_at).limit(50)).all()
    for row in rows:
        row.attempts += 1
        try:
            _smtp_send(row.to_email, row.subject, row.body)
        except (smtplib.SMTPException, OSError) as e:
            row.last_error = f"{type(e).__name__}: {e}"[:300]
            if row.attempts >= MAX_ATTEMPTS:
                row.status, row.body = "failed", ""
                log.error("account email %s given up after %d attempts: %s", row.id, row.attempts, type(e).__name__)
            else:
                row.next_try = now + timedelta(minutes=RETRY_PAUSES_MIN[row.attempts - 1])
                log.warning("account email %s failed (attempt %d): %s", row.id, row.attempts, type(e).__name__)
        else:
            row.status, row.body, row.sent_at, row.last_error = "sent", "", now, ""
            sent += 1
        db.commit()
    return sent


def purge_old(db: Session, now=None) -> int:
    """Drop finished rows older than KEEP_DAYS. Returns how many."""
    cutoff = (now or utcnow()) - timedelta(days=KEEP_DAYS)
    n = db.query(EmailOutbox).filter(EmailOutbox.status != "pending", EmailOutbox.created_at < cutoff).delete()
    db.commit()
    return n


def pending_count() -> int:
    """How many emails are waiting to go out (doctor)."""
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "pending")) or 0


def start_worker() -> None:
    """Start the sending thread (a no-op when account emails are not configured)."""
    if not configured():
        return

    def loop() -> None:
        ticks = 0
        while True:
            try:
                with SessionLocal() as db:
                    deliver_pending(db)
                    if ticks % 360 == 0:
                        purge_old(db)
            except Exception:  # the worker must outlive any single failure
                log.exception("mail worker")
            ticks += 1
            time.sleep(POLL_S)

    threading.Thread(target=loop, daemon=True, name="mail-worker").start()
