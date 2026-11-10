"""创建访谈 — the stage-by-stage dialog that builds a study (goal → mode → audience → screening → consent → format → outline → ... → publish). Staff only.

One conversation per project, shown beside the questionnaire page and the outline page alike. The model proposes, the code applies (app/creation/patches.py).
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import assistant_identity, web_reader
from ..audit import audit
from ..config import settings
from ..creation import engine, patches, stages
from ..creation.stages import LABELS, Stage
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat
from ..recruit_form import MSG_VENDOR_MISSING

router = APIRouter(prefix="/api", tags=["creation"])
SHOWN_MESSAGES = 60


class MessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    material: str = Field(default="", max_length=30500)  # text read from an attached file (POST /api/attachments/extract): given to the model for this turn, never stored or logged


class StageBody(BaseModel):
    stage: Stage


def _project(db: Session, ctx: Ctx, pid: str) -> Project:
    proj = db.get(Project, pid)
    if not proj or proj.org_id != ctx.org_id:
        raise HTTPException(404, "项目不存在")
    ensure_project(db, ctx, pid)
    return proj


def _chat():
    """The model, or None for the offline demo. A cloud model must name its vendor (project rule)."""
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, MSG_VENDOR_MISSING)
    return get_moderator_chat()


def _view(state: stages.State) -> dict:
    last_change = next((m["id"] for m in reversed(state.msgs) if m["role"] == "ai" and m.get("snapshot")), "")
    msgs = [{k: v for k, v in m.items() if k != "snapshot"} | {"can_undo": m["id"] == last_change} if m["role"] == "ai" else m for m in state.msgs[-SHOWN_MESSAGES:]]
    return {"stage": state.stage.value, "label": LABELS[state.stage], "mode": state.mode, "host": state.host, "progress": stages.progress(state), "decisions": state.decisions,
            "messages": msgs, "model": "演示模式" if settings.moderator_provider == "mock" else assistant_identity.NAME}


def _turn(db: Session, ctx: Ctx, proj: Project, state: stages.State, text: str, material: str = "") -> None:
    try:
        engine.run_turn(db, _chat(), proj, state, text, material)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    except engine.CreationError as e:
        raise HTTPException(502, str(e)) from e
    stages.save(proj, state)
    audit(db, ctx.org_id, ctx.actor, "creation.turn", proj.id, stage=state.stage.value)  # what was said is not written to the log
    db.commit()


@router.get("/projects/{pid}/creation")
def get_creation(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The dialog as it stands. The first time, the assistant opens the first stage by itself."""
    proj = _project(db, ctx, pid)
    state = stages.load(proj)
    if not state.msgs:
        _turn(db, ctx, proj, state, engine.START)
    return _view(state)


@router.post("/projects/{pid}/creation/message")
def post_message(pid: str, body: MessageBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """One thing the team says in the current stage; the answer and any change to the study come back with the whole dialog."""
    proj = _project(db, ctx, pid)
    state = stages.load(proj)
    material = body.material
    for url in web_reader.urls_in(body.message):   # a pasted link is read here, on the server (public pages only); a page that cannot be read is told to the model, not hidden
        host = urlparse(url).hostname or "?"
        try:
            page = web_reader.read(url)
            material += f"\n\n【网页：{page['host']} {page['title']}】\n{page['text']}"
        except web_reader.WebError as e:
            material += f"\n\n【网页读取失败：{host}：{e}】"
        audit(db, ctx.org_id, ctx.actor, "creation.web", host)   # the address itself may carry a token: only the host is written down
    _turn(db, ctx, proj, state, body.message.strip(), material[:30500])
    return _view(state)


@router.post("/projects/{pid}/creation/stage")
def go_to_stage(pid: str, body: StageBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Move to another stage (back is always free; ahead too, the assistant then says what is still open). The assistant opens the new stage."""
    proj = _project(db, ctx, pid)
    state = stages.load(proj)
    try:
        target = stages.can_go(state, body.stage)
    except stages.StageError as e:
        raise HTTPException(422, str(e)) from e
    if target is not state.stage:
        order = stages.stages_for(state.mode)
        if order.index(target) > order.index(state.stage) and state.stage.value not in state.done:
            state.done.append(state.stage.value)   # moving on counts as finishing the stage you leave
        state.stage = target
        _turn(db, ctx, proj, state, engine.START)
    return _view(state)


@router.post("/projects/{pid}/creation/advance")
def advance(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """"进入下一阶段": the current stage counts as done and the assistant opens the next one."""
    proj = _project(db, ctx, pid)
    state = stages.load(proj)
    nxt = stages.next_after(state)
    if nxt is None:
        raise HTTPException(422, "已经是最后一个阶段")
    if state.stage.value not in state.done:
        state.done.append(state.stage.value)
    state.stage = nxt
    _turn(db, ctx, proj, state, engine.START)
    return _view(state)


@router.post("/projects/{pid}/creation/undo/{mid}")
def undo(pid: str, mid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Take back the last change the assistant made (only the latest one, so nothing typed by hand afterwards is lost silently)."""
    proj = _project(db, ctx, pid)
    state = stages.load(proj)
    last = next((m for m in reversed(state.msgs) if m["role"] == "ai" and m.get("snapshot")), None)
    if not last or last["id"] != mid:
        raise HTTPException(409, "只能撤销最近一次修改")
    patches.restore(db, proj, state, last["snapshot"])
    last["snapshot"], last["undone"] = "", True
    stages.save(proj, state)
    audit(db, ctx.org_id, ctx.actor, "creation.undo", proj.id)
    db.commit()
    return _view(state)
