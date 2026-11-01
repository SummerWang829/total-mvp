"""The research methods a project can use, and its star map (one node per method with its progress and next step).

A project turns methods on when it is made (the wizard) and can change them later; the menu and the project home show only those. Data of a method that
was switched off is kept, it is just not shown.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AiSession, AsyncStudy, Interview, ListeningSource, Mention, Project, QuantSurvey

METHODS = ("depth", "group", "async", "listening", "quant")
LABELS = {"depth": "深访（一对一）", "group": "座谈会", "async": "异步 AI 研究", "listening": "舆情监测", "quant": "定量结果"}
BLURBS = {"depth": "和受访者一对一视频访谈，可以由真人或 AI 主持，实时字幕、录制、转写、洞察。",
          "group": "最多 10 人的座谈会，发言公平仪表盘和副主持提示卡。",
          "async": "受访者随时自己和 AI 访谈员完成一场文字访谈，一条公开链接就能招人。",
          "listening": "采集新闻、App 评论和公开网页上的声音，自动打情感和话题标签。",
          "quant": "接入外部问卷平台：推送通过甄别的人和问卷结果，和访谈放在一起看。"}
DEFAULT = ("depth", "group", "async", "listening")  # what a project made without choosing gets, and what older projects have (quant is opt-in)


class MethodsError(ValueError):
    """The chosen methods are not usable (none, or one that does not exist)."""


def clean(raw) -> list[str]:
    """The methods in the standard order, without repeats.

    Raises:
        MethodsError: nothing chosen, or an unknown method.
    """
    chosen = list(dict.fromkeys(str(m) for m in (raw or [])))
    if not chosen:
        raise MethodsError("请至少选择一种研究方法")
    if any(m not in METHODS for m in chosen):
        raise MethodsError("有不认识的研究方法")
    return [m for m in METHODS if m in chosen]


def of(project: Project) -> list[str]:
    """The methods this project uses (all of them for a project made before this existed)."""
    got = [m for m in (project.methods or "").split(",") if m in METHODS]
    return got or list(DEFAULT)


def store(methods: list[str]) -> str:
    return ",".join(methods)


def starmap(db: Session, project: Project) -> list[dict]:
    """One node per enabled method: a big number, one line of context, the page to open, and whether anything has started."""
    its = db.scalars(select(Interview).where(Interview.project_id == project.id)).all()
    nodes = []
    for m in of(project):
        if m in ("depth", "group"):
            mine = [i for i in its if (i.kind == "group") == (m == "group") and i.kind != "async"]
            done = sum(i.status == "ended" for i in mine)
            live = sum(i.status == "live" for i in mine)
            nodes.append({"key": m, "title": LABELS[m], "big": done, "unit": "场已完成", "sub": f"共 {len(mine)} 场" + (f"，{live} 场进行中" if live else ""),
                          "page": "rooms" if mine else "people", "started": bool(mine),
                          "next": "在名单里新建场次并发出邀请" if not mine else "继续推进场次、看洞察" if done else "等待访谈开始"})
        elif m == "async":
            studies = db.scalars(select(AsyncStudy).where(AsyncStudy.project_id == project.id)).all()
            mine = [i for i in its if i.kind == "async"]
            sess = {s.interview_id: s for s in db.scalars(select(AiSession).where(AiSession.interview_id.in_([i.id for i in mine]))).all()} if mine else {}
            finished = sum(1 for i in mine if sess.get(i.id) and sess[i.id].finished)
            nodes.append({"key": m, "title": LABELS[m], "big": finished, "unit": "人已完成", "sub": f"{len(studies)} 项研究，{len(mine)} 人受邀" if studies else "还没有研究",
                          "page": "studies", "started": bool(studies), "next": "写研究目标，起草提纲" if not studies else "发布并邀请受访者" if not mine else "看进度和转写"})
        elif m == "listening":
            sources = db.scalar(select(func.count()).select_from(ListeningSource).where(ListeningSource.project_id == project.id)) or 0
            total = db.scalar(select(func.count()).select_from(Mention).where(Mention.project_id == project.id)) or 0
            neg = db.scalar(select(func.count()).select_from(Mention).where(Mention.project_id == project.id, Mention.sentiment == "neg")) or 0
            nodes.append({"key": m, "title": LABELS[m], "big": total, "unit": "条内容", "sub": (f"{sources} 个来源，负面 {round(neg / total * 100)}%" if total else f"{sources} 个来源"),
                          "page": "listening", "started": bool(sources), "next": "添加第一个来源" if not sources else "运行来源，看看大家在说什么" if not total else "看话题和负面内容"})
        elif m == "quant":
            rows = db.scalars(select(QuantSurvey).where(QuantSurvey.project_id == project.id)).all()
            nodes.append({"key": m, "title": LABELS[m], "big": len(rows), "unit": "份问卷结果", "sub": (f"共 {sum(r.respondents for r in rows)} 份有效回答" if rows else "还没有数据"),
                          "page": "quant", "started": bool(rows), "next": "创建密钥，让问卷平台推送，或导入结果表格" if not rows else "把结果和访谈洞察对照着看"})
    return nodes
