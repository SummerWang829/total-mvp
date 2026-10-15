"""A deployed database keeps working (and keeps its data) when a model gains a column."""
import tempfile

from sqlalchemy import create_engine, inspect, text

from app.db import Base, ensure_columns


def test_missing_columns_are_added_with_their_defaults_and_existing_rows_survive():
    eng = create_engine(f"sqlite:///{tempfile.mkdtemp()}/old.db")
    Base.metadata.create_all(eng)
    with eng.begin() as c:  # an "old" database: the table exists without the two newer caption columns, and holds a row
        c.execute(text("ALTER TABLE caption_lines DROP COLUMN text_zh"))
        c.execute(text("ALTER TABLE caption_lines DROP COLUMN dur_ms"))
        c.execute(text("INSERT INTO caption_lines (id, org_id, interview_id, seq, speaker, text, at_ms, created_at) VALUES ('c1','o','i',1,'moderator','你好',1000,'2026-10-15 00:00:00')"))
    assert "text_zh" not in {c["name"] for c in inspect(eng).get_columns("caption_lines")}
    assert sorted(ensure_columns(eng)) == ["caption_lines.dur_ms", "caption_lines.text_zh"]
    with eng.connect() as c:
        row = c.execute(text("SELECT text, at_ms, text_zh, dur_ms FROM caption_lines")).one()
    assert tuple(row) == ("你好", 1000, "", 0)
    assert ensure_columns(eng) == []  # nothing left to do: running it at every start is harmless


def test_a_database_that_is_up_to_date_is_left_alone():
    eng = create_engine(f"sqlite:///{tempfile.mkdtemp()}/new.db")
    Base.metadata.create_all(eng)
    assert ensure_columns(eng) == []
