"""Limits against guessing (app/ratelimit.py): counted on failures, not traffic, so a group behind one office address is never locked out."""
import pytest

from app import ratelimit
from app.config import settings
from conftest import Org


@pytest.fixture(autouse=True)
def limits_on(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", True)
    ratelimit.reset()
    yield
    ratelimit.reset()


def _login(client, email="a@acme.com", password="password123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_repeated_wrong_passwords_pause_the_account_then_even_the_right_one_waits(client):
    Org(client)
    for _ in range(ratelimit.LOGIN_ACCOUNT[0]):
        assert _login(client, password="wrong-password").status_code == 401
    r = _login(client, password="wrong-password")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert _login(client).status_code == 429  # the right password is not tried while the account is paused


def test_a_good_login_clears_the_accounts_failures(client):
    Org(client)
    for _ in range(ratelimit.LOGIN_ACCOUNT[0] - 1):
        _login(client, password="wrong-password")
    assert _login(client).status_code == 200
    for _ in range(ratelimit.LOGIN_ACCOUNT[0] - 1):
        assert _login(client, password="wrong-password").status_code == 401  # the count started again


def test_one_address_guessing_many_accounts_is_stopped(client):
    for i in range(ratelimit.LOGIN_IP[0]):
        assert _login(client, email=f"user{i}@nowhere.com", password="x").status_code == 401
    assert _login(client, email="another@nowhere.com", password="x").status_code == 429


def test_honest_traffic_is_never_counted(client):
    Org(client)
    for _ in range(60):  # a lot of good logins and polling from one address
        assert _login(client).status_code == 200
        assert client.get("/api/health").status_code == 200


def test_probing_interview_links_is_stopped_but_a_real_link_keeps_working(client, org):
    itv = org.interview()
    real = f"/api/public/join/{itv['invite_token']}"
    for _ in range(ratelimit.LINK_PROBES[0] - 1):
        assert client.get("/api/public/join/not-a-real-token").status_code == 404
        assert client.get(real).status_code == 200  # honest polling in between is fine
    assert client.get("/api/public/join/not-a-real-token").status_code == 404  # the last probe the limit allows
    assert client.get("/api/public/join/not-a-real-token").status_code == 429
    assert client.get(real).status_code == 429  # while this address is probing, every link answers 429 for a minute


def test_probing_invitation_tokens_is_counted_too(client):
    for _ in range(ratelimit.LINK_PROBES[0]):
        client.get("/api/public/invite/not-a-real-token")
    assert client.get("/api/public/invite/not-a-real-token").status_code == 429


def test_signup_attempts_are_capped_per_address(client):
    codes = [client.post("/api/auth/register", json={"org_name": "O", "name": "N", "email": f"u{i}@x.com", "password": "password123"}).status_code
             for i in range(ratelimit.REGISTER[0] + 2)]
    assert codes[-1] == 429 and 429 not in codes[:ratelimit.REGISTER[0]]


def test_it_is_off_unless_switched_on(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", False)
    Org(client)
    for _ in range(ratelimit.LOGIN_IP[0] + 5):
        assert _login(client, password="wrong-password").status_code == 401


def test_the_counters_cannot_be_filled_with_random_keys():
    for i in range(ratelimit.MAX_KEYS + 500):
        ratelimit.record(f"probe:10.0.{i}", 60)
    assert len(ratelimit._hits) <= ratelimit.MAX_KEYS
