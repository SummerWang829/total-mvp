from sqlalchemy import create_engine, event
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
