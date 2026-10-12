import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["MEDIA_DIR"] = f"{_tmp}/media"
os.environ["DISABLE_WORKER"] = "1"
for _k, _v in (("LIVEKIT_URL", ""), ("ASR_PROVIDER", "mock"), ("LLM_PROVIDER", "mock"), ("ASR_API_KEY", ""), ("LLM_API_KEY", ""),
               ("MODERATOR_PROVIDER", "mock"), ("MODERATOR_API_KEY", ""), ("MODERATOR_BASE_URL", ""), ("MODERATOR_VENDOR", "")):
    os.environ[_k] = _v  # tests never use a developer's .env (real keys, local video server)

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings.llm_provider = "mock"
    settings.moderator_provider, settings.moderator_vendor = "mock", ""
    yield


@pytest.fixture
def client():
    return TestClient(app)


def H(token):
    return {"Authorization": f"Bearer {token}"}


class Org:
    """Helper that builds an org with one user per role and one project/participant/interview."""

    def __init__(self, client, name="Acme", email="a@acme.com"):
        self.c = client
        r = client.post("/api/auth/register", json={"org_name": name, "name": "Admin", "email": email, "password": "password123"})
        assert r.status_code == 200, r.text
        self.admin = r.json()["token"]
        self.users = {}
        for role in ("researcher", "moderator", "observer"):
            r = client.post("/api/users", headers=H(self.admin), json={"name": role, "email": f"{role}@{email.split('@')[1]}", "password": "password123", "role": role})
            assert r.status_code == 201, r.text
            self.users[role] = r.json()["id"]
        self.tokens = {"admin": self.admin}
        for role in ("researcher", "moderator", "observer"):
            r = client.post("/api/auth/login", json={"email": f"{role}@{email.split('@')[1]}", "password": "password123"})
            self.tokens[role] = r.json()["token"]

    def h(self, role="researcher"):
        return H(self.tokens[role])

    def project(self, **kw):
        r = self.c.post("/api/projects", headers=self.h(), json={"name": "P1", "market": "SG", "language": "en", **kw})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def participant(self, pid, country="SG"):
        r = self.c.post(f"/api/projects/{pid}/participants", headers=self.h(), json={"name": "Wei Ling", "country": country, "language": "en"})
        assert r.status_code == 201, r.text
        return r.json()

    def interview(self, pid=None, observers=True, moderator=True, **kw):
        pid = pid or self.project()
        part = self.participant(pid)
        r = self.c.post(f"/api/projects/{pid}/interviews", headers=self.h(), json={
            "participant_id": part["id"], "title": "深访 1",
            "moderator_id": self.users["moderator"] if moderator else None,
            "observer_ids": [self.users["observer"]] if observers else [], **kw})
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def org(client):
    return Org(client)


def decode_unverified(token):
    return jwt.decode(token, options={"verify_signature": False})
