import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

_sqlite = settings.database_url.startswith("sqlite")
_file_sqlite = _sqlite and ":memory:" not in settings.database_url
# SQLite: wait up to 30 s for a lock instead of failing at once.  A bigger pool: every polling page (waiting room, room, team views)
# holds a connection for the length of its request, and 5 + 10 ran out as soon as a handful of people tested together.
_args = {"check_same_thread": False, "timeout": 30} if _sqlite else {}
_pool = {"pool_size": 20, "max_overflow": 40, "pool_timeout": 30} if (_file_sqlite or not _sqlite) else {}
engine = create_engine(settings.database_url, connect_args=_args, future=True, pool_pre_ping=not _sqlite, **_pool)

if _file_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001 - DB-API connection
        """WAL: readers no longer block the writer (and vice versa) - essential once several browsers poll at once."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


log = logging.getLogger(__name__)


def ensure_columns(eng=None) -> list[str]:
    """Add the columns a model defines but an existing table lacks, so a deployed database keeps its data when a model gains a column.

    Only additive and only when it is safe: the column must be nullable or have a plain Python default (written as the SQL default).
    Anything else is logged and left alone (needs a real migration). Returns what was added, e.g. ["caption_lines.text_zh"].
    """
    eng = eng or engine
    insp = inspect(eng)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            default = col.default.arg if col.default is not None and getattr(col.default, "is_scalar", False) else None
            if default is None and not col.nullable:
                log.error("cannot add %s.%s automatically: NOT NULL without a plain default; migrate it by hand", table.name, col.name)
                continue
            lit = "NULL" if default is None else ("'" + str(default).replace("'", "''") + "'" if isinstance(default, str) else str(int(default) if isinstance(default, bool) else default))
            with eng.begin() as c:
                c.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(eng.dialect)} DEFAULT {lit}'))
            added.append(f"{table.name}.{col.name}")
            log.warning("database: added column %s.%s", table.name, col.name)
    return added
