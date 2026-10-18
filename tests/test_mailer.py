"""Account emails: invitation and reset links by email, "forgot password" that never reveals who has an account, and an outbox that retries."""
import smtplib
from datetime import timedelta

import pytest

from app import mailer, ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import EmailOutbox, UserInvite, utcnow
from conftest import H


@pytest.fixture
def mail(monkeypatch):
    """Account emails switched on, with the SMTP step replaced by a recorder (tests never send real mail)."""
    for k, v in (("mail_host", "smtp.test"), ("mail_from", "星桐 <no-reply@test.example>"), ("public_base_url", "https://app.test")):
        monkeypatch.setattr(settings, k, v)
    box = {"sent": [], "fail": None}

    def fake(to, subject, body):
        if box["fail"]:
            raise box["fail"]
        box["sent"].append((to, subject, body))

    monkeypatch.setattr(mailer, "_smtp_send", fake)
    monkeypatch.setattr(settings, "rate_limit", True)
    ratelimit.reset()
    yield box
    ratelimit.reset()


def flush():
    with SessionLocal() as db:
        return mailer.deliver_pending(db)


def token_of(body):
    return body.split("invite.html?t=")[1].split()[0]


# ---------- invitation and reset links ----------
def test_an_invitation_can_be_sent_by_email_and_the_link_works(org, mail):
    r = org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": "new@acme.com", "role": "researcher", "send_email": True})
    assert r.status_code == 201 and r.json()["emailed"] is True
    assert flush() == 1
    to, subject, body = mail["sent"][0]
    assert to == "new@acme.com" and "邀请" in subject and "https://app.test/invite.html?t=" in body and "Acme" in body
    acc = org.c.post(f"/api/public/invite/{token_of(body)}/accept", json={"name": "新同事", "password": "password123"})
    assert acc.status_code == 200 and acc.json()["user"]["role"] == "researcher"


def test_without_send_email_nothing_is_queued_and_the_link_is_still_returned(org, mail):
    r = org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": "new@acme.com", "role": "researcher"})
    assert r.json()["emailed"] is False and r.json()["invite_path"].startswith("/invite.html?t=")
    assert flush() == 0 and mail["sent"] == []


def test_asking_for_an_email_when_mail_is_not_set_up_is_refused_clearly(org):
    assert not mailer.configured()
    r = org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": "new@acme.com", "role": "researcher", "send_email": True})
    assert r.status_code == 409 and "发信" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.query(UserInvite).count() == 0   # nothing half-made
    assert org.c.get("/api/mail/status", headers=org.h("admin")).json() == {"configured": False}


def test_an_admin_can_email_a_reset_link_and_only_admins_see_the_mail_status(org, mail):
    r = org.c.post(f"/api/users/{org.users['researcher']}/reset-link?send_email=true", headers=org.h("admin"))
    assert r.status_code == 201 and r.json()["emailed"] is True
    flush()
    assert mail["sent"][0][0] == "researcher@acme.com" and "密码" in mail["sent"][0][1]
    assert org.c.get("/api/mail/status", headers=org.h("admin")).json() == {"configured": True}
    assert org.c.get("/api/mail/status", headers=org.h("researcher")).status_code == 403


# ---------- forgot password ----------
def test_forgot_password_emails_a_one_hour_one_time_link_that_sets_a_new_password(org, mail):
    r = org.c.post("/api/auth/forgot", json={"email": "Researcher@Acme.com"})
    assert r.status_code == 202 and r.json() == {"ok": True}
    flush()
    to, _subject, body = mail["sent"][0]
    assert to == "researcher@acme.com" and "1 小时" in body
    tok = token_of(body)
    info = org.c.get(f"/api/public/invite/{tok}").json()
    assert info["kind"] == "reset" and info["email"] == "researcher@acme.com"
    assert org.c.post(f"/api/public/invite/{tok}/accept", json={"password": "brand-new-pass"}).status_code == 200
    assert org.c.post("/api/auth/login", json={"email": "researcher@acme.com", "password": "brand-new-pass"}).status_code == 200
    assert org.c.post("/api/auth/login", json={"email": "researcher@acme.com", "password": "password123"}).status_code == 401
    assert org.c.get(f"/api/public/invite/{tok}").status_code == 404   # used once


def test_forgot_password_answers_the_same_for_unknown_stopped_and_known_accounts(org, mail):
    org.c.patch(f"/api/users/{org.users['observer']}", headers=org.h("admin"), json={"active": False})
    answers = [org.c.post("/api/auth/forgot", json={"email": e}) for e in ("nobody@nowhere.com", "observer@acme.com", "researcher@acme.com", "not an email")]
    assert {(a.status_code, str(a.json())) for a in answers} == {(202, "{'ok': True}")}
    flush()
    assert [m[0] for m in mail["sent"]] == ["researcher@acme.com"]   # only the live account got mail


def test_forgot_password_is_quietly_limited_per_account_and_per_address(org, mail):
    for _ in range(6):
        assert org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"}).status_code == 202
    flush()
    assert len(mail["sent"]) == 3   # the fourth and later asks sent nothing
    mail["sent"].clear()
    for i in range(10):
        org.c.post("/api/auth/forgot", json={"email": f"x{i}@acme.com"})
    ratelimit.reset()


def test_a_new_forgot_link_replaces_the_previous_one(org, mail):
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    flush()
    first, second = (token_of(m[2]) for m in mail["sent"])
    assert org.c.get(f"/api/public/invite/{first}").status_code == 404 and org.c.get(f"/api/public/invite/{second}").status_code == 200


def test_the_forgot_link_expires_after_an_hour(org, mail):
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    with SessionLocal() as db:
        inv = db.query(UserInvite).filter_by(kind="reset").one()
        assert timedelta(minutes=50) < inv.expires_at - utcnow() <= timedelta(hours=1)
        inv.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    flush()
    assert org.c.get(f"/api/public/invite/{token_of(mail['sent'][0][2])}").status_code == 404


def test_forgot_password_does_nothing_when_mail_is_not_set_up(org):
    assert org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"}).status_code == 202
    with SessionLocal() as db:
        assert db.query(EmailOutbox).count() == 0 and db.query(UserInvite).count() == 0


# ---------- the outbox ----------
def test_a_failed_send_is_retried_later_and_the_text_is_wiped_once_sent(org, mail):
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    mail["fail"] = smtplib.SMTPServerDisconnected("down")
    assert flush() == 0
    with SessionLocal() as db:
        row = db.query(EmailOutbox).one()
        assert row.status == "pending" and row.attempts == 1 and row.body and "SMTPServerDisconnected" in row.last_error
    assert flush() == 0   # not due yet: a failed mail waits before the next try
    mail["fail"] = None
    with SessionLocal() as db:
        assert mailer.deliver_pending(db, now=utcnow() + timedelta(minutes=2)) == 1
        row = db.query(EmailOutbox).one()
        assert row.status == "sent" and row.body == "" and row.sent_at and len(mail["sent"]) == 1


def test_after_five_failures_the_mail_is_given_up_and_its_link_is_wiped(org, mail):
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    mail["fail"] = OSError("no route")
    now = utcnow()
    with SessionLocal() as db:
        for i in range(mailer.MAX_ATTEMPTS):
            mailer.deliver_pending(db, now=now + timedelta(hours=3 * (i + 1)))
        row = db.query(EmailOutbox).one()
        assert row.status == "failed" and row.attempts == mailer.MAX_ATTEMPTS and row.body == ""
        assert mailer.deliver_pending(db, now=now + timedelta(days=2)) == 0


def test_old_finished_rows_are_dropped_but_pending_ones_stay(org, mail):
    org.c.post("/api/auth/forgot", json={"email": "researcher@acme.com"})
    flush()
    org.c.post("/api/users/invites", headers=org.h("admin"), json={"email": "n@acme.com", "role": "observer", "send_email": True})
    with SessionLocal() as db:
        assert mailer.purge_old(db) == 0
        assert mailer.purge_old(db, now=utcnow() + timedelta(days=mailer.KEEP_DAYS + 1)) == 1   # the sent one only
        assert db.query(EmailOutbox).filter_by(status="pending").count() == 1


def test_the_email_text_is_bilingual_names_the_company_and_the_lifetime():
    body = mailer.body_for("join", "Acme", "https://x/invite.html?t=abc", days=7)
    assert "Acme" in body and "7 天" in body and "7 days" in body and body.count("https://x/invite.html?t=abc") == 2
    assert "1 hour" in mailer.body_for("reset", "Acme", "https://x/i", hours=1)
