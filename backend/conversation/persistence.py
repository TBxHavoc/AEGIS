"""Small SQLite conversation store; persistence is opt-in from configuration."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from backend.conversation.models import Message


class ConversationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS messages_session_time
                    ON messages(session_id, created_at);
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def ensure_session(self, session_id: str) -> None:
        now = time.time()
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)",
                (session_id, now, now),
            )
            connection.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))

    def append(self, session_id: str, message: Message) -> None:
        self.ensure_session(session_id)
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (message.message_id, session_id, message.role, message.content,
                 message.created_at, json.dumps(message.metadata, sort_keys=True)),
            )

    def history(self, session_id: str, limit: int = 20) -> list[Message]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [Message(row["role"], row["content"], row["message_id"], row["created_at"], json.loads(row["metadata"])) for row in reversed(rows)]

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

