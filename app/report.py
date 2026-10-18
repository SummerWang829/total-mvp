"""The project's research report: one Word document that puts everything the project has learned in order.

Sections: summary, background, methods and sample, core findings (insight pool, confirmed findings first, interview quotes word for word), public opinion,
survey results, and the limits of the evidence. Everything comes from what is already in the system: nothing is written by a model, so every number and every
quote can be traced. Participants appear only as P-001 codes, never by name.
"""
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import methods as methods_mod
from . import pool
from .groups import speaker_label
from .models import AiSession, Evidence, Insight, Interview, Mention, Participant, Project, QuantSurvey, TranscriptSegment
from .repo.search import _codes

MAX_FINDINGS = 15
MAX_QUOTES = 3
MAX_EXAMPLES = 3
LIMITS = ["访谈洞察都经过研究员确认，引文逐字来自转写，时间取自转写；样本量小，结论说明方向，不代表全体。",
          "舆情数据来自公开渠道，是公开发言的样本，不代表全体消费者；情感和话题由模型自动标注，可能有误，请以原文为准。",
          "问卷结果由外部问卷平台汇总后推送，星桐没有核对原始作答数据。",
          "“已印证”表示至少两种来源（访谈、舆情、问卷、团队笔记）的主题用词相近，是自动归并的结果，请结合原文判断。"]


def _clock(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _quotes(db: Session, insight_id: str, code: str) -> list[str]:
    """The verbatim quotes of an approved insight, with who said them (a participant code) and when."""
    out = []
    for ev in db.scalars(select(Evidence).where(Evidence.insight_id == insight_id).order_by(Evidence.start_ms)).all()[:MAX_QUOTES]:
        seg = db.get(TranscriptSegment, ev.segment_id) if ev.segment_id else None
        who = speaker_label(seg.speaker) if seg and seg.speaker not in ("participant", "") else code
        out.append(f"“{ev.quote}” —— {who} {_clock(ev.start_ms)}")
    return out


def build(db: Session, project: Project, now: datetime, client: bool = False) -> list[tuple]:
    """The report as blocks (see app/docx_writer.py). For a client (viewer) the team's internal notes are left out."""
    used = methods_mod.of(project)
    its = db.scalars(select(Interview).where(Interview.project_id == project.id, Interview.purged_at.is_(None))).all()
    people = db.scalars(select(Participant).where(Participant.project_id == project.id, Participant.anonymized_at.is_(None))).all()
    codes = _codes(db, [project.id])
    live = [i for i in its if i.kind in ("depth", "group")]
    asyncs = [i for i in its if i.kind == "async"]
    sess = {s.interview_id: s for s in db.scalars(select(AiSession).where(AiSession.interview_id.in_([i.id for i in asyncs]))).all()} if asyncs else {}
    async_done = sum(1 for i in asyncs if sess.get(i.id) and sess[i.id].finished)
    mentions = db.scalars(select(Mention).where(Mention.project_id == project.id)).all()
    surveys = db.scalars(select(QuantSurvey).where(QuantSurvey.project_id == project.id).order_by(QuantSurvey.title)).all()
    items = pool.gather(db, project, include_notes=not client)
    findings = pool.group(items)

    blocks: list[tuple] = [("title", project.name), ("subtitle", f"研究报告 · {now.strftime('%Y-%m-%d')} · {project.market} · {', '.join(methods_mod.LABELS[m] for m in used)}")]

    summary = []
    if live:
        summary.append(f"访谈：共 {len(live)} 场，已结束 {sum(i.status == 'ended' for i in live)} 场。")
    if asyncs:
        summary.append(f"异步 AI 研究：{len(asyncs)} 人受邀，{async_done} 人完成。")
    if mentions:
        neg = sum(m.sentiment == "neg" for m in mentions)
        summary.append(f"舆情：采集公开内容 {len(mentions)} 条，负面占 {round(neg / len(mentions) * 100)}%。")
    if surveys:
        summary.append(f"问卷：{len(surveys)} 份，合计 {sum(s.respondents for s in surveys)} 份有效回答。")
    confirmed = [f for f in findings if len(f.kinds) >= 2]
    if confirmed:
        summary.append("被多种来源印证的发现：" + "；".join(f"{f.theme}（{'、'.join(pool.KIND_LABEL[k] for k in f.kinds)}）" for f in confirmed[:5]) + "。")
    elif findings:
        summary.append(f"共整理出 {len(findings)} 条发现，目前还没有被两种以上来源印证的。")
    blocks += [("h1", "摘要")] + ([("bullet", s) for s in summary] if summary else [("p", "这个项目还没有可汇总的数据。")])

    blocks += [("h1", "研究背景"), ("p", project.brief.strip() or "（项目没有填写研究背景）")]

    rows = []
    for m in used:
        label = methods_mod.LABELS[m]
        if m in ("depth", "group"):
            mine = [i for i in live if (i.kind == "group") == (m == "group")]
            rows.append([label, f"{len(mine)} 场，已结束 {sum(i.status == 'ended' for i in mine)} 场"])
        elif m == "async":
            rows.append([label, f"{len(asyncs)} 人受邀，{async_done} 人完成"])
        elif m == "listening":
            rows.append([label, f"{len(mentions)} 条公开内容"])
        elif m == "quant":
            rows.append([label, f"{len(surveys)} 份问卷，{sum(s.respondents for s in surveys)} 份有效回答"])
    blocks += [("h1", "研究方法与样本"), ("table", ["方法", "规模"], rows)]
    if people:
        c, lg = Counter(p.country or "未知" for p in people), Counter(p.language or "未知" for p in people)
        blocks += [("p", f"受访者共 {len(people)} 人。"), ("table", ["国家 / 地区", "人数"], [[k, str(v)] for k, v in c.most_common()]), ("table", ["语言", "人数"], [[k, str(v)] for k, v in lg.most_common()])]

    blocks.append(("h1", "核心发现"))
    if not findings:
        blocks.append(("p", "还没有发现。通过洞察、运行舆情来源或接入问卷结果后，会出现在这里。"))
    for f in findings[:MAX_FINDINGS]:
        blocks.append(("h2", f"{f.theme}" + ("  【已印证】" if len(f.kinds) >= 2 else "")))
        blocks.append(("note", "来源：" + "、".join(pool.KIND_LABEL[k] for k in f.kinds)))
        for it in f.items:
            blocks.append(("bullet", f"［{pool.KIND_LABEL[it.kind]}］{it.text}"))
            if it.kind == "interview":
                ins = db.get(Insight, it.ref)
                itv = db.get(Interview, it.interview_id) if it.interview_id else None
                code = codes.get(itv.participant_id, "?") if itv else "?"
                blocks += [("quote", q) for q in (_quotes(db, ins.id, code) if ins else [])]
    if len(findings) > MAX_FINDINGS:
        blocks.append(("note", f"另有 {len(findings) - MAX_FINDINGS} 条发现未列出，见洞察池。"))

    if mentions:
        sent = Counter(m.sentiment or "未分析" for m in mentions)
        blocks += [("h1", "舆情摘要"), ("p", f"共 {len(mentions)} 条：负面 {sent['neg']}，中性 {sent['neu']}，正面 {sent['pos']}" + (f"，未分析 {sent['未分析']}" if sent["未分析"] else "") + "。")]
        topics: Counter = Counter()
        neg_by: Counter = Counter()
        for m in mentions:
            for t in m.topics or []:
                topics[t] += 1
                neg_by[t] += m.sentiment == "neg"
        if topics:
            blocks.append(("table", ["话题", "条数", "负面占比"], [[t, str(n), f"{round(neg_by[t] / n * 100)}%"] for t, n in topics.most_common(10)]))
        for label, key in (("典型的负面内容", "neg"), ("典型的正面内容", "pos")):
            ex = [m for m in mentions if m.sentiment == key][:MAX_EXAMPLES]
            if ex:
                blocks.append(("h2", label))
                blocks += [("quote", f"“{m.text[:300]}”（{m.url[:120]}）") for m in ex]

    if surveys:
        blocks.append(("h1", "问卷结果"))
        for s in surveys:
            blocks += [("h2", f"{s.title}（{s.respondents} 份有效回答）")]
            for q in s.questions or []:
                total = sum(r["count"] for r in q.get("results") or []) or 1
                if q.get("results"):
                    blocks.append(("table", [q["text"], "占比", "人数"], [[r["option"], f"{round(r['count'] / total * 100)}%", str(r["count"])] for r in q["results"]]))
                if q.get("mean") is not None:
                    blocks.append(("p", f"{q['text']}：平均 {q['mean']}"))

    blocks += [("h1", "证据的局限")] + [("bullet", s) for s in LIMITS]
    return blocks
