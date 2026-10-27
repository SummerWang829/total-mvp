"""The participant list: permanent numbers by source, formal / backup against the quotas (counted together, with the overflow shown), the tag library,
and erasing several people at once."""
from app.db import SessionLocal
from app.models import AuditLog
from sqlalchemy import select

from tests.test_recruit_dashboard import QS, imp, publish, run, url


def people(org, pid):
    return {p["name"]: p for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}


def add(org, pid, name, **kw):
    return org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": name, "country": "SG", **kw}).json()


def set_quotas(org, pid, quotas):
    r = org.c.put(f"/api/projects/{pid}/recruiting/quotas", headers=org.h(), json={"quotas": quotas})
    assert r.status_code == 200, r.text


def test_numbers_follow_the_source_and_never_change(client, org):
    pid, tok = publish(org)
    run(client, tok, {"age": 30, "city": "gba"}, {"name": "林小雨", "email": "lin@example.com", "country": "SG"})
    add(org, pid, "手填的人")
    imp(org, pid, "name,email,country\n外部甲,a@example.com,SG\n外部乙,b@example.com,SG\n", source_name="客户 A", confirm_basis=True)
    got = people(org, pid)
    assert got["林小雨"]["code"] == "Q-001" and got["手填的人"]["code"] == "M-001"
    assert got["外部甲"]["code"] == "X-001" and got["外部乙"]["code"] == "X-002"
    org.c.delete(f"/api/participants/{got['外部甲']['id']}/data", headers=org.h())
    add(org, pid, "又一个手填的人")
    again = people(org, pid)
    assert again["外部乙"]["code"] == "X-002" and again["又一个手填的人"]["code"] == "M-002"  # nobody is renumbered, a deleted number is not reused


def test_people_over_the_quota_are_backup_and_the_overflow_is_shown(client, org):
    pid = org.project()
    set_quotas(org, pid, [{"field": "country", "value": "SG", "target": 2}])
    for name in ("甲", "乙", "丙", "丁"):
        add(org, pid, name)
    got = people(org, pid)
    assert [got[n]["tier"] for n in ("甲", "乙", "丙", "丁")] == ["formal", "formal", "backup", "backup"]
    q = org.c.get(f"/api/projects/{pid}/recruiting", headers=org.h()).json()["quotas"][0]
    assert q["have"] == 4 and q["formal"] == 2 and q["backup"] == 2 and q["over"] == 2 and q["full"] is True  # backups are counted, the overflow is shown


def test_a_tier_set_by_hand_is_kept_and_counts(client, org):
    pid = org.project()
    set_quotas(org, pid, [{"field": "country", "value": "SG", "target": 1}])
    for name in ("甲", "乙"):
        add(org, pid, name)
    got = people(org, pid)
    assert got["乙"]["tier"] == "backup" and got["乙"]["tier_manual"] is False
    assert org.c.put(f"/api/participants/{got['乙']['id']}/tier", headers=org.h(), json={"tier": "formal"}).status_code == 200
    assert org.c.put(f"/api/participants/{got['甲']['id']}/tier", headers=org.h(), json={"tier": "backup"}).status_code == 200
    now = people(org, pid)
    assert now["乙"]["tier"] == "formal" and now["乙"]["tier_manual"] is True and now["甲"]["tier"] == "backup"
    org.c.put(f"/api/participants/{got['乙']['id']}/tier", headers=org.h(), json={"tier": "auto"})
    assert people(org, pid)["乙"]["tier_manual"] is False
    assert org.c.put(f"/api/participants/{got['乙']['id']}/tier", headers=org.h(), json={"tier": "maybe"}).status_code == 422


def test_without_quotas_everybody_is_formal(client, org):
    pid = org.project()
    add(org, pid, "甲")
    assert people(org, pid)["甲"]["tier"] == "formal"


def test_interview_kind_tells_human_from_ai(client, org):
    pid = org.project()
    add(org, pid, "没邀请的")
    assert people(org, pid)["没邀请的"]["interview_kind"] == ""


def test_the_tag_library_adds_removes_and_cleans_people(client, org):
    pid, tok = publish(org)
    run(client, tok, {"age": 30, "city": "gba"}, {"name": "林小雨", "email": "lin@example.com", "country": "SG"})  # gets the tag 大湾区 from its option
    lib = {t["tag"]: t["n"] for t in org.c.get(f"/api/projects/{pid}/recruiting/tags", headers=org.h()).json()["tags"]}
    assert lib["大湾区"] == 1 and "非大湾区" in lib  # every tag of the questionnaire, with how many people have it
    r = org.c.post(f"/api/projects/{pid}/recruiting/tags", headers=org.h(), json={"tag": "老客户"})
    assert r.status_code == 201 and "老客户" in [t["tag"] for t in r.json()["tags"]]
    r = org.c.delete(f"/api/projects/{pid}/recruiting/tags", headers=org.h(), params={"tag": "大湾区"})
    assert "大湾区" not in [t["tag"] for t in r.json()["tags"]]
    assert people(org, pid)["林小雨"]["tags"] == []  # taken off the person too
    org.c.post(f"/api/projects/{pid}/recruiting/tags", headers=org.h(), json={"tag": "大湾区"})
    assert "大湾区" in [t["tag"] for t in org.c.get(f"/api/projects/{pid}/recruiting/tags", headers=org.h()).json()["tags"]]  # adding it back un-hides it


def test_erasing_several_people_at_once_is_logged_and_for_staff_only(client, org):
    pid = org.project()
    for name in ("甲", "乙", "丙"):
        add(org, pid, name)
    got = people(org, pid)
    ids = [got["甲"]["id"], got["乙"]["id"]]
    assert org.c.post(f"/api/projects/{pid}/participants/delete", headers=org.h("observer"), json={"ids": ids}).status_code == 403
    r = org.c.post(f"/api/projects/{pid}/participants/delete", headers=org.h(), json={"ids": ids})
    assert r.status_code == 200 and r.json()["erased"] == 2
    left = {x["id"]: x for x in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()}
    assert left[ids[0]]["anonymized"] and left[ids[1]]["anonymized"] and not left[got["丙"]["id"]]["anonymized"]
    assert org.c.post(f"/api/projects/{pid}/participants/delete", headers=org.h(), json={"ids": ids}).status_code == 404  # already gone
    with SessionLocal() as db:
        logged = db.scalars(select(AuditLog).where(AuditLog.action == "participant.bulk_erase")).all()
    assert logged and logged[-1].meta["people"] == 2


def test_the_interview_list_carries_the_moderator_name_and_the_participant_tags_for_the_team_only(client, org):
    pid = org.project()
    added = add(org, pid, "甲", tags=["GBA"])
    org.c.put(f"/api/participants/{added['id']}/tags", headers=org.h(), json={"tags": ["GBA"]})
    me = org.c.get("/api/me", headers=org.h()).json()
    org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": added["id"], "title": "房间", "moderator_id": me["id"]})
    row = org.c.get(f"/api/projects/{pid}/interviews", headers=org.h()).json()[0]
    assert row["moderator_name"] == me["name"] and row["participant"]["tags"] == ["GBA"]
    seen = org.c.get(f"/api/projects/{pid}/interviews", headers=org.h("observer")).json()
    assert seen == [] or (seen[0]["moderator_name"] == "" and "tags" not in seen[0]["participant"])  # a client observer sees neither the team's names nor the tags
