"""Text outline -> AI moderator guide (the room page's upload), and the AI putting the material it asks about on the participant's screen."""
from types import SimpleNamespace

import pytest

from app import ai_room, guide_text
from app.db import SessionLocal
from app.models import Interview, RoomState, Stimulus
from app.moderator.knowledge import InterviewGuide
from conftest import Org
from test_ai_room import _walk_into_the_room

OUTLINE = """目标: 了解新加坡消费者如何选购护肤品
受访者话题: 护肤品选购
语言: zh
时长: 30
## 暖场
- 先聊聊你平时的护肤习惯？
## 话题: 选购决策
- 你上一次买护肤品是怎么选的？
* 了解: 在哪里获得信息
* 价格怎么影响选择
## 素材: ad_film | 新品广告片
- 看完这支广告，你的第一反应是什么？
- 哪句话最让你记住？
## 素材: concept_a | 概念卡 A
- 这张卡片你怎么理解？
## 禁谈
- 政治
"""


def test_outline_becomes_a_valid_guide():
    g = guide_text.parse_guide_text(OUTLINE)
    guide = InterviewGuide.model_validate(g)
    assert guide.language == "zh" and guide.duration_min == 30 and guide.participant_topic == "护肤品选购"
    assert guide.topics[0].title == "选购决策" and [i.text for i in guide.topics[0].must_cover] == ["在哪里获得信息", "价格怎么影响选择"]
    assert [s.id for s in guide.stimuli] == ["ad_film", "concept_a"] and len(guide.stimuli[0].questions) == 2
    assert guide.forbidden_topics == ["政治"]


@pytest.mark.parametrize("bad,why", [
    ("- 一个问题\n", "不属于任何小节"),
    ("目标: x\n## 乱写\n", "不认识的小节"),
    ("目标: x\n## 暖场\n- a\n## 素材:\n", "素材要写成"),
    ("目标: x\n时长: abc\n## 暖场\n- a\n## 话题: t\n- q\n", "时长"),
    ("目标: x\n## 暖场\n- a\n", "提纲不完整"),  # no topic
    ("目标: x\n随便一句话\n", "看不懂"),
])
def test_bad_outlines_are_explained(bad, why):
    with pytest.raises(guide_text.GuideTextError, match=why):
        guide_text.parse_guide_text(bad)


def test_outline_size_limit():
    with pytest.raises(guide_text.GuideTextError, match="太长"):
        guide_text.parse_guide_text("x" * (guide_text.MAX_CHARS + 1))


def test_check_endpoint_and_permissions(org):
    itv = org.interview(moderation="ai")
    url = f"/api/interviews/{itv['id']}/ai/guide/check"
    r = org.c.post(url, headers=org.h("moderator"), json={"text": OUTLINE})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == {"topics": 1, "questions": 1, "stimuli": 2} and body["missing_stimuli"] == ["ad_film", "concept_a"]
    assert org.c.post(url, headers=org.h("moderator"), json={"text": "- x"}).status_code == 422
    assert org.c.post(url, headers=org.h("observer"), json={"text": OUTLINE}).status_code == 403
    other = Org(org.c, "B", "b@b.com")
    assert org.c.post(url, headers=other.h(), json={"text": OUTLINE}).status_code == 404


def test_a_guide_from_an_outline_can_start_the_ai(org):
    itv = _walk_into_the_room(org)
    g = org.c.post(f"/api/interviews/{itv['id']}/ai/guide/check", headers=org.h("moderator"), json={"text": OUTLINE}).json()["guide"]
    r = org.c.post(f"/api/interviews/{itv['id']}/ai/start", headers=org.h("moderator"), json={"guide": g})
    assert r.status_code == 200, r.text


def test_stimulus_follows_the_ai_question(org):
    itv = _walk_into_the_room(org)
    with SessionLocal() as db:
        row = Stimulus(org_id=itv["org_id"] if "org_id" in itv else db.get(Interview, itv["id"]).org_id, interview_id=itv["id"], key="ad_film", kind="video", title="广告", url="https://player.bilibili.com/x")
        db.add(row)
        db.commit()
        sid, it = row.id, db.get(Interview, itv["id"])
        fake = lambda want: SimpleNamespace(fsm=SimpleNamespace(current_stimulus_id=want))
        sync = lambda want: (ai_room._sync_stimulus(db, it, fake(want)), db.commit())  # the caller commits
        sync("ad_film")
        assert db.get(RoomState, itv["id"]).stimulus_id == sid
        sync("ad_film")  # unchanged
        sync(None)  # left the stimuli phase: off the screen again
        assert db.get(RoomState, itv["id"]).stimulus_id is None
        sync("not_installed")  # unknown material: nothing happens
        assert db.get(RoomState, itv["id"]).stimulus_id is None
        # something staff put up by hand is not taken away by the AI
        st = db.get(RoomState, itv["id"])
        st.stimulus_id, st.shown_by = sid, "user:abc"
        db.commit()
        sync(None)
        assert db.get(RoomState, itv["id"]).stimulus_id == sid


def test_engine_reports_the_current_material():
    g = InterviewGuide.model_validate(guide_text.parse_guide_text(OUTLINE))
    from app.moderator.fsm import Phase
    assert Phase.STIMULI  # the property exists on the real state machine:
    from app.moderator.fsm import InterviewStateMachine as M
    assert isinstance(M.current_stimulus_id, property)
