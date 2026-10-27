"""The project's languages: read them, add or switch off a language, choose the default. Admins, project managers and the project's owners; it takes effect at once."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import formats, project_langs
from .. import methods as methods_mod
from ..audit import audit
from ..db import get_db
from ..deps import Ctx, require_roles
from .projects import _can_manage, get_project

router = APIRouter(prefix="/api", tags=["languages"])


class LanguagesBody(BaseModel):
    languages: list[str] | None = Field(default=None, min_length=1, max_length=4)
    default: str | None = Field(default=None, max_length=10)
    formats: dict[str, list[str]] | None = None  # method (depth / group) -> allowed interview formats


@router.get("/projects/{pid}/languages")
def get_languages(pid: str, ctx: Ctx = Depends(require_roles("admin", "researcher", "support", "recruiter")), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    return {**project_langs.info(db, proj), "can_edit": ctx.user.role in ("admin", "recruiter") or _can_manage(db, ctx, pid)}


@router.put("/projects/{pid}/languages")
def put_languages(pid: str, body: LanguagesBody, ctx: Ctx = Depends(require_roles("admin", "researcher", "support", "recruiter")), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    if ctx.user.role not in ("admin", "recruiter") and not _can_manage(db, ctx, pid):
        raise HTTPException(403, "只有管理员、项目经理或项目负责人能改项目语言")
    try:
        if body.languages is not None:
            project_langs.apply(db, proj, body.languages, body.default or proj.language)
        if body.formats is not None:
            proj.formats = formats.apply(proj.formats, methods_mod.of(proj), body.formats)
    except (project_langs.LanguageError, formats.FormatError) as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "project.languages", proj.id, languages=",".join(proj.languages), default=proj.language, formats=str(proj.formats))
    db.commit()
    return {**project_langs.info(db, proj), "can_edit": True}
