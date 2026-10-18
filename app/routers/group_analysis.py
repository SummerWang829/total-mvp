"""Disagreement map of a focus group (staff of the project). The logic is in app/group_analysis.py."""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import docx_writer, group_analysis, jobs
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, require_roles, visible_interview
from ..models import Claim, PersuasionEvent, utcnow
from ..providers import get_llm

router = APIRouter(prefix="/api", tags=["group-analysis"])


@router.post("/interviews/{iid}/disagreement/analyze")
def analyze(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Read the transcript and (re)build the claims and the changes of mind as suggestions (takes several seconds with a real model)."""
    itv = visible_interview(db, ctx, iid)
    try:
        out = group_analysis.analyze(db, itv, get_llm())
    except group_analysis.AnalysisError as e:
        raise HTTPException(e.status, e.message) from e
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:   # a model failure must not become a stack trace for the researcher
        db.rollback()
        raise HTTPException(502, "AI 暂时无法分析，请稍后再试") from e
    audit(db, ctx.org_id, ctx.actor, "disagreement.analyze", itv.id, **out)
    db.commit()
    return out | group_analysis.view(db, itv)


def _job(db: Session, itv, job) -> dict:
    """The background-job form of the analysis (same work, failures become the job's message)."""
    try:
        out = group_analysis.analyze(db, itv, get_llm())
    except group_analysis.AnalysisError as e:
        raise HTTPException(e.status, e.message) from e
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(502, "AI 暂时无法分析，请稍后再试") from e
    audit(db, itv.org_id, f"job:{job.id}", "disagreement.analyze", itv.id, **out)
    return out


jobs.register("disagreement", _job)


@router.get("/interviews/{iid}/disagreement")
def get_map(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.kind != "group":
        raise HTTPException(409, "分歧地图只用于座谈会")
    return group_analysis.view(db, itv)


def _set(db: Session, ctx: Ctx, model, rid: str, status: str, label: str):
    row = db.get(model, rid)
    if not row or row.org_id != ctx.org_id:
        raise HTTPException(404, "没有这一条")
    visible_interview(db, ctx, row.interview_id)
    row.status = status
    audit(db, ctx.org_id, ctx.actor, f"{label}.{status}", row.id)
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/claims/{cid}/{action}")
def claim_action(cid: str, action: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    if action not in ("accept", "reject"):
        raise HTTPException(404, "没有这个操作")
    return _set(db, ctx, Claim, cid, "accepted" if action == "accept" else "rejected", "claim")


@router.post("/persuasion/{eid}/{action}")
def persuasion_action(eid: str, action: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    if action not in ("accept", "reject"):
        raise HTTPException(404, "没有这个操作")
    return _set(db, ctx, PersuasionEvent, eid, "accepted" if action == "accept" else "rejected", "persuasion")


@router.get("/interviews/{iid}/disagreement/export")
def export(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The map as a Word file: matrix, conflicts, changes of mind (with what was said before), consensus and dissent. Participants appear as seat numbers; quotes are verbatim."""
    itv = visible_interview(db, ctx, iid)
    if itv.kind != "group":
        raise HTTPException(409, "分歧地图只用于座谈会")
    v = group_analysis.view(db, itv)
    q = lambda c: f"“{c['quotes'][0]['text']}” —— {c['speaker']}" if c["quotes"] else c["reason"]  # noqa: E731
    blocks: list[tuple] = [("title", itv.title), ("subtitle", f"座谈会分歧地图 · {utcnow().strftime('%Y-%m-%d')} · 共 {v['claims']} 条立场"), ("h1", "立场矩阵"),
                           ("table", ["议题"] + [s["label"] for s in v["speakers"]], [[m["topic"]] + ["、".join(c["stance_zh"] for c in m["cells"].get(s["key"], [])) or "—" for s in v["speakers"]] for m in v["matrix"]])]
    blocks.append(("h1", "各议题的原话"))
    for m in v["matrix"]:
        blocks.append(("h2", m["topic"]))
        blocks += [("quote", f"{c['speaker']}（{c['stance_zh']}）：{q(c)}") for s in v["speakers"] for c in m["cells"].get(s["key"], [])]
    blocks.append(("h1", "正面分歧"))
    blocks += [b for c in v["conflicts"] for b in (("h2", c["topic"]), ("quote", f"{c['a']['stance_zh']}：{q(c['a'])}"), ("quote", f"{c['b']['stance_zh']}：{q(c['b'])}"))] or [("p", "没有发现正面分歧。")]
    blocks.append(("h1", "立场的变化"))
    for e in v["shifts"]:
        blocks.append(("bullet", f"{e['speaker']} 在“{e['topic']}”上从{e['from']}变为{e['to']}" + ("（待复核）" if e["needs_review"] else "") + ("" if e["status"] == "accepted" else "（建议，未确认）")))
        if e["trigger"]:
            blocks.append(("quote", f"此前别人的话：{q(e['trigger'])}"))
    if not v["shifts"]:
        blocks.append(("p", "没有发现立场变化。"))
    blocks.append(("h1", "共识与异议"))
    blocks += [("bullet", f"共识：{c['topic']}（{'、'.join(c['speakers'])} 都{c['stance_zh']}）") for c in v["consensus"]]
    blocks += [("bullet", f"少数派：{d['topic']}：{d['who']}一人{d['stance_zh']}：{q(d['claim'])}") for d in v["dissent"]]
    if not v["consensus"] and not v["dissent"]:
        blocks.append(("p", "没有明显的共识或少数派。"))
    blocks.append(("note", "立场由 AI 从逐字稿中抽取，引文逐字来自转写；标“建议”的内容需要研究员确认后才算数。"))
    audit(db, ctx.org_id, ctx.actor, "disagreement.export", itv.id)
    db.commit()
    return Response(docx_writer.render(blocks), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="disagreement-map.docx"'})
