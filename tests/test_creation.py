"""创建访谈: the stage-by-stage dialog. The model is a fake that says what each test needs; the code's checks, the changes and the undo are the real ones."""
import json

import pytest

from app.creation import engine
from app.db import SessionLocal
from app.models import AuditLog, Project, RecruitForm
from app.routers import creation as creation_router


class FakeChat:
    name = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, temperature=0.0):
        self.calls.append(json.loads(messages[1]["content"]))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


def say(org, pid, text, role="researcher"):
    return org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h(role), json={"message": text})


def use(monkeypatch, *replies):
    fake = FakeChat(*replies)
    monkeypatch.setattr(creation_router, "_chat", lambda: fake)
    return fake


def reply(text="好的。", patch=None, needs=None, ready=False, suggestions=None):
    return {"reply": text, "advice": "", "needs": needs or [], "patch": patch or {}, "suggestions": suggestions or ["下一步"], "ready": ready}


def form(pid):
    with SessionLocal() as db:
        f = db.get(RecruitForm, pid)
        return f


def test_the_first_visit_opens_the_first_stage_and_lists_all_eleven(org):
    pid = org.project(brief="研究国庆访港内地居民对香港保险的认知。")
    r = org.c.get(f"/api/projects/{pid}/creation", headers=org.h()).json()
    assert r["stage"] == "goal" and [p["stage"] for p in r["progress"]] == ["goal", "mode", "audience", "screen", "consent", "format", "outline", "focus", "invite", "trial", "publish"]
    assert [p["state"] for p in r["progress"]][:2] == ["current", "todo"] and len(r["messages"]) == 1 and r["messages"][0]["role"] == "ai"
    assert len(org.c.get(f"/api/projects/{pid}/creation", headers=org.h()).json()["messages"]) == 1   # opening it again does not open it again


def test_only_the_research_team_and_only_their_own_company(org, client):
    pid = org.project()
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/projects/{pid}/creation", headers=org.h(role)).status_code == 403
    other = client.post("/api/auth/register", json={"org_name": "Other", "name": "A", "email": "a@other.com", "password": "password123"}).json()["token"]
    assert client.get(f"/api/projects/{pid}/creation", headers={"Authorization": f"Bearer {other}"}).status_code == 404


def test_what_the_page_lists_as_changed_comes_from_the_applied_patch_and_can_be_undone(org, monkeypatch):
    pid = org.project(brief="旧的背景")
    use(monkeypatch, reply("已按你说的整理。", {"brief": "新的研究背景：了解保险认知", "goals": ["了解自发认知", "比较两个人群"]}))
    r = say(org, pid, "把目标整理一下").json()
    ai = r["messages"][-1]
    assert ai["actions"] == ["已更新研究背景", "已更新研究目标（2 条）"] and ai["can_undo"] is True and "snapshot" not in ai
    with SessionLocal() as db:
        assert db.get(Project, pid).brief.startswith("新的研究背景") and form(pid).goals == ["了解自发认知", "比较两个人群"]
    u = org.c.post(f"/api/projects/{pid}/creation/undo/{ai['id']}", headers=org.h()).json()
    with SessionLocal() as db:
        assert db.get(Project, pid).brief == "旧的背景" and not (form(pid).goals if form(pid) else [])
    assert u["messages"][-1].get("undone") and org.c.post(f"/api/projects/{pid}/creation/undo/{ai['id']}", headers=org.h()).status_code == 409


def test_a_bad_patch_is_sent_back_with_the_reason_and_nothing_is_half_changed(org, monkeypatch):
    pid = org.project(brief="背景")
    bad = reply("改好了。", {"brief": "不该留下的背景", "goals": []})   # the brief would be set first, then the empty goals refused
    fake = use(monkeypatch, bad, reply("这次对了。", {"goals": ["一个目标"]}))
    r = say(org, pid, "整理目标").json()
    assert r["messages"][-1]["actions"] == ["已更新研究目标（1 条）"] and len(fake.calls) == 2 and "goals" in fake.calls[1]["YOUR_PREVIOUS_ANSWER_WAS_REJECTED"]
    with SessionLocal() as db:
        assert db.get(Project, pid).brief == "背景"   # the refused first try left no trace
    use(monkeypatch, bad)
    assert say(org, pid, "再来").status_code == 502
    with SessionLocal() as db:
        assert db.get(Project, pid).brief == "背景"


def test_the_one_stop_way_has_no_invitation_stage_and_needs_an_ai_host(org, monkeypatch):
    pid = org.project()
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok", {}))
    org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h())   # -> mode
    use(monkeypatch, reply("ok", {"recruit_mode": "one", "host_mode": "human"}))
    assert say(org, pid, "一站式，真人主持").status_code == 502   # refused three times: the combination does not exist
    use(monkeypatch, reply("已设为一站式。", {"recruit_mode": "one", "host_mode": "ai"}))
    r = say(org, pid, "一站式，AI 主持").json()
    assert r["mode"] == "one" and r["host"] == "ai" and "invite" not in [p["stage"] for p in r["progress"]] and len(r["progress"]) == 10
    assert org.c.post(f"/api/projects/{pid}/creation/stage", headers=org.h(), json={"stage": "invite"}).status_code == 422


def test_moving_between_stages(org, monkeypatch):
    pid = org.project()
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok"))
    r = org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h()).json()
    assert r["stage"] == "mode" and r["progress"][0]["state"] == "done" and r["progress"][1]["state"] == "current"
    r = org.c.post(f"/api/projects/{pid}/creation/stage", headers=org.h(), json={"stage": "goal"}).json()   # going back is free
    assert r["stage"] == "goal" and r["progress"][0]["state"] == "current" and r["progress"][1]["state"] == "todo" or r["progress"][1]["state"] == "done"
    assert org.c.post(f"/api/projects/{pid}/creation/stage", headers=org.h(), json={"stage": "nonsense"}).status_code == 422


def test_audience_numbers_must_add_up_and_the_need_is_written(org, monkeypatch):
    pid = org.project()
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok"))
    for _ in range(2):
        org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h())   # goal -> mode -> audience
    groups = [{"name": "大湾区", "target": 300, "rule": "广东九市居民"}, {"name": "非大湾区", "target": 200, "rule": "其他内地居民"}]
    use(monkeypatch, reply("拆开了。", {"total": 400, "backup": 50, "groups": groups}))
    assert say(org, pid, "500 人，六四开").status_code == 502   # 300 + 200 is not 400
    use(monkeypatch, reply("拆开了。", {"total": 500, "backup": 50, "groups": groups, "caliber": [{"term": "大湾区", "definition": "广东九个大湾区城市"}]}))
    r = say(org, pid, "500 人，六四开").json()
    assert r["decisions"]["audience"]["total"] == 500 and "已记录 1 个口径" in r["messages"][-1]["actions"][1]
    assert "共 500 人" in form(pid).need and "大湾区 300 人" in form(pid).need


def test_the_screening_questionnaire_goes_through_the_existing_checks(org, monkeypatch):
    pid = org.project()
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok"))
    for _ in range(3):
        org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h())   # -> screen
    q = [{"id": "q1", "type": "single", "text": "您目前常住在哪里？", "required": True, "options": [
        {"id": "o1", "text": "广东九市", "tag": "大湾区"}, {"id": "o2", "text": "香港或澳门", "goto": "end_out"}]}]
    use(monkeypatch, reply("起草好了。", {"questions": q}))
    r = say(org, pid, "起草问卷").json()
    assert r["messages"][-1]["actions"] == ["已更新甄别问卷（1 题）"] and len(form(pid).questions) == 1
    use(monkeypatch, reply("坏的。", {"questions": [{"type": "single", "text": "没有选项"}]}))
    assert say(org, pid, "再起草").status_code == 502 and len(form(pid).questions) == 1


OUTLINE = {"participant_topic": "护手霜使用", "warmup_questions": ["先聊聊你平时的护肤习惯？"], "warmup_depth": ["shallow"],
           "topics": [{"title": "使用场景", "section_guide": "先问最近一次具体使用，再问场景；不要问品牌偏好", "questions": ["你最近一次用护手霜是什么时候？", "当时是在什么场合？"],
                       "depth": ["deep", "shallow"], "guide": ["先让对方回忆当时的情形", ""], "must_cover": ["使用场景"]}]}


def test_the_outline_keeps_section_guides_and_question_notes_and_reads_back(org, monkeypatch):
    pid = org.project(brief="了解护手霜使用", language="zh")
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok"))
    for _ in range(6):
        org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h())   # -> outline
    use(monkeypatch, reply("起草好了。", {"outline": OUTLINE}))
    r = say(org, pid, "起草大纲").json()
    assert r["stage"] == "outline" and r["messages"][-1]["actions"] == ["已更新访谈大纲：1 个环节、3 个问题"]
    text = org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["text"]
    assert "环节指引: 先问最近一次具体使用" in text and "追问: deep" in text and "指引: 先让对方回忆当时的情形" in text
    from app import guide_text
    assert guide_text.parse_guide_text(text)["topics"][0]["section_guide"].startswith("先问最近一次")


def test_a_leading_question_never_reaches_the_outline(org, monkeypatch):
    pid = org.project(brief="了解护手霜使用", language="zh")
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    use(monkeypatch, reply("ok"))
    for _ in range(6):
        org.c.post(f"/api/projects/{pid}/creation/advance", headers=org.h())
    bad = json.loads(json.dumps(OUTLINE))
    bad["topics"][0]["questions"][0] = "你是不是觉得护手霜很有必要？"
    use(monkeypatch, reply("起草好了。", {"outline": bad}))
    assert say(org, pid, "起草大纲").status_code == 502
    assert not (org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["text"] or "").strip()


def test_what_was_said_is_not_written_to_the_audit_trail(org, monkeypatch):
    pid = org.project()
    use(monkeypatch, reply("ok", {"goals": ["一个目标"]}))
    say(org, pid, "这是一句不该出现在审计里的话 SECRET-PHRASE")
    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.action.like("creation.%")).all()
    assert rows and "SECRET-PHRASE" not in str([r.meta for r in rows]) and "SECRET-PHRASE" not in str([r.target for r in rows])


def test_the_attached_file_is_given_to_the_model_for_that_turn_only(org, monkeypatch):
    pid = org.project()
    fake = use(monkeypatch, reply("ok"))
    org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h(), json={"message": "看看这份资料", "material": "【文件：a.txt】新品防晒喷雾"})
    assert "新品防晒喷雾" in fake.calls[-1]["MATERIAL"]
    with SessionLocal() as db:
        assert "新品防晒喷雾" not in (db.get(Project, pid).creation_json or "")


def test_the_offline_demo_walks_through_the_first_stages(org):
    pid = org.project(brief="了解护手霜使用。重点是使用场景。")   # mock model (the test default)
    org.c.get(f"/api/projects/{pid}/creation", headers=org.h())
    r = say(org, pid, "好的，整理一下").json()
    assert "演示模式" in r["messages"][-1]["text"] and r["model"] == "演示模式" and form(pid).goals
    assert engine.START not in str(r["messages"])


def test_the_projects_library_is_given_to_the_model_every_turn(org, monkeypatch):
    pid = org.project()
    org.c.post("/api/repo/documents", headers=org.h(), json={"title": "项目笔记", "text": "客户最在意的是品牌信任", "project_id": pid})
    fake = use(monkeypatch, reply("ok"))
    say(org, pid, "开始")
    assert "品牌信任" in fake.calls[-1]["LIBRARY"]
