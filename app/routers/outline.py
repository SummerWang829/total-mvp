"""The 访谈 page's interview outline: read it, save a hand edit, talk to the AI to change it. Staff only (it holds the research goal); nothing here reaches participants."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import guide_text, library_context, outline, outline_chat
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat
from ..recruit_form import MSG_VENDOR_MISSING
from ..study_draft import DraftError

router = APIRouter(prefix="/api", tags=["outline"])


class OutlineBody(BaseModel):
    text: str = Field(max_length=guide_text.MAX_CHARS)


class EditBody(BaseModel):
    op: Literal["set", "delete", "add", "rename", "del_part", "add_part"]
    kind: Literal["warmup", "topic", "stimulus"] = "topic"
    ref: int = Field(default=0, ge=0, le=50)
    index: int = Field(default=0, ge=0, le=100)
    value: str = Field(default="", max_length=400)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    use_library: bool = True


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    return proj


def _model_label() -> str:
    """Who answers in the chat: the cloud vendor named in .env, the local model, or the offline demo."""
    if settings.moderator_provider == "mock":
        return "演示模式"
    return "本地模型" if moderator_runs_locally() else settings.moderator_vendor.strip()


def _out(proj: Project, db: Session) -> dict:
    local = settings.moderator_provider == "mock" or moderator_runs_locally()
    history = outline_chat.load(proj.outline_chat)
    summary = outline.summary_of(proj.outline_text)
    return {"text": proj.outline_text, "problem": outline.problem_of(proj.outline_text), "summary": summary, "warning": outline.warning_of(proj.outline_text),
            "chat": outline_chat.view(history, proj.outline_text), "marks": outline_chat.marks(history, proj.outline_text),
            "context": {"brief": bool((proj.brief or "").strip()), "library_docs": len(library_context.project_documents(db, proj.id)), "parts": len(summary["parts"]) if summary else 0,
                        "questions": summary["question_count"] if summary else 0},
            "model": _model_label(), "ai_local": local, "ai_vendor": "" if local else settings.moderator_vendor.strip()}


@router.get("/projects/{pid}/outline")
def get_outline(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _out(_project(db, ctx, pid), db)


@router.put("/projects/{pid}/outline")
def put_outline(pid: str, body: OutlineBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Save the edited text as it is (a half-finished edit is kept); the answer says what is wrong with it, if anything."""
    proj = _project(db, ctx, pid)
    proj.outline_text = body.text
    audit(db, ctx.org_id, ctx.actor, "outline.save", pid)
    db.commit()
    return _out(proj, db)


@router.post("/projects/{pid}/outline/edit")
def edit_outline(pid: str, body: EditBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Change one question or part from the outline view (the text is rewritten from the guide, so the two always agree)."""
    proj = _project(db, ctx, pid)
    try:
        proj.outline_text = outline.edit(proj.outline_text, body.op, body.kind, body.ref, body.index, body.value)
    except outline.EditError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "outline.edit", pid)
    db.commit()
    return _out(proj, db)


class ActBody(BaseModel):
    action: Literal["adopt", "dismiss", "undo"]
    sid: str = Field(default="", max_length=20)


@router.post("/projects/{pid}/outline/chat")
def chat_outline(pid: str, body: ChatBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One turn with the assistant: it answers, suggests, or (when asked) changes the outline at once. Both messages are kept with the project. The research background (and, if asked, the library) goes to the AI service."""
    proj = _project(db, ctx, pid)
    mock = settings.moderator_provider == "mock"
    if not mock and not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, MSG_VENDOR_MISSING)
    chat = None if mock else get_moderator_chat()
    library = library_context.library_text(db, pid)[0] if body.use_library else ""
    lang, minutes = outline.settings_of(proj.outline_text, proj.language)
    history, message = outline_chat.load(proj.outline_chat), body.message.strip()
    try:
        result = outline_chat.converse(chat, history, proj.outline_text, message, proj.brief, library, lang, minutes)
    except ChatFatalError as e:
        raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
    except DraftError as e:
        raise HTTPException(502, str(e)) from e
    history, proj.outline_text = outline_chat.record_turn(history, message, result, proj.outline_text)
    proj.outline_chat = outline_chat.dump(history)
    audit(db, ctx.org_id, ctx.actor, "outline.chat", pid, model="mock" if chat is None else chat.name, mode=result["mode"])  # who, which model and what it did; never the message
    db.commit()
    return _out(proj, db)


@router.post("/projects/{pid}/outline/chat/{mid}")
def act_on_chat(pid: str, mid: str, body: ActBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Adopt or drop a suggestion, or undo a change (refused when the outline was edited after that change, so no hand edit is lost)."""
    proj = _project(db, ctx, pid)
    try:
        history, text = outline_chat.act(outline_chat.load(proj.outline_chat), mid, body.action, body.sid, proj.outline_text)
    except outline_chat.ActError as e:
        raise HTTPException(e.status, str(e)) from e
    proj.outline_text, proj.outline_chat = text, outline_chat.dump(history)
    audit(db, ctx.org_id, ctx.actor, "outline.act", pid, what=body.action)
    db.commit()
    return _out(proj, db)
