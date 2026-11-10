"""AI 后台 step 2: editable prompts with versions and a way back. The code's own prompt is the default and is never changed."""
import importlib
import inspect

import pytest

from app import ai_calls, ai_prompts, assistant_identity, outline_translate
from app.ai_prompts import AiPromptVersion, PromptError
from app.db import SessionLocal
from app.models import AuditLog


def org_id(org):
    with SessionLocal() as db:
        return db.execute(__import__("sqlalchemy").text("select id from orgs limit 1")).scalar()


def api(org, method, path, **kw):
    return getattr(org.c, method)(f"/api/ai-console{path}", headers=org.h("admin"), **kw)


def test_without_a_live_version_the_prompt_in_the_code_is_used(org):
    assert ai_prompts.get("recruit.insights", "CODE") == "CODE"
    with ai_calls.org_scope(org_id(org)):
        assert ai_prompts.get("recruit.insights", "CODE") == "CODE"
        assert api(org, "post", "/prompts/recruit.insights/versions", json={"text": "my own JSON prompt"}).status_code == 201
        assert ai_prompts.get("recruit.insights", "CODE") == "CODE"   # a draft changes nothing


def test_a_live_version_replaces_it_for_that_company_only_and_going_back_restores_it(org):
    base = ai_prompts.built_in("recruit.insights")
    text = base + "\nExtra rule: answer briefly."
    assert api(org, "post", "/prompts/recruit.insights/versions", json={"text": text, "note": "更短"}).json()["versions"][0]["version"] == 1
    assert api(org, "post", "/prompts/recruit.insights/live", json={"version": 1}).json()["live"] == 1
    with ai_calls.org_scope(org_id(org)):
        assert ai_prompts.get("recruit.insights", "CODE") == text
    with ai_calls.org_scope("another-company"):
        assert ai_prompts.get("recruit.insights", "CODE") == "CODE"
    view = api(org, "post", "/prompts/recruit.insights/live", json={"version": None}).json()
    assert view["live"] is None and [v["state"] for v in view["versions"]] == ["retired"]
    with ai_calls.org_scope(org_id(org)):
        assert ai_prompts.get("recruit.insights", "CODE") == "CODE"
    assert api(org, "post", "/prompts/recruit.insights/live", json={"version": 1}).json()["live"] == 1   # an older version can come back


def test_only_one_version_is_live_and_the_previous_one_is_kept(org):
    base = ai_prompts.built_in("plan.suggest")
    for extra in ("one", "two"):
        api(org, "post", "/prompts/plan.suggest/versions", json={"text": base + " " + extra})
    api(org, "post", "/prompts/plan.suggest/live", json={"version": 1})
    view = api(org, "post", "/prompts/plan.suggest/live", json={"version": 2}).json()
    assert {v["version"]: v["state"] for v in view["versions"]} == {1: "retired", 2: "live"}
    assert api(org, "post", "/prompts/plan.suggest/live", json={"version": 9}).status_code == 404


def test_the_assistant_identity_line_cannot_be_edited_away(org):
    text = ai_prompts.built_in("creation.chat")
    assert assistant_identity.PROMPT_LINE not in text
    api(org, "post", "/prompts/creation.chat/versions", json={"text": text})
    api(org, "post", "/prompts/creation.chat/live", json={"version": 1})
    with ai_calls.org_scope(org_id(org)):
        assert ai_prompts.get("creation.chat", "CODE").endswith(assistant_identity.PROMPT_LINE)
    assert assistant_identity.PROMPT_LINE in importlib.import_module("app.creation.specs").SYSTEM   # the built-in one has it too


def test_a_prompt_that_would_break_the_feature_is_refused(org):
    base = ai_prompts.built_in("creation.chat")
    for bad, why in (("   ", "空"), (base.replace("{outline_spec}", "x"), "{outline_spec}"), (base.replace("JSON", "text"), "JSON"), ("x" * (ai_prompts.MAX_PROMPT_CHARS + 1), "太长")):
        r = api(org, "post", "/prompts/creation.chat/versions", json={"text": bad})
        assert r.status_code == 422 and why in r.json()["detail"], (why, r.text)
    assert api(org, "get", "/prompts/creation.chat").json()["versions"] == []


def test_only_admins_edit_prompts_and_the_moderator_is_not_among_them(org):
    for role in ("researcher", "moderator", "observer"):
        assert org.c.get("/api/ai-console/prompts", headers=org.h(role)).status_code == 403
        assert org.c.post("/api/ai-console/prompts/recruit.insights/versions", headers=org.h(role), json={"text": "x JSON"}).status_code == 403
    assert api(org, "get", "/prompts/moderator.speak").status_code == 404
    assert api(org, "post", "/prompts/moderator.speak/live", json={"version": None}).status_code == 404
    keys = [f["key"] for f in api(org, "get", "/prompts").json()["features"]]
    assert keys == [k for k in keys if not k.startswith("moderator")] and set(keys) == set(ai_prompts.SLOTS)


def test_other_companies_do_not_see_each_others_versions(org, client):
    api(org, "post", "/prompts/recruit.insights/versions", json={"text": ai_prompts.built_in("recruit.insights")})
    other = client.post("/api/auth/register", json={"org_name": "Other", "name": "A", "email": "a@other.com", "password": "password123"}).json()["token"]
    r = client.get("/api/ai-console/prompts/recruit.insights", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 200 and r.json()["versions"] == []


def test_the_audit_trail_names_the_version_but_never_the_text(org):
    secret = ai_prompts.built_in("recruit.insights") + " SECRET-RULE-TEXT"
    api(org, "post", "/prompts/recruit.insights/versions", json={"text": secret})
    api(org, "post", "/prompts/recruit.insights/live", json={"version": 1})
    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.action.like("ai.prompt.%")).all()
    assert {r.action for r in rows} == {"ai.prompt.save", "ai.prompt.live"} and "SECRET-RULE-TEXT" not in str([r.meta for r in rows])


def test_a_live_prompt_is_what_the_feature_really_sends_and_the_call_log_names_its_version(org):
    base = ai_prompts.built_in("outline.translate")
    api(org, "post", "/prompts/outline.translate/versions", json={"text": base + " MARKER"})
    api(org, "post", "/prompts/outline.translate/live", json={"version": 1})
    seen = []

    class Chat:
        name = "fake"

        def complete(self, messages, temperature=0.0):
            seen.append(messages[0]["content"])
            return {"strings": ["你好"]}

    with ai_calls.org_scope(org_id(org)):
        try:
            outline_translate.translate(Chat(), ["Hello"], "zh")
        except Exception:  # noqa: BLE001 - only the prompt that was sent matters here
            pass
        with SessionLocal() as db:
            assert ai_prompts.live_label(db, org_id(org), "outline.translate") == "v1"
    assert seen and "MARKER" in seen[0]


def test_every_editable_prompt_exists_and_is_read_through_the_store():
    """A feature that still reads its constant directly would ignore what the admin saved."""
    for key, slot in ai_prompts.SLOTS.items():
        module = importlib.import_module(slot.module)
        assert isinstance(getattr(module, slot.attr), str) and ai_prompts.built_in(key)
        readers = inspect.getsource(module) + (inspect.getsource(importlib.import_module("app.creation.engine")) if key.startswith("creation.") else "")
        assert f'ai_prompts.get("{key}"' in readers or f'_call(chat, "{key}"' in readers, f"{slot.module} does not read {key} through ai_prompts"
