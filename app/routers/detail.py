"""Model extras and single-interview export for the respondent detail panel.

Everything the model writes is cached per transcript version (interview_details); the caption
timestamps are never touched. Plain .xlsx / .docx are built with the standard library so the
container needs no new dependency.
"""
import csv
import io
import logging
import zipfile
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import docx_writer
from .. import interview_detail_ai as detail_ai
from .. import interview_facts
from ..db import get_db
from ..deps import Ctx, STAFF, require_roles
from ..models import Interview, InterviewDetail, InterviewTimeline, Project, TranscriptSegment, utcnow
from ..moderator.providers import ChatFatalError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["detail"])


class FixIn(BaseModel):
    text: str = Field(default="", max_length=3000)


class TranslateIn(BaseModel):
    lang: str = Field(default="en", max_length=10)
    force: bool = False


def _interview(db: Session, ctx: Ctx, iid: str) -> Interview:
    itv = db.get(Interview, iid)
    if not itv or itv.org_id != ctx.org_id:
        raise HTTPException(404, "访谈不存在")
    return itv


def _captions(db: Session, iid: str) -> list[dict]:
    rows = db.scalars(select(TranscriptSegment).where(
        TranscriptSegment.interview_id == iid).order_by(TranscriptSegment.idx)).all()
    return [{"id": r.id, "speaker": r.speaker, "text": r.text_orig, "text_zh": r.text_zh,
             "text_en": getattr(r, "text_en", "") or "", "start_ms": r.start_ms, "end_ms": r.end_ms} for r in rows]


def _slices(db: Session, ctx: Ctx, iid: str, caps: list[dict]) -> list[dict]:
    row = db.scalar(select(InterviewTimeline).where(
        InterviewTimeline.interview_id == iid, InterviewTimeline.org_id == ctx.org_id))
    if row and row.segments and row.caption_count == len(caps):
        return row.segments
    return detail_ai.rule_slices(caps)


def _clock(ms) -> str:
    s = max(0, round((int(ms) or 0) / 1000))
    return f"{s // 60:02d}:{s % 60:02d}"


@router.get("/interviews/{iid}/detail")
def get_detail(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _interview(db, ctx, iid)
    caps = _captions(db, iid)
    row = db.scalar(select(InterviewDetail).where(
        InterviewDetail.interview_id == iid, InterviewDetail.org_id == ctx.org_id))
    if row and row.data and row.caption_count == len(caps):
        return {"state": "ready", "data": row.data, "caption_count": len(caps), "needs_generate": False}
    return {"state": "empty", "data": {}, "caption_count": len(caps), "needs_generate": bool(caps)}


@router.post("/interviews/{iid}/detail/translate")
def translate_only(iid: str, body: TranslateIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Translate the caption track only. Called when the researcher picks English; the page
    swaps its text in place from the returned {caption id: translation} map, without a reload."""
    itv = _interview(db, ctx, iid)
    caps = _captions(db, iid)
    wanted = [c for c in caps if (c.get("text") or "").strip()]
    row = db.scalar(select(InterviewDetail).where(
        InterviewDetail.interview_id == iid, InterviewDetail.org_id == ctx.org_id))
    data_now = (row.data or {}) if row else {}
    # Translate everything the panel shows: captions, screening Q&A, profile tags and summary
    # bullets. Keys are the source text itself, so the front end can look each one up exactly.
    extras: list[dict] = []
    # Same source and same truncation as the page's key_answers, so every key matches exactly.
    for item in interview_facts.key_answers(db, itv.participant_id):
        q = (item.get("q") or "").strip()
        if not q:
            continue
        extras.append({"id": f"screen:{q}:q", "speaker": "moderator", "text": q, "start_ms": 0, "end_ms": 0})
        a = (item.get("a") or "").strip()
        if a:
            extras.append({"id": f"screen:{q}:a", "speaker": "participant", "text": a, "start_ms": 0, "end_ms": 0})
    base_tags = data_now.get("tags") or []
    if not base_tags:
        base_tags = (itv.participant.tags or []) if itv.participant else []
    for t in list(base_tags)[:8]:
        t = str(t).strip()
        if t:
            extras.append({"id": f"tag:{t}", "speaker": "participant", "text": t, "start_ms": 0, "end_ms": 0})
    for s in list(data_now.get("summary") or [])[:6]:
        s = str(s).strip()
        if s:
            extras.append({"id": f"sum:{s}", "speaker": "participant", "text": s, "start_ms": 0, "end_ms": 0})
    if not wanted and not extras:
        return {"en": {}, "count": 0, "cached": False, "reason": "还没有可翻译的题目和回复"}
    cached = data_now.get("en") or {}
    needed = [c["id"] for c in wanted] + [e["id"] for e in extras]
    if not body.force and cached and all(k in cached for k in needed):
        return {"en": cached, "count": len(cached), "cached": True, "reason": ""}
    try:
        chat = detail_ai.chat_or_none()
    except RuntimeError as e:
        return {"en": {}, "count": 0, "cached": False, "reason": str(e)}
    if chat is None:
        return {"en": {}, "count": 0, "cached": False, "reason": "离线演示模式：模型未接入"}
    try:
        en = detail_ai.translate(chat, caps, "English") if wanted else {}
        screen_en = detail_ai.translate(chat, extras, "English") if extras else {}
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    merged = {**en, **screen_en}
    if en:
        for seg in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == iid)).all():
            if seg.id in en:
                seg.text_en = en[seg.id]
    if row is None:
        row = InterviewDetail(org_id=ctx.org_id, interview_id=iid)
        db.add(row)
    data = dict(row.data or {})
    data["en"] = {**cached, **merged}
    data["en_count"] = len(data["en"])
    row.data, row.caption_count, row.created_by, row.updated_at = data, len(caps), ctx.actor, utcnow()
    db.commit()
    log.info("detail translate iid=%s captions=%s screen=%s cached=%s", iid, len(en), len(screen_en), False)
    return {"en": data["en"], "count": len(merged), "cached": False, "reason": ""}


@router.post("/interviews/{iid}/detail/generate")
def generate(iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Translate to English, summarise, tag and score. Each feature fails independently."""
    _interview(db, ctx, iid)
    caps = _captions(db, iid)
    if not caps:
        return {"state": "no_transcript", "data": {}, "caption_count": 0,
                "needs_generate": False, "reason": "还没有转写字幕，无法生成"}
    try:
        chat = detail_ai.chat_or_none()
    except RuntimeError as e:
        return {"state": "offline", "data": {}, "caption_count": len(caps),
                "needs_generate": False, "reason": str(e)}
    if chat is None:
        return {"state": "offline", "data": {}, "caption_count": len(caps),
                "needs_generate": False, "reason": "离线演示模式：模型未接入"}
    slices = _slices(db, ctx, iid, caps)
    pairs = [{"ask": g.get("ask") or "", "answer": g.get("answer") or ""} for g in slices]
    answers = [p["answer"] for p in pairs if p["answer"].strip()]
    try:
        en = detail_ai.translate(chat, caps, "English")
        summary = detail_ai.summarize(chat, answers)
        tags = detail_ai.profile_tags(chat, answers)
        quality = detail_ai.quality(chat, pairs)
        brief = detail_ai.export_brief(chat, answers)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    if en:
        for seg in db.scalars(select(TranscriptSegment).where(TranscriptSegment.interview_id == iid)).all():
            if seg.id in en:
                seg.text_en = en[seg.id]
    data = {"en": en, "en_count": len(en), "summary": summary, "tags": tags,
            "quality": quality, "brief": brief}
    row = db.scalar(select(InterviewDetail).where(
        InterviewDetail.interview_id == iid, InterviewDetail.org_id == ctx.org_id))
    if row is None:
        row = InterviewDetail(org_id=ctx.org_id, interview_id=iid)
        db.add(row)
    row.data, row.caption_count = data, len(caps)
    row.created_by, row.updated_at = ctx.actor, utcnow()
    db.commit()
    log.info("detail generated iid=%s en=%s summary=%s tags=%s score=%s", iid,
             len(en), len(summary), len(tags), (quality or {}).get("mean"))
    return {"state": "ready", "data": data, "caption_count": len(caps),
            "needs_generate": False, "reason": ""}


@router.post("/interviews/{iid}/detail/fix")
def fix(iid: str, body: FixIn, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """A correction suggestion only; the researcher confirms before PATCH /transcript/{id}."""
    _interview(db, ctx, iid)
    try:
        chat = detail_ai.chat_or_none()
    except RuntimeError as e:
        return {"text": body.text, "changed": False, "reason": str(e)}
    if chat is None:
        return {"text": body.text, "changed": False, "reason": "离线演示模式：模型未接入"}
    try:
        out = detail_ai.fix_text(chat, body.text)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    return out or {"text": body.text, "changed": False, "reason": "模型没有给出可用建议"}


def _rows(db: Session, ctx: Ctx, iid: str) -> tuple[str, list[list[str]]]:
    itv = _interview(db, ctx, iid)
    caps = _captions(db, iid)
    slices = _slices(db, ctx, iid, caps)
    row = db.scalar(select(InterviewDetail).where(InterviewDetail.interview_id == iid))
    data = row.data if row else {}
    code = (itv.participant.code if itv.participant else None) or "受访者"
    rows: list[list[str]] = [["编号", code], ["状态", itv.status or ""],
                             ["开始时间", str(itv.started_at or "")], ["要点总结", " / ".join(data.get("summary") or [])],
                             ["整体标签", "、".join(data.get("tags") or [])],
                             ["回答质量评分", str((data.get("quality") or {}).get("mean") or "—")], [],
                             ["题号", "主持人提问", "受访者回答", "起始", "结束", "时长"]]
    for i, g in enumerate(slices):
        rows.append([f'Q{g.get("q") or i + 1}', g.get("ask") or "", g.get("answer") or "",
                     _clock(g.get("at_ms")), _clock(g.get("end_ms")),
                     _clock((int(g.get("end_ms") or 0) - int(g.get("at_ms") or 0)))])
    return code, rows


def _xlsx(rows: list[list[str]]) -> bytes:
    def cell(v, col, r):
        ref = f"{chr(65 + col)}{r}"
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'
    body = "".join(f'<row r="{i + 1}">' + "".join(cell(v, j, i + 1) for j, v in enumerate(r[:12])) + "</row>"
                   for i, r in enumerate(rows))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + body + "</sheetData></worksheet>")
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="受访者详情" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbr = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
           '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wbr)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


@router.get("/interviews/{iid}/detail/export")
def export(iid: str, format: str = "xlsx", ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One respondent as a real .xlsx, .docx or .csv. Word/Excel are built without new packages."""
    code, rows = _rows(db, ctx, iid)
    if format == "docx":
        row = db.scalar(select(InterviewDetail).where(InterviewDetail.interview_id == iid))
        data = row.data if row else {}
        blocks = [("title", f"{code} 受访者详情")]
        if (data.get("brief") or {}).get("abstract"):
            blocks.append(("p", data["brief"]["abstract"]))
        blocks.append(("h1", "访问信息"))
        blocks.append(("table", ["项目", "内容"], [r for r in rows[:6] if len(r) == 2]))
        if data.get("summary"):
            blocks.append(("h1", "要点总结"))
            blocks += [("bullet", s) for s in data["summary"]]
        if data.get("tags"):
            blocks.append(("h1", "整体标签"))
            blocks.append(("p", "、".join(data["tags"])))
        q = data.get("quality") or {}
        if q.get("dims"):
            blocks.append(("h1", "回答质量评分依据"))
            blocks.append(("table", ["维度", "分数"], [[k, str(v)] for k, v in q["dims"].items()]))
        body = [r for r in rows if len(r) == 6]
        if body:
            blocks.append(("h1", "访谈逐题问答"))
            blocks.append(("table", body[0], body[1:]))
        out = docx_writer.render(blocks)
        media, name = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "respondent-detail.docx"
    elif format == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        out, media, name = ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "respondent-detail.csv"
    else:
        out = _xlsx(rows)
        media, name = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "respondent-detail.xlsx"
    return Response(out, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})
