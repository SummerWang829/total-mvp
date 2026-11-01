"""Translations of the recruiting questionnaire (app/recruit_translate.py): see the state per language, translate with the AI, edit a question's translation by hand, confirm.
The questionnaire text goes to the AI service when it translates (the page says so). Every project language gets its version automatically; a language the project did not choose can be added
as a team-only version (never shown to candidates)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import project_langs, recruit_form, recruit_translate
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import RECRUITERS, Ctx, require_roles
from ..models import RecruitForm
from ..moderator.compliance import moderator_runs_locally
from ..moderator.providers import ChatFatalError, get_moderator_chat
from .projects import get_project

router = APIRouter(prefix="/api", tags=["recruit-translate"])


class TranslateBody(BaseModel):
    lang: str = Field(max_length=10)
    only_missing: bool = True  # leave confirmed translations alone; with False, everything that is not confirmed is made again


class EditBody(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    options: dict[str, str] = Field(default_factory=dict)


class ConfirmBody(BaseModel):
    qids: list[str] | None = None


def _form(db: Session, ctx: Ctx, pid: str):
    proj = get_project(db, ctx, pid)
    f = db.get(RecruitForm, proj.id)
    if f is None or not recruit_form.load(f.questions):
        raise HTTPException(409, "先写好招募问卷，再翻译")
    return proj, f, recruit_form.load(f.questions)


def _out(proj, f: RecruitForm, questions) -> dict:
    tr = f.translations or {}
    langs = project_langs.of(proj)
    summary = recruit_translate.summarize(questions, tr, langs)
    items = {x["code"]: {q.id: {**(tr.get(x["code"]) or {}).get(q.id, {}), "state": recruit_translate.state_of(q, (tr.get(x["code"]) or {}).get(q.id))} for q in questions} for x in summary["languages"] if not x["source"]}
    return {**summary, "items": items, "candidate": recruit_translate.candidate_languages(questions, tr, langs, proj.language),
            "ai_local": settings.moderator_provider == "mock" or moderator_runs_locally(), "ai_vendor": settings.moderator_vendor.strip()}


@router.get("/projects/{pid}/recruiting/form/translations")
def get_translations(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj, f, qs = _form(db, ctx, pid)
    return _out(proj, f, qs)


@router.post("/projects/{pid}/recruiting/form/translate")
def translate(pid: str, body: TranslateBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The AI translates the questions that need it into one language; the result is marked 机器翻译 until someone confirms it."""
    proj, f, qs = _form(db, ctx, pid)
    if body.lang not in project_langs.NAMES or body.lang == recruit_translate.source_lang(qs):
        raise HTTPException(422, "不能翻译成问卷原文的语言，也不认识这个语言")
    mock = settings.moderator_provider == "mock"
    if not mock and not moderator_runs_locally() and not settings.moderator_vendor.strip():
        raise HTTPException(409, recruit_form.MSG_VENDOR_MISSING)
    cur = (f.translations or {}).get(body.lang) or {}
    todo = [q for q in qs if not body.only_missing or recruit_translate.state_of(q, cur.get(q.id)) in ("missing", "stale")]
    if not todo:
        return _out(proj, f, qs)
    chat = None if mock else get_moderator_chat()
    try:
        made = recruit_translate.translate(chat, todo, body.lang)
    except ChatFatalError as e:
        raise HTTPException(502, f"小星星助手暂时不可用：{e.reason}") from e
    except recruit_form.FormError as e:
        raise HTTPException(502, str(e)) from e
    f.translations = recruit_translate.store(f.translations or {}, qs, body.lang, made, "machine")
    audit(db, ctx.org_id, ctx.actor, "recruit.translate", proj.id, lang=body.lang, questions=len(todo), model="mock" if chat is None else chat.name)  # never the text
    db.commit()
    return _out(proj, f, qs)


@router.put("/projects/{pid}/recruiting/form/translations/{lang}/{qid}")
def edit_translation(pid: str, lang: str, qid: str, body: EditBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Type or correct one question's translation by hand; a person wrote it, so it counts as confirmed."""
    proj, f, qs = _form(db, ctx, pid)
    q = next((x for x in qs if x.id == qid), None)
    if q is None or lang not in project_langs.NAMES:
        raise HTTPException(404, "没有这道题或这个语言")
    if set(body.options) != {o.id for o in q.options} or any(not v.strip() for v in body.options.values()):
        raise HTTPException(422, "每个选项都要有译文")
    f.translations = recruit_translate.store(f.translations or {}, qs, lang, {qid: {"text": body.text.strip(), "options": {k: v.strip()[:120] for k, v in body.options.items()}}}, "confirmed")
    audit(db, ctx.org_id, ctx.actor, "recruit.translation.edit", proj.id, lang=lang)
    db.commit()
    return _out(proj, f, qs)


@router.post("/projects/{pid}/recruiting/form/translations/{lang}/confirm")
def confirm_translation(pid: str, lang: str, body: ConfirmBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj, f, qs = _form(db, ctx, pid)
    f.translations = recruit_translate.confirm(f.translations or {}, qs, lang, body.qids)
    audit(db, ctx.org_id, ctx.actor, "recruit.translation.confirm", proj.id, lang=lang)
    db.commit()
    return _out(proj, f, qs)


def auto_translate(db: Session, proj, f: RecruitForm, qs: list[recruit_form.Question], ctx: Ctx | None = None, stale: bool = True) -> list[str]:
    """Make the version of every project language that is missing, and (with `stale`) the translations the original has outgrown. A best effort: a failure leaves that language out of what candidates are offered.

    Returns the language codes that were translated. Never raises for a model that is down or refuses: the questionnaire still publishes in its own language.
    """
    src, done = recruit_translate.source_lang(qs), []
    mock = settings.moderator_provider == "mock"
    if not mock and not moderator_runs_locally() and not settings.moderator_vendor.strip():
        return done  # the vendor is not named, so nothing may be sent
    chat = None if mock else get_moderator_chat()
    for lang in project_langs.of(proj):
        cur = (f.translations or {}).get(lang) or {}
        todo = [q for q in qs if lang != src and recruit_translate.state_of(q, cur.get(q.id)) in (("missing", "stale") if stale else ("missing",))]
        if not todo:
            continue
        try:
            made = recruit_translate.translate(chat, todo, lang)
        except (ChatFatalError, recruit_form.FormError):
            continue
        f.translations = recruit_translate.store(f.translations or {}, qs, lang, made, "machine")
        done.append(lang)
    if done and ctx is not None:
        audit(db, ctx.org_id, ctx.actor, "recruit.translate.auto", proj.id, langs=",".join(done), model="mock" if chat is None else chat.name)
    return done


class AllBody(BaseModel):
    stale: bool = False  # also translate again what the original has outgrown (the 更新译文 button); the automatic pass on opening the page leaves it, so the notice stays until a person asks


@router.post("/projects/{pid}/recruiting/form/translate-all")
def translate_all(pid: str, body: AllBody | None = None, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Every project language gets its version: what is missing is made; what is out of date only when asked (`stale`)."""
    proj, f, qs = _form(db, ctx, pid)
    if auto_translate(db, proj, f, qs, ctx, stale=bool(body and body.stale)):
        db.commit()
    return _out(proj, f, qs)
