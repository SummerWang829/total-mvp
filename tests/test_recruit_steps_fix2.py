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


def test_someone_imported_to_be_screened_joins_the_list_only_when_they_qualify(client, org):
    from test_screen_form import published
    pid, tok = published(org)
    r = org.c.post(f"/api/projects/{pid}/participants/import", headers=org.h(), json={"csv": "姓名,邮箱\n林小雨,lin@example.com\n王大明,wang@example.com", "dry_run": False,
                                                                                        "source_name": "客户老客户", "mode": "screen", "confirm_basis": True}).json()
    assert r["created"] == 2
    ppl = {x["name"]: x for x in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert ppl["林小雨"]["screen_result"] == "" and ppl["林小雨"]["source"] == "import"   # waiting: the list page does not show them yet
    answers = {"age": 28, "use": "daily", "chan": ["on"], "why": "价格"}
    first = client.post(f"/api/public/screen/{tok}/step", json={"answers": {}}).json()
    assert client.post(f"/api/public/screen/{tok}/submit", json={"answers": answers, "started": first.get("started", ""), "name": "林小雨", "email": "lin@example.com", "country": "SG", "consent": True}).status_code == 201
    ppl = {x["name"]: x for x in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert ppl["林小雨"]["screen_result"] == "qualified" and "线上买家" in ppl["林小雨"]["tags"] and ppl["王大明"]["screen_result"] == ""


def test_a_repeat_is_taken_out_and_the_order_stays():
    """修正方案第 3 轮: the same question twice (or a second age question) keeps only one, the one with tags; the team's order is never re-sorted."""
    from app.recruit_steps import arrange

    def q(qid, text, tag="", goto=""):
        a, b = ("18-24岁", "25-34岁") if "年龄" in text else ("是", "否")
        return {"id": qid, "type": "single", "text": text, "options": [{"id": "o1", "text": a, "goto": goto, "tag": tag, "excl": []}, {"id": "o2", "text": b, "goto": "", "tag": "", "excl": []}], "rules": []}
    qs = [q("q1", "你或你的家人是否在下列行业工作？", goto="q5"), q("q2", "您目前主要使用的车辆品牌？"), q("q3", "请问您的年龄是？"),
          q("q4", "您常住的城市属于以下哪一类？"), q("q5", "请问您的性别是？"), q("q6", "再确认一下：请问您的年龄是？", tag="18-24岁"),
          q("q7", "您目前主要使用的车辆品牌是？")]
    out, removed = arrange(qs)
    assert [x["id"] for x in out] == ["q1", "q2", "q4", "q5", "q6"]   # q3 (age, no tags) and q7 (repeats q2) go; nothing moves
    assert len(removed) == 2
    assert out[0]["options"][0]["goto"] == "q5"


def test_remaking_a_step_keeps_the_order_the_team_set(org):
    """用户 2026-11-20 第 3 轮: once the team has put questions in an order (e.g. in the dialog), confirming a step again does not re-sort them."""
    pid = org.project()
    _need(org, pid)
    conds = org.c.post(url(pid, "/steps/include/suggest"), headers=org.h(), json={}).json()["conds"]
    _confirm(org, pid, "include", {"conds": conds})
    excl = org.c.post(url(pid, "/steps/exclude/suggest"), headers=org.h(), json={}).json()["excl"]
    out = _confirm(org, pid, "exclude", {"excl": excl})
    qs = out["questions"]
    assert qs[0]["step"] == "exclude"   # first time: exclusions take their usual place in front
    mine = [q for q in qs if q["step"] == "include"] + [q for q in qs if q["step"] != "include"]   # the team moves the conditions to the top
    again = org.c.put(url(pid, "/steps/exclude"), headers=org.h(), json={"data": {"excl": excl}, "questions": mine}).json()
    assert [q["step"] for q in again["questions"]][: len(conds)] == ["include"] * len(conds)
