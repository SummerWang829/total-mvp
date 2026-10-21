"""The public recruiting questionnaire: a candidate opens the link, answers question by question, and the ones who qualify leave their contact and land in the project's list.

No login: the link's token is the only way in. The server walks the logic (app/recruit_form.py) and shows one question at a time; the page never learns which answers screen
someone out. Nothing is kept about people who are screened out except a count (and which question ended it); a person who qualifies is stored with their answers (which the
AI moderator may later read: the consent text of an AI interview says so).
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ratelimit, recruit, recruit_form, screener
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import client_ip
from ..models import Org, Participant, Project, RecruitForm
from ..retention import days_of

router = APIRouter(prefix="/api/public/screen", tags=["screening"])
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StepBody(BaseModel):
    answers: dict = Field(default_factory=dict)


class SubmitBody(BaseModel):
    answers: dict = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=200)
    country: str = Field(min_length=2, max_length=2)
    consent: bool = False


def _live(db: Session, token: str) -> tuple[RecruitForm, Project]:
    f = db.scalar(select(RecruitForm).where(RecruitForm.token == token)) if token else None
    if f is None:
        raise HTTPException(404, "Invalid link / 链接无效")
    return f, db.get(Project, f.project_id)


def _open(f: RecruitForm) -> None:
    if f.status != "published" or f.source != "ai":
        raise HTTPException(410, "This questionnaire is closed / 这份问卷已经关闭")


@router.get("/{token}")
def screen_info(token: str, db: Session = Depends(get_db)):
    """What the page shows before the first question: who asks, the language, how the answers are used."""
    f, proj = _live(db, token)
    return {"org": db.get(Org, f.org_id).name, "language": proj.language, "open": f.status == "published" and f.source == "ai", "dpo": settings.dpo_email,
            "retention_days": days_of(proj), "countries": sorted(settings.allowed_countries), "total": len(f.questions or [])}


@router.post("/{token}/step")
def screen_step(token: str, body: StepBody, request: Request, db: Session = Depends(get_db)):
    """The next question given the answers so far, or the end. Someone screened out is only counted (and where); nothing about them is stored."""
    if wait := ratelimit.screen_step_allowed(client_ip(request)):
        raise HTTPException(429, ratelimit.MESSAGE, headers={"Retry-After": str(wait)})
    f, _ = _live(db, token)
    _open(f)
    try:
        step = recruit_form.next_step(recruit_form.load(f.questions), body.answers)
    except recruit_form.AnswerError as e:
        raise HTTPException(422, "Please check your answer / 请检查你的回答") from e
    if step.result == "out":
        f.n_out = (f.n_out or 0) + 1
        f.out_by = {**(f.out_by or {}), step.ended_at: (f.out_by or {}).get(step.ended_at, 0) + 1}
        db.commit()
    return {"question": step.question, "result": step.result}


@router.post("/{token}/submit", status_code=201)
def screen_submit(token: str, body: SubmitBody, request: Request, db: Session = Depends(get_db)):
    """A person who qualified leaves their contact: they join the project's participant list, already marked as qualified, with their answers and tags.

    The answers are walked again here (the page's word is not taken), so only someone who really qualifies gets in. The same email in the same project is
    accepted without adding a second row, and the answer does not tell whether it was already there.
    """
    if wait := ratelimit.signup_allowed(client_ip(request)):
        raise HTTPException(429, ratelimit.MESSAGE, headers={"Retry-After": str(wait)})
    f, proj = _live(db, token)
    _open(f)
    if not body.consent:
        raise HTTPException(422, "Please agree to the notice / 请先同意隐私说明")
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "Please enter a valid e-mail / 请填写有效的邮箱")
    try:
        step = recruit_form.next_step(recruit_form.load(f.questions), body.answers)
    except recruit_form.AnswerError as e:
        raise HTTPException(422, "Please check your answers / 请检查你的回答") from e
    if step.result != "qualified":
        raise HTTPException(409, "Please finish the questions first / 请先完成问卷")
    country = body.country.upper()
    if not settings.region_open(country):
        raise HTTPException(403, "Sorry, this study is not open in your region yet / 抱歉，这项研究暂时不面向你所在的地区")
    same = db.scalar(select(Participant.id).where(Participant.project_id == proj.id, Participant.email == email, Participant.anonymized_at.is_(None)))
    if same is None:
        p = Participant(org_id=proj.org_id, project_id=proj.id, name=body.name.strip(), email=email, country=country, language=proj.language,
                        tags=recruit.clean_tags(step.tags), screening="eligible", screen_result="qualified", screen_reason=f"招募问卷（第 {f.version} 版）：符合")
        db.add(p)
        db.flush()
        screener.save(db, proj.org_id, p.id, step.items)
        f.n_qualified = (f.n_qualified or 0) + 1
        audit(db, proj.org_id, "participant:screen", "recruit.form_qualified", proj.id, participant=p.id)
    db.commit()
    return {"ok": True}
