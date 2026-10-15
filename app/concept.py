"""Concept-test templates: the moderator's script and the materials that go on the participant's screen.

The materials are fixed here on purpose. Participants' browsers load them (a player page in an iframe, or a picture), so the addresses
must come from this reviewed list - never from a form field.  Videos are the embedded player of a public page, never a file we host.
"""
import json
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Interview, InterviewPlan, RoomState, Stimulus, utcnow

BILIBILI_PLAYER = "https://player.bilibili.com/player.html?bvid={bv}&page=1&high_quality=1&danmaku=0&autoplay=0"
VIDEO_NOTE = "B 站网友转载的电视广告片（不是欧莱雅官方上传），仅供内部测试；正式使用前请换成官方素材或取得授权。"
CARD_NOTE = "虚拟概念卡，由本平台生成，灵感来自巴黎欧莱雅玻尿酸系列，不代表真实产品信息。"

LOREAL_SKINCARE = {
    "key": "loreal_skincare_concept",
    "title": "欧莱雅护肤概念测试（10 分钟）",
    "language": "zh",
    "duration_min": 10,
    "research_goal": "了解目标消费者对一支欧莱雅玻尿酸安瓶类护肤广告片和两个虚拟护肤概念的第一反应：看懂了什么、信不信、适不适合自己、愿不愿意买。",
    "stimuli": [
        {"key": "ad_film", "kind": "video", "title": "广告片：敷颜玻尿酸七天安瓶精华 · 迪丽热巴篇（2018，15 秒）",
         "url": BILIBILI_PLAYER.format(bv="BV1rea6e9EqZ"), "source_note": VIDEO_NOTE},
        {"key": "concept_a", "kind": "image", "title": "概念 A：7 夜安瓶精华", "url": "/concepts/concept_a.svg", "source_note": CARD_NOTE},
        {"key": "concept_b", "kind": "image", "title": "概念 B：即时水光精华", "url": "/concepts/concept_b.svg", "source_note": CARD_NOTE},
        {"key": "ad_film_2", "kind": "video", "title": "备用广告片：复颜玻尿酸充盈导入 · 范冰冰篇（2016，16 秒）",
         "url": BILIBILI_PLAYER.format(bv="BV1afsge6EQH"), "source_note": VIDEO_NOTE},
    ],
    "steps": [
        {"id": "warmup", "title": "开场与热身", "minutes": 1, "show": None,
         "say": "谢谢你来。今天大概 10 分钟，我会给你看一支广告和两个产品概念，没有对错，你怎么想就怎么说。",
         "questions": ["先聊聊，你平时的护肤步骤大概是怎样的？", "最近一次买精华或安瓶，是在什么情况下买的？"],
         "probes": ["为什么选那一款？", "当时有没有犹豫？"],
         "must_cover": ["护肤习惯", "最近一次购买场景"]},
        {"id": "ad", "title": "广告片的第一反应", "minutes": 2.5, "show": "ad_film",
         "say": "我现在给你看一支 15 秒的广告，请你点一下播放，看完告诉我你的感受。",
         "questions": ["看完这支广告，你的第一反应是什么？", "你觉得它想告诉你什么？", "哪个画面或哪句话你印象最深？", "它让你更想了解这个产品，还是不太想？为什么？"],
         "probes": ["能具体说说是哪个画面吗？", "“好看”是指什么？", "你说的“有点假”，是哪里让你觉得假？"],
         "must_cover": ["第一反应", "理解的核心信息", "印象最深的点", "兴趣变化及原因"]},
        {"id": "concept_a", "title": "概念 A：7 夜安瓶精华", "minutes": 2.5, "show": "concept_a",
         "say": "接下来是第一个产品概念，请你先自己看 20 秒，不用急着说。",
         "questions": ["用你自己的话讲讲，这个产品是什么、有什么用？", "1 到 5 分，你有多喜欢它？为什么打这个分？",
                       "“7 夜”这个说法，你相信吗？", "它适合你吗？适合你的什么情况？", "这个价格，你觉得合理吗？多少钱你会觉得太贵？"],
         "probes": ["你最在意里面哪一句？", "是什么让你不太信？", "如果要改一个地方，你改什么？"],
         "must_cover": ["理解（复述）", "喜欢度 1–5 及原因", "可信度", "与自己的相关性", "价格感知"]},
        {"id": "concept_b", "title": "概念 B：即时水光精华，并与 A 比较", "minutes": 2, "show": "concept_b",
         "say": "这是第二个概念，同样先看一下。",
         "questions": ["这个概念，你的第一印象是什么？", "和刚才的 A 比，你更喜欢哪一个？为什么？", "它们的差别，你觉得最大的是什么？"],
         "probes": ["“更喜欢”主要是因为哪一点？", "如果只能买一个，你选哪个？"],
         "must_cover": ["第一印象", "A/B 偏好及原因", "差异感知"]},
        {"id": "wrapup", "title": "购买意向与收尾", "minutes": 2, "show": None,
         "say": "最后几个问题。",
         "questions": ["如果它们都在售，1 到 5 分，你一个月内会考虑买更喜欢的那个吗？", "什么情况下你会犹豫、不买？", "用一句话，你会怎么向朋友介绍你更喜欢的那个？", "还有什么想补充的吗？"],
         "probes": ["要是它便宜一点，你的分数会变吗？", "你会去哪里买？"],
         "must_cover": ["购买意向 1–5", "不买的顾虑", "一句话推荐语"]},
    ],
}

TEMPLATES = {LOREAL_SKINCARE["key"]: LOREAL_SKINCARE}


class TemplateError(Exception):
    """The requested template does not exist."""


def at_ms(itv: Interview) -> int:
    """Milliseconds since the interview started, on OUR clock (never taken from a browser); 0 before it starts."""
    if not itv.started_at:
        return 0
    return max(0, int((utcnow() - itv.started_at) / timedelta(milliseconds=1)))


def public_script(template: dict) -> dict:
    """The part of a template that is stored as the interview's script (the stimuli are stored as their own rows)."""
    return {k: template[k] for k in ("key", "title", "language", "duration_min", "research_goal", "steps")}


def install(db: Session, org_id: str, interview_id: str, key: str) -> InterviewPlan:
    """Give an interview the template's script and materials (replacing any earlier ones and clearing the screen).

    Raises:
        TemplateError: the template name is unknown.
    """
    tpl = TEMPLATES.get(key)
    if not tpl:
        raise TemplateError("没有这个模板：" + key)
    db.execute(delete(Stimulus).where(Stimulus.interview_id == interview_id))
    db.execute(delete(RoomState).where(RoomState.interview_id == interview_id))
    db.execute(delete(InterviewPlan).where(InterviewPlan.interview_id == interview_id))
    db.flush()
    for pos, s in enumerate(tpl["stimuli"]):
        db.add(Stimulus(org_id=org_id, interview_id=interview_id, key=s["key"], position=pos, kind=s["kind"], title=s["title"], url=s["url"], source_note=s["source_note"]))
    plan = InterviewPlan(interview_id=interview_id, org_id=org_id, template=key, title=tpl["title"], script_json=json.dumps(public_script(tpl), ensure_ascii=False))
    db.add(plan)
    return plan


def plan_out(db: Session, interview_id: str) -> dict | None:
    plan = db.get(InterviewPlan, interview_id)
    if not plan:
        return None
    return {"template": plan.template, "title": plan.title, "script": json.loads(plan.script_json), "stimuli": stimuli_out(db, interview_id, staff=True)}


def stimuli_out(db: Session, interview_id: str, staff: bool) -> list[dict]:
    rows = db.scalars(select(Stimulus).where(Stimulus.interview_id == interview_id).order_by(Stimulus.position)).all()
    return [stimulus_out(s, staff) for s in rows]


def stimulus_out(s: Stimulus, staff: bool) -> dict:
    """Staff get the title, key and source note; the participant only gets what is needed to draw it (no title: it could name the brand)."""
    if not staff:
        return {"id": s.id, "kind": s.kind, "url": s.url}
    return {"id": s.id, "key": s.key, "kind": s.kind, "title": s.title, "url": s.url, "source_note": s.source_note}


def current_stimulus(db: Session, interview_id: str, staff: bool) -> dict | None:
    """What is on the participant's screen right now (None: nothing)."""
    st = db.get(RoomState, interview_id)
    if not st or not st.stimulus_id:
        return None
    s = db.get(Stimulus, st.stimulus_id)
    return stimulus_out(s, staff) if s and s.interview_id == interview_id else None
