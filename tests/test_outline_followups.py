"""Follow-up depth and interviewer guidance under each outline question: the text format, edits keeping them in step, versions, the AI draft."""
from app import guide_text, outline
from test_outline_versions import TEXT, get, make, pid, questions, url  # noqa: F401 — pid is a fixture

NOTED = TEXT.replace("- 你平时是怎么防晒的？\n", "- 你平时是怎么防晒的？\n  指引: 先听日常做法，不提产品\n").replace("- 当时最看重哪一点？\n", "- 当时最看重哪一点？\n  追问: deep\n  指引: 追问取舍的原因\n")


def metas(text):
    m = guide_text.parse_guide_text(text)["meta"]
    return m["warmup"], m["topics"][0]


def test_text_format_round_trips_and_defaults_stay_unwritten():
    warm, topic = metas(NOTED)
    assert warm == [{"depth": "shallow", "guide": "先听日常做法，不提产品"}]
    assert topic == [{"depth": "shallow", "guide": ""}, {"depth": "deep", "guide": "追问取舍的原因"}]
    again = guide_text.guide_to_text(guide_text.parse_guide_text(NOTED))
    assert metas(again) == (warm, topic) and "追问: shallow" not in again
    assert guide_text.guide_to_text(guide_text.parse_guide_text(TEXT)).count("追问") == 0  # an outline without notes is written exactly as before


def test_summary_carries_depth_and_guidance():
    q = outline.summary_of(NOTED)["parts"][1]["questions"]
    assert q[1]["depth"] == "deep" and q[1]["guide"] == "追问取舍的原因" and q[0]["depth"] == "shallow"


def test_meta_edit_and_structure_edits_keep_notes_in_step():
    t = outline.edit(NOTED, "meta", "topic", 0, 0, "free|多听少问")
    assert metas(t)[1][0] == {"depth": "free", "guide": "多听少问"}
    t = outline.edit(t, "add", "topic", 0, 0, "还有别的吗？")
    assert [m["depth"] for m in metas(t)[1]] == ["free", "deep", "shallow"]
    t = outline.edit(t, "delete", "topic", 0, 0, "")
    assert [m["guide"] for m in metas(t)[1]] == ["追问取舍的原因", ""]
    t = outline.edit(t, "add_part", "topic", 0, 0, "新话题|第一个问题？")
    assert len(guide_text.parse_guide_text(t)["meta"]["topics"]) == 2
    t = outline.edit(t, "del_part", "topic", 0, 0, "")
    assert len(guide_text.parse_guide_text(t)["meta"]["topics"]) == 1
    import pytest
    with pytest.raises(outline.EditError):
        outline.edit(NOTED, "meta", "topic", 0, 0, "bogus|x")


def test_a_bad_depth_line_is_pointed_out():
    bad = TEXT.replace("- 你平时是怎么防晒的？\n", "- 你平时是怎么防晒的？\n  追问: 很多\n")
    assert "追问" in outline.problem_of(bad)


def test_the_edit_api_sets_depth_and_translates_guidance_for_another_language(org, pid):
    make(org, pid, "en|live")
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "meta", "kind": "topic", "ref": 0, "index": 0, "value": "deep|先问最近一次", "sync": ["en|live"]})
    assert r.status_code == 200 and r.json()["sync"]["synced"] == ["en|live"]
    mine = r.json()["summary"]["parts"][1]["questions"][0]
    assert mine["depth"] == "deep" and mine["guide"] == "先问最近一次"
    en = get(org, pid, "en|live")["summary"]["parts"][1]["questions"][0]
    assert en["depth"] == "deep" and en["guide"] == "[EN] 先问最近一次"
    r = org.c.post(url(pid, "/edit"), headers=org.h(), json={"op": "meta", "kind": "topic", "ref": 0, "index": 0, "value": "none|"})  # only this version
    assert get(org, pid, "en|live")["summary"]["parts"][1]["questions"][0]["depth"] == "deep" and r.json()["summary"]["parts"][1]["questions"][0]["depth"] == "none"


def test_a_new_version_carries_the_guidance_and_the_draft_writes_it(org):
    pid = org.project(language="zh")
    r = org.c.post(url(pid, "/chat"), headers=org.h(), json={"message": "帮我起草大纲"})
    assert r.status_code == 200
    qs = [q for p in r.json()["summary"]["parts"] for q in p["questions"] if not q.get("cover")]
    assert qs and all(q["guide"] and q["depth"] == "shallow" for q in qs)  # the offline draft writes guidance for every question


def test_ask_only_answers_without_changing_or_keeping_anything(org, pid):
    before = get(org, pid)
    r = org.c.post(url(pid, "/chat"), headers=org.h(), json={"message": "针对这一处：“你平时是怎么防晒的？”\n这句话好吗？\n请只回答，不要修改大纲。", "ask_only": True})
    assert r.status_code == 200 and r.json()["reply"] and "summary" not in r.json()
    after = get(org, pid)
    assert after["text"] == before["text"] and after["chat"] == before["chat"]  # nothing changed, nothing saved in the conversation


def test_a_long_message_from_several_selected_passages_is_accepted(org, pid):
    msg = "请一次性修改以下 3 处，其余保持不变：\n" + "\n".join(f"{i}. “{'问题' * 40}”——要求：更口语" for i in range(1, 4))
    assert 1000 < len(msg + "x" * 900) <= 4000
    r = org.c.post(url(pid, "/chat"), headers=org.h(), json={"message": msg + "x" * 900})
    assert r.status_code == 200
