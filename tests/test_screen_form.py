"""The recruiting questionnaire as a system: logic (carry on / jump / screen out / finish), testing while editing, publishing as a link and QR code, the public page, and the list."""
import pytest

from app.recruit_form import AnswerError, Question, next_step


def url(pid, tail=""):
    return f"/api/projects/{pid}/recruiting/form{tail}"


def qs():
    """age (number: under 18 screens out) -> use (single: "never" screens out, "daily" jumps past the frequency question) -> freq -> chan (multi, tags) -> why (text, optional)."""
    return [
        {"id": "age", "type": "number", "text": "您的年龄？", "rules": [{"op": "lt", "value": 18, "goto": "end_out"}]},
        {"id": "use", "type": "single", "text": "您用过这类产品吗？", "options": [{"id": "never", "text": "从没用过", "goto": "end_out"}, {"id": "daily", "text": "每天用", "goto": "chan"}, {"id": "some", "text": "偶尔用"}]},
        {"id": "freq", "type": "single", "text": "多久用一次？", "options": [{"id": "w", "text": "每周"}, {"id": "m", "text": "每月", "goto": "end_ok"}]},
        {"id": "chan", "type": "multi", "text": "在哪里买？", "options": [{"id": "on", "text": "线上", "tag": "线上买家"}, {"id": "off", "text": "线下", "tag": "线下买家"}, {"id": "none", "text": "都不买", "goto": "end_out"}]},
        {"id": "why", "type": "text", "text": "最看重什么？", "required": False},
    ]


def walk(answers):
    return next_step([Question.model_validate(q) for q in qs()], answers)


def test_the_logic_carries_on_jumps_ends_and_screens_out():
    first = walk({})
    assert first.question["id"] == "age" and first.question["index"] == 1 and "goto" not in str(first.question)  # candidates never see the logic
    assert walk({"age": 17}).result == "out" and walk({"age": 17}).ended_at == "age"  # a number rule screens out
    assert walk({"age": 30}).question["id"] == "use"
    assert walk({"age": 30, "use": "never"}).result == "out"  # an option that screens out
    assert walk({"age": 30, "use": "daily"}).question["id"] == "chan"  # a jump skips the frequency question
    assert walk({"age": 30, "use": "some"}).question["id"] == "freq"
    assert walk({"age": 30, "use": "some", "freq": "m"}).result == "qualified"  # an option that finishes as qualified
    assert walk({"age": 30, "use": "some", "freq": "w"}).question["id"] == "chan"
    assert walk({"age": 30, "use": "daily", "chan": ["on", "none"]}).result == "out"  # one ending option among several chosen
    assert walk({"age": 30, "use": "daily", "chan": ["on", "off"]}).question["id"] == "why"  # the optional last question still comes
    final = walk({"age": 30, "use": "daily", "chan": ["on", "off"], "why": ""})
    assert final.result == "qualified" and final.tags == ["线上买家", "线下买家"] and ("您的年龄？", "30") in final.items and ("在哪里买？", "线上、线下") in final.items


def test_bad_answers_are_refused_and_answers_off_the_path_are_ignored():
    for bad in ({"age": "abc"}, {"age": None}, {"age": 30, "use": "zzz"}, {"age": 30, "use": ["never"]}, {"age": 30, "use": "daily", "chan": []}, {"age": 30, "use": "daily", "chan": ["on", "on"]}, {"age": float("inf")}):
        with pytest.raises(AnswerError):
            walk(bad)
    assert walk({"age": 30, "use": "daily", "freq": "garbage"}).question["id"] == "chan"  # freq is skipped by the jump, so its answer does not count


def test_jumps_can_only_go_forward_and_to_real_questions(org):
    pid = org.project()
    backwards = qs()
    backwards[3]["options"][0]["goto"] = "age"
    assert org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": backwards}).status_code == 422
    nowhere = qs()
    nowhere[1]["options"][1]["goto"] = "nowhere"
    assert org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": nowhere}).status_code == 422
    assert org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": qs()}).status_code == 200


def test_the_team_can_test_the_form_while_editing_without_anything_being_stored(org):
    pid = org.project()
    r = org.c.post(url(pid, "/preview"), headers=org.h(), json={"questions": qs(), "answers": {"age": 12}}).json()
    assert r["result"] == "out" and "第 1 题" in r["reason"]
    assert org.c.post(url(pid, "/preview"), headers=org.h(), json={"questions": qs(), "answers": {"age": 30}}).json()["question"]["id"] == "use"
    assert org.c.post(url(pid, "/preview"), headers=org.h(), json={"questions": qs(), "answers": {"age": "x"}}).status_code == 422
    assert org.c.get(url(pid), headers=org.h()).json()["stats"] == {"qualified": 0, "out": 0, "out_by": {}}


def published(org):
    pid = org.project()
    org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": qs()})
    f = org.c.post(url(pid, "/publish"), headers=org.h()).json()
    return pid, f["link"].split("t=")[1]


def test_publishing_makes_a_link_and_a_qr_code_and_closing_stops_it(client, org):
    pid = org.project()
    assert org.c.post(url(pid, "/publish"), headers=org.h()).status_code == 422  # nothing written yet
    org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": qs()})
    f = org.c.post(url(pid, "/publish"), headers=org.h()).json()
    assert f["status"] == "published" and f["link"].startswith("/screen.html?t=") and len(f["link"]) > 30
    qr = org.c.get(url(pid, "/qr.svg"), headers=org.h())
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg") and b"<svg" in qr.content
    tok = f["link"].split("t=")[1]
    assert client.get(f"/api/public/screen/{tok}").json()["open"] is True
    closed = org.c.post(url(pid, "/close"), headers=org.h()).json()
    assert closed["status"] == "closed" and client.get(f"/api/public/screen/{tok}").json()["open"] is False
    assert client.post(f"/api/public/screen/{tok}/step", json={"answers": {}}).status_code == 410
    assert org.c.post(url(pid, "/publish"), headers=org.h()).json()["link"] == f["link"]  # the same link opens again
    assert client.get("/api/public/screen/not-a-token").status_code == 404


def test_a_candidate_who_qualifies_lands_in_the_list_with_answers_and_tags(client, org):
    pid, tok = published(org)
    step = client.post(f"/api/public/screen/{tok}/step", json={"answers": {}}).json()
    assert step["question"]["id"] == "age" and "goto" not in str(step) and "tag" not in str(step)
    answers = {"age": 28, "use": "daily", "chan": ["on"], "why": "价格"}
    assert client.post(f"/api/public/screen/{tok}/step", json={"answers": answers}).json()["result"] == "qualified"
    me = {"answers": answers, "name": "林小雨", "email": "Lin@Example.com", "country": "SG", "consent": True}
    assert client.post(f"/api/public/screen/{tok}/submit", json={**me, "consent": False}).status_code == 422  # the notice must be agreed to
    assert client.post(f"/api/public/screen/{tok}/submit", json={**me, "email": "nope"}).status_code == 422
    assert client.post(f"/api/public/screen/{tok}/submit", json={**me, "country": "ID"}).status_code == 403  # a region that is not open yet
    assert client.post(f"/api/public/screen/{tok}/submit", json={**me, "answers": {"age": 12}}).status_code == 409  # the answers are walked again here
    assert client.post(f"/api/public/screen/{tok}/submit", json=me).status_code == 201
    assert client.post(f"/api/public/screen/{tok}/submit", json=me).status_code == 201  # the same email again: accepted, no second row
    people = [p for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json() if p["email"] == "lin@example.com"]
    assert len(people) == 1 and people[0]["screen_result"] == "qualified" and people[0]["tags"] == ["线上买家"] and people[0]["screening"] == "eligible"
    answered = org.c.get(f"/api/participants/{people[0]['id']}/screener", headers=org.h()).json()["text"]
    assert "您的年龄？：28" in answered and "在哪里买？：线上" in answered
    assert org.c.get(url(pid), headers=org.h()).json()["stats"]["qualified"] == 1


def test_a_candidate_who_is_screened_out_is_only_counted(client, org):
    pid, tok = published(org)
    assert client.post(f"/api/public/screen/{tok}/step", json={"answers": {"age": 15}}).json() == {"question": None, "result": "out"}
    client.post(f"/api/public/screen/{tok}/step", json={"answers": {"age": 30, "use": "never"}})
    stats = org.c.get(url(pid), headers=org.h()).json()["stats"]
    assert stats["out"] == 2 and stats["out_by"] == {"age": 1, "use": 1} and stats["qualified"] == 0
    assert org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json() == []  # nothing about them is kept
    assert client.post(f"/api/public/screen/{tok}/step", json={"answers": {"age": "abc"}}).status_code == 422


def test_an_html_link_cannot_be_published_and_switching_to_it_closes_the_link(client, org):
    pid, tok = published(org)
    org.c.put(url(pid), headers=org.h(), json={"source": "html", "html_url": "https://survey.example.com/x"})
    assert client.get(f"/api/public/screen/{tok}").json()["open"] is False
    assert org.c.post(url(pid, "/publish"), headers=org.h()).status_code == 422


def test_marking_groups_for_the_asynchronous_ai_turns_the_method_on_and_shows_in_the_monitor(org):
    from test_plans import approved, draft, plan, save
    pid = draft(org)["id"]
    cfg = approved(org, pid)
    assert "async" not in plan(org, pid)["config"]["methods"]
    marked = {**cfg, "lt": {**cfg["lt"], "rows": [{**cfg["lt"]["rows"][0], "a": True}]}}
    assert save(org, pid, marked).json()["config"]["methods"] == ["depth", "async"]  # 仅深访 + a marked group
    org.c.post(f"/api/projects/{pid}/plan/submit", headers=org.h())
    org.c.post(f"/api/projects/{pid}/plan/approve", headers=org.h("admin"))
    org.c.post(f"/api/projects/{pid}/plan/publish", headers=org.h())
    mon = org.c.get(f"/api/projects/{pid}/monitor", headers=org.h()).json()
    assert mon["i"]["rows"][0]["async"] is True and mon["i"]["async_target"] == 5 and mon["q"]["rows"] == [] and mon["f"]["async_target"] == 0
    assert org.c.get(f"/api/projects/{pid}", headers=org.h()).json()["methods"] == ["depth", "async"]


def test_the_research_background_goes_to_the_library_and_from_there_to_the_ai(org):
    from test_plans import draft, plan
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    assert org.c.post(f"/api/projects/{pid}/plan/brief-to-library", headers=org.h()).status_code == 422  # still empty
    org.c.put(f"/api/projects/{pid}/plan", headers=org.h(), json={"brief": "新品防晒霜进入新加坡市场，了解年轻人的购买决策", "config": cfg})
    assert org.c.post(f"/api/projects/{pid}/plan/brief-to-library", headers=org.h()).json()["library_docs"] == 1
    org.c.put(f"/api/projects/{pid}/plan", headers=org.h(), json={"brief": "新品防晒霜进入新加坡市场，改了一版", "config": cfg})
    assert org.c.post(f"/api/projects/{pid}/plan/brief-to-library", headers=org.h()).json()["library_docs"] == 1  # refreshed, not a second copy
    docs = [d for d in org.c.get("/api/repo/documents", headers=org.h()).json() if (d.get("project") or {}).get("id") == pid]
    assert len(docs) == 1 and docs[0]["title"] == "研究背景（立项）"
    assert plan(org, pid)["library_docs"] == 1
    from app import library_context
    from app.db import SessionLocal
    with SessionLocal() as db:
        text, n = library_context.library_text(db, pid)
    assert n == 1 and "改了一版" in text


def test_the_ai_draft_combines_the_idea_the_plan_background_and_the_library(org):
    from app import library_context
    from app.moderator.providers import MockModeratorChat
    from app.recruit_form import generate
    chat = MockModeratorChat(script=[{"questions": [{"type": "text", "text": "好的问题？"}]}])
    generate(chat, "补充：重点看购买渠道", "zh", library="## 旧报告\n去年的发现", brief="新品防晒霜进入新加坡市场")
    prompt = chat.calls[0][1]["content"]
    assert "补充：重点看购买渠道" in prompt and "新品防晒霜进入新加坡市场" in prompt and "去年的发现" in prompt
    from test_plans import draft, plan
    pid = draft(org)["id"]
    cfg = plan(org, pid)["config"]
    assert org.c.post(f"/api/projects/{pid}/recruiting/form/generate", headers=org.h(), json={"idea": ""}).status_code == 422  # nothing written anywhere
    org.c.put(f"/api/projects/{pid}/plan", headers=org.h(), json={"brief": "新品防晒霜进入新加坡市场，了解年轻人的购买决策", "config": cfg})
    r = org.c.post(f"/api/projects/{pid}/recruiting/form/generate", headers=org.h(), json={"idea": ""})  # the background alone is enough
    assert r.status_code == 200 and r.json()["questions"]
    assert library_context.BRIEF_TITLE  # (the background reaches the library through the wizard; the library text is always handed over)
