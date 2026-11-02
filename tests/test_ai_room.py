"""Sprint 5: AI moderation in the room (text chat): start, the participant's chat, take over / hand back, whispers, alerts,
observers, tenant isolation, and the transcript that the chat becomes."""
import pytest

from app import ai_room
from app.db import SessionLocal
from app.models import AiSession, ChatMessage, Project, TranscriptSegment
from conftest import Org, H


def _walk_into_the_room(org: Org, **kw) -> dict:
    """An AI interview whose participant has consented (AI box ticked), been let in, and entered; the interview is live."""
    itv = org.interview(moderation="ai", **kw)
    t = itv["invite_token"]
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": True, "speaker": True})
    r = org.c.post(f"/api/public/join/{t}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    assert r.status_code == 201, r.text
    org.c.post(f"/api/interviews/{itv['id']}/start", headers=org.h("moderator"))
    assert org.c.post(f"/api/public/join/{t}/enter").status_code == 200
    return itv


def _start(org: Org, itv: dict, role: str = "moderator", **body):
    return org.c.post(f"/api/interviews/{itv['id']}/ai/start", headers=org.h(role), json=body)


def _say(org: Org, itv: dict, text: str):
    return org.c.post(f"/api/public/join/{itv['invite_token']}/chat", json={"text": text})


def _staff(org: Org, itv: dict, after: int = 0, role: str = "moderator") -> dict:
    return org.c.get(f"/api/interviews/{itv['id']}/ai/state?after={after}", headers=org.h(role)).json()


def _participant(org: Org, itv: dict, after: int = 0) -> dict:
    return org.c.get(f"/api/public/join/{itv['invite_token']}/chat?after={after}").json()


@pytest.fixture
def room(org):
    itv = _walk_into_the_room(org)
    assert _start(org, itv).status_code == 200
    return org, itv


# ---------- starting ----------
def test_nothing_runs_before_staff_start_the_ai(org):
    itv = _walk_into_the_room(org)
    assert _participant(org, itv) == {"started": False}
    assert _say(org, itv, "hello").status_code == 409  # not started: the participant cannot talk to nobody


def test_starting_puts_the_ai_opening_in_the_chat_and_it_says_it_is_an_ai(room):
    org, itv = room
    msgs = _participant(org, itv)["messages"]
    assert len(msgs) == 1 and msgs[0]["sender"] == "ai"
    assert "AI" in msgs[0]["text"] and "not a person" in msgs[0]["text"].lower().replace("not a real person", "not a person")


def test_only_staff_and_moderators_start_it_and_only_once_and_only_for_ai_interviews(org):
    itv = _walk_into_the_room(org)
    assert _start(org, itv, "observer").status_code == 403
    assert _start(org, itv, "researcher").status_code == 200
    assert _start(org, itv).status_code == 409
    human = org.interview()
    org.c.post(f"/api/interviews/{human['id']}/start", headers=org.h("moderator"))
    assert _start(org, human).status_code == 409  # a human-moderated interview has no AI to start


def test_it_cannot_start_before_the_interview_is_live(org):
    itv = org.interview(moderation="ai")
    r = _start(org, itv)
    assert r.status_code == 409 and "开始访谈" in r.json()["detail"]


def test_a_guide_that_is_not_valid_is_refused_with_a_reason(org):
    itv = _walk_into_the_room(org)
    r = _start(org, itv, guide={"topics": []})
    assert r.status_code == 422 and "提纲" in r.json()["detail"]


def test_start_uses_the_projects_own_outline_and_the_demo_only_when_there_is_none(org):
    itv = _walk_into_the_room(org)
    own = f"goal: learn how people buy coffee\nlanguage: {itv['language']}\nminutes: 45\n## warmup\n- ownfirstquestion?\n## topic: buying\n- what did you last buy?\n"
    with SessionLocal() as db:
        db.get(Project, itv["project_id"]).outline_text = own
        db.commit()
    assert _start(org, itv).status_code == 200
    with SessionLocal() as db:
        sess = db.query(AiSession).filter_by(interview_id=itv["id"]).one()
        assert "ownfirstquestion" in sess.guide_json


def test_a_project_outline_that_breaks_a_red_line_is_refused_not_replaced_by_the_demo(org):
    itv = _walk_into_the_room(org)
    bad = f"goal: learn how people buy coffee\nlanguage: {itv['language']}\nminutes: 45\n## warmup\n- ownfirstquestion?\n## topic: buying\n- what did you last buy and why?\n"
    with SessionLocal() as db:
        db.get(Project, itv["project_id"]).outline_text = bad
        db.commit()
    r = _start(org, itv)
    assert r.status_code == 422 and "提纲" in r.json()["detail"]


# ---------- the conversation ----------
def test_the_participant_types_and_the_ai_answers(room):
    org, itv = room
    assert _say(org, itv, "Yes, I'm ready.").status_code == 201
    assert _say(org, itv, "I usually order lunch on Grab because it is quick.").status_code == 201
    msgs = _participant(org, itv)["messages"]
    senders = [m["sender"] for m in msgs]
    assert senders[:2] == ["ai", "participant"] and senders[-1] == "ai" and len(msgs) == 5
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)
    assert _participant(org, itv, after=msgs[-2]["seq"])["messages"] == [msgs[-1]]  # polling returns only what is new


def test_the_participant_sees_nothing_internal_and_staff_see_the_ai_notes(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    _say(org, itv, "I use Grab mostly.")
    mine = _participant(org, itv)
    assert set(mine) == {"started", "control", "finished", "messages"}  # no alert, no engine state
    for m in mine["messages"]:
        assert set(m) == {"seq", "sender", "text", "at_ms"}
    ai_lines = [m for m in _staff(org, itv)["messages"] if m["sender"] == "ai"]
    assert any(m["note"] and m["operator"] for m in ai_lines)  # why the AI asked (research purpose, operator) is for the team


def test_blank_and_huge_messages_are_refused(room):
    org, itv = room
    assert _say(org, itv, "   ").status_code == 422
    assert _say(org, itv, "x" * 2001).status_code == 422


def test_a_participant_who_is_not_in_the_room_cannot_chat(org):
    itv = org.interview(moderation="ai")
    assert _say(org, itv, "hi").status_code == 409
    assert org.c.get("/api/public/join/not-a-token/chat").status_code == 404


# ---------- take over / hand back ----------
def test_while_a_human_has_control_the_ai_stays_silent_and_remembers_the_conversation(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher")).status_code == 200
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher")).status_code == 409
    before = len(_participant(org, itv)["messages"])
    _say(org, itv, "I am not sure how to explain it.")
    msgs = _participant(org, itv)["messages"]
    assert len(msgs) == before + 1 and msgs[-1]["sender"] == "participant"  # the AI did not answer (red line H11)
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/say", headers=org.h("researcher"), json={"text": "No rush, take your time."}).status_code == 200
    assert _participant(org, itv)["messages"][-1]["sender"] == "moderator"
    assert _participant(org, itv)["control"] == "human"
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher")).status_code == 200
    _say(org, itv, "Mostly price, I think.")
    assert _participant(org, itv)["messages"][-1]["sender"] == "ai"  # the AI speaks again after the hand back


def test_the_human_can_only_type_while_in_control(room):
    org, itv = room
    r = org.c.post(f"/api/interviews/{itv['id']}/ai/say", headers=org.h("researcher"), json={"text": "hello"})
    assert r.status_code == 409 and "接管" in r.json()["detail"]
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher")).status_code == 409  # the AI is already in control


# ---------- whispers ----------
def test_a_whisper_reaches_the_ai_but_never_the_participant_or_the_observer(room):
    org, itv = room
    secret = "Ask about the delivery fees next"
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/whisper", headers=org.h("researcher"), json={"text": secret}).status_code == 200
    _say(org, itv, "Yes, ready.")
    for view in (_participant(org, itv)["messages"], _staff(org, itv, role="observer")["messages"]):
        assert secret not in str(view)
    assert any(m["sender"] == "whisper" and m["text"] == secret for m in _staff(org, itv)["messages"])


def _grants(token: str) -> dict:
    import jwt
    return jwt.decode(token, options={"verify_signature": False})["video"]


def test_staff_only_watch_an_ai_interview_until_a_researcher_takes_over(room):
    org, itv = room
    for who in ("researcher", "admin", "moderator", "observer"):
        r = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h(who), json={}).json()
        g = _grants(r["token"])
        assert not g["canPublish"] and g["hidden"] and r["view_only"], who   # no voice, camera or screen; the participant never sees them
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("moderator")).status_code == 403   # a moderator account cannot take over
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher")).status_code == 200
    r = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("researcher"), json={}).json()
    assert _grants(r["token"])["canPublish"] and not r["view_only"] and r["can_takeover"]   # now text, voice, video and screen are allowed
    assert _grants(org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("observer"), json={}).json()["token"])["canPublish"] is False
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher")).status_code == 200
    r = org.c.post(f"/api/interviews/{itv['id']}/token", headers=org.h("researcher"), json={}).json()
    assert r["view_only"] and not _grants(r["token"])["canPublish"]   # handed back: view-only again


def test_the_client_observer_may_whisper_but_not_take_over_or_read_the_teams_notes(room):
    org, itv = room
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/whisper", headers=org.h("observer"), json={"text": "ask about price"}).status_code == 200
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("observer")).status_code == 403
    st = org.c.get(f"/api/interviews/{itv['id']}/ai/state", headers=org.h("observer")).json()
    assert all(m["sender"] != "whisper" for m in st["messages"])   # observers read the conversation only


def test_the_rooms_panels_read_the_ai_chat_when_there_are_no_captions(room):
    org, itv = room
    for i in range(4):
        _say(org, itv, f"我平时在网上买护肤品，因为方便，也因为有评价可以参考，价格也比商场便宜很多（第 {i} 次）。")
    d = org.c.get(f"/api/interviews/{itv['id']}/assist", headers=org.h("researcher")).json()
    assert d["talk"]["moderator_pct"] is not None and d["talk"]["participant_pct"] is not None   # who said how much comes from the conversation
    assert org.c.get(f"/api/interviews/{itv['id']}/assist", headers=org.h("observer")).status_code == 403


def test_handing_back_takes_staff_publishing_rights_away_in_the_video_room(monkeypatch):
    from app import livekit
    calls = []

    class R:
        def json(self): return {"participants": [{"identity": "staff:u1"}, {"identity": "participant:p1"}, {"identity": "obs:u2"}]}

    monkeypatch.setattr(livekit.settings, "livekit_url", "ws://127.0.0.1:7880")
    monkeypatch.setattr("httpx.post", lambda url, json=None, headers=None, timeout=None: (calls.append((url.rsplit("/", 1)[-1], json)), R())[1])
    livekit.revoke_publishing("room1")
    updates = [j for n, j in calls if n == "UpdateParticipant"]
    assert [u["identity"] for u in updates] == ["staff:u1"] and updates[0]["permission"]["can_publish"] is False   # only the staff member, never the participant
    calls.clear(); monkeypatch.setattr(livekit.settings, "livekit_url", "")
    livekit.revoke_publishing("room1"); assert calls == []   # no video server: nothing to do


def test_a_whisper_has_a_limit(room):
    org, itv = room
    url = f"/api/interviews/{itv['id']}/ai/whisper"
    r = org.c.post(url, headers=org.h("moderator"), json={"text": "y" * 501})
    assert r.status_code == 422
    assert org.c.post(url, headers=org.h("moderator"), json={"text": "  "}).status_code == 422


# ---------- alerts ----------
def test_a_crisis_gets_a_safe_reply_and_a_red_alert_only_staff_can_see(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    _say(org, itv, "I feel like ending my life.")
    msgs = _participant(org, itv)["messages"]
    assert "995" in msgs[-1]["text"] or "1767" in msgs[-1]["text"]  # the fixed safety statement, not a model's improvisation
    staff = _staff(org, itv)
    assert staff["alert"] == "red" and staff["alert_reason"]
    assert "alert" not in _participant(org, itv) and "alert" not in _staff(org, itv, role="observer")
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/ack-alert", headers=org.h("researcher")).status_code == 200
    assert _staff(org, itv)["alert"] == "none"


def test_after_the_ai_has_said_goodbye_the_chat_is_closed(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    _say(org, itv, "I want to stop now.")
    assert _participant(org, itv)["finished"] is True
    assert _say(org, itv, "wait").status_code == 409
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher")).status_code in (409,)


# ---------- observers and other organisations ----------
def test_observers_read_the_conversation_and_nothing_else(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    obs = _staff(org, itv, role="observer")
    assert obs["messages"] and "alert" not in obs and "engine_alive" not in obs
    assert all(set(m) == {"seq", "sender", "text", "at_ms"} for m in obs["messages"])
    for action in ("takeover", "handback", "ack-alert"):
        assert org.c.post(f"/api/interviews/{itv['id']}/ai/{action}", headers=org.h("observer")).status_code == 403
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/say", headers=org.h("observer"), json={"text": "hi"}).status_code == 403


def test_another_organisation_cannot_see_or_control_it(client, room):
    org, itv = room
    other = Org(client, name="Other", email="o@other.com")
    for method, path in (("get", "state"), ("post", "takeover"), ("post", "ack-alert")):
        r = getattr(client, method)(f"/api/interviews/{itv['id']}/ai/{path}", headers=other.h("admin"))
        assert r.status_code == 404


# ---------- the transcript ----------
def test_ending_turns_the_chat_into_a_transcript_with_the_ai_labelled_and_no_whispers(room):
    org, itv = room
    org.c.post(f"/api/interviews/{itv['id']}/ai/whisper", headers=org.h("moderator"), json={"text": "private note"})
    _say(org, itv, "Yes, ready.")
    _say(org, itv, "I order from Grab because it is quick.")
    org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher"))
    org.c.post(f"/api/interviews/{itv['id']}/ai/say", headers=org.h("researcher"), json={"text": "Thanks, let me add something."})
    assert org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator")).status_code == 200
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("researcher")).json()
    assert {s["speaker"] for s in segs} == {"ai_moderator", "participant", "moderator"}
    assert "I order from Grab because it is quick." in [s["text_orig"] for s in segs]  # verbatim: evidence quotes can point at it
    assert "private note" not in str(segs)
    assert [s["idx"] for s in segs] == list(range(len(segs)))
    assert all(s["end_ms"] >= s["start_ms"] for s in segs)


def test_a_quote_from_an_ai_interview_can_back_an_insight(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    _say(org, itv, "I order from Grab because it is quick.")
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    segs = org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h("researcher")).json()
    seg = next(s for s in segs if s["speaker"] == "participant" and "Grab" in s["text_orig"])
    r = org.c.post(f"/api/interviews/{itv['id']}/insights", headers=org.h("researcher"),
                   json={"theme": "Speed", "statement": "Speed decides the app.", "evidence": [{"segment_id": seg["id"], "quote": "I order from Grab because it is quick."}]})
    assert r.status_code == 201, r.text


def test_retention_deletes_the_chat(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    org.c.post(f"/api/interviews/{itv['id']}/end", headers=org.h("moderator"))
    from app.models import Interview
    from app.retention import purge_interview

    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.query(ChatMessage).filter_by(interview_id=itv["id"]).count() == 0
        assert db.query(TranscriptSegment).filter_by(interview_id=itv["id"]).count() == 0


# ---------- a server restart ----------
def test_after_a_restart_the_ai_does_not_start_over_the_human_takes_the_room(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    ai_room.forget_engines_for_tests()  # the process restarted: the engine and its memory are gone
    before = len(_participant(org, itv)["messages"])
    assert _say(org, itv, "Hello? Are you there?").status_code == 201
    mine = _participant(org, itv)
    assert mine["control"] == "human" and len(mine["messages"]) == before + 1  # the participant's line was kept, nobody invented an answer
    staff = _staff(org, itv)
    assert staff["alert"] == "red" and "重启" in staff["alert_reason"] and staff["engine_alive"] is False
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher")).status_code == 409  # it cannot resume without its memory
    assert org.c.post(f"/api/interviews/{itv['id']}/ai/say", headers=org.h("researcher"), json={"text": "Sorry, I am here."}).status_code == 200


def test_the_ai_actions_are_in_the_audit_log(room):
    org, itv = room
    org.c.post(f"/api/interviews/{itv['id']}/ai/takeover", headers=org.h("researcher"))
    org.c.post(f"/api/interviews/{itv['id']}/ai/handback", headers=org.h("researcher"))
    actions = [a["action"] for a in org.c.get("/api/audit?limit=100", headers=H(org.admin)).json()]
    assert {"ai.start", "ai.takeover", "ai.handback"} <= set(actions)


def test_every_line_of_the_ai_records_which_prompt_version_and_model_said_it_for_the_team_only(room):
    org, itv = room
    _say(org, itv, "Yes, ready.")
    staff = _staff(org, itv)
    ai = [m for m in staff["messages"] if m["sender"] == "ai"]
    assert len(ai) >= 2 and all(m["prompt_version"].startswith("v0") and m["model"] for m in ai)
    assert all(not m.get("prompt_version") for m in staff["messages"] if m["sender"] != "ai")
    mine = _participant(org, itv)
    assert "prompt_version" not in str(mine) and "model" not in " ".join(m for m in str(mine).split() if "model" in m)
    assert "prompt_version" not in str(_staff(org, itv, role="observer"))
