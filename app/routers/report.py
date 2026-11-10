"""The project's research report (staff and clients of the project): the analysis cards for the page, the team's template, and the Word file. See app/report.py and app/report_analysis.py."""
import io
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import docx_writer, project_context as pctx, ratelimit, report, report_analysis
from ..audit import audit
from ..db import get_db
from ..deps import READERS, STAFF, Ctx, require_roles
from ..models import utcnow
from ..moderator.providers import ChatError, ChatFatalError, get_moderator_chat
from .insight_chat import _status
from .projects import get_project

router = APIRouter(prefix="/api", tags=["report"])
MAX_TEMPLATE_BYTES = 2_000_000
MAX_IDS = 60
HEAD = re.compile(r"<w:p[ >].*?</w:p>", re.S)
STYLE = re.compile(r'<w:pStyle w:val="([^"]+)"')
TEXT = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")


class TemplateBody(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    on: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    off: list[str] = Field(default_factory=list, max_length=MAX_IDS)


class FocusBody(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    add: bool = False   # True: another block written by hand, kept beside the earlier ones


def _chat(st: dict):
    return None if st["mock"] or not st["ready"] else get_moderator_chat()


def _word_blocks(db: Session, proj, now, client: bool) -> list[tuple]:
    """The Word file: title, then the analysis cards with their readings, then the old sections (background, methods, sample...) as the appendix."""
    old = report.build(db, proj, now, client=client)
    data = report_analysis.build(db, proj, client=client)
    out: list[tuple] = list(old[:2]) + [("h1", "总体结论"), ("p", data["summary"]["t"])]
    for c in data["cards"]:
        out += [("h1", c["title"]), ("note", c["desc"]), ("table", ["", "人数", "样本"], [[b["label"], str(b["k"]), str(b["n"])] for b in c["bars"]]), ("p", c["note"]["t"])]
    return out + [("pagebreak",), ("h1", "附录")] + list(old[2:])


@router.get("/projects/{pid}/report")
def project_report(pid: str, format: str = "docx", ctx: Ctx = Depends(require_roles(*READERS)), db: Session = Depends(get_db)):
    """The research report: a Word download, or the older sections as JSON blocks (format=json) for the appendix."""
    if format not in ("docx", "json"):
        raise HTTPException(422, "格式只能是 docx 或 json")
    proj = get_project(db, ctx, pid)
    client = ctx.user.role == "viewer"
    blocks = report.build(db, proj, utcnow(), client=client)
    audit(db, ctx.org_id, ctx.actor, "report.export", proj.id, format=format, blocks=len(blocks))
    if format == "json":
        db.commit()
        return {"blocks": [list(b) for b in blocks]}
    words = _word_blocks(db, proj, utcnow(), client)
    db.commit()
    return Response(docx_writer.render(words), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="research-report.docx"'})


@router.get("/projects/{pid}/report/analysis")
def analysis(pid: str, ctx: Ctx = Depends(require_roles(*READERS)), db: Session = Depends(get_db)):
    """The report page: analysis cards counted from the interviews, with the template's block list (team only) and whether a model can write the readings."""
    proj = get_project(db, ctx, pid)
    data = report_analysis.build(db, proj, client=ctx.user.role == "viewer")
    proj_meta = pctx.meta(db, proj)
    st = _status()
    db.commit()
    return {**data, "meta": {k: proj_meta[k] for k in
                             ("country", "language", "languages", "topic", "purpose", "objectives", "duration", "source")},
            "ai": {"ready": st["ready"] and not st["mock"], "mock": st["mock"], "vendor": st["vendor"], "local": st["local"], "message": st["message"]}}


@router.put("/projects/{pid}/report/template")
def save_template(pid: str, body: TemplateBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Which blocks are on or off and in what order (the team's choice; the next read of the report follows it)."""
    proj = get_project(db, ctx, pid)
    t = report_analysis.template_of(db, proj)
    t.order, t.on, t.off = [x[:160] for x in body.order], [x[:160] for x in body.on], [x[:160] for x in body.off]
    t.updated_at = utcnow()
    audit(db, ctx.org_id, ctx.actor, "report.template", proj.id, blocks=len(t.order))
    db.commit()
    return {"ok": True}


def _pick(db: Session, proj, text: str, add: bool) -> dict:
    """Choose the blocks that answer the words (a model picks from the real lists when one may be used; otherwise the words are matched)."""
    st = _status()
    t = report_analysis.template_of(db, proj)
    ps, cols, shown = report_analysis.people(db, proj)
    try:
        ids = report_analysis.pick(text, ps, shown, cols, _chat(st))
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    t.focus = (t.focus + "\n" + text).strip()[:600] if add else text
    t.picked = list(dict.fromkeys(([*t.picked] if add else []) + ids))
    t.off = [x for x in t.off if x not in ids]
    t.updated_at = utcnow()
    return {"picked": ids, "ai": _chat(st) is not None}


@router.post("/projects/{pid}/report/focus")
def set_focus(pid: str, body: FocusBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """What the team most wants to see (or one more block written by hand): picks the comparisons for it. The words go to the AI service when one is used; never into the log."""
    proj = get_project(db, ctx, pid)
    if wait := ratelimit.ask_allowed(ctx.user.id):
        raise HTTPException(429, f"操作太频繁，请 {wait} 秒后再试")
    out = _pick(db, proj, body.text.strip(), body.add)
    audit(db, ctx.org_id, ctx.actor, "report.focus", proj.id, picked=len(out["picked"]), ai=out["ai"])   # counts only, never the words
    db.commit()
    return out


@router.post("/projects/{pid}/report/generate")
def generate(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Have the model write the readings and the overall conclusion from the counts (the numbers are sent, no answers and no names)."""
    proj = get_project(db, ctx, pid)
    st = _status()
    if not st["ready"]:
        raise HTTPException(409, st["message"])
    if st["mock"]:
        raise HTTPException(409, "当前是演示模式，小星星助手未连接")
    if wait := ratelimit.ask_allowed(ctx.user.id):
        raise HTTPException(429, f"操作太频繁，请 {wait} 秒后再试")
    try:
        out = report_analysis.write_notes(db, proj, get_moderator_chat())
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    except ChatError as e:
        raise HTTPException(502, "小星星助手暂时无法写解读，请稍后再试") from e
    audit(db, ctx.org_id, ctx.actor, "report.generate", proj.id, written=out["written"])
    db.commit()
    return out


@router.get("/projects/{pid}/report/template/file")
def template_file(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The default template as a Word file: one heading per block in the current order, for the team to edit and upload again."""
    proj = get_project(db, ctx, pid)
    data = report_analysis.build(db, proj)
    blocks: list[tuple] = [("title", f"{proj.name} 报告模版"), ("note", "每个标题是一章；改标题、调顺序、删掉不要的章，或新加一章写下你想看什么，再上传。数字和图表由系统按数据填入。")]
    for b in data["blocks"]:
        blocks += [("h1", b["title"]), ("p", "")]
    db.commit()
    return Response(docx_writer.render(blocks), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="report-template.docx"'})


def _headings(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError) as e:
        raise HTTPException(422, "这不是有效的 Word 文件（.docx）") from e
    out = []
    for para in HEAD.findall(xml):
        st = STYLE.search(para)
        if st and st.group(1).lower().startswith(("heading", "title")) and st.group(1) != "Title":
            text = "".join(TEXT.findall(para)).strip()
            if text:
                out.append(text[:80])
    return out


@router.post("/projects/{pid}/report/template/import")
async def import_template(pid: str, request: Request, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Upload an edited template (.docx): its headings become the blocks, in its order. A known heading keeps its block; any other heading is read as something the team wants to see."""
    proj = get_project(db, ctx, pid)
    data = await request.body()
    if not data or len(data) > MAX_TEMPLATE_BYTES:
        raise HTTPException(422, "文件是空的或太大")
    heads = _headings(data)
    if not heads:
        raise HTTPException(422, "文件里没有标题（请用“标题 1”样式写每一章）")
    order, wanted = [], []
    for h in heads:
        if any(w in h for w in ("总体结论", "摘要", "结论")):
            order.append("summary")
        elif "主题" in h:
            order.append("themes")
        elif any(w in h for w in ("附录", "背景", "方法", "样本")):
            order.append("appendix")
        else:
            wanted.append(h)
    t = report_analysis.template_of(db, proj)
    ps, cols, shown = report_analysis.people(db, proj)
    st, picked, unmatched = _status(), [], []
    try:
        for h in wanted[:report_analysis.MAX_PICKS]:
            got = report_analysis.pick(h, ps, shown, cols, _chat(st))
            picked += got
            if not got:
                unmatched.append(h)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    t.picked = list(dict.fromkeys(picked))
    t.focus = "\n".join(wanted)[:600]
    t.order = list(dict.fromkeys(["summary", *t.picked, *order]))
    t.on, t.off = [*t.picked], [i for i in ("summary", "themes", "appendix") if i not in order]
    t.updated_at = utcnow()
    audit(db, ctx.org_id, ctx.actor, "report.template.import", proj.id, headings=len(heads), picked=len(t.picked))
    db.commit()
    return {"headings": len(heads), "picked": len(t.picked), "unmatched": unmatched}
