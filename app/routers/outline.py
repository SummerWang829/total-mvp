"""The 访谈 page's interview outline: read it, save a hand edit, talk to the AI to change it. Staff only (it holds the research goal); nothing here reaches participants."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import formats, guide_text, library_context, outline, outline_export, outline_lint, outline_translate, outline_versions, project_context as pctx, project_langs, assistant_identity
from .. import methods as methods_mod
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import STAFF, Ctx, ensure_project, require_roles
from ..models import Project, RecruitForm, utcnow
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat, text_extras
from ..recruit_form import MSG_VENDOR_MISSING
from ..study_draft import DraftError
from .projects import _can_manage

router = APIRouter(prefix="/api", tags=["outline"])
GENERATE_TIMEOUT_S = 30   # 生成访问大纲的单次硬超时：必须远低于网关回源超时，避免用户只看到 524


class GoalsBody(BaseModel):
    confirmed: bool = True


class OutlineBody(BaseModel):
    text: str = Field(max_length=guide_text.MAX_CHARS)
    v: str | None = Field(default=None, max_length=20)


class EditBody(BaseModel):
    op: Literal["set", "delete", "add", "insert", "dup", "move", "rename", "del_part", "add_part", "meta", "mark"]
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


def _screener_of(db: Session, proj: Project) -> dict:
    """The LATEST recruiting questionnaire, so 逐题分析 never shows an old draft's questions."""
    f = db.get(RecruitForm, proj.id)
    if f is None:
        return {"questions": [], "version": 0, "status": "", "published_at": None, "source": ""}
    qs = [str(q.get("text") or "").strip() for q in (f.questions or []) if isinstance(q, dict)]
    return {"questions": [q for q in qs if q], "version": int(f.version or 0), "status": f.status or "",
            "published_at": f.published_at.isoformat() if f.published_at else None, "source": f.source or ""}


def _versions_of(proj: Project, meta: dict) -> list[dict]:
    """Version names follow the project's real languages and formats, not platform defaults."""
    fam = meta["formats"]["family_labels"]
    langs = set(meta["language_codes"])
    many = len(outline_versions.families_of(proj)) > 1
    base = outline_versions.base_key(proj)
    out = []
    for row in outline_versions.listing(proj):
        # Only the languages / format families the project overview actually configured.
        if row["key"] != base and (row["lang"] not in langs or row["family"] not in fam):
            continue
        v = dict(row)
        fl = fam.get(v["family"]) or outline_versions.FAMILY_NAMES.get(v["family"], "")
        v["lang_name"] = pctx.lang_label(v["lang"])
        v["format_label"] = fl
        v["name"] = v["lang_name"] + (f" · {fl}" if many and fl else "")
        out.append(v)
    return out


def _families_of(versions: list[dict]) -> list[str]:
    """Only the format families that actually have a version; otherwise outline.js reads v.key of undefined."""
    return [f for f in ("live", "text") if any(v["family"] == f for v in versions)]


def _out(proj: Project, db: Session, key: str | None = None, ctx: Ctx | None = None) -> dict:
    key = _version(proj, key)
    text = outline_versions.text_of(proj, key)
    local = settings.moderator_provider == "mock" or moderator_runs_locally()
    summary = outline.summary_of(text)
    base = outline_versions.base_key(proj)
    base_sum = outline.summary_of(outline_versions.text_of(proj, base)) if key != base else None
    info = project_langs.info(db, proj)
    meta = pctx.meta(db, proj)
    can_edit = bool(ctx and (ctx.user.role in ("admin", "recruiter") or _can_manage(db, ctx, proj.id)))
    readonly = bool(ctx and ctx.user.role == "viewer")
    _gf = db.get(RecruitForm, proj.id)
    try:
        _planned = round(outline_lint._planned_minutes(guide_text.parse_guide_text(text), None), 1) if summary else 0.0
    except Exception:  # noqa: BLE001 - a broken outline must not break the page
        _planned = 0.0
    return {"text": text, "problem": outline.problem_of(text), "summary": summary, "warning": outline.warning_of(text),
            "readonly": readonly, "role": ctx.user.role if ctx else "researcher",
            "v": key, "base": base, "versions": _versions_of(proj, meta), "families": _families_of(_versions_of(proj, meta)),
            "family_names": outline_versions.FAMILY_NAMES, "lang": outline_versions.split(key)[0],
            "setup": {"source": meta["source"], "country": meta["country"], "language": meta["language"],
                      "languages": meta["languages"], "formats": meta["formats"]["names"],
                      "format_label": meta["formats"]["label"], "formats_source": meta["formats"]["source"]},
            "screener": _screener_of(db, proj),
            "ref": [[{"text": q["text"], "cover": bool(q.get("cover"))} for q in part["questions"]] for part in base_sum["parts"]] if base_sum else None,
            "settings": {"title": summary["topic"] if summary else "", "minutes": summary["minutes"] if summary else outline.DEFAULT_MINUTES, "brief": proj.brief or "", "goals": _goals(db, proj.id),
                         "languages": [{"code": r["code"], "name": pctx.lang_label(r["code"]), "on": r["on"], "default": r["default"], "in_use": r["in_use"]}
                                       for c in meta["language_codes"] for r in info["rows"] if r["code"] == c],
                         "formats": meta["formats"]["codes"], "format_names": formats.FORMATS, "can_edit_langs": can_edit},
            "context": {"brief": bool((proj.brief or "").strip()), "library_docs": len(library_context.project_documents(db, proj.id)), "parts": len(summary["parts"]) if summary else 0,
                        "questions": summary["question_count"] if summary else 0},
            "model": _model_label(), "ai_local": local, "ai_vendor": "" if local else settings.moderator_vendor.strip(),
            "goals": [str(g) for g in ((_gf.goals if _gf else []) or []) if str(g).strip()],
            "goals_confirmed": bool(_gf and _gf.goals_confirmed_at), "planned_minutes": _planned}


@router.get("/projects/{pid}/outline")
def get_outline(pid: str, v: str | None = None, ctx: Ctx = Depends(require_roles(*STAFF, "viewer")), db: Session = Depends(get_db)):
    return _out(_project(db, ctx, pid), db, v, ctx)


@router.post("/projects/{pid}/outline/goals")
def confirm_goals(pid: str, body: GoalsBody, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The researcher signs the research goals off (or takes it back)."""
    proj = _project(db, ctx, pid)
    form = db.get(RecruitForm, proj.id)
    if form is None:
        form = RecruitForm(project_id=proj.id, org_id=proj.org_id)
        db.add(form)
    form.goals_confirmed_at = utcnow() if body.confirmed else None
    audit(db, ctx.org_id, ctx.actor, "outline.goals_confirm", proj.id, confirmed=body.confirmed)
    db.commit()
    return {"goals_confirmed": body.confirmed, "goals": [str(g) for g in (form.goals or [])]}


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
def edit_outline(pid: str, body: EditBody, ctx: Ctx = Depends(require_roles(*STAFF, "viewer")), db: Session = Depends(get_db)):
    """Change one question or part from the outline view (the text is rewritten from the guide, so the two always agree)."""
    proj = _project(db, ctx, pid)
    key = _version(proj, body.v)
    # A client (viewer) may only mark / unmark a question as 重点关注 (and write the reason); every other edit is staff-only.
    if ctx.user.role == "viewer" and body.op != "mark":
        raise HTTPException(403, "客户只能标注重点关注，不能修改大纲")
    others = outline_versions.targets_for(proj, key, body.sync)
    translating = body.op in outline_versions.WORDING and any(outline_versions.split(k)[0] != outline_versions.split(key)[0] for k in others)
    value = body.value
    if body.op == "mark":
        by = "client" if ctx.user.role == "viewer" else "researcher"
        parts = value.strip().split("|")
        # value is "<flag>[|<reason>]" -> append the author role as a third segment.
        value = f"{parts[0]}|{parts[1] if len(parts) > 1 else ''}|{by}"
    try:
        sync = outline_versions.edit(proj, key, body.sync, body.op, body.kind, body.ref, body.index, value, _translator() if translating else None)
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


@router.get("/projects/{pid}/outline/export")
def export_outline(pid: str, format: str = "md", v: str | None = None, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """The outline as a finished document (任务包 11): Markdown or a printable HTML. Research team only; clients get an outward version through the report."""
    proj = _project(db, ctx, pid)
    key = _version(proj, v)
    summary = outline.summary_of(outline_versions.text_of(proj, key))
    if summary is None:
        raise HTTPException(422, "提纲还不完整，先把它写好再导出")
    form = db.get(RecruitForm, pid)
    if format == "html":
        body = outline_export.build_html(summary, proj, db, form, key)
        name = f"{(proj.name or 'outline')[:40]}-{key}.html"
        return Response(body, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    body = outline_export.build_markdown(summary, proj, db, form, key)
    name = f"{(proj.name or 'outline')[:40]}-{key}.md"
    return Response(body, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

@router.post("/projects/{pid}/outline/generate")
def generate_main_outline(pid: str, ctx: Ctx = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """生成访问大纲：用创建访谈里已做好的大纲规范/构造算法，按研究背景、目标、招募需求出一版。

    An existing outline is never overwritten. Offline (mock) returns a reason instead of failing the page.
    """
    import json
    proj = _project(db, ctx, pid)
    try:
        old = outline.summary_of(proj.outline_text or "")
        if old and (old.get("question_count") or 0) > 0:
            return {"generated": False, "reason": "已有访问大纲，未覆盖"}
    except Exception:
        pass
    chat = _translator()
    if chat is None:
        return {"generated": False, "reason": "模型未接入（演示模式）"}
    from ..creation import patches as creation_patches, specs as creation_specs
    f = db.get(RecruitForm, proj.id)
    goals = list(f.goals or []) if f else []
    need = (f.need if f else "") or ""
    brief = proj.brief or ""
    spec = getattr(creation_specs, "OUTLINE_SPEC", "")
    system = ("你是深访大纲设计助手。按下面的规范，把研究背景与目标写成一份访问大纲。"
              "只输出一个 JSON 对象：{\"participant_topic\":\"受访者称呼，最多 12 字\",\"warmup_questions\":[\"...\"],\"topics\":[{\"title\":\"...\",\"questions\":[\"...\"]}]}。"
              "暖场 1-2 题，话题 3-5 个、每话题 3-5 题；不要是非题、一句两问、诱导性提问；每题只问一件事，而且必须是完整的问句"
              "（至少 8 个字，不要写「在预约环节？」这类半截话）。\n\n"
              "OUTLINE SPEC\n" + spec)
    user = json.dumps({"研究背景": brief[:2000], "研究目标": goals, "招募需求": need[:2000]}, ensure_ascii=False)
    # 硬超时：底层 httpx 是 60s × 3 次重试，模型卡住时整条请求会拖过网关的回源超时，前端就只看到 524。这里 30 秒一定给结论。
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        try:
            # A worker thread does not inherit contextvars; copy the context so text_extras() reaches the call.
            # The 30-second hard timeout below is unchanged.
            with text_extras():
                ctx = contextvars.copy_context()
                future = pool.submit(ctx.run, chat.complete, [{"role": "system", "content": system}, {"role": "user", "content": user}], settings.ai_temperature)
            raw = future.result(timeout=GENERATE_TIMEOUT_S)
        except FutureTimeout:
            return {"generated": False, "reason": "模型这次响应太慢（已等 30 秒）。请再点一次「生成访问大纲」，或在左边对话里让它出大纲。"}
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        goal = ("；".join(goals) or brief)[:1000]
        guide, meta = creation_patches.build_guide(data, goal, proj.language or "zh", 45)
        text = guide_text.guide_to_text({**guide, "meta": meta})
        guide_text.parse_guide_text(text)  # must read back
    except Exception as exc:  # noqa: BLE001
        return {"generated": False, "reason": ("生成失败：%s %s" % (type(exc).__name__, exc))[:300]}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    proj.outline_text = text
    outline_versions.put_text(proj, outline_versions.base_key(proj), text)
    audit(db, ctx.org_id, ctx.actor, "outline.generate", pid)
    db.commit()
    return {"generated": True, "text": text[:200]}

