"""检查提示: plain rules on the study in the making (no model)."""
import json

from app.creation import checks, stages
from app.db import SessionLocal
from app.models import Project, RecruitForm

OUTLINE = chr(10).join([
    "目标: 了解护手霜使用", "受访者话题: 护手霜", "语言: zh", "时长: 45", "",
    "## 暖场", "- 先聊聊你平时的护肤习惯？", "",
    "## 话题: 使用场景", "- 你是不是觉得冬天一定要用护手霜？", "  追问: deep", "- 你最近一次用护手霜是什么时候？你当时在哪里？", "- 当时是什么场合" + "呢" * 80 + "？",
    "* 了解: 使用场景", ""])


def ids(org, pid):
    with SessionLocal() as db:
        p = db.get(Project, pid)
        return {f["id"]: f for f in checks.run(p, db.get(RecruitForm, pid), stages.load(p))}


def test_an_empty_study_only_asks_for_goals(org):
    pid = org.project()
    assert set(ids(org, pid)) == {"no_goals"}


def test_the_outline_is_checked_for_leading_double_long_and_missing_guides(org):
    pid = org.project(language="zh")
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    found = ids(org, pid)
    assert "leading:2" in found and found["leading:2"]["level"] == "warn" and "中立" in found["leading:2"]["fix"]
    assert "double:3" in found and "long:4" in found and "no_guide:2" in found and "no_section_guide:0" in found


def test_the_length_is_compared_with_the_plan(org):
    pid = org.project(language="zh")
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    with SessionLocal() as db:
        p = db.get(Project, pid)
        state = stages.load(p)
        state.decisions["minutes"] = 5
        stages.save(p, state)
        db.commit()
    assert "too_long" in ids(org, pid)


def test_a_research_goal_nobody_asks_about_is_flagged(org):
    pid = org.project(language="zh")
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": OUTLINE})
    with SessionLocal() as db:
        db.add(RecruitForm(project_id=pid, org_id=db.get(Project, pid).org_id, goals=["了解冬天护手霜的使用场景", "比较不同价位品牌的信任度"]))
        db.commit()
    found = ids(org, pid)
    assert "uncovered:1" in found and "uncovered:0" not in found and "no_goals" not in found


def test_screening_calibre_and_size_are_flagged(org):
    pid = org.project()
    qs = [{"id": f"q{i}", "type": "text", "text": "您的家庭月收入是多少？" if i == 1 else f"第 {i} 题？", "required": True, "options": [], "rules": []} for i in range(1, 12)]
    with SessionLocal() as db:
        db.add(RecruitForm(project_id=pid, org_id=db.get(Project, pid).org_id, goals=["g"], questions=qs))
        db.commit()
    found = ids(org, pid)
    assert {"screen_many", "calibre_income", "screen_no_exit"} <= set(found)


def test_the_dialog_view_carries_the_findings_and_the_model_gets_them(org, monkeypatch):
    from app.routers import creation as creation_router
    seen = []

    class Chat:
        name = "fake"

        def complete(self, messages, temperature=0.0):
            seen.append(json.loads(messages[1]["content"]))
            return {"reply": "好的", "patch": {}, "needs": [], "suggestions": [], "ready": False}

    monkeypatch.setattr(creation_router, "_chat", lambda: Chat())
    pid = org.project()
    r = org.c.post(f"/api/projects/{pid}/creation/message", headers=org.h(), json={"message": "你好"}).json()
    assert [c["id"] for c in r["checks"]] == ["no_goals"] and "还没有研究目标" in seen[-1]["CHECKS"][0]
