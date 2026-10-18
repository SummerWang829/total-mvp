"""Quantitative results from a survey platform: pushed through a keyed API or imported from a spreadsheet, kept next to the interviews.

The platform is not built here: an outside survey tool sends (1) the people who passed screening, with their answers, and (2) the aggregated results of a
survey. Keys belong to one project, are shown once and stored as hashes, and can be revoked. Everything sent is checked for size and shape; nothing is
trusted to be well formed.
"""
import csv
import hashlib
import io
import re
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import screener
from .config import settings
from .models import IngestKey, Participant, Project, QuantSurvey, utcnow

KEY_PREFIX = "xtk_"
MAX_PEOPLE_PER_CALL = 200
MAX_QUESTIONS = 100
MAX_OPTIONS = 60
TYPES = ("single", "multi", "scale", "open")
MAX_TEXT = 300


class QuantError(ValueError):
    """What was sent cannot be used; the message says why in plain Chinese (the API returns it as 422)."""


def new_key() -> tuple[str, str, str]:
    """(the key to show once, its hash, a short prefix to recognise it by)."""
    key = KEY_PREFIX + secrets.token_urlsafe(30)
    return key, hash_key(key), key[:8]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_row(db: Session, presented: str) -> IngestKey | None:
    """The live key row for a presented key (None for an unknown or revoked key)."""
    if not presented.startswith(KEY_PREFIX):
        return None
    row = db.scalar(select(IngestKey).where(IngestKey.key_hash == hash_key(presented)))
    return row if row and row.revoked_at is None else None


def _text(v, limit: int = MAX_TEXT) -> str:
    return str(v if v is not None else "").strip()[:limit]


def clean_questions(raw) -> list[dict]:
    """The questions of a survey as stored: {"key","text","type","results":[{"option","count"}],"mean"}.

    Raises:
        QuantError: not a list, too many, or a question without text or results.
    """
    if not isinstance(raw, list) or not raw or len(raw) > MAX_QUESTIONS:
        raise QuantError(f"questions 要是 1 到 {MAX_QUESTIONS} 道题的列表")
    out = []
    for n, q in enumerate(raw, start=1):
        if not isinstance(q, dict) or not _text(q.get("text")):
            raise QuantError(f"第 {n} 题缺少题目文字")
        typ = _text(q.get("type"), 10) or "single"
        if typ not in TYPES:
            raise QuantError(f"第 {n} 题的类型要是 {'/'.join(TYPES)} 之一")
        results = []
        for r in q.get("results") or []:
            try:
                count = int(r["count"])
            except (KeyError, TypeError, ValueError) as e:
                raise QuantError(f"第 {n} 题的选项需要 option 和数字 count") from e
            if count < 0 or not _text(r.get("option")):
                raise QuantError(f"第 {n} 题的选项不对")
            results.append({"option": _text(r["option"], 100), "count": count})
        if len(results) > MAX_OPTIONS:
            raise QuantError(f"第 {n} 题的选项太多（最多 {MAX_OPTIONS}）")
        mean = q.get("mean")
        try:
            mean = None if mean is None else round(float(mean), 3)
        except (TypeError, ValueError) as e:
            raise QuantError(f"第 {n} 题的 mean 要是数字") from e
        if typ != "open" and not results and mean is None:
            raise QuantError(f"第 {n} 题没有结果（results 或 mean）")
        out.append({"key": _text(q.get("key"), 60) or f"q{n}", "text": _text(q["text"]), "type": typ, "results": results, "mean": mean})
    return out


def upsert_survey(db: Session, org_id: str, project_id: str, external_id: str, title: str, respondents, questions, source: str) -> tuple[QuantSurvey, bool]:
    """Create or update a survey by its external id (sending the same results again changes nothing; newer results replace older). Returns (row, created).

    Raises:
        QuantError: unusable input.
    """
    external_id = _text(external_id, 100)
    if not external_id or not _text(title, 200):
        raise QuantError("需要 survey_id 和 title")
    try:
        n = int(respondents)
    except (TypeError, ValueError) as e:
        raise QuantError("respondents 要是数字") from e
    if not 0 <= n <= 10_000_000:
        raise QuantError("respondents 不合理")
    qs = clean_questions(questions)
    row = db.scalar(select(QuantSurvey).where(QuantSurvey.project_id == project_id, QuantSurvey.external_id == external_id))
    created = row is None
    if created:
        row = QuantSurvey(org_id=org_id, project_id=project_id, external_id=external_id, title=_text(title, 200), respondents=n, questions=qs, source=source)
        db.add(row)
    else:
        row.title, row.respondents, row.questions, row.source, row.updated_at = _text(title, 200), n, qs, source, utcnow()
    db.flush()
    return row, created


def parse_results_csv(text: str) -> list[dict]:
    """A results spreadsheet (columns: question, option, count; optional: type) as questions.

    Raises:
        QuantError: empty, missing columns, or a count that is not a number.
    """
    text = text.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text), dialect=csv.excel_tab if text[:2000].count("\t") > text[:2000].count(",") else csv.excel))
    names = {k.strip().lower(): k for k in (rows[0].keys() if rows else [])}
    alias = {"question": ("question", "题目", "问题"), "option": ("option", "选项"), "count": ("count", "人数", "数量")}
    col = {k: next((names[a] for a in alts if a in names), None) for k, alts in alias.items()}
    if not rows or not all(col.values()):
        raise QuantError("表格需要“题目、选项、人数”三列（question, option, count），第一行是表头")
    qs: dict[str, dict] = {}
    for line, r in enumerate(rows, start=2):
        q, o = _text(r.get(col["question"])), _text(r.get(col["option"]), 100)
        if not q and not o:
            continue
        try:
            c = int(float(_text(r.get(col["count"])).replace(",", "")))
        except ValueError as e:
            raise QuantError(f"第 {line} 行的人数不是数字") from e
        typ = _text(r.get(names.get("type", ""), ""), 10) or "single"
        qs.setdefault(q, {"text": q, "type": typ if typ in TYPES else "single", "results": []})["results"].append({"option": o, "count": c})
    return clean_questions(list(qs.values()))


def upsert_people(db: Session, project: Project, people: list, now: datetime | None = None) -> dict:
    """Create or update the people a survey platform sends (matched by their external id in this project). Returns {"created", "updated"}.

    Raises:
        QuantError: too many, or a person without external_id / name, or a bad answers list.
    """
    if not isinstance(people, list) or not people or len(people) > MAX_PEOPLE_PER_CALL:
        raise QuantError(f"participants 要是 1 到 {MAX_PEOPLE_PER_CALL} 人的列表")
    created = updated = 0
    for n, p in enumerate(people, start=1):
        if not isinstance(p, dict) or not _text(p.get("external_id"), 100) or not _text(p.get("name"), 100):
            raise QuantError(f"第 {n} 人缺少 external_id 或 name")
        answers = []
        for a in p.get("answers") or []:
            if not isinstance(a, dict) or not _text(a.get("question"), screener.MAX_QUESTION_CHARS) or not _text(a.get("answer"), screener.MAX_ANSWER_CHARS):
                raise QuantError(f"第 {n} 人的 answers 每项要有 question 和 answer")
            answers.append((_text(a["question"], screener.MAX_QUESTION_CHARS), _text(a["answer"], screener.MAX_ANSWER_CHARS)))
        if len(answers) > screener.MAX_ITEMS:
            raise QuantError(f"第 {n} 人的 answers 太多（最多 {screener.MAX_ITEMS}）")
        email = _text(p.get("email"), 200)
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise QuantError(f"第 {n} 人的邮箱格式不对")
        country = _text(p.get("country"), 2).upper()
        lang = _text(p.get("language"), 10).lower()
        if lang and lang not in ("en", "zh", "ms", "ta"):
            raise QuantError(f"第 {n} 人的 language 只能是 en / zh / ms / ta")
        ext = _text(p.get("external_id"), 100)
        row = db.scalar(select(Participant).where(Participant.project_id == project.id, Participant.external_id == ext))
        tags = [_text(t, 30) for t in (p.get("tags") or []) if _text(t, 30)][:10]
        if row is None:
            row = Participant(org_id=project.org_id, project_id=project.id, external_id=ext, name=_text(p["name"], 100), email=email, country=country, language=lang or project.language,
                              screening="eligible" if (not country or settings.region_open(country)) else "blocked_region", tags=tags)
            db.add(row)
            created += 1
        elif row.anonymized_at is None:
            row.name, row.email, row.country, row.tags = _text(p["name"], 100), email or row.email, country or row.country, tags or row.tags
            row.language = lang or row.language
            updated += 1
        else:
            continue  # erased on request: never brought back by a later push
        db.flush()
        if answers:
            screener.save(db, project.org_id, row.id, answers)
    db.flush()
    return {"created": created, "updated": updated}
