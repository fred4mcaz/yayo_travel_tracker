"""SQLite engine and session handling.

WAL mode matters here: the IMAP poller writes on a background thread while you
are reading the app on your phone, and the default rollback journal would make
those block each other.
"""

from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()
settings.var_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    echo=False,
    # SQLite + threaded scheduler: allow cross-thread use of the connection pool.
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # Wait rather than immediately raising "database is locked" under contention.
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
