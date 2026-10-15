"""Team management: one-time invite / reset links, role and active changes, last-admin protection, tenant isolation."""
from datetime import timedelta

from app.db import SessionLocal
from app.models import UserInvite, utcnow
from conftest import H, Org


def _token(path: str) -> str:
    return path.split("t=", 1)[1]


def test_invite_join_flow(client):
    org = Org(client)
    r = client.post("/api/users/invites", headers=org.h("admin"), json={"email": "New@Acme.com", "role": "researcher"})
    assert r.status_code == 201, r.text
    t = _token(r.json()["invite_path"])
    info = client.get(f"/api/public/invite/{t}").json()
    assert info["email"] == "new@acme.com" and info["kind"] == "join" and info["role"] == "researcher"
    assert client.post(f"/api/public/invite/{t}/accept", json={"name": "N", "password": "short"}).status_code == 422
    assert client.post(f"/api/public/invite/{t}/accept", json={"name": " ", "password": "password123"}).status_code == 422
    ok = client.post(f"/api/public/invite/{t}/accept", json={"name": "Nina", "password": "password123"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "researcher"
    assert client.get("/api/me", headers=H(ok.json()["token"])).json()["email"] == "new@acme.com"
    assert client.get(f"/api/public/invite/{t}").status_code == 404  # one use only
    assert client.post("/api/auth/login", json={"email": "new@acme.com", "password": "password123"}).status_code == 200


def test_invite_permissions_and_duplicates(client):
    org = Org(client)
    body = {"email": "x@acme.com", "role": "observer"}
    for role in ("researcher", "moderator", "observer"):
        assert client.post("/api/users/invites", headers=org.h(role), json=body).status_code == 403
        assert client.get("/api/users/invites", headers=org.h(role)).status_code == 403
    assert client.post("/api/users/invites", headers=org.h("admin"), json={"email": "researcher@acme.com", "role": "observer"}).status_code == 409
    assert client.post("/api/users/invites", headers=org.h("admin"), json={"email": "x@acme.com", "role": "boss"}).status_code == 422
    assert client.post("/api/users/invites", headers=org.h("admin"), json={"email": "not-an-email", "role": "observer"}).status_code == 422


def test_invite_list_revoke_expire_and_no_token_leak(client):
    org = Org(client)
    r = client.post("/api/users/invites", headers=org.h("admin"), json={"email": "a1@acme.com", "role": "observer"}).json()
    t = _token(r["invite_path"])
    listed = client.get("/api/users/invites", headers=org.h("admin")).json()
    assert [i["email"] for i in listed] == ["a1@acme.com"] and t not in str(listed) and "invite_path" not in listed[0]
    assert client.delete(f"/api/users/invites/{r['id']}", headers=org.h("admin")).status_code == 204
    assert client.get(f"/api/public/invite/{t}").status_code == 404
    r2 = client.post("/api/users/invites", headers=org.h("admin"), json={"email": "a2@acme.com", "role": "observer"}).json()
    with SessionLocal() as db:
        db.get(UserInvite, r2["id"]).expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/api/public/invite/{_token(r2['invite_path'])}").status_code == 404
    assert client.get("/api/users/invites", headers=org.h("admin")).json() == []


def test_invites_are_per_company(client):
    a, b = Org(client, "A", "a@a.com"), Org(client, "B", "b@b.com")
    r = client.post("/api/users/invites", headers=a.h("admin"), json={"email": "z@a.com", "role": "observer"}).json()
    assert client.get("/api/users/invites", headers=b.h("admin")).json() == []
    assert client.delete(f"/api/users/invites/{r['id']}", headers=b.h("admin")).status_code == 404
    assert client.post(f"/api/users/{a.users['observer']}/reset-link", headers=b.h("admin")).status_code == 404
    assert client.patch(f"/api/users/{a.users['observer']}", headers=b.h("admin"), json={"active": False}).status_code == 404


def test_reset_link_changes_password(client):
    org = Org(client)
    r = client.post(f"/api/users/{org.users['moderator']}/reset-link", headers=org.h("admin"))
    assert r.status_code == 201
    t = _token(r.json()["invite_path"])
    assert client.get(f"/api/public/invite/{t}").json()["kind"] == "reset"
    assert client.post(f"/api/public/invite/{t}/accept", json={"password": "brandnew-pass"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "moderator@acme.com", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "moderator@acme.com", "password": "brandnew-pass"}).status_code == 200
    assert client.post(f"/api/public/invite/{t}/accept", json={"password": "another-pass1"}).status_code == 404


def test_role_and_active_changes(client):
    org = Org(client)
    uid = org.users["researcher"]
    assert client.patch(f"/api/users/{uid}", headers=org.h("researcher"), json={"role": "admin"}).status_code == 403  # no self-promotion
    assert client.patch(f"/api/users/{uid}", headers=org.h("admin"), json={"role": "boss"}).status_code == 422
    assert client.patch(f"/api/users/{uid}", headers=org.h("admin"), json={"role": "moderator"}).json()["role"] == "moderator"
    assert client.patch(f"/api/users/{uid}", headers=org.h("admin"), json={"active": False}).json()["active"] is False
    assert client.get("/api/me", headers=org.h("researcher")).status_code == 401  # switched off: the old session stops working
    assert client.post("/api/auth/login", json={"email": "researcher@acme.com", "password": "password123"}).status_code == 401
    assert client.patch(f"/api/users/{uid}", headers=org.h("admin"), json={"active": True}).json()["active"] is True
    assert client.post("/api/auth/login", json={"email": "researcher@acme.com", "password": "password123"}).status_code == 200


def test_cannot_lock_the_company_out(client):
    org = Org(client)
    me = client.get("/api/me", headers=org.h("admin")).json()["id"]
    assert client.patch(f"/api/users/{me}", headers=org.h("admin"), json={"active": False}).status_code == 409
    assert client.patch(f"/api/users/{me}", headers=org.h("admin"), json={"role": "observer"}).status_code == 409
    # a second admin may be demoted, but then the remaining one can never be removed by a different admin either
    r = client.post("/api/users", headers=org.h("admin"), json={"name": "B", "email": "b2@acme.com", "password": "password123", "role": "admin"}).json()
    login = client.post("/api/auth/login", json={"email": "b2@acme.com", "password": "password123"}).json()["token"]
    assert client.patch(f"/api/users/{me}", headers=H(login), json={"active": False}).status_code == 200
    assert client.patch(f"/api/users/{r['id']}", headers=org.h("researcher"), json={"active": False}).status_code == 403
