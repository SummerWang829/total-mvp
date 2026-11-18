"""Outline workbench, stage 1: data rules (no pages, no model calls)."""
import pytest
from sqlalchemy import select, text

from app import ai_room
from app.db import SessionLocal, engine, ensure_columns
from app.models import CaptionLine, Interview, Project
from app.outline_wb import service as svc
from app.outline_wb.models import CodedAnswer, Mark, OutlineSnapshot


_OPEN: list = []


@pytest.fixture(autouse=True)
def _close_sessions():
    """Sessions a test opened must be closed before the next test drops the tables (SQLite would answer "database is locked")."""
    yield
    while _OPEN:
        _OPEN.pop().close()


def _setup(org):
    """A project with one outline (version 1, three items) and one interview, in a session the test commits itself."""
    org_id_pid = org.project()
    itv = org.interview(pid=org_id_pid)
    db = SessionLocal()
    _OPEN.append(db)
    proj = db.get(Project, org_id_pid)
    o, v = svc.create_outline(db, proj.org_id, proj.id, "A 品牌进入新加坡")
    a = svc.add_item(db, v, module="暖场", kind="question", texts={"zh": "请先介绍一下你自己和平时怎么逛街。"}, minutes=2)
    b = svc.add_item(db, v, module="核心", kind="question", texts={"zh": "最近一次买护肤品是什么时候，为什么选它？"}, must_cover=["购买原因"], minutes=4)
    c = svc.add_item(db, v, module="核心", kind="rating", texts={"zh": "给这个包装打个分，满分十分。"}, minutes=2)
    db.commit()
    return db, proj, o, v, (a, b, c), db.get(Interview, itv["id"])


def test_version_rev_counts_every_edit_and_usage_is_counted(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    rev0 = v.rev
    svc.update_item(db, v, b.item_uid, minutes=5)
    assert v.rev == rev0 + 1                      # not locked: it can be edited; the edit count goes up
    assert svc.usage_count(db, v.id) == 0
    svc.start_interview(db, itv, v)
    db.commit()
    assert svc.usage_count(db, v.id) == 1         # "这份大纲已被 1 场使用"
    svc.update_item(db, v, b.item_uid, minutes=6)  # still allowed after use
    assert v.rev == rev0 + 2


def test_snapshot_is_frozen_shared_and_used_by_the_ai_host(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    snap = svc.start_interview(db, itv, v, goal="了解护肤品选购", topic="护肤品")
    db.commit()
    assert (itv.outline_version_id, itv.outline_rev, itv.outline_snapshot_id) == (v.id, v.rev, snap.id)
    before = [i["texts"]["zh"] for i in snap.content["items"]]
    svc.update_item(db, v, b.item_uid, texts={"zh": "改过的问题"})   # the outline changes while the interview runs
    db.commit()
    db.refresh(snap)
    assert [i["texts"]["zh"] for i in snap.content["items"]] == before                # the snapshot did not move
    assert svc.start_interview(db, itv, v).id == snap.id                              # a running interview keeps its snapshot
    guide = ai_room.project_guide(db, itv)                                            # the AI host reads the snapshot, not the live outline
    asked = [q for t in guide.topics for q in t.questions] + list(guide.warmup_questions)
    assert "改过的问题" not in asked and any("最近一次买护肤品" in q for q in asked)


def test_same_version_and_rev_share_one_snapshot_and_new_rev_makes_a_new_one(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    s1 = svc.snapshot_for(db, v)
    s2 = svc.snapshot_for(db, v)
    assert s1.id == s2.id
    svc.update_item(db, v, a.item_uid, minutes=3)
    s3 = svc.snapshot_for(db, v)
    db.commit()
    assert s3.id != s1.id and len(db.scalars(select(OutlineSnapshot).where(OutlineSnapshot.version_id == v.id)).all()) == 2


def test_renaming_a_label_keeps_history_correct(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    line = CaptionLine(org_id=proj.org_id, interview_id=itv.id, seq=1, speaker="participant", text="我上个月买了一支精华，因为朋友推荐。")
    db.add(line)
    db.flush()
    svc.assign_line(line, b.item_uid, "host")
    mark = svc.add_mark(db, proj.org_id, itv.id, line, 0, 6, "unclear", "这里没听懂", "u1", "researcher")
    f = svc.add_focus(db, proj.org_id, proj.id, "为什么买", "u1", "viewer", item_uid=b.item_uid)
    ca = svc.add_coded(db, proj.org_id, f.id, itv.id, line.id, "朋友推荐", 0.9)
    old_label = b.label
    svc.set_label(db, v, b.item_uid, "H7")
    db.commit()
    assert old_label != "H7" and svc.label_of(db, v.id, line.item_uid) == "H7"   # the history shows the new number
    assert svc.label_of(db, v.id, db.get(Mark, mark.id).item_uid) == "H7"
    assert svc.label_of(db, v.id, f.item_uid) == "H7"
    assert db.get(CodedAnswer, ca.id).focus_id == f.id
    with pytest.raises(svc.OutlineError):                                         # numbers stay unique inside a version
        svc.set_label(db, v, a.item_uid, "H7")


def test_item_uid_survives_a_new_version(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    v2 = svc.new_version(db, v, summary="改了第 2 题")
    db.commit()
    assert {i.item_uid for i in svc.items_of(db, v2.id)} == {a.item_uid, b.item_uid, c.item_uid}
    assert v2.number == 2 and v2.base_version_id == v.id and o.current_version_id == v2.id


def test_ai_original_code_is_kept_after_a_human_changes_it(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    f = svc.add_focus(db, proj.org_id, proj.id, "为什么买", "u1", "researcher")
    ca = svc.add_coded(db, proj.org_id, f.id, itv.id, "L1", "价格", 0.6, origin="volunteered", mentioned_in_item_uid=a.item_uid)
    svc.human_recode(db, proj.org_id, ca.id, "朋友推荐")
    db.commit()
    got = db.get(CodedAnswer, ca.id)
    assert (got.code, got.ai_code, got.coded_by, got.confirmed) == ("朋友推荐", "价格", "human", True)
    assert got.origin == "volunteered" and got.mentioned_in_item_uid == a.item_uid   # where it was said unprompted


def test_revisions_only_a_person_with_the_right_role_accepts_and_red_lines_block(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    ops = [{"op": "rewrite", "item_uid": b.item_uid, "after": {"lang": "zh", "text": "更口语的问法"}, "reason": "太书面"}]
    r = svc.propose(db, proj.org_id, o.id, v.id, ops)[0]
    assert r.status == "pending" and svc.items_of(db, v.id)[1].texts["zh"].startswith("最近一次")   # a proposal changes nothing
    with pytest.raises(svc.OutlineError) as e:
        svc.accept(db, proj.org_id, r.id, "u9", "viewer")                  # a client cannot accept
    assert e.value.status == 403
    svc.accept(db, proj.org_id, r.id, "u1", "researcher")
    assert svc.items_of(db, v.id)[1].texts["zh"] == "更口语的问法" and r.status == "accepted" and r.decided_by == "u1"
    blocked = svc.propose(db, proj.org_id, o.id, v.id, [{"op": "rewrite", "item_uid": b.item_uid, "after": {"text": "难道你不觉得它很好吗？"}}], quality={"blocked": True})[0]
    with pytest.raises(svc.OutlineError):
        svc.accept(db, proj.org_id, blocked.id, "u1", "admin")             # a red-line revision cannot be accepted
    with pytest.raises(svc.OutlineError):                                  # at most three operations per request
        svc.propose(db, proj.org_id, o.id, v.id, ops * 4)
    with pytest.raises(svc.OutlineError):                                  # a rating question cannot take a "config" of a different kind of item: a note can't be configured
        n = svc.add_item(db, v, kind="note", module="核心", texts={"zh": "说明"})
        svc.propose(db, proj.org_id, o.id, v.id, [{"op": "config", "item_uid": n.item_uid, "after": {"config": {}}}])


def test_host_engine_revision_needs_a_training_case_number_and_insert_works(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    r = svc.propose(db, proj.org_id, o.id, v.id, [{"op": "rewrite", "item_uid": b.item_uid, "after": {"text": "x"}, "destination": "host_engine"}], source="pilot")[0]
    with pytest.raises(svc.OutlineError):
        svc.accept(db, proj.org_id, r.id, "u1", "researcher")
    svc.accept(db, proj.org_id, r.id, "u1", "researcher", train_case_id="TC-0042")
    assert r.train_case_id == "TC-0042" and svc.items_of(db, v.id)[1].texts["zh"].startswith("最近一次")   # the outline is untouched
    ins = svc.propose(db, proj.org_id, o.id, v.id, [{"op": "insert", "item_uid": a.item_uid, "after": {"kind": "concept", "texts": {"zh": "看一下这张概念卡"}, "config": {"title": "概念 A"}}}])[0]
    svc.accept(db, proj.org_id, ins.id, "u1", "researcher")
    assert [i.kind for i in svc.items_of(db, v.id)] == ["question", "concept", "question", "rating"]


def test_marks_keep_status_and_the_revision_that_solved_them(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    line = CaptionLine(org_id=proj.org_id, interview_id=itv.id, seq=1, speaker="participant", text="这个问题我没太明白意思。")
    db.add(line)
    db.flush()
    m = svc.add_mark(db, proj.org_id, itv.id, line, 0, 6, "unclear", "", "u2", "viewer")
    assert (m.status, m.author_role, m.quote) == ("open", "client", "这个问题我没")   # the quote is copied by the server
    rev = svc.propose(db, proj.org_id, o.id, v.id, [{"op": "rewrite", "item_uid": b.item_uid, "after": {"text": "换个问法"}}], source="pilot", source_ref=m.id)[0]
    svc.resolve_mark(db, proj.org_id, m.id, rev.id)
    assert (m.status, m.resolved_revision_id) == ("resolved", rev.id)
    with pytest.raises(svc.OutlineError):
        svc.add_mark(db, proj.org_id, itv.id, line, 5, 99, "other", "", "u1", "researcher")   # out of range


def test_guessing_the_question_uses_the_outline_order(org):
    db, proj, o, v, (a, b, c), itv = _setup(org)
    items = [{"item_uid": i.item_uid, "kind": i.kind, "is_backup": i.is_backup, "texts": i.texts} for i in svc.items_of(db, v.id)]
    # a vague line that fits two questions equally well goes to the one that follows the current question
    items[0]["texts"] = {"zh": "你平时怎么买东西"}
    items[1]["texts"] = {"zh": "你上次怎么买东西"}
    assert svc.guess_item_uid("我平时怎么买东西", items, last_uid=None) is not None
    assert svc.guess_item_uid("我上次怎么买东西", items, last_uid=items[0]["item_uid"]) == items[1]["item_uid"]
    assert svc.guess_item_uid("今天天气不错", items) is None            # nothing similar: no number is given


def test_retention_deletes_marks_and_answers_but_not_snapshots(org):
    from app.retention import purge_interview
    db, proj, o, v, (a, b, c), itv = _setup(org)
    snap = svc.start_interview(db, itv, v)
    line = CaptionLine(org_id=proj.org_id, interview_id=itv.id, seq=1, speaker="participant", text="一句话一句话一句话")
    db.add(line)
    db.flush()
    svc.add_mark(db, proj.org_id, itv.id, line, 0, 3, "other", "", "u1", "researcher")
    f = svc.add_focus(db, proj.org_id, proj.id, "q", "u1", "researcher")
    svc.add_coded(db, proj.org_id, f.id, itv.id, line.id, "x", 0.9)
    svc.add_decision(db, proj.org_id, itv.id, line.id, "followup", "不够深", 0.4, ["购买原因"])
    db.commit()
    purge_interview(db, itv, "test")
    db.commit()
    assert not db.scalars(select(Mark)).all() and not db.scalars(select(CodedAnswer)).all()
    assert db.get(OutlineSnapshot, snap.id) is not None


def test_tenant_isolation_and_repeatable_migration(org, client):
    from tests.conftest import Org
    db, proj, o, v, items, itv = _setup(org)
    other = Org(client, name="Other", email="b@other.com")
    with pytest.raises(svc.OutlineError) as e:
        svc.get_version(db, "another-org", v.id)
    assert e.value.status == 404
    with pytest.raises(svc.OutlineError):
        svc.create_outline(db, "another-org", proj.id, "x")                  # a project of another company
    assert other is not None
    assert ensure_columns() == [] and ensure_columns() == []                  # running the column migration again changes nothing
    with engine.begin() as c:                                                 # and it repairs a missing column
        c.execute(text("ALTER TABLE interviews DROP COLUMN is_pilot"))
    assert "interviews.is_pilot" in ensure_columns() and ensure_columns() == []


def test_demo_data_builds_once_with_numbered_pilot_lines(org):
    from app.outline_wb import demo
    from app.outline_wb.models import FocusQuestion
    pid = org.project()
    db = SessionLocal()
    _OPEN.append(db)
    made = demo.build(db, pid)
    db.commit()
    assert len(made["interviews"]) == 2 and demo.build(db, pid) == {}          # a second run changes nothing
    v = svc.get_version(db, db.get(Project, pid).org_id, made["version"])
    assert [i.kind for i in svc.items_of(db, v.id)].count("concept") == 1 and {"video", "website", "rating"} <= {i.kind for i in svc.items_of(db, v.id)}
    itv = db.get(Interview, made["interviews"][0])
    assert itv.is_pilot and itv.outline_snapshot_id
    lines = db.scalars(select(CaptionLine).where(CaptionLine.interview_id == itv.id)).all()
    assert lines and all(l.item_uid and l.item_source == "ai" for l in lines)
    assert db.scalars(select(CodedAnswer).where(CodedAnswer.origin == "volunteered")).first().mentioned_in_item_uid
    assert db.scalars(select(FocusQuestion)).first().asked_role == "client"
    assert ai_room.project_guide(db, itv) is not None                              # the AI host can run on the demo outline
