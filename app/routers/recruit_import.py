"""Importing a client's raw data (Confirmit Data Map export): preview -> mapping -> precheck -> commit -> rollback / orphan pool.

Every endpoint here is additive: the old "paste a list of people" import (``/participants/import``) stays exactly as it was, because it
answers a different question (a list with names, no codes). This one never creates a person by itself.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import import_map, recruit
from ..audit import audit
from ..db import get_db
from ..deps import RECRUITERS, Ctx, require_roles
from ..models import Participant, ParticipantImportBatch, ParticipantImportOrphan, RecruitDataMap, utcnow
from .projects import get_project

router = APIRouter(prefix="/api", tags=["recruiting-import"])


class PreviewBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)


class MapBody(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    mapping: dict = Field(default_factory=dict)


class CommitBody(MapBody):
    filename: str = Field(default="", max_length=200)
    source_name: str = Field(default="", max_length=120)
    save_mapping_as: str = Field(default="", max_length=120)  # optional: keep this mapping for next time
    confirm: bool = False  # the team confirms it read the precheck numbers


class MappingBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mapping: dict = Field(default_factory=dict)


class AttachBody(BaseModel):
    participant_id: str = Field(min_length=1, max_length=32)


def _rules(db: Session, project_id: str) -> tuple[list[dict], int, list[dict]]:
    """The project's eligibility rules, their version, and its quotas (empty lists when none are set)."""
    from ..models import RecruitSettings

    s = db.get(RecruitSettings, project_id)
    if not s:
        return [], 0, []
    return s.rules or [], s.rules_version, s.quotas or []


@router.post("/projects/{pid}/recruiting/import/preview")
def import_preview(pid: str, body: PreviewBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Read the file and guess the mapping: what the wizard shows before anyone maps anything."""
    get_project(db, ctx, pid)
    try:
        header, rows = import_map.read_table(body.csv)
    except import_map.ImportError_ as e:
        raise HTTPException(422, str(e)) from e
    suggested = import_map.suggest_mapping(header)
    email_col = next((c for c, s in suggested.items() if s["target"] == "email"), "")
    return {
        "columns": header,
        "rows": len(rows),
        "sample": [{"line": line, "cells": cells} for line, cells in rows[:5]],
        "mapping": suggested,
        "email_column": email_col,
        "saved_mappings": [{"id": m.id, "name": m.name, "mapping": m.mapping} for m in import_map.mappings_for(db, ctx.org_id, pid)],
    }


@router.post("/projects/{pid}/recruiting/import/precheck")
def import_precheck(pid: str, body: MapBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """What the import would do — counts and how the quotas would stand — without writing anything."""
    proj = get_project(db, ctx, pid)
    try:
        header, rows = import_map.read_table(body.csv)
        mapping = import_map.normalize_mapping(body.mapping)
        items, bad = import_map.decode(header, rows, mapping)
    except import_map.ImportError_ as e:
        raise HTTPException(422, str(e)) from e
    rules, _version, quotas = _rules(db, proj.id)
    out = import_map.precheck(db, proj.id, items, rules, quotas)
    out["failed_rows"] = (out.get("failed_rows") or []) + [b for b in bad if b["line"] == 0]
    out["failed"] = out["failed"] + len([b for b in bad if b["line"] == 0])
    db.rollback()  # nothing was meant to be written
    return out


@router.post("/projects/{pid}/recruiting/import/commit")
def import_commit(pid: str, body: CommitBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Write the rows in one batch. Everything that matches nobody becomes an orphan row instead of a new person."""
    proj = get_project(db, ctx, pid)
    if not body.confirm:
        raise HTTPException(422, "请先核对预检数字并勾选确认")
    try:
        header, rows = import_map.read_table(body.csv)
        mapping = import_map.normalize_mapping(body.mapping)
        items, bad = import_map.decode(header, rows, mapping)
        rules, version, _quotas = _rules(db, proj.id)
        batch = import_map.commit(db, ctx.org_id, proj.id, items, rules, version, filename=body.filename,
                                  source_name=body.source_name, actor=ctx.actor, mapping=mapping)
        if body.save_mapping_as.strip():
            import_map.save_mapping(db, ctx.org_id, proj.id, body.save_mapping_as, mapping)
    except import_map.ImportError_ as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "participant.import_batch", proj.id, batch=batch.id, rows=batch.rows,
          matched=batch.matched, conflicted=batch.conflicted, orphaned=batch.orphaned, failed=batch.failed,
          source=batch.source_name)
    db.commit()
    return {"batch": _batch_out(batch), "notices": [b for b in bad if b["line"] == 0]}


def _batch_out(b: ParticipantImportBatch) -> dict:
    return {"id": b.id, "filename": b.filename, "source_name": b.source_name, "rows": b.rows, "matched": b.matched,
            "conflicted": b.conflicted, "orphaned": b.orphaned, "failed": b.failed, "actor": b.actor,
            "created_at": b.created_at.isoformat() if b.created_at else "", "rolled_back": bool(b.rolled_back_at)}


@router.get("/projects/{pid}/recruiting/import/batches")
def import_batches(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    get_project(db, ctx, pid)
    rows = db.scalars(select(ParticipantImportBatch).where(ParticipantImportBatch.project_id == pid)
                      .order_by(ParticipantImportBatch.created_at.desc())).all()
    pending = db.scalars(select(ParticipantImportOrphan).where(ParticipantImportOrphan.project_id == pid,
                                                              ParticipantImportOrphan.status == "pending")).all()
    return {"batches": [_batch_out(b) for b in rows], "orphans_pending": len(pending)}


@router.post("/projects/{pid}/recruiting/import/batches/{bid}/rollback")
def import_rollback(pid: str, bid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Undo a whole batch: every field and answer it changed goes back to what it was."""
    proj = get_project(db, ctx, pid)
    batch = db.get(ParticipantImportBatch, bid)
    if not batch or batch.project_id != proj.id:
        raise HTTPException(404, "没有这一批导入")
    try:
        out = import_map.rollback(db, batch)
    except import_map.ImportError_ as e:
        raise HTTPException(422, str(e)) from e
    audit(db, ctx.org_id, ctx.actor, "participant.import_rollback", proj.id, batch=batch.id, **out)
    db.commit()
    return {"ok": True, **out, "batch": _batch_out(batch)}


@router.get("/projects/{pid}/recruiting/import/orphans")
def import_orphans(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """The rows that matched nobody, waiting for someone to attach them to a person or ignore them."""
    get_project(db, ctx, pid)
    rows = db.scalars(select(ParticipantImportOrphan).where(ParticipantImportOrphan.project_id == pid,
                                                           ParticipantImportOrphan.status == "pending")
                      .order_by(ParticipantImportOrphan.created_at.desc())).all()
    return {"orphans": [{"id": o.id, "email": o.email, "batch_id": o.batch_id, "reason": o.reason,
                         "raw": o.raw_row, "answers": o.answers,
                         "created_at": o.created_at.isoformat() if o.created_at else ""} for o in rows]}


@router.post("/projects/{pid}/recruiting/import/orphans/{oid}/attach")
def import_orphan_attach(pid: str, oid: str, body: AttachBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Write an orphan row's answers into a person the team picked (the original row stays)."""
    proj = get_project(db, ctx, pid)
    orphan = db.get(ParticipantImportOrphan, oid)
    if not orphan or orphan.project_id != proj.id:
        raise HTTPException(404, "没有这条未匹配数据")
    person = db.get(Participant, body.participant_id)
    if not person or person.project_id != proj.id or person.anonymized_at:
        raise HTTPException(404, "没有这个受访者")
    import_map.attach_orphan(db, ctx.org_id, orphan, person, ctx.actor)
    audit(db, ctx.org_id, ctx.actor, "participant.import_attach", proj.id, orphan=oid, participant=person.id)
    db.commit()
    return {"ok": True}


@router.post("/projects/{pid}/recruiting/import/orphans/{oid}/ignore")
def import_orphan_ignore(pid: str, oid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    """Keep the row for the record but stop offering it."""
    proj = get_project(db, ctx, pid)
    orphan = db.get(ParticipantImportOrphan, oid)
    if not orphan or orphan.project_id != proj.id:
        raise HTTPException(404, "没有这条未匹配数据")
    orphan.status, orphan.handled_at, orphan.handled_by = "ignored", utcnow(), ctx.actor[:100]
    audit(db, ctx.org_id, ctx.actor, "participant.import_ignore", proj.id, orphan=oid)
    db.commit()
    return {"ok": True}


@router.get("/projects/{pid}/recruiting/import/mappings")
def mapping_list(pid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    get_project(db, ctx, pid)
    return {"mappings": [{"id": m.id, "name": m.name, "mapping": m.mapping,
                          "last_used_at": m.last_used_at.isoformat() if m.last_used_at else ""}
                         for m in import_map.mappings_for(db, ctx.org_id, pid)]}


@router.put("/projects/{pid}/recruiting/import/mappings")
def mapping_save(pid: str, body: MappingBody, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    try:
        mapping = import_map.normalize_mapping(body.mapping)
        row = import_map.save_mapping(db, ctx.org_id, proj.id, body.name, mapping)
    except import_map.ImportError_ as e:
        raise HTTPException(422, str(e)) from e
    db.commit()
    return {"id": row.id, "name": row.name}


@router.delete("/projects/{pid}/recruiting/import/mappings/{mid}")
def mapping_delete(pid: str, mid: str, ctx: Ctx = Depends(require_roles(*RECRUITERS)), db: Session = Depends(get_db)):
    proj = get_project(db, ctx, pid)
    row = db.get(RecruitDataMap, mid)
    if row and row.org_id == ctx.org_id and row.project_id in (proj.id, ""):
        db.delete(row)
        db.commit()
    return {"ok": True}
