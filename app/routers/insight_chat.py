"""The research assistant of the 洞察与片段 page: a person's own conversations about one project (list, open, ask, delete). Staff only; app/insight_chat.py does the work."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import insight_chat, ratelimit, assistant_identity
from ..audit import audit
from ..chat_models import ChatThread
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project, utcnow
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat
from ..recruit_form import MSG_VENDOR_MISSING

router = APIRouter(prefix="/api", tags=["insight-chat"])


class AskBody(BaseModel):
    message: str = Field(min_length=1, max_length=insight_chat.MAX_ANSWER)
    scope: list[str] = Field(default_factory=lambda: list(insight_chat.SCOPES), max_length=3)
    ask_only: bool = False  # 只问不改: answer, and keep the question and the answer out of the saved conversation


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    return proj


def _thread(db: Session, ctx: Ctx, cid: str) -> ChatThread:
    """One of this person's own conversations; anything else (another person's, another company's) is a 404."""
    t = db.get(ChatThread, cid)
    if not t or t.org_id != ctx.org_id or t.user_id != ctx.user.id:
        raise HTTPException(404, "对话不存在")
    ensure_project(db, ctx, t.project_id)
    return t


def _status() -> dict:
    mock = settings.moderator_provider == "mock"
    local = mock or moderator_runs_locally()
    ready = mock or local or bool(settings.moderator_vendor.strip())
    return {"ready": ready, "mock": mock, "local": local, "vendor": "" if local else settings.moderator_vendor.strip(), "message": "" if ready else MSG_VENDOR_MISSING}


def _thread_out(db: Session, ctx: Ctx, t: ChatThread) -> dict:
    return {"id": t.id, "title": t.title, "updated_at": t.updated_at.isoformat(), "messages": [insight_chat.message_out(db, ctx, t.project_id, m) for m in insight_chat.load(t.messages)]}


@router.get("/projects/{pid}/chat/status")
def chat_status(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    return _status()


@router.get("/projects/{pid}/chats")
def list_chats(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    rows = db.scalars(select(ChatThread).where(ChatThread.project_id == pid, ChatThread.user_id == ctx.user.id).order_by(ChatThread.updated_at.desc()).limit(insight_chat.MAX_THREADS)).all()
    return [{"id": t.id, "title": t.title or "新对话", "updated_at": t.updated_at.isoformat()} for t in rows]


@router.post("/projects/{pid}/chats", status_code=201)
def new_chat(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    _project(db, ctx, pid)
    t = ChatThread(org_id=ctx.org_id, project_id=pid, user_id=ctx.user.id, messages="[]")
    db.add(t)
    db.flush()
    for old in db.scalars(select(ChatThread).where(ChatThread.project_id == pid, ChatThread.user_id == ctx.user.id).order_by(ChatThread.updated_at.desc()).offset(insight_chat.MAX_THREADS)).all():
        db.delete(old)  # only the latest few conversations are kept
    db.commit()
    return _thread_out(db, ctx, t)


@router.get("/chats/{cid}")
def get_chat(cid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _thread_out(db, ctx, _thread(db, ctx, cid))


@router.delete("/chats/{cid}", status_code=204)
def delete_chat(cid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    db.delete(_thread(db, ctx, cid))
    db.commit()


@router.post("/chats/{cid}/ask")
def ask(cid: str, body: AskBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One question in a conversation. The question, the research background and the passages found go to the AI service; the answer comes back as analysis + quotes + suggestions."""
    t = _thread(db, ctx, cid)
    proj = _project(db, ctx, t.project_id)
    st = _status()
    if not st["ready"]:
        raise HTTPException(409, st["message"])
    if wait := ratelimit.ask_allowed(ctx.user.id):
        raise HTTPException(429, f"提问太频繁，请 {wait} 秒后再试")
    chat = None if st["mock"] else get_moderator_chat()
    if assistant_identity.asks_identity(body.message):   # "你是什么模型": 小星星助手 answers here; no model is asked
        ai = {"role": "ai", "answer": assistant_identity.REPLY, "suggestions": [], "followups": [], "refs": []}
    else:
        try:
            ai = insight_chat.reply(db, ctx, proj, t, body.message.strip(), body.scope, chat, proj.outline_text)
        except ChatFatalError as e:
            raise HTTPException(502, f"AI 模型服务拒绝了请求：{e.reason}") from e
        except insight_chat.ChatUnavailable as e:
            raise HTTPException(502, e.message) from e
    if body.ask_only:   # a side question about one passage: the conversation stays as it was
        audit(db, ctx.org_id, ctx.actor, "chat.ask", t.id, model="mock" if chat is None else chat.name, ask_only=True)
        db.commit()
        return {"title": t.title, "message": insight_chat.message_out(db, ctx, proj.id, ai)}
    msgs = insight_chat.load(t.messages) + [{"role": "user", "text": body.message.strip()}, ai]
    t.messages = json.dumps(msgs, ensure_ascii=False)
    t.title = t.title or body.message.strip()[:40]
    t.updated_at = utcnow()
    audit(db, ctx.org_id, ctx.actor, "chat.ask", t.id, model="mock" if chat is None else chat.name)  # who and which model; never the question
    db.commit()
    return {"title": t.title, "message": insight_chat.message_out(db, ctx, proj.id, ai)}
