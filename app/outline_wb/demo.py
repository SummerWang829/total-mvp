"""Made-up demo data for the outline workbench (stage 2 builds the pages on it): "A 品牌进入新加坡" with a full outline (concept card, video, website task, rating),
two pilot transcripts with question numbers and AI host notes, focus questions and coded answers. Nothing here is real: names and sentences are invented.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CaptionLine, Interview, Participant, Project, utcnow
from . import service as svc
from .models import Outline, StudySetting

DEMO_NAME = "A 品牌进入新加坡（演示）"
# (module, kind, label, zh, en, minutes, must_cover, config)
ITEMS = [
    ("暖场", "question", "H1", "先聊聊你平时是怎么逛街、买日常用品的？", "Tell me how you usually shop for everyday items.", 3, [], {}),
    ("核心", "question", "Q1", "最近一次买护肤品是什么时候？当时为什么选它？", "When did you last buy skincare, and why that one?", 5, ["购买原因", "购买渠道"], {}),
    ("核心", "concept", "C1", "现在给你看一张产品概念卡，请先看一下。", "Here is a product concept card; please take a look.", 3, [], {"title": "概念 A：温和修护精华", "watermark": True, "no_download": True}),
    ("核心", "question", "Q2", "看完之后，你第一反应是什么？为什么？", "What is your first reaction, and why?", 4, ["第一印象", "理由"], {}),
    ("核心", "rating", "R1", "如果满分是十分，你有多想试用？", "On a scale of 1-10, how much would you like to try it?", 2, [], {"scale": 10}),
    ("品牌", "video", "V1", "接下来看一段 30 秒的品牌短片。", "Next, a 30-second brand film.", 3, [], {"title": "品牌短片", "from_s": 0, "to_s": 30, "no_download": True}),
    ("品牌", "website", "W1", "请打开官网，找到你会怎么买这款产品。", "Open the website and show how you would buy this product.", 6, ["购买路径"], {"url": "https://example.com/shop", "time_limit_s": 240, "show": "new_tab_share_screen"}),
    ("收尾", "question", "Z1", "还有什么想补充的吗？", "Is there anything you would like to add?", 2, [], {}),
]
SLOTS = {"品类": ("护肤", "text", 0.95), "市场": ("新加坡", "project", 1.0), "目标人群": ("25-40 岁都市女性", "inferred", 0.6), "时长": ("45 分钟", "default", 0.5)}
LINES = [   # (speaker, text, item label, source)
    ("moderator", "最近一次买护肤品是什么时候？当时为什么选它？", "Q1", "ai"),
    ("participant", "上个月，朋友推荐的一款精华，我主要是看成分。", "Q1", "ai"),
    ("moderator", "看完之后，你第一反应是什么？为什么？", "Q2", "ai"),
    ("participant", "我觉得价格可能偏贵，但包装很好看。", "Q2", "ai"),
]


def build(db: Session, project_id: str, user_id: str = "") -> dict:
    """Create the demo in `project_id`. Returns what was made; does nothing (returns {}) when the demo is already there. The caller commits."""
    proj = db.get(Project, project_id)
    if proj is None:
        raise svc.OutlineError("项目不存在", 404)
    if db.scalar(select(Outline).where(Outline.project_id == project_id, Outline.name == DEMO_NAME)):
        return {}
    o, v = svc.create_outline(db, proj.org_id, proj.id, DEMO_NAME, user_id)
    by_label = {}
    for mod, kind, label, zh, en, minutes, must, cfg in ITEMS:
        by_label[label] = svc.add_item(db, v, label=label, module=mod, kind=kind, texts={"zh": zh, "en": en}, minutes=minutes, must_cover=must, config=cfg,
                                       key_question="护肤品选购的决定因素" if label in ("Q1", "Q2") else "")
    db.add(StudySetting(org_id=proj.org_id, outline_id=o.id, background="A 品牌准备进入新加坡市场，想了解当地都市女性怎么选护肤品。",
                        slots={k: {"value": val, "source": src, "confidence": conf} for k, (val, src, conf) in SLOTS.items()},
                        decisions=["是否进入新加坡市场"], key_questions=["护肤品选购的决定因素"], hypotheses=["价格是主要顾虑"],
                        rounds={"goal": {"status": "done"}, "audience": {"status": "done"}, "execution": {"status": "doing"}}, gates={"goal": True, "structure": False, "final": False}))
    focus = svc.add_focus(db, proj.org_id, proj.id, "她们买护肤品最看重什么？", user_id, "viewer", item_uid=by_label["Q1"].item_uid)
    made = {"outline": o.id, "version": v.id, "interviews": []}
    for n in (1, 2):
        part = Participant(org_id=proj.org_id, project_id=proj.id, name=f"演示受访者 {n}", country="SG", language="zh", source="manual", code=f"M-9{n:02d}")
        db.add(part)
        db.flush()
        itv = Interview(org_id=proj.org_id, project_id=proj.id, participant_id=part.id, title=f"Pilot {n}", language="zh", status="ended", moderation="ai", is_pilot=True,
                        invite_hash=f"demo-{o.id}-{n}", started_at=utcnow(), ended_at=utcnow(), duration_min=45)
        db.add(itv)
        db.flush()
        svc.start_interview(db, itv, v, goal="了解护肤品选购", topic="护肤品")
        last = None
        for seq, (speaker, text, label, source) in enumerate(LINES, 1):
            line = CaptionLine(org_id=proj.org_id, interview_id=itv.id, seq=seq, speaker=speaker, text=text, at_ms=seq * 20000, lang="zh")
            db.add(line)
            db.flush()
            svc.assign_line(line, by_label[label].item_uid, source)
            if speaker == "moderator":
                svc.add_decision(db, proj.org_id, itv.id, line.id, "next_question", "上一题已问透，进入下一题", 0.8, [])
            elif label == "Q1":
                svc.add_coded(db, proj.org_id, focus.id, itv.id, line.id, "朋友推荐", 0.9)
            elif label == "Q2":   # said while another question was asked: counted apart from the answers to the focus question itself
                svc.add_coded(db, proj.org_id, focus.id, itv.id, line.id, "价格", 0.7, origin="volunteered", mentioned_in_item_uid=by_label["Q2"].item_uid)
            last = line
        if n == 1:
            svc.add_mark(db, proj.org_id, itv.id, last, 0, 6, "unclear", "这里受访者没听懂“偏贵”指什么", user_id, "viewer")
        made["interviews"].append(itv.id)
    return made
