"""The invitation letter: a session without a name, the length, the email (checked, queued, worded in the participant's language), changing a session
after the invitation went out, and the rule that the kind of moderator is locked once the participant has agreed."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app import invites, mailer
from app.config import settings
from app.db import SessionLocal
from app.models import Consent, EmailOutbox, Interview, Participant

LINK = "https://app.test/join.html?t="


@pytest.fixture
def mail(monkeypatch):
    for k, v in (("mail_host", "smtp.test"), ("mail_from", "星桐 <no-reply@test.example>"), ("public_base_url", "https://app.test")):
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(invites, "domain_resolves", lambda d: d != "nowhere.invalid")
    yield


def person(org, pid, name="CC", email="cc@example.com", language="zh"):
    r = org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": name, "email": email, "country": "SG", "language": language})
    assert r.status_code == 201, r.text
    return r.json()


def invite(org, pid, who, **kw):
    r = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": who["id"], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def outbox():
    with SessionLocal() as db:
        return db.scalars(select(EmailOutbox).where(EmailOutbox.kind.in_(["invite", "invite_update"])).order_by(EmailOutbox.created_at)).all()


def test_a_session_needs_no_name_and_keeps_the_length(client, org):
    pid = org.project()
    who = person(org, pid)
    it = invite(org, pid, who)
    assert it["title"].startswith("深访 · M-") and it["duration_min"] == 45
    assert invite(org, pid, person(org, pid, "乙", "b@example.com"), duration_min=60, title="我的名字")["title"] == "我的名字"
    assert org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": who["id"], "duration_min": 2}).status_code == 422  # 5 to 240 minutes


def test_the_email_check_says_what_is_wrong(monkeypatch):
    monkeypatch.setattr(invites, "domain_resolves", lambda d: d != "nowhere.invalid")
    assert invites.check_email("a@example.com") == ""
    assert "缺少 @" in invites.check_email("abc") and "域名不完整" in invites.check_email("a@bb") and "收不到邮件" in invites.check_email("a@nowhere.invalid")
    assert invites.check_email("  ") == "没有邮箱"


def test_the_letter_is_written_in_singapore_time_and_the_participants_language():
    itv = Interview(scheduled_at=datetime(2026, 10, 28, 6, 0), duration_min=45, moderation="human")
    zh = invites.compose(itv, Participant(name="CC", language="zh"), "李研究", LINK + "x")
    en = invites.compose(itv, Participant(name="CC", language="en"), "Li", LINK + "x")
    assert "10/28 周三 14:00" in zh[0] and "10/28 周三 14:00（新加坡时间，SGT）" in zh[1] and "李研究（真人主持）" in zh[1] and "约 45 分钟" in zh[1] and LINK + "x" in zh[1]
    assert "Wed" in en[0] and "Singapore time" in en[1] and "human moderator" in en[1]
    none = invites.compose(Interview(scheduled_at=None, duration_min=30, moderation="ai"), Participant(name="CC", language="zh"), "", None, update=True)
    assert "随时可进入" in none[1] and "稍后通知" in none[1] and "AI 主持" in none[1] and "链接不变" in none[1] and "http" not in none[1]  # a change notice repeats no link


def test_sending_without_a_mail_service_or_with_a_bad_address_still_creates_the_session(client, org):
    pid = org.project()
    a = invite(org, pid, person(org, pid, "甲", "a@example.com"), send_email=True)
    assert a["mail"]["status"] == "no_mailer" and "创建并生成邀请函" in a["mail"]["reason"] and a["invite_path"]
    b = invite(org, pid, person(org, pid, "乙", "abc"), send_email=True)
    assert b["mail"]["status"] == "bad_email" and "缺少 @" in b["mail"]["reason"]
    c = invite(org, pid, person(org, pid, "丙", ""), send_email=True)
    assert c["mail"]["status"] == "no_email"
    assert outbox() == []


def test_sending_queues_one_letter_with_the_link_and_the_state_follows_the_outbox(client, org, mail):
    pid = org.project()
    it = invite(org, pid, person(org, pid), send_email=True, scheduled_at="2026-10-28T06:00:00", moderator_id=org.c.get("/api/me", headers=org.h()).json()["id"])
    assert it["mail"] == {"status": "queued", "reason": ""} and it["mail_status"] == "pending"
    rows = outbox()
    assert len(rows) == 1 and rows[0].to_email == "cc@example.com" and rows[0].ref_id == it["id"] and it["invite_path"].split("t=")[1] in rows[0].body
    with SessionLocal() as db:
        mailer.deliver_pending(db) if False else None
        row = db.get(EmailOutbox, rows[0].id)
        row.status = "failed"
        db.commit()
    assert org.c.get(f"/api/projects/{pid}/interviews", headers=org.h()).json()[0]["mail_status"] == "failed"  # the page can say "the mail did not go out"
    org.c.post(f"/api/interviews/{it['id']}/invite/send", headers=org.h())
    assert len(outbox()) == 2


def test_changing_a_session_updates_it_and_a_notice_goes_out_without_a_new_link(client, org, mail):
    pid = org.project()
    it = invite(org, pid, person(org, pid), send_email=True)
    with SessionLocal() as db:
        db.get(EmailOutbox, outbox()[0].id).status = "sent"
        db.commit()
    me = org.c.get("/api/me", headers=org.h()).json()["id"]
    r = org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"scheduled_at": "2026-10-29T07:30:00", "moderator_id": me, "duration_min": 60, "send_email": True})
    assert r.status_code == 200 and r.json()["duration_min"] == 60 and r.json()["moderator_id"] == me and r.json()["scheduled_at"].startswith("2026-10-29T07:30")
    assert r.json()["mail"]["status"] == "queued"
    notice = outbox()[-1]
    assert notice.kind == "invite_update" and "join.html" not in notice.body and "10/29" in notice.subject
    only = org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"duration_min": 30}).json()
    assert only["scheduled_at"].startswith("2026-10-29") and only["duration_min"] == 30  # fields that were not sent stay as they were


def test_a_failed_first_letter_is_sent_in_full_again_with_a_new_link(client, org, mail):
    pid = org.project()
    it = invite(org, pid, person(org, pid), send_email=True)
    with SessionLocal() as db:
        db.get(EmailOutbox, outbox()[0].id).status = "failed"
        db.commit()
        before = db.get(Interview, it["id"]).invite_hash
    r = org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"duration_min": 50, "send_email": True})
    assert r.json()["mail"]["status"] == "queued"
    last = outbox()[-1]
    assert last.kind == "invite" and "join.html?t=" in last.body  # nothing reached them yet, so it is the whole invitation, with a link that works
    with SessionLocal() as db:
        assert db.get(Interview, it["id"]).invite_hash != before


def test_the_kind_of_moderator_is_locked_after_the_participant_agrees(client, org):
    pid = org.project()
    it = invite(org, pid, person(org, pid))
    ok = org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"moderation": "human"})
    assert ok.status_code == 200
    with SessionLocal() as db:
        db.add(Consent(org_id=db.get(Interview, it["id"]).org_id, interview_id=it["id"], participant_id=it["participant"]["id"], version="v1", text_sha256="x" * 64,
                       recording=True, observers=True, cross_border=True, country="SG"))
        db.commit()
    locked = org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"moderation": "ai"})
    assert locked.status_code in (409, 422)
    assert org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"duration_min": 55}).status_code == 200  # the rest can still change


def test_only_the_team_can_change_a_session_and_an_ended_one_is_closed(client, org):
    pid = org.project()
    it = invite(org, pid, person(org, pid))
    assert org.c.put(f"/api/interviews/{it['id']}", headers=org.h("observer"), json={"duration_min": 50}).status_code == 403
    assert org.c.put(f"/api/interviews/{it['id']}", headers=org.h("moderator"), json={"duration_min": 50}).status_code == 403
    org.c.post(f"/api/interviews/{it['id']}/end", headers=org.h())
    assert org.c.put(f"/api/interviews/{it['id']}", headers=org.h(), json={"duration_min": 50}).status_code == 409


def test_the_team_can_correct_a_participants_email_and_the_list_carries_their_chosen_times(client, org):
    pid = org.project()
    who = person(org, pid, "甲", "wrong")
    r = org.c.put(f"/api/participants/{who['id']}/email", headers=org.h(), json={"email": "Right@Example.com "})
    assert r.status_code == 200 and r.json()["email"] == "right@example.com"
    assert org.c.put(f"/api/participants/{who['id']}/email", headers=org.h(), json={"email": "abc"}).status_code == 422
    assert org.c.put(f"/api/participants/{who['id']}/email", headers=org.h("observer"), json={"email": "a@b.co"}).status_code == 403
    with SessionLocal() as db:
        db.get(Participant, who["id"]).availability = ["2026-10-28T06:00:00", "2026-10-29T08:00:00"]
        db.commit()
    got = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()[0]
    assert got["availability"] == ["2026-10-28T06:00:00", "2026-10-29T08:00:00"]
