"""The languages a project allows (shown on the project overview, changed there at any time without publishing again).

`Project.languages` holds the codes (en / zh / ms / ta); `Project.language` stays the default: the language an interview gets when nobody picks one.
A project made before this existed has no list: it allows its one language. Adding a language is immediate; a language that open interviews still use cannot be switched off.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import formats, methods as methods_mod
from .config import settings
from .models import Interview, Project

NAMES = {"zh": "中文", "en": "English", "ms": "马来语", "ta": "泰米尔语"}
CONSENT_REVIEWED = {"zh", "en"}  # the consent and invitation wording legal has approved; ms / ta are still waiting for a native and legal review
AI_MODERATION = {"zh", "en"}  # the AI moderator speaks these (app/moderator/templates.py)


class LanguageError(ValueError):
    """The change is refused; the message says why, in plain Chinese."""


def of(p: Project) -> list[str]:
    """The codes the project allows, default first."""
    langs = [x for x in (p.languages or []) if x in NAMES] or [p.language if p.language in NAMES else "en"]
    return [p.language] + [x for x in langs if x != p.language] if p.language in langs else langs


def in_use(db: Session, project_id: str) -> dict[str, int]:
    """Interviews that have not ended, by language."""
    rows = db.execute(select(Interview.language, func.count()).where(Interview.project_id == project_id, Interview.status != "ended", Interview.purged_at.is_(None)).group_by(Interview.language)).all()
    return {lang: n for lang, n in rows}


def info(db: Session, p: Project) -> dict:
    """What the overview card shows: one row per language the platform offers."""
    on, use = of(p), in_use(db, p.id)
    live = methods_mod.of(p)
    return {"default": p.language, "languages": on, "formats": formats.of(p.formats, live), "format_names": formats.FORMATS,
            "rows": [{"code": c, "name": NAMES[c], "on": c in on, "default": c == p.language, "consent_reviewed": c in CONSENT_REVIEWED, "ai": c in AI_MODERATION, "in_use": use.get(c, 0)} for c in settings.languages if c in NAMES]}


def apply(db: Session, p: Project, languages: list[str], default: str) -> None:
    """Set the allowed languages and the default. The caller commits.

    Raises:
        LanguageError: an unknown language, a default that is not allowed, none left, or a language that open interviews still use.
    """
    langs = list(dict.fromkeys(languages))
    if not langs or any(x not in NAMES for x in langs):
        raise LanguageError("至少保留一种语言，并且只能是中文、English、马来语、泰米尔语")
    if default not in langs:
        raise LanguageError("默认访谈语言必须是项目允许的语言之一")
    use = in_use(db, p.id)
    busy = [NAMES[x] for x in of(p) if x not in langs and use.get(x)]
    if busy:
        raise LanguageError(f"{'、'.join(busy)}还有场次在用，先把这些场次改成别的语言再停用")
    p.languages, p.language = langs, default
