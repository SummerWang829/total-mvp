"""The 访谈 page's interview outline: saved per project, checked while editing, changed in a conversation with the AI; staff only, other companies cannot see it."""
from conftest import Org

TEXT = """目标: 了解新加坡年轻人如何选购防晒霜
受访者话题: 防晒霜选购
语言: zh
时长: 30
## 暖场
- 先简单介绍一下你自己和平时防晒的习惯？
## 话题: 选购决策
- 你上一次买防晒霜是怎么选的？
* 了解: 在哪里获得信息
"""


def test_empty_outline_then_save_and_read(org):
    pid = org.project()
    r = org.c.get(f"/api/projects/{pid}/outline", headers=org.h())
    assert r.status_code == 200 and r.json()["text"] == "" and r.json()["summary"] is None and r.json()["chat"] == []
    r = org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": TEXT})
    assert r.status_code == 200 and r.json()["problem"] == ""
    s = r.json()["summary"]
    assert s["minutes"] == 30 and s["question_count"] == 2 and [p["title"] for p in s["parts"]] == ["开场与热身", "选购决策"]
    assert org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["text"] == TEXT


def test_a_broken_edit_is_kept_and_explained(org):
    pid = org.project()
    r = org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": "- 没有放在任何一段里的问题？"})
    assert r.status_code == 200 and r.json()["problem"] and r.json()["summary"] is None
    assert org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["text"].startswith("- 没有")


def test_chat_drafts_when_empty_and_keeps_the_conversation(org):
    pid = org.project()
    r = org.c.post(f"/api/projects/{pid}/outline/chat", headers=org.h(), json={"message": "帮我起草一份 45 分钟的大纲"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["summary"] and d["summary"]["question_count"] >= 3 and d["problem"] == ""
    assert [m["role"] for m in d["chat"]] == ["user", "ai"] and d["chat"][0]["text"].startswith("帮我起草")
    r2 = org.c.post(f"/api/projects/{pid}/outline/chat", headers=org.h(), json={"message": "再加一个追问", "use_library": False})
    assert r2.status_code == 200 and r2.json()["text"] == d["text"] and len(r2.json()["chat"]) == 4  # the offline mock never rewrites an existing outline


def test_staff_only_and_other_companies_cannot_see_it(org):
    pid = org.project()
    for role in ("moderator", "observer"):
        assert org.c.get(f"/api/projects/{pid}/outline", headers=org.h(role)).status_code in (401, 403, 404)
    other = Org(org.c, "Other", "boss@other.test")
    assert org.c.get(f"/api/projects/{pid}/outline", headers=other.h()).status_code == 404
    assert org.c.put(f"/api/projects/{pid}/outline", headers=other.h(), json={"text": "x"}).status_code == 404


def test_edit_a_question_add_delete_and_rename(org):
    pid = org.project()
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": TEXT})
    url = f"/api/projects/{pid}/outline/edit"
    r = org.c.post(url, headers=org.h(), json={"op": "set", "kind": "topic", "ref": 0, "index": 0, "value": "最近一次买防晒霜，你是怎么挑的？"})
    assert r.status_code == 200 and "最近一次买防晒霜" in r.json()["text"] and r.json()["problem"] == ""
    r = org.c.post(url, headers=org.h(), json={"op": "add", "kind": "topic", "ref": 0, "value": "用了之后感觉怎么样？"})
    assert r.json()["summary"]["question_count"] == 3
    r = org.c.post(url, headers=org.h(), json={"op": "rename", "kind": "topic", "ref": 0, "value": "选购过程"})
    assert r.json()["summary"]["parts"][1]["title"] == "选购过程"
    r = org.c.post(url, headers=org.h(), json={"op": "add_part", "value": "使用感受|用了一段时间后，有什么变化？"})
    assert [p["title"] for p in r.json()["summary"]["parts"]][-1] == "使用感受"
    r = org.c.post(url, headers=org.h(), json={"op": "del_part", "kind": "topic", "ref": 1})
    assert [p["title"] for p in r.json()["summary"]["parts"]] == ["开场与热身", "选购过程"]
    assert org.c.post(url, headers=org.h(), json={"op": "set", "kind": "topic", "ref": 9, "index": 0, "value": "x？"}).status_code == 422
    assert org.c.post(url, headers=org.h(), json={"op": "set", "kind": "topic", "ref": 0, "index": 0, "value": " "}).status_code == 422


def test_edit_refuses_while_the_text_is_broken(org):
    pid = org.project()
    org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": "- 没有放在任何一段里的问题？"})
    r = org.c.post(f"/api/projects/{pid}/outline/edit", headers=org.h(), json={"op": "delete", "kind": "warmup", "index": 0})
    assert r.status_code == 422 and "先把文字里的问题改好" in r.text
