"""Studies done by participants alone, any time: outline drafting and publishing (frozen versions), personal links, the participant's text
conversation that survives leaving and a server restart, the transcript afterwards, closing, and who may touch what."""
import pytest

from app import ai_room
from app.db import SessionLocal
from app.models import AiSession, Interview, TranscriptSegment
from conftest import H
from test_project_members import second_researcher

ANSWERS = ["好的，我准备好了。", "我平时会先看成分，因为皮肤比较敏感，之前用过一款换季就过敏，所以现在很谨慎。", "信息主要来自小红书和朋友推荐，我比较相信真实用户的体验。",
           "价格大概两百以内吧，太贵的我不会买，一般也会等活动的时候入手。", "最麻烦的是不知道哪款真的适合自己，试错成本很高。",
           "理想的话就是有人能根据我的肤质直接推荐，而不是自己到处查。", "我不想继续了，谢谢。"]


def make_study(org, language="zh", **kw):
    pid = org.project()
    r = org.c.post(f"/api/projects/{pid}/studies", headers=org.h(), json={"title": "护肤选购", "goal": "了解消费者如何选购护肤品", "language": language, "duration_min": 20, **kw})
    assert r.status_code == 201, r.text
    return pid, r.json()


def published(org, language="zh"):
    pid, st = make_study(org, language)
    assert org.c.post(f"/api/studies/{st['id']}/draft", headers=org.h()).status_code == 200
    r = org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h())
    assert r.status_code == 200, r.text
    return pid, r.json()


def invited(org, language="zh"):
    pid, st = published(org, language)
    part = org.participant(pid)
    r = org.c.post(f"/api/studies/{st['id']}/invites", headers=org.h(), json={"participant_ids": [part["id"]]})
    assert r.status_code == 201 and len(r.json()["invites"]) == 1, r.text
    inv = r.json()["invites"][0]
    return pid, st, part, inv["invite_path"].split("t=")[1], inv["interview_id"]


def consent(org, token):
    r = org.c.post(f"/api/public/join/{token}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    assert r.status_code == 201, r.text


def say(org, token, text):
    return org.c.post(f"/api/public/join/{token}/chat", json={"text": text})


def chat(org, token, after=0):
    return org.c.get(f"/api/public/join/{token}/chat?after={after}").json()


# ---------- outline: draft, edit, publish ----------
def test_the_ai_drafts_an_outline_the_researcher_edits_and_publishes_as_a_frozen_version(org):
    pid, st = make_study(org)
    assert st["status"] == "draft" and st["version"] == 0 and st["outline_text"] == ""
    assert org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).status_code == 422   # nothing to publish yet
    d = org.c.post(f"/api/studies/{st['id']}/draft", headers=org.h()).json()
    assert "## 暖场" in d["outline_text"] and d["drafted_by"]
    edited = d["outline_text"].replace("现状与习惯", "日常习惯")
    e = org.c.put(f"/api/studies/{st['id']}/outline", headers=org.h(), json={"text": edited}).json()
    assert e["problem"] is None and "日常习惯" in e["outline_text"]
    p1 = org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).json()
    assert p1["status"] == "published" and p1["version"] == 1
    org.c.put(f"/api/studies/{st['id']}/outline", headers=org.h(), json={"text": edited.replace("日常习惯", "第三版")})
    assert org.c.get(f"/api/studies/{st['id']}", headers=org.h()).json()["version"] == 1   # editing never changes the published version
    assert org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).json()["version"] == 2


def test_a_bad_outline_is_kept_but_reported_and_cannot_be_published(org):
    pid, st = make_study(org)
    r = org.c.put(f"/api/studies/{st['id']}/outline", headers=org.h(), json={"text": "随便写点什么"})
    assert r.status_code == 200 and r.json()["problem"] and r.json()["outline_text"] == "随便写点什么"
    assert org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).status_code == 422


def test_an_outline_whose_own_question_breaks_a_red_line_cannot_be_published(org):
    pid, st = make_study(org, language="en")
    bad = ("goal: x\ntopic_for_participant: skincare\n## warmup\n- How did you make your choice back then, and what did you consider?\n"
           "## topic: Choosing\n- What did you do last time?\n")
    saved = org.c.put(f"/api/studies/{st['id']}/outline", headers=org.h(), json={"text": bad}).json()
    assert saved["problem"] and "安全检查" in saved["problem"]   # said while editing already
    r = org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h())
    assert r.status_code == 422 and "安全检查" in r.json()["detail"]


def test_the_study_language_and_length_override_what_the_text_says(org):
    pid, st = make_study(org, language="en")
    org.c.post(f"/api/studies/{st['id']}/draft", headers=org.h())
    pub = org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).json()
    assert pub["language"] == "en" and pub["duration_min"] == 20


def test_only_listed_durations_and_languages_can_be_chosen(org):
    pid = org.project()
    base = {"title": "t", "goal": "g", "language": "zh"}
    assert org.c.post(f"/api/projects/{pid}/studies", headers=org.h(), json={**base, "duration_min": 7}).status_code == 422
    assert org.c.post(f"/api/projects/{pid}/studies", headers=org.h(), json={**base, "language": "ms", "duration_min": 20}).status_code == 422


# ---------- invitations ----------
def test_only_a_published_study_can_invite_and_each_person_gets_one_personal_session(org):
    pid, st = make_study(org)
    part = org.participant(pid)
    assert org.c.post(f"/api/studies/{st['id']}/invites", headers=org.h(), json={"participant_ids": [part["id"]]}).status_code == 409
    org.c.post(f"/api/studies/{st['id']}/draft", headers=org.h())
    org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h())
    first = org.c.post(f"/api/studies/{st['id']}/invites", headers=org.h(), json={"participant_ids": [part["id"]]}).json()
    assert first["invites"][0]["invite_path"].startswith("/join.html?t=")
    again = org.c.post(f"/api/studies/{st['id']}/invites", headers=org.h(), json={"participant_ids": [part["id"]]}).json()
    assert again["invites"] == [] and "已经被邀请" in again["skipped"][0]["why"]
    detail = org.c.get(f"/api/studies/{st['id']}", headers=org.h()).json()
    assert detail["counts"]["invited"] == 1 and detail["sessions"][0]["state"] == "sent"


def test_a_participant_from_another_project_cannot_be_invited(org):
    pid, st = published(org)
    other = org.participant(org.project())
    assert org.c.post(f"/api/studies/{st['id']}/invites", headers=org.h(), json={"participant_ids": [other["id"]]}).status_code == 422


# ---------- the participant's side ----------
def test_the_participant_consents_with_the_text_only_wording_and_talks_to_the_ai_until_it_closes(org):
    pid, st, part, tok, iid = invited(org)
    info = org.c.get(f"/api/public/join/{tok}").json()
    assert info["async"] is True and "文字对话" in info["consent"]["text"] and "AI" in info["consent"]["text"]
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 403   # consent first
    consent(org, tok)
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 200
    first = chat(org, tok)
    assert first["started"] and first["messages"][0]["sender"] == "ai" and "AI" in first["messages"][0]["text"]   # the opening says it is an AI
    assert "文字对话" in first["messages"][0]["text"] and "录音录像" not in first["messages"][0]["text"]   # and says truthfully how it is kept: as text, no audio or video
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 200   # opening the link again carries on, it does not restart
    assert len(chat(org, tok)["messages"]) == len(first["messages"])
    for a in ANSWERS:
        if chat(org, tok)["finished"]:
            break
        assert say(org, tok, a).status_code == 201
    state = chat(org, tok)
    assert state["finished"] is True
    detail = org.c.get(f"/api/studies/{st['id']}", headers=org.h()).json()
    assert detail["sessions"][0]["state"] == "finished" and detail["counts"]["finished"] == 1
    segs = org.c.get(f"/api/interviews/{iid}/transcript", headers=org.h()).json()
    assert {s["speaker"] for s in segs} == {"ai_moderator", "participant"} and len(segs) >= 4
    with SessionLocal() as db:
        assert db.get(AiSession, iid).snapshot is None   # nothing is kept once it is over
        assert db.get(Interview, iid).status == "ended"
    assert say(org, tok, "还有一句").status_code == 409
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 409


def test_the_participant_can_leave_and_come_back_even_after_a_server_restart(org):
    pid, st, part, tok, iid = invited(org)
    consent(org, tok)
    org.c.post(f"/api/public/join/{tok}/async/start")
    say(org, tok, ANSWERS[0])
    say(org, tok, ANSWERS[1])
    before = chat(org, tok)["messages"]
    ai_room.forget_engines_for_tests()   # the server restarted: the engine in memory is gone
    r = say(org, tok, ANSWERS[2])
    assert r.status_code == 201
    after = chat(org, tok)
    assert after["control"] == "ai" and len(after["messages"]) == len(before) + 2   # the answer and the AI's reply: it carried on
    assert org.c.get(f"/api/studies/{st['id']}", headers=org.h()).json()["sessions"][0]["alert"] == "none"


def test_if_the_saved_state_is_damaged_the_session_goes_quiet_and_the_team_is_alerted_instead_of_starting_over(org):
    pid, st, part, tok, iid = invited(org)
    consent(org, tok)
    org.c.post(f"/api/public/join/{tok}/async/start")
    say(org, tok, ANSWERS[0])
    with SessionLocal() as db:
        s = db.get(AiSession, iid)
        s.snapshot = s.snapshot[:-9] + b"damaged!!"
        db.commit()
    ai_room.forget_engines_for_tests()
    before = len(chat(org, tok)["messages"])
    assert say(org, tok, ANSWERS[1]).status_code == 201
    assert len(chat(org, tok)["messages"]) == before + 1   # only the participant's own line was stored; the AI did not restart
    d = org.c.get(f"/api/studies/{st['id']}", headers=org.h()).json()["sessions"][0]
    assert d["alert"] == "red"


def test_a_session_link_cannot_be_used_for_another_kind_of_interview_and_wrong_links_fail(org):
    assert org.c.post("/api/public/join/not-a-token/async/start").status_code == 404
    itv = org.interview()
    assert org.c.post(f"/api/public/join/{itv['invite_token']}/async/start").status_code == 404   # an ordinary interview is not a study


def test_closing_the_study_ends_unfinished_sessions_and_keeps_what_was_said(org):
    pid, st, part, tok, iid = invited(org)
    consent(org, tok)
    org.c.post(f"/api/public/join/{tok}/async/start")
    say(org, tok, ANSWERS[0])
    r = org.c.post(f"/api/studies/{st['id']}/close", headers=org.h())
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["sessions"][0]["state"] == "ended_early"
    assert len(org.c.get(f"/api/interviews/{iid}/transcript", headers=org.h()).json()) >= 2
    assert say(org, tok, "再说一句").status_code == 409
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 409
    assert org.c.post(f"/api/studies/{st['id']}/close", headers=org.h()).status_code == 409
    assert org.c.post(f"/api/studies/{st['id']}/publish", headers=org.h()).status_code == 409


def test_a_link_before_the_study_is_closed_but_never_opened_stops_working_too(org):
    pid, st, part, tok, iid = invited(org)
    consent(org, tok)
    org.c.post(f"/api/studies/{st['id']}/close", headers=org.h())
    assert org.c.post(f"/api/public/join/{tok}/async/start").status_code == 409


# ---------- who may do what ----------
def test_other_researchers_companies_and_roles_cannot_reach_a_study(org, client):
    pid, st = published(org)
    other = second_researcher(org)
    sid = st["id"]
    for method, url, body in [("get", f"/api/studies/{sid}", None), ("post", f"/api/studies/{sid}/draft", None), ("put", f"/api/studies/{sid}/outline", {"text": "x"}),
                              ("post", f"/api/studies/{sid}/publish", None), ("post", f"/api/studies/{sid}/close", None),
                              ("post", f"/api/studies/{sid}/invites", {"participant_ids": ["x"]}), ("get", f"/api/projects/{pid}/studies", None)]:
        r = getattr(org.c, method)(url, headers=other["h"], **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, url, r.status_code)
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/studies/{sid}", headers=org.h(role)).status_code == 403
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert foreign.c.get(f"/api/studies/{sid}", headers=foreign.h("admin")).status_code == 404
    assert org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]}).status_code == 201
    assert org.c.get(f"/api/studies/{sid}", headers=other["h"]).status_code == 200   # once added to the project, they can


def test_staff_can_watch_a_study_session_in_the_usual_room_views(org):
    pid, st, part, tok, iid = invited(org)
    consent(org, tok)
    org.c.post(f"/api/public/join/{tok}/async/start")
    say(org, tok, ANSWERS[0])
    staff = org.c.get(f"/api/interviews/{iid}/ai/state", headers=org.h()).json()
    assert staff["started"] and any(m["sender"] == "participant" for m in staff["messages"])
    itv = org.c.get(f"/api/interviews/{iid}", headers=org.h()).json()
    assert itv["kind"] == "async" and itv["study_id"] == st["id"]


def test_drafting_with_a_real_style_model_fills_the_prompt_and_accepts_its_json(org):
    """The offline draft skips the prompt; this runs the model path with a fake model (a formatting slip once made every real draft fail)."""
    from app import study_draft
    from app.moderator.providers import MockModeratorChat
    chat = MockModeratorChat(lambda messages: study_draft.mock_draft("g", "zh", 20))
    guide = study_draft.draft_guide(chat, "了解如何选购护肤品", "zh", 20)
    system = chat.calls[0][0]["content"]
    assert "zh" in system and "{language}" not in system and "{minutes}" not in system and "20 minutes" in system
    assert guide.language == "zh" and len(guide.topics) == 3
    bad = MockModeratorChat([{"nonsense": True}])
    with pytest.raises(study_draft.DraftError):
        study_draft.draft_guide(bad, "目标", "zh", 20)
    assert len(bad.calls) == study_draft.MAX_ATTEMPTS   # asked again once, then gave up
