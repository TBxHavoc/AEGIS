"""Private, local SQLite memory with explicit categories and retention."""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock


class MemoryCategory(StrEnum):
    CONVERSATION_HISTORY = "conversation_history"
    SHORT_TERM_CONTEXT = "short_term_context"
    LONG_TERM_PERSONAL = "long_term_personal"
    EXPLICIT = "explicit"
    RETRIEVED_KNOWLEDGE = "retrieved_knowledge"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    category: MemoryCategory
    content: str
    source: str
    created_at: float
    expires_at: float | None
    explicit: bool


class SensitiveMemoryError(ValueError):
    """Raised when content looks like a credential or secret."""


class MemoryStore:
    _secret_patterns = (
        re.compile(r"(?i)(api[_ -]?key|secret|password|token)\s*[:=]\s*\S+"),
        re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
                source TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL, explicit INTEGER NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS memories_category ON memories(category)")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _validate(self, content: str) -> None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("memory content must be non-empty text")
        if any(pattern.search(content) for pattern in self._secret_patterns):
            raise SensitiveMemoryError("content appears to contain a credential or secret")

    def store(self, category: MemoryCategory, content: str, *, source: str = "user", retention_seconds: float | None = None,
              explicit: bool = False) -> MemoryRecord:
        self._validate(content)
        if category == MemoryCategory.CONVERSATION_HISTORY and not explicit:
            raise PermissionError("conversation history requires explicit retention consent")
        now = time.time()
        expires = now + retention_seconds if retention_seconds is not None else None
        record = MemoryRecord(str(uuid.uuid4()), category, content, source, now, expires, explicit)
        with self._lock, self._connection() as connection:
            connection.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (record.memory_id, record.category.value, record.content, record.source,
                                record.created_at, record.expires_at, int(record.explicit)))
        return record

    def _purge_expired(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?", (time.time(),))

    def retrieve(self, memory_id: str) -> MemoryRecord | None:
        self._purge_expired()
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        return self._row(row) if row else None

    def search(self, query: str, *, category: MemoryCategory | None = None, limit: int = 20) -> list[MemoryRecord]:
        self._purge_expired()
        terms = [term for term in re.findall(r"[\w-]+", query.lower()) if term]
        if not terms:
            return []
        where = " AND ".join("lower(content) LIKE ?" for _ in terms)
        args: list[object] = [f"%{term}%" for term in terms]
        if category is not None:
            where += " AND category=?"
            args.append(category.value)
        args.append(max(1, min(limit, 100)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?", args).fetchall()
        records = [self._row(row) for row in rows]
        return sorted(records, key=lambda record: sum(term in record.content.lower() for term in terms), reverse=True)

    def update(self, memory_id: str, content: str) -> MemoryRecord:
        self._validate(content)
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError(memory_id)
            connection.execute("UPDATE memories SET content=? WHERE memory_id=?", (content, memory_id))
        return self.retrieve(memory_id)  # type: ignore[return-value]

    def delete(self, memory_id: str) -> bool:
        with self._lock, self._connection() as connection:
            result = connection.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
            return result.rowcount > 0

    def inspect(self, *, category: MemoryCategory | None = None, limit: int = 100) -> list[MemoryRecord]:
        self._purge_expired()
        with self._lock, self._connection() as connection:
            if category is None:
                rows = connection.execute("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM memories WHERE category=? ORDER BY created_at DESC LIMIT ?", (category.value, limit)).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: tuple[object, ...]) -> MemoryRecord:
        return MemoryRecord(str(row[0]), MemoryCategory(str(row[1])), str(row[2]), str(row[3]), float(row[4]),
                            float(row[5]) if row[5] is not None else None, bool(row[6]))

