import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from models import Session


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    folder           TEXT,
    hostname         TEXT    NOT NULL,
    port             INTEGER NOT NULL DEFAULT 22,
    username         TEXT,
    auth_method      TEXT    NOT NULL
                             CHECK (auth_method IN ('password', 'local', 'keyfile')),
    identity_file    TEXT,
    proxy_jump       TEXT,
    sort_order       INTEGER,
    background_color TEXT,
    speed_dial       INTEGER,
    created_at       TEXT,
    updated_at       TEXT
)
"""

_SPEED_DIAL_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_speed_dial "
    "ON sessions(speed_dial) WHERE speed_dial IS NOT NULL"
)


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            # Migrate existing databases that predate background_color.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "background_color" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN background_color TEXT")
        self._migrate_auth_methods()
        with self._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "speed_dial" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN speed_dial INTEGER")
            conn.execute(_SPEED_DIAL_INDEX)

    def _migrate_auth_methods(self) -> None:
        """Older databases allowed password/default/identity/fido2 and the
        CHECK constraint can't be altered in place, so the table is rebuilt:
        password stays, a session with no key file becomes 'local' (ssh's own
        default keys), and anything with a key file becomes 'keyfile'. The
        original file is copied aside first, once."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
            ).fetchone()
            if row is None or "'keyfile'" in row[0]:
                return

        shutil.copy2(self._path, self._path.with_name(self._path.name + ".pre-auth-migration.bak"))

        new_table = _SCHEMA.replace("CREATE TABLE IF NOT EXISTS sessions", "CREATE TABLE sessions_new")
        conn = self._connect()
        try:
            conn.executescript(
                f"""
                BEGIN;
                {new_table};
                INSERT INTO sessions_new
                    (id, name, folder, hostname, port, username, auth_method,
                     identity_file, proxy_jump, sort_order, background_color,
                     created_at, updated_at)
                SELECT id, name, folder, hostname, port, username,
                       CASE WHEN auth_method = 'password' THEN 'password'
                            WHEN identity_file IS NULL OR TRIM(identity_file) = '' THEN 'local'
                            ELSE 'keyfile' END,
                       identity_file, proxy_jump, sort_order, background_color,
                       created_at, updated_at
                FROM sessions;
                DROP TABLE sessions;
                ALTER TABLE sessions_new RENAME TO sessions;
                COMMIT;
                """
            )
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def all_sessions(self) -> list[Session]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY folder, sort_order, name"
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def search_sessions(self, query: str) -> list[Session]:
        q = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE  name     LIKE ?
                   OR  hostname LIKE ?
                   OR  username LIKE ?
                   OR  folder   LIKE ?
                ORDER BY folder, sort_order, name
                """,
                (q, q, q, q),
            ).fetchall()
        return [_row_to_session(r) for r in rows]

    def get_by_speed_dial(self, number: int) -> Optional[Session]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE speed_dial=?", (number,)).fetchone()
        return _row_to_session(row) if row else None

    @staticmethod
    def _release_speed_dial(conn: sqlite3.Connection, session: Session) -> None:
        """A speed-dial number belongs to one session at a time: giving it to
        this one takes it from whichever session held it before."""
        if session.speed_dial is not None:
            conn.execute(
                "UPDATE sessions SET speed_dial=NULL WHERE speed_dial=? AND id IS NOT ?",
                (session.speed_dial, session.id),
            )

    def add_session(self, session: Session) -> int:
        with self._connect() as conn:
            self._release_speed_dial(conn, session)
            cursor = conn.execute(
                """
                INSERT INTO sessions
                    (name, folder, hostname, port, username, auth_method,
                     identity_file, proxy_jump, sort_order, background_color,
                     speed_dial, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    session.name,
                    session.folder,
                    session.hostname,
                    session.port,
                    session.username,
                    session.auth_method,
                    session.identity_file,
                    session.proxy_jump,
                    session.sort_order,
                    session.background_color,
                    session.speed_dial,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_session(self, session: Session) -> None:
        with self._connect() as conn:
            self._release_speed_dial(conn, session)
            conn.execute(
                """
                UPDATE sessions
                SET    name=?, folder=?, hostname=?, port=?, username=?,
                       auth_method=?, identity_file=?, proxy_jump=?,
                       sort_order=?, background_color=?, speed_dial=?,
                       updated_at=datetime('now')
                WHERE  id=?
                """,
                (
                    session.name,
                    session.folder,
                    session.hostname,
                    session.port,
                    session.username,
                    session.auth_method,
                    session.identity_file,
                    session.proxy_jump,
                    session.sort_order,
                    session.background_color,
                    session.speed_dial,
                    session.id,
                ),
            )

    def delete_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        name=row["name"],
        folder=row["folder"],
        hostname=row["hostname"],
        port=row["port"],
        username=row["username"],
        auth_method=row["auth_method"],
        identity_file=row["identity_file"],
        proxy_jump=row["proxy_jump"],
        sort_order=row["sort_order"],
        background_color=row["background_color"],
        speed_dial=row["speed_dial"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
