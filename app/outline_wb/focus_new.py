"""中途新增关注问题 (stage 5): a focus question can be added while interviews run. Three things, none of which depends on a model being right on the spot:
1 which questions it belongs to (words and near-words; the researcher confirms, the top two are ticked when they are close enough), or a new question for the next interviews (a pending revision);
2 how its answers are coded (collect the words first and group them after eight, a template, or the researcher's own list);
3 looking back through the transcripts already there for people who talked about it in other questions ("volunteered"), which the researcher confirms before they count.
Callers commit.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CaptionLine, ChatMessage, Interview, Project
from . import board, quality, service as svc, session as sess, writer
from .models import CodedAnswer, FocusQuestion, Revision

PICK_MIN = 0.25            # a question this close is ticked for the researcher (at most two)
SHOW = 3
TEMPLATES = {"attitude": ["很认可", "一般", "不认可", "说不清"], "reason": ["价格", "功效", "成分", "包装", "品牌", "推荐", "其他"]}
MAX_CODES = 12
BACKFILL_MAX = 30
GROUP_SIM = 0.25           # how alike two answers must be (shared two-character pieces) to fall into one group


def _pieces(s: str) -> set[str]:
    flat = re.sub(r"\W+", "", s)
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _share(question: str, text: str) -> float:
    q = _pieces(question)
    return len(q & _pieces(text)) / len(q) if q else 0.0


def suggest_items(db: Session, proj: Project, text: str) -> dict:
    """The questions of the current outline that most resemble the new focus question, with how close each is; the top two that are close enough come back ticked."""
    o, v, st = sess.ensure(db, proj)
    lang = sess.context_of(db, proj, st)["language"]
    scored = []
    for it in svc.items_of(db, v.id):
        if it.kind not in ("question", "rating") or it.is_backup:
            continue
        hay = " ".join([it.texts.get(lang, ""), *it.must_cover, it.key_question])
        scored.append({"uid": it.item_uid, "label": it.label, "text": it.texts.get(lang, ""), "score": round(_share(text, hay), 2)})
    scored.sort(key=lambda x: -x["score"])
    top = scored[:SHOW]
    for n, c in enumerate(top):
        c["checked"] = n < 2 and c["score"] >= PICK_MIN
    return {"candidates": top, "none_fit": not any(c["checked"] for c in top)}


def create(db: Session, proj: Project, text: str, item_uids: list[str], coding: str, codes: list[str], user_id: str, role: str) -> FocusQuestion:
    """Add a focus question during the interviews."""
    text = text.strip()
    if not text:
        raise svc.OutlineError("先写下要弄清什么")
    if coding not in ("collect", "attitude", "reason", "custom"):
        raise svc.OutlineError("编码方式不对")
    if coding in TEMPLATES:
        codes = TEMPLATES[coding]
    elif coding == "custom":
        codes = list(dict.fromkeys(c.strip()[:20] for c in codes if c.strip()))[:MAX_CODES]
        if len(codes) < 2:
            raise svc.OutlineError("自己定编码表，至少要有两个类别")
    else:
        codes = []
    f = svc.add_focus(db, proj.org_id, proj.id, text, user_id, role, timing="during", item_uid=item_uids[0] if item_uids else None)
    f.item_uids, f.coding, f.codes = list(item_uids), coding, codes
    return f


def add_question(db: Session, proj: Project, fid: str, chat, user_id: str = "") -> Revision:
    """"Add a question for the next interviews": a pending revision that inserts one question after the last question of the outline. The wording is a plain template (the researcher edits it
    on the outline page); accepting it points the focus question at the new question. Interviews already running keep the snapshot they started with.

    Raises:
        OutlineError: no such focus question, or the outline has no question to put it after, or the wording is blocked.
    """
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    o, v, st = sess.ensure(db, proj, user_id)
    ctx = sess.context_of(db, proj, st)
    last = [i for i in svc.items_of(db, v.id) if i.kind in ("question", "rating")][-1:]
    if not last:
        raise svc.OutlineError("大纲里还没有题")
    q = f"关于“{f.text.strip().rstrip('？?')[:24]}”，你是怎么看的？"
    res = quality.check_question(q, ctx["language"], ctx["format"])
    if res["blocked"]:
        raise svc.OutlineError("生成的问法碰到红线，请到“生成大纲”页自己写这道题")
    return svc.propose(db, proj.org_id, o.id, v.id, [{"op": "insert", "item_uid": last[0].item_uid, "after": {"kind": "question", "module": last[0].module, "texts": {ctx["language"]: q}, "minutes": 2.0,
                       "config": {"depth": "shallow"}}, "reason": f"关注问题“{f.text[:20]}”没有对应的题"}], proposed_by="ai", source="board", source_ref=f.id, quality=res)[0]


def link_after_accept(db: Session, r: Revision) -> None:
    """A question added for a focus question was accepted: the focus question now points at it (so the next interviews are asked and coded)."""
    if r.source != "board" or r.op != "insert" or not (r.after or {}).get("new_uid"):
        return
    f = db.get(FocusQuestion, r.source_ref)
    if f is not None and f.org_id == r.org_id:
        uid = r.after["new_uid"]
        f.item_uid = f.item_uid or uid
        f.item_uids = list(dict.fromkeys([*(f.item_uids or []), uid]))


def backfill_candidates(db: Session, proj: Project, fid: str) -> dict:
    """Lines already said, in other questions, that talk about the focus question and are not coded for it yet. The researcher ticks the ones that count."""
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    mine = set([f.item_uid, *(f.item_uids or [])])
    done = {c.line_id for c in db.scalars(select(CodedAnswer).where(CodedAnswer.focus_id == fid))}
    out = []
    for itv in board._real(db, proj):
        labels = {i["item_uid"]: i["label"] for i in board._items(db, itv.outline_snapshot_id)}
        rows = [(r, "caption") for r in db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id, CaptionLine.speaker == "participant"))]
        rows += [(r, "chat") for r in db.scalars(select(ChatMessage).where(ChatMessage.interview_id == itv.id, ChatMessage.sender == "participant"))]
        for r, kind in rows:
            if r.id in done or r.item_uid in mine or len(re.sub(r"\W+", "", r.text)) < board.MIN_WORDS:
                continue
            rel = board.relevance(r.text, f.text, f.codes or [])
            if rel >= board.RELEVANT:
                out.append({"line_id": r.id, "kind": kind, "who": board._label(itv.participant), "in": labels.get(r.item_uid or "", ""), "text": r.text, "relevance": round(rel, 2), "ended": itv.status == "ended"})
    out.sort(key=lambda x: -x["relevance"])
    return {"candidates": out[:BACKFILL_MAX], "total": len(out)}


def backfill_confirm(db: Session, proj: Project, fid: str, line_ids: list[str]) -> dict:
    """Count the ticked lines as "volunteered" answers. They are coded in the background (waiting to be grouped when the focus question collects words first)."""
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    allowed = {c["line_id"]: c for c in backfill_candidates(db, proj, fid)["candidates"]}
    made = 0
    for lid in line_ids[:BACKFILL_MAX]:
        if lid not in allowed:
            continue
        row = db.get(CaptionLine, lid) or db.get(ChatMessage, lid)
        if row is None or row.org_id != proj.org_id:
            continue
        ca = svc.add_coded(db, proj.org_id, f.id, row.interview_id, lid, "", 1.0 if f.coding == "collect" and not f.codes else 0.0, origin="volunteered", mentioned_in_item_uid=row.item_uid)
        made += 1
        if not (f.coding == "collect" and not f.codes):
            board.submit_recode(ca.id)
    return {"added": made}


def clusters(db: Session, proj: Project, fid: str) -> dict:
    """Group the answers that are still waiting by what they say (shared two-character pieces, greedy), each group named by its most common piece. Proposals only: nothing is saved until the researcher names and confirms."""
    d = board.focus_detail(db, proj, fid, "all")
    pend = [c for c in db.scalars(select(CodedAnswer).where(CodedAnswer.focus_id == fid, CodedAnswer.org_id == proj.org_id, CodedAnswer.code == ""))]
    if len(pend) < board.CLUSTER_MIN:
        raise svc.OutlineError(f"至少攒够 {board.CLUSTER_MIN} 条才能归类，现在 {len(pend)} 条")
    people = {i.id: board._label(i.participant) for i in board._real(db, proj)}
    groups: list[dict] = []
    for c in pend:
        text = board._line_text(db, c)
        pc = _pieces(text)
        best, best_s = None, 0.0
        for g in groups:
            sim = len(pc & g["pieces"]) / max(1, len(pc | g["pieces"]))
            if sim > best_s:
                best, best_s = g, sim
        if best is not None and best_s >= GROUP_SIM:
            best["members"].append({"id": c.id, "who": people.get(c.interview_id, "—"), "text": text})
            best["pieces"] |= pc
            for p in pc:
                best["freq"][p] = best["freq"].get(p, 0) + 1
        else:
            groups.append({"members": [{"id": c.id, "who": people.get(c.interview_id, "—"), "text": text}], "pieces": set(pc), "freq": {p: 1 for p in pc}})
    out = []
    for g in sorted(groups, key=lambda g: -len(g["members"])):
        name = max(g["freq"], key=lambda k: g["freq"][k]) if g["freq"] and len(g["members"]) > 1 else (g["members"][0]["text"][:6] if g["members"] else "其他")
        out.append({"name": name, "members": g["members"]})
    return {"groups": out, "pending": len(pend), "focus": d["text"]}


def set_codes(db: Session, proj: Project, fid: str, groups: list[dict]) -> dict:
    """The researcher's confirmed groups become the focus question's codes and the answers in them are coded by a person (confirmed). Answers not in any group stay waiting and are not counted."""
    f = db.get(FocusQuestion, fid)
    if f is None or f.org_id != proj.org_id or f.project_id != proj.id:
        raise svc.OutlineError("关注问题不存在", 404)
    names = []
    for g in groups:
        name = str(g.get("name", "")).strip()[:20]
        ids = [i for i in g.get("answer_ids", []) if isinstance(i, str)]
        if not name or not ids:
            continue
        for cid in ids:
            c = db.get(CodedAnswer, cid)
            if c is not None and c.focus_id == fid and c.org_id == proj.org_id and not c.code:
                c.code, c.coded_by, c.confirmed = name, "human", True
        names.append(name)
    if not names:
        raise svc.OutlineError("至少确认一个类别")
    f.codes = list(dict.fromkeys([*(f.codes or []), *names]))[:MAX_CODES]
    return board.focus_detail(db, proj, fid, "all")
