"""Importing a client's raw data (Confirmit Data Map export) into a project's participant files.

Why this is separate from ``app/recruit.py``: that one imports a *list of people* (name, e-mail, country, tags) and is happy with
plain text. This one imports *answers from a questionnaire the client programmed elsewhere*: the columns are variable names
(``Q1_REGION``), the values are codes (``1``), and there is often **no name at all**. So three things differ:

1. **Matching is by e-mail only** (normalized: trimmed, lower-cased). A name is not required and never used to match, because the
   export usually has ``LIN XIAOYU`` where our list has ``林小雨``.
2. **A mapping decodes the codes**: ``Q1_REGION=1`` becomes ``Q1 常住城市或地区 → 广深珠佛惠莞中江肇``. Without it the reports would
   show "1".
3. **Nothing is created by itself**: a row whose e-mail is not in the list goes to the orphan pool (``participant_import_orphans``),
   never straight into the list. And every import is one batch (``participant_import_batches``) that can be rolled back as a whole.

The pages call this through ``app/routers/recruit_import.py``.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import recruit, screener
from .models import (
    Participant,
    ParticipantImportBatch,
    ParticipantImportOrphan,
    RecruitDataMap,
    utcnow,
)

MAX_ROWS = recruit.MAX_ROWS
MAX_COLUMNS = 120
TARGETS = ("question", "name", "email", "tags", "country", "language")
USES = ("screen", "segment", "quota", "archive")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}")
# Columns that are never a questionnaire answer, whatever the mapping says.
RESERVED = {"email", "e-mail", "mail", "邮箱", "电子邮件", "name", "姓名", "名字", "country", "国家", "language", "语言", "tags", "标签"}


class ImportError_(ValueError):
    """The file or the mapping cannot be used; the message says why in plain Chinese."""


@dataclass
class Item:
    """One decoded data row."""

    line: int
    email: str = ""
    name: str = ""
    country: str = ""
    language: str = ""
    tags: list[str] = field(default_factory=list)
    answers: list[tuple[str, str]] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    why: str = ""  # set when the row cannot be used at all


# ---------------------------------------------------------------- reading
def email_key(value: str) -> str:
    """The normalized key two e-mails are compared by: no spaces, lower case. Empty stays empty."""
    return (value or "").strip().casefold()


def looks_like_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch((value or "").strip()))


def read_table(text: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """The header and the rows of a CSV / TSV export, as (line number, {column: value}).

    Accepts a BOM, comma or tab, and quoted cells. A completely empty row is skipped. The header is not renamed here: an export
    calls its columns whatever it likes (``Q1_REGION``) and the mapping is what knows the meaning.
    """
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        raise ImportError_("文件是空的")
    sample = text[:4000]
    dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = [(h or "").strip() for h in next(reader, [])]
    while header and not header[-1]:  # trailing empty columns are common in exports
        header.pop()
    if not header:
        raise ImportError_("第一行没有列名")
    if len(header) > MAX_COLUMNS:
        raise ImportError_(f"列太多了（{len(header)} 列，最多 {MAX_COLUMNS}）")
    if len(set(header)) != len(header):
        dup = sorted({h for h in header if header.count(h) > 1})
        raise ImportError_("列名重复：" + "、".join(dup[:5]))
    rows: list[tuple[int, dict[str, str]]] = []
    for line, cells in enumerate(reader, start=2):
        if not any((c or "").strip() for c in cells):
            continue
        if len(rows) >= MAX_ROWS:
            raise ImportError_(f"一次最多导入 {MAX_ROWS} 行，请分几次")
        rows.append((line, {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(header)}))
    if not rows:
        raise ImportError_("只有表头，没有数据")
    return header, rows


# ---------------------------------------------------------------- mapping
def suggest_mapping(header: list[str]) -> dict:
    """A first guess so the team rarely has to type anything: e-mail-like columns become the key, the rest become questions."""
    out: dict[str, dict] = {}
    for col in header:
        low = col.strip().lower()
        if low in ("email", "e-mail", "mail", "q_email", "邮箱", "电子邮件") or "email" in low or "邮箱" in col:
            out[col] = {"target": "email", "use": "archive", "question": "邮箱", "values": {}}
        elif low in ("name", "q_name", "姓名", "名字") or low.endswith("_name") or "姓名" in col:
            out[col] = {"target": "name", "use": "archive", "question": "姓名", "values": {}}
        elif low in RESERVED:
            out[col] = {"target": "tags" if low in ("tags", "标签") else low, "use": "archive", "question": col, "values": {}}
        else:
            out[col] = {"target": "question", "use": "screen", "question": col, "values": {}}
    return out


def normalize_mapping(raw) -> dict:
    """The mapping as stored. Refuses anything it cannot use, saying which column is wrong.

    Each entry: ``{"target": question|name|email|tags|country|language, "question": 题干, "values": {"1": "…"}, "use": screen|segment|quota|archive}``
    """
    if not isinstance(raw, dict) or not raw:
        raise ImportError_("还没有设置字段映射")
    if len(raw) > MAX_COLUMNS:
        raise ImportError_("映射的列太多了")
    out: dict[str, dict] = {}
    for col, spec in raw.items():
        col = str(col).strip()
        if not col:
            continue
        if not isinstance(spec, dict):
            raise ImportError_(f"“{col}”这一列的设置格式不对")
        target = str(spec.get("target") or "question").strip()
        use = str(spec.get("use") or ("archive" if target != "question" else "screen")).strip()
        if target not in TARGETS:
            raise ImportError_(f"“{col}”的目标字段不认识：{target}")
        if use not in USES:
            raise ImportError_(f"“{col}”的用途不认识：{use}")
        values = spec.get("values") or {}
        if not isinstance(values, dict):
            raise ImportError_(f"“{col}”的值映射格式不对")
        clean_values = {str(k).strip(): str(v).strip()[:300] for k, v in values.items() if str(k).strip() != ""}
        question = str(spec.get("question") or col).strip()[:300]
        out[col] = {"target": target, "use": use, "question": question, "values": clean_values}
    if not any(s["target"] == "email" for s in out.values()):
        raise ImportError_("必须把一列指定为“邮箱”——它是匹配名单的唯一依据")
    return out


def decode(header: list[str], rows: list[tuple[int, dict[str, str]]], mapping: dict) -> tuple[list[Item], list[dict]]:
    """Turn raw rows into items: codes read through the mapping, answers in questionnaire order.

    A row without a usable e-mail is still returned (with ``why``) so the caller can report it; nothing is silently dropped.
    """
    order = [col for col in header if col in mapping]
    email_col = next((c for c in order if mapping[c]["target"] == "email"), "")
    items: list[Item] = []
    bad: list[dict] = []
    seen_codes: dict[str, set] = {}  # a column whose values are not in the mapping: report it once, not per row
    for line, cells in rows:
        it = Item(line=line, raw=dict(cells))
        for col in order:
            spec = mapping[col]
            value = (cells.get(col) or "").strip()
            if not value:
                continue
            if spec["target"] == "email":
                it.email = value[:200]
            elif spec["target"] == "name":
                it.name = value[:100]
            elif spec["target"] == "country":
                it.country = value.upper()[:2]
            elif spec["target"] == "language":
                it.language = value.lower()[:10]
            elif spec["target"] == "tags":
                it.tags = recruit.clean_tags(value)
            else:  # a question: decode the code
                codes = [c.strip() for c in re.split(r"[,;|，；]", value) if c.strip()]
                texts = []
                for code in codes:
                    text = spec["values"].get(code)
                    if text is None:
                        text = code  # no mapping for this code: keep it visible rather than dropping the answer
                        seen_codes.setdefault(col, set()).add(code)
                    if text not in texts:
                        texts.append(text)
                answer = "、".join(texts)[:1000]
                it.answers.append((spec["question"], answer))
                if spec["use"] == "segment":
                    for text in texts:
                        tag = text.strip()[:recruit.MAX_TAG_CHARS]
                        if tag and tag not in it.tags and len(it.tags) < recruit.MAX_TAGS:
                            it.tags.append(tag)
        if not it.email:
            it.why = "这一行没有邮箱，没法匹配名单"
        elif not looks_like_email(it.email):
            it.why = f"邮箱格式不对：{it.email[:60]}"
        if it.why:
            bad.append({"line": line, "why": it.why})
        items.append(it)
    for col, codes in seen_codes.items():
        sample = "、".join(sorted(codes)[:6])
        bad.append({"line": 0, "why": f"“{col}”这些值没有对应的文字：{sample}（先补上值映射再导入，否则会原样显示）"})
    return items, bad


# ---------------------------------------------------------------- matching
def by_email(db: Session, project_id: str) -> dict[str, list[Participant]]:
    """Active people of the project, keyed by normalized e-mail."""
    out: dict[str, list[Participant]] = {}
    for p in db.scalars(select(Participant).where(Participant.project_id == project_id, Participant.anonymized_at.is_(None))).all():
        key = email_key(p.email)
        if key:
            out.setdefault(key, []).append(p)
    return out


def classify(db: Session, project_id: str, items: list[Item]) -> dict:
    """Sort the rows: matched (one person), conflicted (nobody to write to, or a repeated e-mail), orphaned, failed."""
    index = by_email(db, project_id)
    counts: dict[str, int] = {}
    for it in items:
        if not it.why and it.email:
            counts[email_key(it.email)] = counts.get(email_key(it.email), 0) + 1
    matched, conflicted, orphaned, failed = [], [], [], []
    repeated: set[str] = set()  # the last row of a repeated e-mail is the one written; the earlier ones are reported
    for it in reversed(items):  # last row wins, so walk backwards and keep the first seen
        key = email_key(it.email)
        if it.why:
            failed.append({"line": it.line, "why": it.why, "email": it.email})
            continue
        if counts.get(key, 0) > 1:
            if key in repeated:
                conflicted.append({"line": it.line, "why": "同一个邮箱在本批出现多次（按最后一行写入）", "email": it.email})
                continue
            repeated.add(key)
            conflicted.append({"line": it.line, "why": "同一个邮箱在本批出现多次（按最后一行写入）", "email": it.email})
            people = index.get(key, [])
            if len(people) == 1:
                matched.append((it, people[0], "同一个邮箱在本批出现多次"))
            elif len(people) > 1:
                conflicted.append({"line": it.line, "why": f"这个邮箱在名单里有 {len(people)} 个人，无法确定写给谁", "email": it.email})
            else:
                orphaned.append((it, "名单里没有这个邮箱"))
            continue
        people = index.get(key, [])
        if not people:
            orphaned.append((it, "名单里没有这个邮箱"))
        elif len(people) > 1:
            conflicted.append({"line": it.line, "why": f"这个邮箱在名单里有 {len(people)} 个人，无法确定写给谁", "email": it.email})
        else:
            matched.append((it, people[0], ""))
    matched.reverse()  # keep the file's order in the preview
    return {"matched": matched, "conflicted": conflicted, "orphaned": orphaned, "failed": failed}


# ---------------------------------------------------------------- precheck
def _quota_hit(q: dict, p: Participant, answers: list[tuple[str, str]], tags: list[str]) -> bool:
    """Does this person count towards the quota? (the same rule as recruit._matches, on the answers about to be written)"""
    if q["field"] == "country":
        return p.country == q["value"]
    if q["field"] == "language":
        return p.language == q["value"]
    if q["field"] == "tag":
        return q["value"] in tags
    return any(q["question"].lower() in qu.lower() and q["value"].lower() in an.lower() for qu, an in answers)


def precheck(db: Session, project_id: str, items: list[Item], rules: list[dict], quotas: list[dict]) -> dict:
    """What the import would do, without writing anything.

    The numbers a PM actually decides on: how many people change to qualified / disqualified, and how each quota would stand
    afterwards (``over`` = would exceed its target, which is worth fixing before importing, not after).
    """
    groups = classify(db, project_id, items)
    to_q = to_d = same = 0
    changes: list[dict] = []
    add_by_quota = [0] * len(quotas)
    for it, p, note in groups["matched"]:
        new_result = p.screen_result
        if rules and p.screen_reason != recruit.EXTERNAL_REASON:
            new_result, _ = recruit.evaluate(rules, p, it.answers)
        if new_result != p.screen_result:
            if new_result == "qualified":
                to_q += 1
            elif new_result == "disqualified":
                to_d += 1
            changes.append({"line": it.line, "name": p.name, "from": p.screen_result or "未判定", "to": new_result, "note": note})
        else:
            same += 1
        tags = list(dict.fromkeys((p.tags or []) + it.tags))
        for i, q in enumerate(quotas):
            if new_result != "disqualified" and _quota_hit(q, p, it.answers, tags):
                add_by_quota[i] += 1
    progress = recruit.quota_progress(db, project_id, quotas)
    quota_after = []
    for q, add in zip(progress, add_by_quota):
        have = q["have"] + add
        quota_after.append({"name": q["name"], "add": add, "have": q["have"], "after": have,
                            "target": q["target"], "over": max(0, have - q["target"]), "full": have >= q["target"]})
    return {
        "rows": len(items),
        "matched": len(groups["matched"]),
        "conflicted": len(groups["conflicted"]),
        "orphaned": len(groups["orphaned"]),
        "failed": len(groups["failed"]),
        "impact": {"to_qualified": to_q, "to_disqualified": to_d, "unchanged": same, "quotas": quota_after},
        "changes": changes[:200],
        "orphan_rows": [{"line": it.line, "email": it.email, "why": why} for it, why in groups["orphaned"]][:200],
        "conflict_rows": groups["conflicted"][:200],
        "failed_rows": groups["failed"][:200],
    }


# ---------------------------------------------------------------- commit
def _snapshot_of(p: Participant, db: Session) -> dict:
    return {
        "id": p.id,
        "before": {
            "screen_result": p.screen_result,
            "screen_reason": p.screen_reason,
            "screen_version": p.screen_version,
            "tags": list(p.tags or []),
            "source": p.source,
            "source_name": p.source_name,
            "email": p.email,
            "name": p.name,
            "country": p.country,
            "language": p.language,
        },
        "answers": [[a["question"], a["answer"]] for a in screener.items_for(db, p.id)],
    }


def commit(db: Session, org_id: str, project_id: str, items: list[Item], rules: list[dict], rules_version: int,
           filename: str = "", source_name: str = "", actor: str = "", mapping: dict | None = None) -> ParticipantImportBatch:
    """Write the rows into the people they belong to, in one batch. The caller commits the session.

    Only people already in the list are written to. Everything else becomes an orphan row: nothing is created here.
    """
    groups = classify(db, project_id, items)
    label = source_name.strip()[:120] or f"外部原始数据 · {utcnow():%m-%d}"
    batch = ParticipantImportBatch(org_id=org_id, project_id=project_id, filename=filename[:200], source_name=label,
                                   rows=len(items), mapping=mapping or {}, actor=actor[:100], snapshot={"people": []})
    db.add(batch)
    db.flush()
    snapshots: list[dict] = []
    for it, p, _note in groups["matched"]:
        snapshots.append(_snapshot_of(p, db))
        screener.save(db, org_id, p.id, it.answers)
        p.source, p.source_name = "import", label
        if it.email and not p.email:
            p.email = it.email
        if it.country and not p.country:
            p.country = it.country
        if it.language and not p.language:
            p.language = it.language
        if it.tags:
            merged = list(dict.fromkeys((p.tags or []) + it.tags))[:recruit.MAX_TAGS]
            p.tags = merged
    if rules and snapshots:
        recruit.apply_rules(db, project_id, rules, rules_version)
    for it, why in groups["orphaned"]:
        db.add(ParticipantImportOrphan(batch_id=batch.id, org_id=org_id, project_id=project_id, email=it.email[:200],
                                       raw_row=it.raw, answers=[[q, a] for q, a in it.answers], reason=why[:200]))
    for row in groups["failed"]:
        db.add(ParticipantImportOrphan(batch_id=batch.id, org_id=org_id, project_id=project_id, email=str(row.get("email", ""))[:200],
                                       raw_row={}, answers=[], reason=row["why"][:200]))
    batch.matched, batch.conflicted = len(groups["matched"]), len(groups["conflicted"])
    batch.orphaned, batch.failed = len(groups["orphaned"]), len(groups["failed"])
    batch.snapshot = {"people": snapshots}
    db.flush()
    return batch


def rollback(db: Session, batch: ParticipantImportBatch) -> dict:
    """Undo one batch: put back every field and every answer it changed, and drop its still-pending orphan rows."""
    if batch.rolled_back_at:
        raise ImportError_("这一批已经回滚过了")
    restored = 0
    for row in (batch.snapshot or {}).get("people", []):
        p = db.get(Participant, row["id"])
        if not p:
            continue
        before = row.get("before", {})
        for key in ("screen_result", "screen_reason", "screen_version", "source", "source_name", "email", "name", "country", "language"):
            if key in before:
                setattr(p, key, before[key])
        p.tags = list(before.get("tags") or [])
        old = [tuple(x) for x in row.get("answers", [])]
        if old:
            screener.save(db, batch.org_id, p.id, old)
        else:
            screener.clear(db, p.id)
        restored += 1
    dropped = 0
    for o in db.scalars(select(ParticipantImportOrphan).where(ParticipantImportOrphan.batch_id == batch.id)).all():
        if o.status == "pending":
            db.delete(o)
            dropped += 1
    batch.rolled_back_at = utcnow()
    db.flush()
    return {"restored": restored, "orphans_dropped": dropped}


def attach_orphan(db: Session, org_id: str, orphan: ParticipantImportOrphan, participant: Participant, actor: str = "") -> None:
    """Write an orphan row's answers into the person the team picked. The original row stays for the record."""
    answers = [tuple(x) for x in (orphan.answers or [])]
    if answers:
        screener.save(db, org_id, participant.id, answers)
        participant.source_name = participant.source_name or "外部原始数据（手工挂接）"
    orphan.status, orphan.participant_id = "attached", participant.id
    orphan.handled_at, orphan.handled_by = utcnow(), actor[:100]
    db.flush()


# ---------------------------------------------------------------- saved mappings
def save_mapping(db: Session, org_id: str, project_id: str, name: str, mapping: dict) -> RecruitDataMap:
    """Store (or refresh by name) a mapping so the next import of the same client is one click."""
    name = (name or "").strip()[:120]
    if not name:
        raise ImportError_("给这套映射起个名字，下次才能直接选")
    row = db.scalars(select(RecruitDataMap).where(RecruitDataMap.org_id == org_id, RecruitDataMap.project_id == project_id,
                                                 RecruitDataMap.name == name)).first()
    if row is None:
        row = RecruitDataMap(org_id=org_id, project_id=project_id, name=name, mapping=mapping)
        db.add(row)
    else:
        row.mapping = mapping
    row.last_used_at = utcnow()
    db.flush()
    return row


def mappings_for(db: Session, org_id: str, project_id: str) -> list[RecruitDataMap]:
    return list(db.scalars(select(RecruitDataMap).where(RecruitDataMap.org_id == org_id,
                                                        RecruitDataMap.project_id.in_([project_id, ""]))).all())
