"""修正方案第 2 轮（甄别问卷）: the dialog under ② ③ ④ returns a whole new table (empty conditions never show), quotas read from an attached file, ⑥ adds questions by
itself when the questionnaire is too short, goals written in one line become one goal per line, and a PowerPoint is read."""
import io
import zipfile

from test_recruit_steps import _confirm, _need, url

from app import attachments


def test_the_dialog_changes_the_conditions_and_drops_empty_ones(org):
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    r = org.c.post(url(pid, "/steps/include/chat"), headers=org.h(), json={"text": "加一条近 12 个月保养过 2 次以上", "draft": conds}).json()
    assert len(r["conds"]) == len(conds) + 1 and r["reply"]
    gone = org.c.post(url(pid, "/steps/include/chat"), headers=org.h(), json={"text": f"去掉{conds[0]['name']}", "draft": r["conds"]}).json()
    assert len(gone["conds"]) == len(conds)
    blank = {"id": "x", "name": "新条件", "question": "", "type": "single", "use": True, "options": [{"text": "符合", "pass": True}, {"text": "不符合", "pass": False}]}
    out = _confirm(org, pid, "exclude", {"excl": [blank]})
    assert not any(q["step"] == "exclude" for q in out["questions"])   # an empty condition is not a question


def test_quotas_are_read_from_an_attached_file(org):
    pid = org.project()
    r = org.c.post(url(pid, "/steps/quota/chat"), headers=org.h(), json={"text": "请读附件里的配额", "draft": [],
                                                                         "refs": [{"kind": "file", "title": "配额.xlsx", "text": "表：配额\n本田车主 3人\n新势力车主 2人"}]}).json()
    assert [(q["name"], q["target"]) for q in r["quotas"]] == [("本田车主", 3), ("新势力车主", 2)] and sum(q["target"] for q in r["quotas"]) == 5


def test_a_short_questionnaire_gets_questions_added_once(org):
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    _confirm(org, pid, "include", {"conds": conds[:1]})
    r = org.c.post(url(pid, "/steps/test/expand"), headers=org.h()).json()
    assert len(r["fresh"]) == 4 and len(r["questions"]) == 5   # one question: the offline AI adds its four templates (a model fills up to eight)
    assert org.c.post(url(pid, "/steps/test/expand"), headers=org.h()).json()["fresh"] == []   # once
    again = _confirm(org, pid, "include", {"conds": conds[:2]})
    assert sum(1 for q in again["questions"] if q["cond_id"] == "auto") == 4   # remaking ② keeps the added ones


def test_goals_in_one_line_are_split(org):
    pid = org.project()
    out = org.c.put(url(pid), headers=org.h(), json={"source": "ai", "questions": [], "goals": ["1. 对比品牌认知。    2. 识别痛点。 3. 比较期待"]}).json()
    assert out["goals"] == ["对比品牌认知。", "识别痛点。", "比较期待"]


def test_a_powerpoint_is_read_slide_by_slide():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("ppt/slides/slide2.xml", "<p:sld><a:p><a:r><a:t>新势力车主</a:t></a:r></a:p><a:p><a:r><a:t>2 人</a:t></a:r></a:p></p:sld>")
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:p><a:r><a:t>配额</a:t></a:r></a:p></p:sld>")
    out = attachments.extract("配额.pptx", b.getvalue())
    assert out["kind"] == "ppt" and out["text"].splitlines() == ["第 1 页：配额", "第 2 页：新势力车主 / 2 人"]
