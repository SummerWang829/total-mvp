"""Coding and analysis (v1): a codebook per project, codes on insights (by hand or suggested by the model), and a code x participant overview.

Staff only. Participants appear as P-001 codes. A suggestion by the model stays a suggestion (it does not count) until a researcher accepts it.
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import participant_code
from ..audit import audit
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles, visible_interview
from ..models import Code, Insight, InsightCode, Interview, Participant, SegmentCode, TranscriptSegment
from ..providers import get_llm
from .projects import get_project

router = APIRouter(prefix="/api", tags=["analysis"])
MAX_CODES = 200
SUGGEST_BATCH = 30  # insights per model call


class CodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class SetCodes(BaseModel):
    code_ids: list[str] = Field(max_length=20)


def _code(db: Session, ctx: Ctx, code_id: str) -> Code:
    c = db.get(Code, code_id)
    if not c or c.org_id != ctx.org_id:
        raise HTTPException(404, "没有这个编码")
    ensure_project(db, ctx, c.project_id, "没有这个编码")
    return c


def _get_or_create(db: Session, ctx: Ctx, project_id: str, name: str) -> Code:
    name = name.strip()
    found = db.scalar(select(Code).where(Code.project_id == project_id, Code.name == name))
    if found:
        return found
    if (db.scalar(select(func.count()).select_from(Code).where(Code.project_id == project_id)) or 0) >= MAX_CODES:
        raise HTTPException(409, "编码太多了（最多 200 个），请先删除不用的")
    c = Code(org_id=ctx.org_id, project_id=project_id, name=name, created_by=ctx.user.id)
    db.add(c)
    db.flush()
    return c


@router.post("/projects/{pid}/codes", status_code=201)
def add_code(pid: str, body: CodeIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    c = _get_or_create(db, ctx, proj.id, body.name)
    audit(db, ctx.org_id, ctx.actor, "code.create", c.id)
    db.commit()
    return {"id": c.id, "name": c.name}


@router.delete("/codes/{code_id}", status_code=204)
def delete_code(code_id: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    c = _code(db, ctx, code_id)
    db.execute(delete(InsightCode).where(InsightCode.code_id == c.id))
    db.execute(delete(SegmentCode).where(SegmentCode.code_id == c.id))
    db.delete(c)
    audit(db, ctx.org_id, ctx.actor, "code.delete", c.id)
    db.commit()


def _insight_in_org(db: Session, ctx: Ctx, insight_id: str) -> Insight:
    ins = db.get(Insight, insight_id)
    if not ins or ins.org_id != ctx.org_id:
        raise HTTPException(404, "洞察不存在")
    visible_interview(db, ctx, ins.interview_id)
    return ins


def _project_of(db: Session, ins: Insight) -> str:
    return db.get(Interview, ins.interview_id).project_id


@router.put("/insights/{insight_id}/codes")
def set_codes(insight_id: str, body: SetCodes, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Make exactly these codes the insight's accepted codes (a suggestion that is in the list is accepted; one that is left out is dropped)."""
    ins = _insight_in_org(db, ctx, insight_id)
    pid = _project_of(db, ins)
    ids = list(dict.fromkeys(body.code_ids))
    for cid in ids:
        c = _code(db, ctx, cid)
        if c.project_id != pid:
            raise HTTPException(422, "编码不属于这个项目")
    db.execute(delete(InsightCode).where(InsightCode.insight_id == ins.id))
    for cid in ids:
        db.add(InsightCode(insight_id=ins.id, code_id=cid, org_id=ctx.org_id, suggested=False))
    audit(db, ctx.org_id, ctx.actor, "insight.codes", ins.id, count=len(ids))
    db.commit()
    return {"code_ids": ids}


@router.post("/projects/{pid}/codes/suggest")
def suggest(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Ask the model to propose codes for the insights that have none yet. Proposals are stored as suggestions, never as accepted codes."""
    proj = get_project(db, ctx, pid)
    insights = db.scalars(select(Insight).join(Interview, Interview.id == Insight.interview_id)
                          .where(Interview.project_id == proj.id, Interview.org_id == ctx.org_id).order_by(Insight.created_at)).all()
    coded = set(db.scalars(select(InsightCode.insight_id).where(InsightCode.insight_id.in_([i.id for i in insights]))).all()) if insights else set()
    todo = [i for i in insights if i.id not in coded]
    if not todo:
        return {"suggested": 0, "message": "所有洞察都已有编码或建议"}
    book = [c.name for c in db.scalars(select(Code).where(Code.project_id == proj.id).order_by(Code.name))]
    made = 0
    for k in range(0, len(todo), SUGGEST_BATCH):
        batch = todo[k:k + SUGGEST_BATCH]
        try:
            names = get_llm().suggest_codes([f"{i.theme}：{i.statement}" for i in batch], book)
        except Exception as e:  # a model failure must not look like "nothing to suggest"
            db.rollback()
            raise HTTPException(502, f"模型调用失败：{type(e).__name__}")
        for ins, row in zip(batch, names):
            for name in row:
                c = _get_or_create(db, ctx, proj.id, name)
                if c.name not in book:
                    book.append(c.name)
                if not db.get(InsightCode, (ins.id, c.id)):
                    db.add(InsightCode(insight_id=ins.id, code_id=c.id, org_id=ctx.org_id, suggested=True))
                    made += 1
    audit(db, ctx.org_id, ctx.actor, "code.suggest", proj.id, count=made)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "编码刚刚被别人改过，请再试一次")
    return {"suggested": made}


@router.get("/projects/{pid}/analysis")
def analysis(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Everything the analysis page draws: the codebook with counts, the interviews, and every insight with its codes."""
    proj = get_project(db, ctx, pid)
    code_of = participant_code.code_map(db, proj.id)
    itvs = db.scalars(select(Interview).where(Interview.project_id == proj.id, Interview.org_id == ctx.org_id).order_by(Interview.created_at)).all()
    codes = db.scalars(select(Code).where(Code.project_id == proj.id).order_by(Code.name)).all()
    links = defaultdict(list)
    for ic in db.scalars(select(InsightCode).where(InsightCode.org_id == ctx.org_id, InsightCode.code_id.in_([c.id for c in codes]))) if codes else []:
        links[ic.insight_id].append(ic)
    insights = []
    for itv in itvs:
        for ins in db.scalars(select(Insight).where(Insight.interview_id == itv.id).order_by(Insight.created_at)):
            insights.append({"id": ins.id, "interview_id": itv.id, "theme": ins.theme, "statement": ins.statement, "status": ins.status,
                             "codes": [{"id": ic.code_id, "suggested": ic.suggested} for ic in links.get(ins.id, [])]})
    counts = defaultdict(lambda: defaultdict(int))  # code -> interview -> accepted insights
    for ins in insights:
        for c in ins["codes"]:
            if not c["suggested"]:
                counts[c["id"]][ins["interview_id"]] += 1
    return {"codes": [{"id": c.id, "name": c.name, "total": sum(counts[c.id].values()), "by_interview": dict(counts[c.id])} for c in codes],
            "interviews": [{"id": i.id, "pcode": code_of.get(i.participant_id, "?"), "title": i.title} for i in itvs],
            "insights": insights}


# ---------- codes on lines of the transcript ----------
MAX_SEGMENT_CODES = 10


@router.get("/interviews/{iid}/segment-codes")
def segment_codes(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Which codes are on which lines of this interview: {"codes": [{id, name}], "by_segment": {segment id: [code ids]}}."""
    itv = visible_interview(db, ctx, iid)
    rows = db.execute(select(SegmentCode.segment_id, SegmentCode.code_id).where(SegmentCode.interview_id == itv.id)).all()
    by: dict[str, list[str]] = defaultdict(list)
    for seg, code in rows:
        by[seg].append(code)
    codes = db.scalars(select(Code).where(Code.project_id == itv.project_id).order_by(Code.name)).all()
    return {"codes": [{"id": c.id, "name": c.name} for c in codes], "by_segment": by}


@router.put("/transcript/{segment_id}/codes")
def set_segment_codes(segment_id: str, body: SetCodes, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Make exactly these codes the line's codes (each must be a code of the interview's project)."""
    seg = db.get(TranscriptSegment, segment_id)
    if not seg or seg.org_id != ctx.org_id:
        raise HTTPException(404, "转写不存在")
    itv = visible_interview(db, ctx, seg.interview_id)
    ids = list(dict.fromkeys(body.code_ids))
    if len(ids) > MAX_SEGMENT_CODES:
        raise HTTPException(422, f"一句话最多 {MAX_SEGMENT_CODES} 个标签")
    for cid in ids:
        if _code(db, ctx, cid).project_id != itv.project_id:
            raise HTTPException(422, "编码不属于这个项目")
    db.execute(delete(SegmentCode).where(SegmentCode.segment_id == seg.id))
    for cid in ids:
        db.add(SegmentCode(segment_id=seg.id, code_id=cid, org_id=ctx.org_id, interview_id=itv.id, created_by=ctx.user.id))
    audit(db, ctx.org_id, ctx.actor, "segment.codes", seg.id, count=len(ids))
    db.commit()
    return {"code_ids": ids}


@router.get("/projects/{pid}/codes/{code_id}/segments")
def segments_of_code(pid: str, code_id: str, limit: int = 200, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Every line tagged with this code across the project's interviews (participants as codes), to read them together."""
    proj = get_project(db, ctx, pid)
    c = _code(db, ctx, code_id)
    if c.project_id != proj.id:
        raise HTTPException(404, "没有这个编码")
    code_of = participant_code.code_map(db, proj.id)
    rows = db.execute(select(TranscriptSegment, Interview).join(SegmentCode, SegmentCode.segment_id == TranscriptSegment.id).join(Interview, Interview.id == TranscriptSegment.interview_id)
                      .where(SegmentCode.code_id == c.id, Interview.project_id == proj.id, Interview.purged_at.is_(None)).order_by(Interview.created_at, TranscriptSegment.idx).limit(min(limit, 500))).all()
    return {"code": {"id": c.id, "name": c.name}, "segments": [{"id": s.id, "interview_id": i.id, "interview": i.title, "who": code_of.get(i.participant_id, "?"), "speaker": s.speaker,
                                                               "start_ms": s.start_ms, "text": s.text_orig} for s, i in rows]}
