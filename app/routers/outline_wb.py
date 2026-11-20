"""大纲工作台 — the "生成大纲" page's API: three rounds of questions, the overview table, suggestions, module-by-module questions, the side dialog's revisions and the focus questions.

Researchers (admin, researcher, support) work on the outline; a client (viewer) can read it and add focus questions, nothing else. A client can never accept a revision.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles, visible_interview
from ..models import Project
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatError, ChatFatalError, get_moderator_chat
from ..outline_wb import board, focus_new, pilot as wbpilot, session as sess, stage3
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


class MarkBody(BaseModel):
    line_id: str = Field(max_length=40)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    tag: str = Field(max_length=12)
    note: str = Field(default="", max_length=500)


class PilotBody(BaseModel):
    value: bool = True


class RecodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=80)


class BoardChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class BoardAdoptBody(BaseModel):
    ids: list[str] = Field(max_length=3)


class FocusSuggestBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class FocusNewBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    item_uids: list[str] = Field(default_factory=list, max_length=3)
    coding: str = Field(default="collect", max_length=10)
    codes: list[str] = Field(default_factory=list, max_length=12)


class LinesBody(BaseModel):
    line_ids: list[str] = Field(max_length=30)


class GroupsBody(BaseModel):
    groups: list[dict] = Field(max_length=12)


class CurrentBody(BaseModel):
    item_uid: str | None = Field(default=None, max_length=40)


class LineItemBody(BaseModel):
    item_uid: str = Field(max_length=40)


class FocusBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    item_uid: str | None = Field(default=None, max_length=40)


class LinkBody(BaseModel):
    item_uid: str | None = Field(default=None, max_length=40)


class LinkManyBody(BaseModel):
    item_uids: list[str] = Field(default_factory=list, max_length=40)
    state: str = Field(default="linked", max_length=10)


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ResetBody(BaseModel):
    mode: str = Field(max_length=10)


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


@router.put("/focus/{fid}/link")
def link_focus_many(pid: str, fid: str, body: LinkManyBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """修正方案第 1 轮第 3 条: several questions, 新增想法 (a pending new question) or 暂不对应."""
    chat = _chat() if body.state == "new_idea" else None
    return _run(db, sess.set_focus_link, db, _project(db, ctx, pid), fid, body.item_uids, body.state, chat, ctx.user.id, ctx.user.role)


@router.put("/focus/{fid}/idea")
def revise_idea(pid: str, fid: str, body: TextBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.revise_idea, db, _project(db, ctx, pid), fid, body.text, ctx.user.id, ctx.user.role)


@router.put("/background")
def edit_background(pid: str, body: TextBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.edit_background, db, _project(db, ctx, pid), body.text, ctx.user.id)


@router.post("/overview/keep")
def keep_overview(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, sess.keep_overview, db, _project(db, ctx, pid), ctx.user.id)


@router.post("/reset")
def reset(pid: str, body: ResetBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """修正方案第 1 轮第 7 条 重新开始 (only the researcher's answers, or everything on a version nobody has used); undone within 10 minutes."""
    p = _project(db, ctx, pid)
    out = _run(db, sess.reset, db, p, body.mode, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.reset", pid, ip=ctx.ip, mode=body.mode)
    db.commit()
    return out


@router.post("/reset/undo")
def undo_reset(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    out = _run(db, sess.undo_reset, db, p, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.reset_undo", pid, ip=ctx.ip)
    db.commit()
    return out


@router.get("/preview")
def preview(pid: str, lang: str | None = None, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.preview, db, _project(db, ctx, pid), lang)


@router.get("/rehearsal")
def rehearsal(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.rehearsal_view, db, _project(db, ctx, pid))


@router.post("/rehearsal")
def run_rehearsal(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.rehearse, db, _project(db, ctx, pid), ctx.user.id)


@router.get("/pilots")
def pilots(pid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, stage3.pilots, db, _project(db, ctx, pid))


@router.put("/pilots/{iid}")
def set_pilot(pid: str, iid: str, body: PilotBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.set_pilot, db, _project(db, ctx, pid), iid, body.value)


@router.get("/pilot-summary")
def pilot_summary(pid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, stage3.pilot_summary, db, _project(db, ctx, pid))


@router.post("/pilot-update")
def pilot_update(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.update_outline, db, _project(db, ctx, pid), ctx.user.id)


@router.get("/pilots/{iid}")
def pilot(pid: str, iid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, stage3.pilot_view, db, _project(db, ctx, pid), iid)


@router.post("/pilots/{iid}/marks")
def add_mark(pid: str, iid: str, body: MarkBody, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, stage3.mark, db, _project(db, ctx, pid), iid, body.line_id, body.start, body.end, body.tag, body.note, ctx.user.id, ctx.user.role)


@router.post("/pilots/{iid}/suggest")
def suggest_fixes(pid: str, iid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, stage3.suggest_fixes, db, _project(db, ctx, pid), iid, _chat(), ctx.user.id)


@router.get("/publish/check")
def publish_check(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    return {**_run(db, stage3.publish_checks, db, p), "versions": _run(db, stage3.versions, db, p)}


@router.post("/publish")
def publish(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    out = _run(db, stage3.publish, db, p, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.publish", pid, ip=ctx.ip, version=out["version"])
    db.commit()
    return out


# ---- 真人 Pilot 多轮（修正方案第 1 轮第 9、10 条）--------------------------------------------------------------------------------------

class RoundBody(BaseModel):
    participant_id: str = Field(min_length=1, max_length=40)
    scheduled_at: datetime | None = None
    watcher_ids: list[str] = Field(default_factory=list, max_length=40)
    send_email: bool = True


class DecisionBody(BaseModel):
    decision: str = Field(max_length=10)


class ReadyBody(BaseModel):
    value: bool = True


@router.get("/pilot-rounds")
def pilot_rounds(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, wbpilot.rounds, db, _project(db, ctx, pid))


@router.post("/pilot-rounds")
def start_pilot(pid: str, body: RoundBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """发起一轮 Pilot: a real interview on the current version (host as the project chose), the participant invited, the chosen team members emailed to watch hidden."""
    from .interviews import NewInterview, create_interview
    p = _project(db, ctx, pid)
    r = _run(db, wbpilot.open_round, db, p, ctx.user.id)
    ctx_min = sess.context_of(db, p, sess.ensure(db, p)[2]).get("minutes") or 45
    made = create_interview(pid, NewInterview(participant_id=body.participant_id, title=f"Pilot 第 {r.number} 轮", scheduled_at=body.scheduled_at, duration_min=max(15, min(240, int(ctx_min))),
                                              send_email=body.send_email, moderation="ai" if p.host_mode == "ai" else "human", moderator_id=ctx.user.id if ctx.user.role in ("researcher", "admin") and p.host_mode != "ai" else None), ctx, db)
    from ..models import Interview
    itv = db.get(Interview, made["id"])
    r = db.get(type(r), r.id)
    watchers = _run(db, wbpilot.attach, db, p, itv, r, body.watcher_ids)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.pilot.start", pid, ip=ctx.ip, round=r.number, invited=len(watchers))   # how many, never who
    db.commit()
    return {"round": r.number, "interview": made, "watchers": watchers, "rounds": _run(db, wbpilot.rounds, db, p)}


@router.get("/pilot-rounds/{rid}")
def pilot_review(pid: str, rid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, wbpilot.review, db, _project(db, ctx, pid), rid, ctx.user.role)


@router.post("/pilot-rounds/{rid}/suggest")
def pilot_suggest(pid: str, rid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, wbpilot.suggest, db, _project(db, ctx, pid), rid, _chat(), ctx.user.id)


@router.put("/marks/{mid}/decision")
def mark_decision(pid: str, mid: str, body: DecisionBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, wbpilot.decide, db, _project(db, ctx, pid), mid, body.decision, ctx.user.id, ctx.user.role)


@router.post("/pilot-rounds/{rid}/next-version")
def pilot_next_version(pid: str, rid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    out = _run(db, wbpilot.next_version, db, p, rid, ctx.user.id)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.wbpilot.next_version", pid, ip=ctx.ip, version=out["version"])
    db.commit()
    return out


@router.put("/ready")
def pilot_ready(pid: str, body: ReadyBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, wbpilot.ready, db, _project(db, ctx, pid), ctx.user.id, body.value)


@router.post("/publish/skip-pilot")
def publish_skip_pilot(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """跳过 Pilot，直接上线 (the user's design, 2026-11-19: no reason to fill in and no audit entry; only the red-line check stays)."""
    return _run(db, wbpilot.skip_publish, db, _project(db, ctx, pid), ctx.user.id)


# ---- 实时看板（访谈直播）----------------------------------------------------------------------------------------------------------

@router.get("/board")
def board_view(pid: str, since: float = 0.0, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    out = _run(db, board.board, db, p, since)
    out["suggestions"] = [] if ctx.user.role in VIEWERS else _run(db, board.suggestions, db, p)
    out["sessions"] = _run(db, board.sessions, db, p)
    out["can_edit"] = ctx.user.role not in VIEWERS
    return out


@router.get("/board/changes")
def board_changes(pid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, board.changes, db, _project(db, ctx, pid))


@router.get("/board/focus/{fid}")
def board_focus(pid: str, fid: str, origin: str = "all", ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, board.focus_detail, db, _project(db, ctx, pid), fid, origin)


@router.get("/board/answer/{cid}")
def board_answer(pid: str, cid: str, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    return _run(db, board.answer_detail, db, _project(db, ctx, pid), cid)


@router.put("/board/answer/{cid}")
def board_recode(pid: str, cid: str, body: RecodeBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, board.recode, db, _project(db, ctx, pid), cid, body.code)


@router.post("/board/answer/{cid}/report")
def board_report(pid: str, cid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, board.to_report, db, _project(db, ctx, pid), cid, ctx.user.id)


@router.post("/board/chat")
def board_chat(pid: str, body: BoardChatBody, ctx: Ctx = Depends(require_roles(*STAFF, *VIEWERS)), db: Session = Depends(get_db)):
    from .. import assistant_identity
    p = _project(db, ctx, pid)
    if assistant_identity.asks_identity(body.text):
        return {"reply": assistant_identity.REPLY, "who": []}
    return _run(db, board.ask, db, p, body.text)


@router.post("/board/suggestions/adopt")
def board_adopt(pid: str, body: BoardAdoptBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, board.adopt, db, _project(db, ctx, pid), body.ids, ctx.user.id)


# ---- 中途新增关注问题（阶段 5）------------------------------------------------------------------------------------------------------------

@router.post("/board/focus/suggest")
def focus_suggest(pid: str, body: FocusSuggestBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, focus_new.suggest_items, db, _project(db, ctx, pid), body.text)


@router.post("/board/focus/new")
def focus_create(pid: str, body: FocusNewBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    p = _project(db, ctx, pid)
    f = _run(db, focus_new.create, db, p, body.text, body.item_uids, body.coding, body.codes, ctx.user.id, ctx.user.role)
    return {"id": f.id}


@router.post("/board/focus/{fid}/add-question")
def focus_add_question(pid: str, fid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    r = _run(db, focus_new.add_question, db, _project(db, ctx, pid), fid, None, ctx.user.id)
    return {"revision": {"id": r.id, "op": r.op, "after": r.after, "reason": r.reason, "quality": r.quality, "status": r.status}}


@router.get("/board/focus/{fid}/backfill")
def focus_backfill(pid: str, fid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, focus_new.backfill_candidates, db, _project(db, ctx, pid), fid)


@router.post("/board/focus/{fid}/backfill")
def focus_backfill_confirm(pid: str, fid: str, body: LinesBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, focus_new.backfill_confirm, db, _project(db, ctx, pid), fid, body.line_ids)


@router.get("/board/focus/{fid}/clusters")
def focus_clusters(pid: str, fid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, focus_new.clusters, db, _project(db, ctx, pid), fid)


@router.post("/board/focus/{fid}/codes")
def focus_codes(pid: str, fid: str, body: GroupsBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _run(db, focus_new.set_codes, db, _project(db, ctx, pid), fid, body.groups)


# ---- 题号归属：真人主持说“正在问这一题”，研究员事后改某一句的题号 ------------------------------------------------------------------------

@router.put("/interviews/{iid}/current-question")
def current_question(pid: str, iid: str, body: CurrentBody, ctx: Ctx = Depends(require_roles(*STAFF, "moderator")), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.project_id != pid:
        raise HTTPException(404, "访谈不存在")
    _run(db, board.set_current, db, itv, body.item_uid)
    return {"current": itv.current_item_uid}


@router.put("/interviews/{iid}/lines/{lid}/item")
def line_item(pid: str, iid: str, lid: str, body: LineItemBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    itv = visible_interview(db, ctx, iid)
    if itv.project_id != pid:
        raise HTTPException(404, "访谈不存在")
    out = _run(db, board.reassign_line, db, itv, lid, body.item_uid)
    audit(db, ctx.org_id, ctx.actor, "outline_wb.line_item", iid, ip=ctx.ip)   # only that a number was fixed, never the words
    db.commit()
    return out


room_router = APIRouter(prefix="/api", tags=["outline-wb"])


@room_router.put("/interviews/{iid}/current-question")
def room_current_question(iid: str, body: CurrentBody, ctx: Ctx = Depends(require_roles(*STAFF, "moderator")), db: Session = Depends(get_db)):
    """The room's "标为正在问" button: the human moderator says which question is being asked (null = back to automatic)."""
    itv = visible_interview(db, ctx, iid)
    if itv.moderation == "ai":
        raise HTTPException(409, "AI 主持的场次不需要指定")
    _run(db, board.set_current, db, itv, body.item_uid)
    return {"current": itv.current_item_uid}


class RoomMarkBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)
    tag: str = Field(default="other", max_length=12)


@room_router.get("/interviews/{iid}/pilot-marks")
def room_pilot_marks(iid: str, ctx: Ctx = Depends(require_roles(*STAFF, "moderator", "observer")), db: Session = Depends(get_db)):
    """The 标记问题 panel of a pilot room (404 when the interview is not a pilot: the room then shows no panel). Never for the participant."""
    itv = visible_interview(db, ctx, iid)
    return _run(db, wbpilot.room_marks, db, itv, ctx.user.role)


@room_router.post("/interviews/{iid}/pilot-marks")
def room_add_pilot_mark(iid: str, body: RoomMarkBody, ctx: Ctx = Depends(require_roles(*STAFF, "moderator", "observer")), db: Session = Depends(get_db)):
    """Mark a problem during a pilot: only the words are sent; the time and the question number are set by the server."""
    itv = visible_interview(db, ctx, iid)
    _run(db, wbpilot.add_room_mark, db, itv, ctx.user.id, ctx.user.role, body.note, body.tag)
    return _run(db, wbpilot.room_marks, db, itv, ctx.user.role)
