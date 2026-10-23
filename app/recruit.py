"""Recruiting: bulk import from a spreadsheet, tags, eligibility rules over the screening answers, and quotas.

Import (CSV, UTF-8, first row = headers): `name` is required; `email`, `country`, `language` and `tags` (separated by ; or |) are known columns; every other
column is read as a screening question (the header is the question, the cell is the answer), exactly like pasted screening answers.

Eligibility rules are a short, versioned list that is evaluated over the screening answers (and country / language / tags). The result is one of
qualified / disqualified / review, always with the reason in plain words, so a person can see why and change it.

Quotas say how many people of a kind the study needs ("country SG: 10", "tag 女性: 5") and how many there are so far.
"""
import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import screener
from .config import settings
from .models import Interview, Participant, ScreenerAnswer

MAX_ROWS = 500
EXTERNAL_REASON = "外部名单（未经问卷甄别）"  # people brought in from another channel and invited directly: the rules do not judge them again
MAX_TAGS = 10
MAX_TAG_CHARS = 30
MAX_RULES = 30
MAX_QUOTAS = 30
KNOWN = {"name", "email", "country", "language", "tags"}
ALIASES = {"姓名": "name", "名字": "name", "邮箱": "email", "email": "email", "国家": "country", "语言": "language", "标签": "tags"}
OPS = ("equals", "in", "not_in", "contains", "not_contains", "gte", "lte", "between", "answered")
EFFECTS = ("require", "exclude", "flag")
FIELDS = ("country", "language", "tags")


class RecruitError(ValueError):
    """The upload or the rules cannot be used; the message says why in plain Chinese."""


@dataclass
class ImportRow:
    line: int
    name: str
    email: str = ""
    country: str = ""
    language: str = ""
    tags: list[str] = field(default_factory=list)
    answers: list[tuple[str, str]] = field(default_factory=list)


def clean_tags(raw) -> list[str]:
    """Tags from a list or from text separated by ; | , (trimmed, de-duplicated, at most MAX_TAGS of MAX_TAG_CHARS)."""
    items = re.split(r"[;|；，,]", raw) if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for t in items:
        t = str(t).strip()[:MAX_TAG_CHARS]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def parse_csv(text: str) -> tuple[list[ImportRow], list[dict]]:
    """Read the spreadsheet text. Returns (rows, problems): a row with a problem is left out and reported with its line number.

    Raises:
        RecruitError: empty, no `name` column, or more than MAX_ROWS rows.
    """
    text = text.lstrip("﻿")
    if not text.strip():
        raise RecruitError("文件是空的")
    sample = text[:2000]
    dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = [h.strip() for h in next(reader, [])]
    keys = [ALIASES.get(h.lower(), ALIASES.get(h, h.lower() if h.lower() in KNOWN else h)) for h in header]
    if "name" not in keys:
        raise RecruitError("第一行必须有“name”（或“姓名”）这一列")
    rows: list[ImportRow] = []
    problems: list[dict] = []
    for line, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(rows) + len(problems) >= MAX_ROWS:
            raise RecruitError(f"一次最多导入 {MAX_ROWS} 行，请分几次")
        rec = {k: (cells[i].strip() if i < len(cells) else "") for i, k in enumerate(keys)}
        if not rec.get("name"):
            problems.append({"line": line, "why": "没有姓名"})
            continue
        answers = [(k, v) for k, v in rec.items() if k not in KNOWN and k and v]
        if any(len(k) > screener.MAX_QUESTION_CHARS or len(v) > screener.MAX_ANSWER_CHARS for k, v in answers) or len(answers) > screener.MAX_ITEMS:
            problems.append({"line": line, "why": "甄别题太长或太多"})
            continue
        email = rec.get("email", "")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            problems.append({"line": line, "why": f"邮箱格式不对：{email[:40]}"})
            continue
        country = rec.get("country", "").upper()
        if country and (len(country) != 2 or not country.isalpha()):
            problems.append({"line": line, "why": "国家要写两位代码，例如 SG"})
            continue
        lang = rec.get("language", "").lower()
        if lang and lang not in ("en", "zh", "ms", "ta"):
            problems.append({"line": line, "why": "语言只能是 en / zh / ms / ta"})
            continue
        rows.append(ImportRow(line, rec["name"][:100], email[:200], country, lang, clean_tags(rec.get("tags", "")), answers))
    return rows, problems


def import_rows(db: Session, org_id: str, project_id: str, rows: list[ImportRow], default_language: str, dry_run: bool, source: str = "", source_name: str = "", invite: bool = False) -> dict:
    """Create the participants (skipping ones already in the project with the same email, or the same name and country). The caller commits.

    Returns {"created": n, "skipped": [{"line", "why"}]}.
    """
    existing = db.scalars(select(Participant).where(Participant.project_id == project_id)).all()
    seen_email = {p.email.lower() for p in existing if p.email}
    seen_name = {(p.name.strip().lower(), p.country) for p in existing}
    created, skipped = 0, []
    for r in rows:
        key = (r.name.strip().lower(), r.country)
        if (r.email and r.email.lower() in seen_email) or (not r.email and key in seen_name):
            skipped.append({"line": r.line, "why": "已经在这个项目里"})
            continue
        if r.email:
            seen_email.add(r.email.lower())
        seen_name.add(key)
        created += 1
        if dry_run:
            continue
        screening = "eligible" if (not r.country or settings.region_open(r.country)) else "blocked_region"
        p = Participant(org_id=org_id, project_id=project_id, name=r.name, email=r.email, country=r.country, language=r.language or default_language, screening=screening, tags=r.tags,
                        source=source, source_name=source_name, screen_result="qualified" if invite else "", screen_reason=EXTERNAL_REASON if invite else "")
        db.add(p)
        db.flush()
        screener.save(db, org_id, p.id, r.answers)
    return {"created": created, "skipped": skipped}


# ---------- rules ----------
def validate_rules(rules: list) -> list[dict]:
    """The rules as stored (anything unknown is refused with the rule's number).

    Each rule: {"field": country|language|tags} or {"question": text}; "op"; "value"; "effect"; optional "note".

    Raises:
        RecruitError: not a list, too many, or a rule that cannot work.
    """
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise RecruitError(f"最多 {MAX_RULES} 条规则")
    out = []
    for n, r in enumerate(rules, start=1):
        if not isinstance(r, dict):
            raise RecruitError(f"第 {n} 条规则格式不对")
        fld, q, op, eff = str(r.get("field", "")).strip(), str(r.get("question", "")).strip(), r.get("op"), r.get("effect")
        if bool(fld) == bool(q) or (fld and fld not in FIELDS):
            raise RecruitError(f"第 {n} 条：要么填国家/语言/标签，要么填甄别题的关键词（只能填一个）")
        if op not in OPS or eff not in EFFECTS:
            raise RecruitError(f"第 {n} 条：条件或处理方式不对")
        val = r.get("value")
        if op in ("gte", "lte"):
            try:
                val = float(val)
            except (TypeError, ValueError) as e:
                raise RecruitError(f"第 {n} 条：这个条件需要一个数字") from e
        elif op == "between":
            try:
                val = [float(val[0]), float(val[1])]
            except (TypeError, ValueError, IndexError, KeyError) as e:
                raise RecruitError(f"第 {n} 条：区间要写两个数字") from e
        elif op in ("in", "not_in"):
            val = [str(v).strip() for v in (val if isinstance(val, list) else re.split(r"[;|；，,]", str(val or ""))) if str(v).strip()]
            if not val:
                raise RecruitError(f"第 {n} 条：请填写至少一个值")
        elif op in ("equals", "contains", "not_contains"):
            val = str(val or "").strip()
            if not val:
                raise RecruitError(f"第 {n} 条：请填写要比较的内容")
        else:
            val = None
        out.append({"field": fld, "question": q[:200], "op": op, "value": val, "effect": eff, "note": str(r.get("note", ""))[:200]})
    return out


def _number(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def _holds(op: str, value, answer: str) -> bool:
    a = answer.strip().lower()
    if op == "answered":
        return bool(a)
    if op == "equals":
        return a == str(value).lower()
    if op == "contains":
        return str(value).lower() in a
    if op == "not_contains":
        return str(value).lower() not in a
    if op in ("in", "not_in"):
        hit = any(v.lower() == a or v.lower() in a for v in value)
        return hit if op == "in" else not hit
    n = _number(a)
    if n is None:
        return False
    return n >= value if op == "gte" else n <= value if op == "lte" else value[0] <= n <= value[1]


def evaluate(rules: list[dict], p: Participant, answers: list[tuple[str, str]]) -> tuple[str, str]:
    """(result, reason) of one participant: disqualified if an `exclude` rule matches or a `require` rule fails; review if an answer is missing or a
    `flag` rule matches; qualified otherwise. Without rules everyone is simply 'qualified'."""
    reasons_no, reasons_review = [], []
    for r in rules:
        label = r["note"] or (r["field"] or r["question"])
        if r["field"]:
            got = {"country": p.country, "language": p.language, "tags": ";".join(p.tags or [])}[r["field"]]
            if not got and r["field"] != "tags":   # not known yet (a country is declared at consent): a person decides, it is not a "no"
                if r["effect"] == "require":
                    reasons_review.append(f"缺少“{label}”的信息")
                continue
        else:
            got = next((a for q, a in answers if r["question"].lower() in q.lower()), None)
            if got is None:
                if r["effect"] == "require":
                    reasons_review.append(f"缺少“{label}”的答案")
                continue
        ok = _holds(r["op"], r["value"], got)
        if r["effect"] == "require" and not ok:
            reasons_no.append(f"不满足：{label}")
        elif r["effect"] == "exclude" and ok:
            reasons_no.append(f"命中排除条件：{label}")
        elif r["effect"] == "flag" and ok:
            reasons_review.append(f"需要人工看：{label}")
    if reasons_no:
        return "disqualified", "；".join(reasons_no)[:300]
    if reasons_review:
        return "review", "；".join(reasons_review)[:300]
    return "qualified", ""


def apply_rules(db: Session, project_id: str, rules: list[dict], version: int) -> dict:
    """Evaluate every participant of the project and store the result with the rule version. The caller commits. Returns the counts."""
    db.flush()  # (sessions here do not flush by themselves: people and answers added a moment ago must be seen)
    people = db.scalars(select(Participant).where(Participant.project_id == project_id, Participant.anonymized_at.is_(None))).all()
    by_person: dict[str, list[tuple[str, str]]] = {}
    for a in db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id.in_([p.id for p in people]) if people else ScreenerAnswer.id == "")):
        by_person.setdefault(a.participant_id, []).append((a.question, a.answer))
    counts = {"qualified": 0, "disqualified": 0, "review": 0}
    for p in people:
        if p.screen_reason == EXTERNAL_REASON:
            counts["qualified"] += 1
            continue
        p.screen_result, p.screen_reason = evaluate(rules, p, by_person.get(p.id, []))
        p.screen_version = version
        counts[p.screen_result] += 1
    return counts


# ---------- quotas ----------
def validate_quotas(items: list) -> list[dict]:
    """Quotas as stored: {"name", "field": country|language|tag|answer, "question" (for answer), "value", "target"}.

    Raises:
        RecruitError: too many or unusable.
    """
    if not isinstance(items, list) or len(items) > MAX_QUOTAS:
        raise RecruitError(f"最多 {MAX_QUOTAS} 个配额")
    out = []
    for n, q in enumerate(items, start=1):
        if not isinstance(q, dict) or q.get("field") not in ("country", "language", "tag", "answer") or not str(q.get("value", "")).strip():
            raise RecruitError(f"第 {n} 个配额：请选择类型并填写值")
        if q["field"] == "answer" and not str(q.get("question", "")).strip():
            raise RecruitError(f"第 {n} 个配额：请填写甄别题关键词")
        try:
            target = int(q.get("target"))
        except (TypeError, ValueError) as e:
            raise RecruitError(f"第 {n} 个配额：目标人数要是数字") from e
        if not 1 <= target <= 10000:
            raise RecruitError(f"第 {n} 个配额：目标人数要在 1 到 10000 之间")
        value = str(q["value"]).strip()[:100]
        out.append({"name": (str(q.get("name", "")).strip() or f"{q['field']}={value}")[:100], "field": q["field"], "question": str(q.get("question", "")).strip()[:200],
                    "value": value.upper() if q["field"] == "country" else value, "target": target})
    return out


def quota_progress(db: Session, project_id: str, quotas: list[dict]) -> list[dict]:
    """For each quota: how many people match (not disqualified), how many of them have a session, and the target."""
    people = db.scalars(select(Participant).where(Participant.project_id == project_id, Participant.anonymized_at.is_(None), Participant.screen_result != "disqualified")).all()
    invited = set(db.scalars(select(Interview.participant_id).where(Interview.project_id == project_id)))
    answers: dict[str, list[tuple[str, str]]] = {}
    if any(q["field"] == "answer" for q in quotas) and people:
        for a in db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id.in_([p.id for p in people]))):
            answers.setdefault(a.participant_id, []).append((a.question, a.answer))
    out = []
    for q in quotas:
        def match(p: Participant) -> bool:
            if q["field"] == "country":
                return p.country == q["value"]
            if q["field"] == "language":
                return p.language == q["value"]
            if q["field"] == "tag":
                return q["value"] in (p.tags or [])
            return any(q["question"].lower() in qu.lower() and q["value"].lower() in an.lower() for qu, an in answers.get(p.id, []))
        hit = [p for p in people if match(p)]
        out.append({**q, "have": len(hit), "invited": sum(p.id in invited for p in hit), "full": len(hit) >= q["target"]})
    return out
