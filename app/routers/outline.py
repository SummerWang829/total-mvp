"""The 访谈 page's interview outline: read it, save a hand edit, talk to the AI to change it. Staff only (it holds the research goal); nothing here reaches participants."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import formats, guide_text, library_context, outline, outline_translate, outline_versions, project_langs, assistant_identity
from .. import methods as methods_mod
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project, RecruitForm
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat
from ..recruit_form import MSG_VENDOR_MISSING
from ..study_draft import DraftError
from .projects import _can_manage

router = APIRouter(prefix="/api", tags=["outline"])


class OutlineBody(BaseModel):
    text: str = Field(max_length=guide_text.MAX_CHARS)
    v: str | None = Field(default=None, max_length=20)


class EditBody(BaseModel):
    op: Literal["set", "delete", "add", "insert", "dup", "move", "rename", "del_part", "add_part", "meta"]
    kind: Literal["warmup", "topic", "stimulus"] = "topic"
    ref: int = Field(default=0, ge=0, le=50)
    index: int = Field(default=0, ge=0, le=100)
    value: str = Field(default="", max_length=600)  # "meta": "<depth>|<guidance>"
    v: str | None = Field(default=None, max_length=20)  # the version being edited (language|family); none = the main one
    sync: list[str] = Field(default_factory=list, max_length=20)  # the other versions that get a change of wording too (同步到)


class BatchBody(BaseModel):
    action: Literal["delete", "depth"]
    items: list[tuple[Literal["warmup", "topic", "stimulus"], int, int]] = Field(min_length=1, max_length=100)  # (kind, part position, question position)
    depth: str = Field(default="", max_length=10)
    v: str | None = Field(default=None, max_length=20)
    sync: list[str] = Field(default_factory=list, max_length=20)


class SettingsBody(BaseModel):
    """The 设置 page. Title and length belong to a version (and to the ones chosen in 同步到); the background and goals, the languages and the formats are the project's own, shared with the plan, the questionnaire and the overview."""
    v: str | None = Field(default=None, max_length=20)
    sync: list[str] = Field(default_factory=list, max_length=20)
    title: str | None = Field(default=None, max_length=40)
    minutes: int | None = Field(default=None, ge=10, le=180)
    brief: str | None = Field(default=None, max_length=5000)
    goals: list[str] | None = Field(default=None, max_length=8)
    languages: list[str] | None = Field(default=None, min_length=1, max_length=4)
    default: str | None = Field(default=None, max_length=10)
    formats: list[str] | None = Field(default=None, max_length=5)


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
    return assistant_identity.NAME   # never the model or vendor (user 2026-11-01)


def _goals(db: Session, pid: str) -> list[str]:
    f = db.get(RecruitForm, pid)
    return list(f.goals or []) if f else []


def _version(proj: Project, key: str | None) -> str:
    try:
        return outline_versions.resolve(proj, key)
    except outline_versions.VersionError as e:
        raise HTTPException(404, str(e)) from e


def _translator():
    """The model that translates when versions are kept in step: the same gate as the chat (a cloud model must name its vendor); None offline. Raises HTTPException 409 when the vendor is missing."""
    if settings.moderator_provider == "mock":
        return None
    if not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, MSG_VENDOR_MISSING)
    return get_moderator_chat()


def _out(proj: Project, db: Session, key: str | None = None, ctx: Ctx | None = None) -> dict:
    key = _version(proj, key)
    text = outline_versions.text_of(proj, key)
    local = settings.moderator_provider == "mock" or moderator_runs_locally()
    summary = outline.summary_of(text)
    base = outline_versions.base_key(proj)
    base_sum = outline.summary_of(outline_versions.text_of(proj, base)) if key != base else None
    info = project_langs.info(db, proj)
    can_edit = bool(ctx and (ctx.user.role in ("admin", "recruiter") or _can_manage(db, ctx, proj.id)))
    return {"text": text, "problem": outline.problem_of(text), "summary": summary, "warning": outline.warning_of(text),
            "v": key, "base": base, "versions": outline_versions.listing(proj), "families": outline_versions.families_of(proj),
            "family_names": outline_versions.FAMILY_NAMES, "lang": outline_versions.split(key)[0],
            "ref": [[{"text": q["text"], "cover": bool(q.get("cover"))} for q in part["questions"]] for part in base_sum["parts"]] if base_sum else None,
            "settings": {"title": summary["topic"] if summary else "", "minutes": summary["minutes"] if summary else outline.DEFAULT_MINUTES, "brief": proj.brief or "", "goals": _goals(db, proj.id),
                         "languages": [{"code": r["code"], "name": r["name"], "on": r["on"], "default": r["default"], "in_use": r["in_use"]} for r in info["rows"]],
                         "formats": formats.of(proj.formats, methods_mod.of(proj)).get("depth", []), "format_names": formats.FORMATS, "can_edit_langs": can_edit},
            "context": {"brief": bool((proj.brief or "").strip()), "library_docs": len(library_context.project_documents(db, proj.id)), "parts": len(summary["parts"]) if summary else 0,
                        "questions": summary["question_count"] if summary else 0},
            "model": _model_label(), "ai_local": local, "ai_vendor": "" if local else settings.moderator_vendor.strip()}


@router.get("/projects/{pid}/outline")
def get_outline(pid: str, v: str | None = None, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    return _out(_project(db, ctx, pid), db, v, ctx)


@router.put("/projects/{pid}/outline")
def put_outline(pid: str, body: OutlineBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Save the edited text as it is (a half-finished edit is kept); the answer says what is wrong with it, if anything."""
    proj = _project(db, ctx, pid)
    key = _version(proj, body.v)
    was_main = key == outline_versions.base_key(proj)
    outline_versions.put_text(proj, key, body.text)
    audit(db, ctx.org_id, ctx.actor, "outline.save", pid)
    db.commit()
    return _out(proj, db, outline_versions.base_key(proj) if was_main else key, ctx)  # the main version is named after the language its text is written in, which a saved text may change


@router.post("/projects/{pid}/outline/edit")
def edit_outline(pid: str, body: EditBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Change one question or part from the outline view (the text is rewritten from the guide, so the two always agree)."""
    proj = _project(db, ctx, pid)
    key = _version(proj, body.v)
    others = outline_versions.targets_for(proj, key, body.sync)
    translating = body.op in outline_versions.WORDING and any(outline_versions.split(k)[0] != outline_versions.split(key)[0] for k in others)
    try:
        sync = outline_versions.edit(proj, key, body.sync, body.op, body.kind, body.ref, body.index, body.value, _translator() if translating else None)
    except outline.EditError as e:
        raise HTTPException(422, str(e)) from e
    except (outline_translate.TranslateError, ChatFatalError) as e:
        raise HTTPException(502, f"同步到其他语言时翻译没成功：{getattr(e, 'reason', e)}") from e
    audit(db, ctx.org_id, ctx.actor, "outline.edit", pid, synced=len(sync["synced"]))
    db.commit()
    return {**_out(proj, db, key, ctx), "sync": sync}


@router.post("/projects/{pid}/outline/batch")
def batch_outline(pid: str, body: BatchBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Delete several questions, or set their follow-up depth, in one go (in the open version and the ones chosen in 同步到)."""
    proj = _project(db, ctx, pid)
    key = _version(proj, body.v)
    try:
        sync = outline_versions.batch(proj, key, body.sync, body.action, body.items, body.depth)
    except outline.EditError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "outline.batch", pid, what=body.action, n=sync["done"])
    db.commit()
    return {**_out(proj, db, key, ctx), "sync": sync}


@router.post("/projects/{pid}/outline/versions/{key}/generate")
def generate_version(pid: str, key: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Make a version that does not exist yet (translated, or rewritten for a text chat) from the best one there is. The words go to the AI service when the language differs."""
    proj = _project(db, ctx, pid)
    key = _version(proj, key)
    try:
        outline_versions.generate(proj, key, _translator())
    except outline_versions.VersionError as e:
        raise HTTPException(422, str(e)) from e
    except (outline_translate.TranslateError, ChatFatalError) as e:
        raise HTTPException(502, f"生成这个版本时翻译没成功：{getattr(e, 'reason', e)}") from e
    audit(db, ctx.org_id, ctx.actor, "outline.version", pid, version=key)
    db.commit()
    return _out(proj, db, key, ctx)


@router.put("/projects/{pid}/outline/settings")
def put_settings(pid: str, body: SettingsBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The 设置 page. The research background, the goals, the languages and the formats are the project's own data (the plan, the questionnaire and the overview read the same), so a change here shows there at once."""
    proj = _project(db, ctx, pid)
    key, sync = _version(proj, body.v), {"synced": [], "skipped": []}
    try:
        if body.languages is not None or body.formats is not None:
            if ctx.user.role not in ("admin", "recruiter") and not _can_manage(db, ctx, pid):
                raise HTTPException(403, "只有管理员、项目经理或项目负责人能改项目语言和访谈形式")
            if body.languages is not None:
                project_langs.apply(db, proj, body.languages, body.default or proj.language)
            if body.formats is not None:
                proj.formats = formats.apply(proj.formats, methods_mod.of(proj), {"depth": body.formats})
        if body.title is not None or body.minutes is not None:
            title = body.title.strip() if body.title is not None else None
            valid = set(outline_versions.keys_of(proj))
            others = [k for k in body.sync if k in valid and k != key and outline_versions.text_of(proj, k).strip()] if title else []
            translating = any(outline_versions.split(k)[0] != outline_versions.split(key)[0] for k in others)
            sync = outline_versions.set_header(proj, key, body.sync, title, body.minutes, _translator() if translating else None)
    except (project_langs.LanguageError, formats.FormatError, outline.EditError) as e:
        raise HTTPException(422, str(e)) from e
    except (outline_translate.TranslateError, ChatFatalError) as e:
        raise HTTPException(502, f"同步到其他语言时翻译没成功：{getattr(e, 'reason', e)}") from e
    if body.brief is not None:
        proj.brief = body.brief
    if body.goals is not None:
        form = db.get(RecruitForm, pid)
        if form is None:
            form = RecruitForm(project_id=pid, org_id=ctx.org_id)
            db.add(form)
        form.goals = [g.strip()[:300] for g in body.goals if g.strip()]
    audit(db, ctx.org_id, ctx.actor, "outline.settings", pid)
    db.commit()
    if key not in outline_versions.keys_of(proj):  # a language or format was switched off: the open version is gone
        key = outline_versions.base_key(proj)
    return {**_out(proj, db, key, ctx), "sync": sync}


