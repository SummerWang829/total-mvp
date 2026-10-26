"""The recruiting dashboard (配额与受访者名单): imports with a source, the numbers, the comparison between the people who qualified and the ones who were screened out,
the AI's reading of those numbers, and copying / exporting the list."""
import csv
import io

from app.models import AuditLog
from app.db import SessionLocal
from sqlalchemy import select

QS = [
    {"id": "age", "type": "number", "text": "您的年龄？", "rules": [{"op": "lt", "value": 18, "goto": "end_out"}]},
    {"id": "city", "type": "single", "text": "您住在哪里？", "options": [{"id": "gba", "text": "大湾区", "tag": "大湾区"}, {"id": "non", "text": "非大湾区", "tag": "非大湾区"}, {"id": "hk", "text": "香港", "goto": "end_out"}]},
]


def url(pid, tail=""):
    return f"/api/projects/{pid}/recruiting{tail}"


def publish(org):
    pid = org.project()
    org.c.put(url(pid, "/form"), headers=org.h(), json={"source": "ai", "questions": QS})
    return pid, org.c.post(url(pid, "/form/publish"), headers=org.h()).json()["link"].split("t=")[1]


def run(client, tok, answers, contact=None):
    client.post(f"/api/public/screen/{tok}/step", json={"answers": {}})  # opening the first question
    r = client.post(f"/api/public/screen/{tok}/step", json={"answers": answers}).json()
    if contact and r["result"] == "qualified":
        assert client.post(f"/api/public/screen/{tok}/submit", json={"answers": answers, **contact, "consent": True}).status_code == 201
    return r


def person(name, email):
    return {"name": name, "email": email, "country": "SG"}


def imp(org, pid, text, **kw):
    return org.c.post(f"/api/projects/{pid}/participants/import", headers=org.h(), json={"csv": text, **kw})


def test_an_import_needs_a_source_and_the_confirmation_and_marks_every_person_with_it(org):
    pid = org.project()
    csv_text = "name,email,country\n陈美玲,chen@example.com,SG\n王大明,wang@example.com,SG\n"
    assert imp(org, pid, csv_text).status_code == 422  # no source
    assert imp(org, pid, csv_text, source_name="客户 A 老客户名单").status_code == 422  # no confirmation of a lawful basis
    assert imp(org, pid, csv_text, dry_run=True).json()["created"] == 2  # a preview needs neither
    r = imp(org, pid, csv_text, source_name="客户 A 老客户名单", confirm_basis=True).json()
    assert r["created"] == 2
    people = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()
    assert all(p["source"] == "import" and p["source_name"].startswith("客户 A 老客户名单 · ") for p in people)
    assert all(p["screen_result"] == "qualified" and "外部名单" in p["screen_reason"] for p in people)  # invited straight away, not through the questionnaire
    again = imp(org, pid, csv_text, source_name="客户 A 老客户名单", confirm_basis=True).json()
    assert again["created"] == 0 and len(again["skipped"]) == 2  # the same e-mail twice is not added twice
    with SessionLocal() as db:
        logged = db.scalars(select(AuditLog).where(AuditLog.action == "participant.import")).all()
    assert logged and logged[-1].meta["basis_confirmed"] is True and "客户 A" in logged[-1].meta["source"]


def test_people_imported_to_be_screened_are_still_judged_by_the_rules_and_external_ones_are_not(org):
    pid = org.project()
    org.c.put(url(pid, "/rules"), headers=org.h(), json={"rules": [{"field": "country", "op": "equals", "value": "MY", "effect": "require"}]})
    imp(org, pid, "name,email,country\n直接邀请的人,a@example.com,SG\n", source_name="合作方", confirm_basis=True)  # invite (default)
    imp(org, pid, "name,email,country\n要甄别的人,b@example.com,SG\n", source_name="合作方", confirm_basis=True, mode="screen")
    by = {p["name"]: p for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert by["直接邀请的人"]["screen_result"] == "qualified"  # the rules never judge an externally invited person
    assert by["要甄别的人"]["screen_result"] == "disqualified"
    org.c.post(url(pid, "/apply"), headers=org.h())
    assert {p["name"]: p["screen_result"] for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}["直接邀请的人"] == "qualified"


def test_the_dashboard_counts_who_started_qualified_and_was_screened_out_without_keeping_the_screened_out(client, org):
    pid, tok = publish(org)
    run(client, tok, {"age": 30, "city": "gba"}, person("林小雨", "lin@example.com"))
    run(client, tok, {"age": 28, "city": "gba"}, person("王大明", "wang@example.com"))
    run(client, tok, {"age": 40, "city": "non"}, person("陈美玲", "chen@example.com"))
    run(client, tok, {"age": 15})  # screened out on the first question
    run(client, tok, {"age": 33, "city": "hk"})  # screened out on the second
    d = org.c.get(url(pid, "/dashboard"), headers=org.h()).json()
    k = d["kpi"]
    assert k["started"] == 5 and k["finished"] == 5 and k["qualified"] == 3 and k["pass_rate"] == 60 and k["usable"] == 3
    assert [f["n"] for f in d["funnel"]][:3] == [5, 5, 3]
    assert {o["qid"]: o["n"] for o in d["out_by"]} == {"age": 1, "city": 1} and d["out_by"][0]["label"].startswith("第 ")
    assert d["sources"] == [{"name": "问卷链接 / 二维码", "n": 3}]
    assert {t["tag"]: t["n"] for t in d["tags"]} == {"大湾区": 2, "非大湾区": 1}
    assert org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json().__len__() == 3  # the two screened out left nothing behind but the tally
    row = next(r for r in d["compare"] if r["option"] == "大湾区")
    assert row["qualified"] == 67 and row["out"] == 0 and row["gap"] == 67 and row["type"] == "single"  # 2 of 3 qualified chose it; the one screened out at the city question chose Hong Kong
    assert any("67% 选了「大湾区」" in x for x in d["traits"]["qualified"])  # three qualified people are enough to say what they share


def test_shared_traits_need_enough_people_and_the_ai_summary_gets_only_statistics(client, org):
    from app.moderator.providers import MockModeratorChat
    from app import recruit_insights
    pid, tok = publish(org)
    assert org.c.post(url(pid, "/dashboard/summary"), headers=org.h()).status_code == 422  # nothing to read yet
    for i in range(4):
        run(client, tok, {"age": 30 + i, "city": "gba"}, person(f"人{i}", f"p{i}@example.com"))
    d = org.c.get(url(pid, "/dashboard"), headers=org.h()).json()
    assert any("100% 选了「大湾区」" in t for t in d["traits"]["qualified"]) and any("平均 31" in t or "平均 32" in t for t in d["traits"]["qualified"])
    assert org.c.post(url(pid, "/dashboard/summary"), headers=org.h()).json()["summary"]  # the offline reading
    bot = MockModeratorChat(script=[{"summary": "合格的人集中在大湾区。"}])
    assert recruit_insights.summarize(bot, d) == "合格的人集中在大湾区。"
    sent = " ".join(m["content"] for m in bot.calls[0])
    assert "p0@example.com" not in sent and "人0" not in sent and "大湾区" in sent  # numbers and the researcher's own texts, no one's details


def test_the_list_says_where_each_person_came_from_and_shows_key_answers(client, org):
    pid, tok = publish(org)
    run(client, tok, {"age": 30, "city": "gba"}, person("林小雨", "lin@example.com"))
    imp(org, pid, "name,email,country,买过几次\n陈美玲,chen@example.com,SG,3 次\n", source_name="客户 A 名单", confirm_basis=True)
    org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": "手填的人"})
    by = {p["name"]: p for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert by["林小雨"]["source"] == "link" and by["林小雨"]["source_name"] == "问卷链接 / 二维码" and "30" in by["林小雨"]["key_answers"]
    assert by["陈美玲"]["source"] == "import" and by["陈美玲"]["key_answers"] == "3 次"
    assert by["手填的人"]["source"] == "manual"
    d = org.c.get(url(pid, "/dashboard"), headers=org.h()).json()
    assert {s["name"] for s in d["sources"]} >= {"问卷链接 / 二维码", "手动添加"} and any(s["name"].startswith("客户 A 名单") for s in d["sources"]) and d["kpi"]["external"] == 1


def test_exporting_the_list_gives_a_csv_with_or_without_contacts_and_is_logged(client, org):
    pid, tok = publish(org)
    run(client, tok, {"age": 30, "city": "gba"}, person("林小雨", "lin@example.com"))
    imp(org, pid, "name,email,country\n陈美玲,chen@example.com,SG\n", source_name="客户 A 名单", confirm_basis=True)
    ids = {p["name"]: p["id"] for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    r = org.c.post(f"/api/projects/{pid}/participants/export", headers=org.h(), json={})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and r.content.startswith(b"\xef\xbb\xbf")  # Excel reads the Chinese
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == ["编号", "姓名", "邮箱", "国家", "语言", "标签", "资格", "来源", "关键回答"] and len(rows) == 3
    assert rows[1][:3] == ["P-001", "林小雨", "lin@example.com"] and rows[2][6] == "外部名单 · 可邀请" and rows[2][7].startswith("客户 A 名单")
    mine = org.c.post(f"/api/projects/{pid}/participants/export", headers=org.h(), json={"ids": [ids["陈美玲"]], "contacts": False})
    rows2 = list(csv.reader(io.StringIO(mine.content.decode("utf-8-sig"))))
    assert rows2[0] == ["编号", "国家", "语言", "标签", "资格", "来源", "关键回答"] and len(rows2) == 2 and "chen@example.com" not in mine.text and "陈美玲" not in mine.text
    assert rows2[1][0] == "P-002"  # the same number the list shows
    assert org.c.post(f"/api/projects/{pid}/participants/export-log", headers=org.h(), json={"count": 2}).status_code == 204
    with SessionLocal() as db:
        actions = [a.action for a in db.scalars(select(AuditLog)).all()]
        exports = [a for a in db.scalars(select(AuditLog).where(AuditLog.action == "participant.export")).all()]
    assert "participant.copy" in actions and len(exports) == 2 and exports[-1].meta["contacts"] is False and exports[0].meta["scope"] == "all"
    assert "lin@example.com" not in str([a.meta for a in exports])  # the log holds how many, never the people


def test_only_the_research_team_can_export_copy_or_read_the_dashboard(client, org):
    pid, _ = publish(org)
    for role in ("observer", "moderator"):
        h = org.h(role)
        assert org.c.post(f"/api/projects/{pid}/participants/export", headers=h, json={}).status_code == 403
        assert org.c.post(f"/api/projects/{pid}/participants/export-log", headers=h, json={"count": 1}).status_code == 403
        assert org.c.get(url(pid, "/dashboard"), headers=h).status_code == 403
        assert org.c.post(url(pid, "/dashboard/summary"), headers=h).status_code == 403
