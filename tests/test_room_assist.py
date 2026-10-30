"""The room's side panels: outline progress, pace, who talks, follow-up hints, chapter summaries (rules only, quotes word for word)."""
from app import room_assist as ra
from app.db import SessionLocal
from app.models import CaptionLine

OUTLINE = {"minutes": 30, "parts": [
    {"kind": "warmup", "title": "开场", "questions": [{"text": "Could you tell me how you usually buy skincare products?"}]},
    {"kind": "topic", "title": "顾虑", "questions": [{"text": "What makes you hesitate to try a new brand from overseas?"}, {"text": "How important is the packaging to you?"}]},
    {"kind": "topic", "title": "价格", "questions": [{"text": "Would you pay more for an organic label?"}]}]}


def L(i, speaker, text, at):
    return {"id": f"l{i}", "speaker": speaker, "text": text, "at_ms": at}


LINES = [L(1, "moderator", "Could you tell me how you usually buy skincare products?", 1000), L(2, "participant", "I watch reviews first, it is fine usually.", 9000),
         L(3, "moderator", "What makes you hesitate to try a new brand from overseas?", 30000), L(4, "participant", "Last time I bought one, it turned out to be fake, but returning it was easy.", 42000)]


def test_questions_are_covered_by_what_the_moderator_actually_said():
    cov = ra.coverage(OUTLINE, LINES)
    assert [x["covered"] for x in cov["questions"]] == [True, True, False, False] and cov["covered"] == 2 and cov["total"] == 4 and cov["current"] == 2
    assert cov["questions"][1]["at_ms"] == 30000 and cov["questions"][0]["part"] == 0


def test_pace_says_when_time_is_ahead_of_the_questions():
    cov = ra.coverage(OUTLINE, LINES)
    late = ra.pacing(cov, 20 * 60000, 30)   # 67% of the time, 50% of the questions
    assert late["behind"] is True and "压缩第" in late["advice"] and late["used_pct"] == 67 and late["covered_pct"] == 50
    assert ra.pacing(cov, 8 * 60000, 30)["behind"] is False
    assert ra.pacing(cov, None, 30)["advice"] == ""   # not started: nothing to say


def test_the_moderator_who_talks_too_much_is_nudged_only_with_enough_lines():
    few = ra.talk_share(LINES)
    assert few["advice"] == "" and few["moderator_pct"] is None
    many = [L(i, "moderator", "x" * 50, i) for i in range(5)] + [L(9, "participant", "y" * 20, 9), L(10, "participant", "y" * 20, 10)]
    t = ra.talk_share(many)
    assert t["moderator_pct"] > 40 and "多让受访者说" in t["advice"] and t["moderator_pct"] + t["participant_pct"] == 100


def test_follow_up_hints_quote_the_line_and_are_not_repeated():
    hints = ra.probe_suggestions(LINES, set())
    rules = {h["rule"] for h in hints}
    assert {"an experience, not expanded", "a turn, second half not expanded"} <= rules
    assert all(h["trigger"] in "Last time I bought one, it turned out to be fake, but returning it was easy. I watch reviews first, it is fine usually." for h in hints)
    again = ra.probe_suggestions(LINES, {h["key"] for h in hints})
    assert not ({h["key"] for h in hints} & {h["key"] for h in again})
    zh = ra.probe_suggestions([L(1, "participant", "我平时用得还行，但是价格大概 200 元以内。", 5)], set())
    assert any("具体指什么" in h["question"] for h in zh) and any("为什么是这个数" in h["question"] for h in zh)


def test_a_chapter_is_summarised_once_all_its_questions_were_asked():
    lines = LINES + [L(5, "moderator", "How important is the packaging to you?", 60000), L(6, "participant", "Neat packaging feels safe, especially for a gift.", 70000)]
    cov = ra.coverage(OUTLINE, lines)
    ch = ra.chapter_summaries(OUTLINE, lines, cov)
    assert [c["title"] for c in ch] == ["开场", "顾虑"]
    assert ch[1]["points"] and all(p["text"] in " ".join(x["text"] for x in lines) for p in ch[1]["points"])   # word for word
    assert ra.chapter_summaries(OUTLINE, LINES[:2], ra.coverage(OUTLINE, LINES[:2]))[0]["title"] == "开场"


def test_endpoint_needs_a_team_role_and_reads_the_project_outline(org):
    itv = org.interview()
    org.c.put(f"/api/projects/{itv['project_id']}/outline", headers=org.h(), json={"text": "目标: x\n受访者话题: x\n语言: en\n时长: 30\n## 暖场\n- Could you tell me how you usually buy skincare products?\n## 话题: 顾虑\n- What makes you hesitate to try a new brand from overseas?\n"})
    with SessionLocal() as db:
        db.add(CaptionLine(org_id=itv["org_id"] if "org_id" in itv else db_org(db, itv["id"]), interview_id=itv["id"], seq=1, speaker="moderator", text="Could you tell me how you usually buy skincare products?", at_ms=1000))
        db.add(CaptionLine(org_id=db_org(db, itv["id"]), interview_id=itv["id"], seq=2, speaker="participant", text="Last time it was fake, but returning it was easy.", at_ms=5000))
        db.commit()
    d = org.c.get(f"/api/interviews/{itv['id']}/assist", headers=org.h("moderator")).json()
    assert d["has_outline"] and d["outline"]["covered"] == 1 and d["outline"]["total"] == 2 and d["outline"]["current"] == 1 and d["probes"]
    qs = [q for p in d["outline"]["parts"] for q in p["questions"]]
    assert all(q["depth"] in ("none", "shallow", "deep", "free") and "guide" in q for q in qs)   # the room can open a question to show its follow-up points
    assert org.c.get(f"/api/interviews/{itv['id']}/assist", headers=org.h("observer")).status_code == 403


def db_org(db, iid):
    from app.models import Interview
    return db.get(Interview, iid).org_id
