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


# ---------- question cards: copy, insert above / below, drag to reorder ----------
DRAFT = chr(10).join(["目标: 了解购物习惯", "受访者话题: 购物习惯", "语言: zh", "时长: 45", "", "## 暖场", "- 先简单介绍一下你自己吧？", "", "## 话题: 现状与习惯", "- 你最近一次遇到这类需求时，是怎么做的？",
                     "* 了解: 现状", "", "## 话题: 选择与考虑", "- 当时你是怎么做出选择的？", "* 了解: 选择", "", "## 话题: 困扰与期待", "- 这个过程中有没有让你觉得麻烦的地方？", "* 了解: 困扰", ""])


def draft(org, pid):
    """A small saved outline: a warm-up and three topics of one question each."""
    assert org.c.put(f"/api/projects/{pid}/outline", headers=org.h(), json={"text": DRAFT}).status_code == 200


def _q(org, pid, **b):
    r = org.c.post(f"/api/projects/{pid}/outline/edit", headers=org.h(), json=b)
    return r


def test_copy_insert_and_move_keep_each_questions_depth_and_guidance(org):
    pid = org.project()
    draft(org, pid)
    topic = 0
    for text in ("第二个问题？", "第三个问题？", "第四个问题？"):
        assert _q(org, pid, op="add", kind="topic", ref=topic, value=text).status_code == 200
    parts = org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["summary"]["parts"]
    first = [q for q in next(x for x in parts if x["kind"] == "topic" and x["ref"] == topic)["questions"] if not q.get("cover")]
    assert _q(org, pid, op="meta", kind="topic", ref=topic, index=0, value="deep|看他怎么选").status_code == 200
    r = _q(org, pid, op="dup", kind="topic", ref=topic, index=0).json()
    qs = [q for q in next(x for x in r["summary"]["parts"] if x["kind"] == "topic" and x["ref"] == topic)["questions"] if not q.get("cover")]
    assert len(qs) == len(first) + 1 and qs[0]["text"] == qs[1]["text"] and qs[1]["depth"] == "deep" and qs[1]["guide"] == "看他怎么选"
    r = _q(org, pid, op="insert", kind="topic", ref=topic, index=0, value="最先问的一个新问题？").json()
    qs = [q for q in next(x for x in r["summary"]["parts"] if x["kind"] == "topic" and x["ref"] == topic)["questions"] if not q.get("cover")]
    assert qs[0]["text"] == "最先问的一个新问题？" and qs[0]["depth"] == "shallow" and qs[1]["depth"] == "deep"
    r = _q(org, pid, op="move", kind="topic", ref=topic, index=0, value=f"topic:{topic}:3").json()   # drop it before the 4th question (the list was 0-based, the first one leaves)
    qs = [q for q in next(x for x in r["summary"]["parts"] if x["kind"] == "topic" and x["ref"] == topic)["questions"] if not q.get("cover")]
    assert qs[2]["text"] == "最先问的一个新问题？" and qs[0]["depth"] == "deep"
    assert _q(org, pid, op="move", kind="topic", ref=topic, index=0, value="topic:99:0").status_code == 422
    assert _q(org, pid, op="insert", kind="topic", ref=topic, index=0, value=" ").status_code == 422


def test_a_question_can_be_dragged_into_another_part_and_an_emptied_part_goes(org):
    pid = org.project()
    draft(org, pid)
    parts = org.c.get(f"/api/projects/{pid}/outline", headers=org.h()).json()["summary"]["parts"]
    n = sum(len([q for q in x["questions"] if not q.get("cover")]) for x in parts)
    r = _q(org, pid, op="move", kind="topic", ref=0, index=0, value="warmup:0:1")
    assert r.status_code == 200, r.text
    assert _q(org, pid, op="move", kind="warmup", ref=0, index=0, value="warmup:0:0").status_code == 200
    assert sum(len([q for q in x["questions"] if not q.get("cover")]) for x in r.json()["summary"]["parts"]) == n
