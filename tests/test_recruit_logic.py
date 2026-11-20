"""修正方案第 4 轮 (用户 2026-11-20: 像 SurveyMonkey / Qualtrics 一样): display logic on questions and options, options that follow an earlier answer,
shuffled options with fixed ones, piped answers — all decided on the server; a candidate never sees or may pick a hidden option."""
import pytest

from app import recruit_form as rf


def _form():
    city = {"id": "q1", "type": "single", "text": "您常住哪个城市？", "options": [{"id": "bj", "text": "北京"}, {"id": "sh", "text": "上海"}, {"id": "ot", "text": "其他", "goto": "end_out"}]}
    dist = {"id": "q2", "type": "single", "text": "您住在{{q1}}的哪个区？", "options": [
        {"id": "cy", "text": "朝阳区", "show_if": {"conds": [{"q": "q1", "op": "any", "opts": ["bj"]}]}},
        {"id": "hd", "text": "海淀区", "show_if": {"conds": [{"q": "q1", "op": "any", "opts": ["bj"]}]}},
        {"id": "pd", "text": "浦东新区", "show_if": {"conds": [{"q": "q1", "op": "any", "opts": ["sh"]}]}}]}
    car = {"id": "q3", "type": "multi", "text": "您家有哪些车？", "randomize": True, "options": [{"id": f"c{i}", "text": f"品牌{i}"} for i in range(1, 9)] + [{"id": "none", "text": "以上都没有", "fixed": True}]}
    ev = {"id": "q4", "type": "single", "text": "电动车多久充一次电？", "show_if": {"conds": [{"q": "q3", "op": "any", "opts": ["c1", "c2"]}]}, "options": [{"id": "d", "text": "每天"}, {"id": "w", "text": "每周"}]}
    return rf.normalize({"source": "ai", "questions": [city, dist, car, ev]}).questions


def test_options_follow_the_earlier_answer_and_the_answer_is_piped():
    qs = _form()
    s = rf.next_step(qs, {"q1": "sh"})
    assert [o["text"] for o in s.question["options"]] == ["浦东新区"] and s.question["text"] == "您住在上海的哪个区？"
    with pytest.raises(rf.AnswerError):
        rf.next_step(qs, {"q1": "sh", "q2": "cy"})   # a Beijing district is not an answer for someone in Shanghai


def test_a_question_is_asked_only_when_its_condition_holds_and_shuffling_keeps_fixed_options():
    qs = _form()
    s = rf.next_step(qs, {"q1": "bj", "q2": "cy"}, seed="abc")
    texts = [o["text"] for o in s.question["options"]]
    assert texts[-1] == "以上都没有" and sorted(texts[:-1]) == sorted(f"品牌{i}" for i in range(1, 9))
    assert rf.next_step(qs, {"q1": "bj", "q2": "cy"}, seed="abc").question["options"] == s.question["options"]   # one order per candidate
    assert rf.next_step(qs, {"q1": "bj", "q2": "cy", "q3": ["c5"]}).result == "qualified"   # q4 is skipped for them
    assert rf.next_step(qs, {"q1": "bj", "q2": "cy", "q3": ["c1"]}).question["id"] == "q4"


def test_logic_may_only_look_back_at_options_that_exist():
    qs = [q.model_dump() for q in _form()]
    qs[1]["options"][0]["show_if"] = {"conds": [{"q": "q3", "op": "any", "opts": ["c1"]}]}   # q3 comes later
    with pytest.raises(rf.FormError, match="前面"):
        rf.normalize({"source": "ai", "questions": qs})
    qs = [q.model_dump() for q in _form()]
    qs[3]["show_if"] = {"conds": [{"q": "q1", "op": "any", "opts": ["xx"]}]}
    with pytest.raises(rf.FormError, match="没有的选项"):
        rf.normalize({"source": "ai", "questions": qs})


def test_many_options_are_allowed():
    q = {"id": "q1", "type": "single", "text": "区？", "options": [{"text": f"区{i}"} for i in range(86)]}
    assert len(rf.normalize({"source": "ai", "questions": [q]}).questions[0].options) == 86


# ---- the editor (app/recruit_logic_ai.py) with a scripted model ----
from app import recruit_logic_ai as ed  # noqa: E402
from app.moderator.providers import MockModeratorChat as ScriptedModeratorChat  # noqa: E402

BASE = [{"id": "q1", "type": "single", "text": "您常住哪个城市？", "options": [{"id": "o1", "text": "北京"}, {"id": "o2", "text": "上海"}]},
        {"id": "q2", "type": "single", "text": "您的年龄是？", "options": [{"id": "o1", "text": "18-34岁"}, {"id": "o2", "text": "35岁及以上"}]}]


def test_districts_follow_the_city_through_option_groups():
    reply = {"reply": "已改好 Q2：按 Q1 的城市出区", "questions": [{"id": "q1", "keep": True}, {"id": "q3", "type": "single", "text": "您住在哪个区？",
             "option_groups": {"q": "q1", "groups": {"o1": [f"北京区{i}" for i in range(16)], "o2": [f"上海区{i}" for i in range(16)]}, "always": ["以上都不是"]}}, {"id": "q2", "keep": True}]}
    out = ed.edit(ScriptedModeratorChat([reply]), BASE, "第2题按第1题的城市出区", "q2", "zh", [], "")
    assert out["changed"] and [q["id"] for q in out["questions"]] == ["q1", "q3", "q2"]
    qs = rf.normalize({"source": "ai", "questions": out["questions"]}).questions
    assert len(rf.next_step(qs, {"q1": "o2"}).question["options"]) == 17   # 16 Shanghai districts + 以上都不是


def test_a_bad_answer_is_sent_back_once_with_the_reason_in_chinese():
    bad = {"reply": "x", "questions": [{"id": "q1", "keep": True}, {"id": "q2", "type": "single", "text": "Age?", "options": [{"text": "a"}, {"text": "b"}]}]}
    good = {"reply": "已改", "questions": [{"id": "q2", "keep": True}, {"id": "q1", "keep": True}]}
    chat = ScriptedModeratorChat([bad, good])
    out = ed.edit(chat, BASE, "年龄挪到最前", "q2", "zh", [], "")
    assert out["changed"] and [q["id"] for q in out["questions"]] == ["q2", "q1"]
    assert "问卷的语言" in chat.calls[1][-1]["content"]   # the model was told why, in Chinese
    with __import__("pytest").raises(ed.EditError) as e:
        ed.edit(ScriptedModeratorChat([bad, bad]), BASE, "年龄挪到最前", "q2", "zh", [], "")
    assert "List should" not in str(e.value)


def test_asking_who_it_is_or_a_plain_question_changes_nothing():
    assert ed.edit(ScriptedModeratorChat([]), BASE, "你是什么模型", "q1", "zh", [], "")["reply"] == "我是小星星助手。"
    out = ed.edit(ScriptedModeratorChat([{"reply": "建议把年龄放前面", "questions": None}]), BASE, "你觉得顺序合理吗", "q1", "zh", [], "")
    assert not out["changed"] and out["reply"] == "建议把年龄放前面"
