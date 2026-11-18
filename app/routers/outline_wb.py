"""大纲工作台 — the "生成大纲" page's API: three rounds of questions, the overview table, suggestions, module-by-module questions, the side dialog's revisions and the focus questions.

Researchers (admin, researcher, support) work on the outline; a client (viewer) can read it and add focus questions, nothing else. A client can never accept a revision.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatError, ChatFatalError, get_moderator_chat
from ..outline_wb import session as sess
from ..outline_wb.service import OutlineError
from ..recruit_form import MSG_VENDOR_MISSING

router = APIRouter(prefix="/api/projects/{pid}/outline-wb", tags=["outline-wb"])
VIEWERS = ("viewer",)


class AnswerBody(BaseModel):
    slot: str = Field(max_length=30)
    value: str | int | list[str]


class IdsBody(BaseModel):
    ids: list[str] = Field(max_length=3)


class ModuleBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    purpose: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200)
    minutes: int | None = None
    depth: str | None = None


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    selected: str = Field(default="", max_length=40)


class DecideBody(BaseModel):
    train_case_id: str = Field(default="", max_length=60)


class ItemBody(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    followups: list[str] | None = None
    minutes: float | None = None
    backup: bool | None = None
    depth: str | None = None


class InsertBody(BaseModel):
    kind: str = Field(default="question", max_length=12)
    text: str = Field(min_length=1, max_length=300)
    after_uid: str | None = Field(default=None, max_length=40)
    module: str = Field(default="", max_length=40)
    minutes: float | None = None
    config: dict = Field(default_factory=dict)


class FocusBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    item_uid: str | None = Field(default=None, max_length=40)


class LinkBody(BaseModel):
    item_uid: str | None = Field(default=None, max_length=40)


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    p = db.get(Project, pid)
    if not p or p.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    return p


def _chat():
    """The model, or None for the offline demo; a cloud model must name its vendor (project rule)."""
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, MSG_VENDOR_MISSING)
    return get_moderator_chat()


def _run(db: Session, fn, *args, **kwargs):
    """Run an action, commit, and turn the workbench's and the model's refusals into plain answers."""
    try:
        out = fn(*args, **kwargs)
        db.commit()
        return out
    except OutlineError as e:
        db.rollback()
        raise HTTPException(e.status, str(e)) from e
    except ChatFatalError as e:
        db.rollback()
        raise HTTPException(503, "小星星助手暂时不可用（服务拒绝了请求），请联系管理员") from e
    except ChatError as e:
        db.rollback()
        raise HTTPException(502, "这一步模型响应太慢或没有给出可用的结果，请再点一次重试") from e


@router.get("")
def get_view(pid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, sess.view, db, _project(db, ctx, pid), ctx.user.id)


@router.post("/goal/confirm")
def confirm_goal(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    _run(db, sess.confirm_goal, db, p, ctx.user.id)
    return _run(db, sess.view, db, p, ctx.user.id)


@router.post("/answer")
def answer(pid: str, body: AnswerBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    _run(db, sess.answer, db, p, body.slot, body.value, ctx.user.id)
    return _run(db, sess.view, db, p, ctx.user.id)


@router.post("/overview")
def build_overview(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.build_overview, db, _project(db, ctx, pid), ctx.user.id)


@router.put("/overview/modules/{mid}")
def edit_module(pid: str, mid: str, body: ModuleBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.edit_module, db, _project(db, ctx, pid), mid, body.model_dump(exclude_none=True), ctx.user.id)


@router.post("/suggestions/adopt")
def adopt(pid: str, body: IdsBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    out = _run(db, sess.adopt, db, p, body.ids, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.adopt", pid, ip=ctx.ip, count=len(body.ids))   # only how many, never the text
    db.commit()
    return out


@router.post("/suggestions/{sid}/dismiss")
def dismiss(pid: str, sid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    _run(db, sess.dismiss, db, p, sid, ctx.user.id)
    return _run(db, sess.view, db, p, ctx.user.id)


@router.post("/overview/confirm")
def confirm_overview(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.confirm_overview, db, _project(db, ctx, pid), ctx.user.id)


@router.post("/modules/{mid}/generate")
def generate(pid: str, mid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    return _run(db, sess.generate_module, db, p, mid, _chat(), ctx.user.id)


@router.post("/modules/{mid}/confirm")
def confirm_module(pid: str, mid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.confirm_module, db, _project(db, ctx, pid), mid, ctx.user.id)


@router.post("/chat")
def chat(pid: str, body: ChatBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    from .. import assistant_identity
    p = _project(db, ctx, pid)
    if assistant_identity.asks_identity(body.text):   # "你是什么模型": answered here, before any model is asked
        return {"reply": assistant_identity.REPLY, "revisions": []}
    return _run(db, sess.chat_turn, db, p, _chat(), body.text, body.selected, ctx.user.id)


@router.post("/revisions/{rid}/accept")
def accept(pid: str, rid: str, body: DecideBody | None = None, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, sess.decide, db, _project(db, ctx, pid), rid, True, ctx.user.id, ctx.user.role, (body.train_case_id if body else ""))


@router.post("/revisions/{rid}/reject")
def reject(pid: str, rid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, sess.decide, db, _project(db, ctx, pid), rid, False, ctx.user.id, ctx.user.role)


@router.put("/items/{uid}")
def edit_item(pid: str, uid: str, body: ItemBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.edit_item, db, _project(db, ctx, pid), uid, body.model_dump(exclude_none=True), ctx.user.id)


@router.post("/items")
def insert_item(pid: str, body: InsertBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.insert_item, db, _project(db, ctx, pid), body.model_dump(), ctx.user.id)


@router.delete("/items/{uid}")
def delete_item(pid: str, uid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.delete_item, db, _project(db, ctx, pid), uid, ctx.user.id)


@router.post("/focus")
def add_focus(pid: str, body: FocusBody, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, sess.add_focus, db, _project(db, ctx, pid), body.text, ctx.user.id, ctx.user.role, body.item_uid)


@router.put("/focus/{fid}")
def link_focus(pid: str, fid: str, body: LinkBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.link_focus, db, _project(db, ctx, pid), fid, body.item_uid)
