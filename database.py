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
                             CHECK (auth_method IN ('password', 'default', 'identity', 'fido2')),
    identity_file    TEXT,
    proxy_jump       TEXT,
    sort_order       INTEGER,
    background_color TEXT,
    created_at       TEXT,
    updated_at       TEXT
)
"""


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

    def add_session(self, session: Session) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions
                    (name, folder, hostname, port, username, auth_method,
                     identity_file, proxy_jump, sort_order, background_color,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
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
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_session(self, session: Session) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET    name=?, folder=?, hostname=?, port=?, username=?,
                       auth_method=?, identity_file=?, proxy_jump=?,
                       sort_order=?, background_color=?, updated_at=datetime('now')
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
