"""Local document index with source tracking and simple bounded retrieval."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from backend.security.guardrails import validate_path
from backend.security.control import ProtectedPaths


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    source: str
    content: str
    created_at: float


class KnowledgeIndex:
    def __init__(self, path: str | Path, allowed_root: str | Path,
                 protected_roots: tuple[str | Path, ...] = ()) -> None:
        self.path = Path(path)
        self.allowed_root = Path(allowed_root).resolve()
        self.protected_roots = tuple(Path(item).resolve() for item in protected_roots)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, source TEXT, content TEXT, created_at REAL)")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def ingest_file(self, path: str | Path) -> KnowledgeDocument:
        supplied = Path(path).expanduser()
        if not supplied.is_absolute(): supplied = self.allowed_root / supplied
        lexical = Path(__import__("os").path.abspath(supplied))
        ProtectedPaths(self.protected_roots).validate(lexical, (self.allowed_root,), allow_missing=False)
        if lexical != self.allowed_root and self.allowed_root not in lexical.parents:
            raise PermissionError("path is outside the approved knowledge root")
        if any(part.casefold() in {".git", ".agents", ".codex", ".aws"} for part in lexical.relative_to(self.allowed_root).parts):
            raise PermissionError("protected project metadata cannot be indexed")
        current = self.allowed_root
        for part in lexical.relative_to(self.allowed_root).parts:
            current = current / part
            if current.is_symlink(): raise PermissionError("symlink documents cannot be indexed")
        safe = validate_path(lexical, self.allowed_root, must_exist=True)
        if safe.suffix.lower() not in {".txt", ".md", ".markdown", ".rst", ".py", ".json", ".yaml", ".yml", ".html", ".css", ".js"}:
            raise ValueError("unsupported local text document type")
        if not safe.is_file() or safe.stat().st_size > 1_000_000:
            raise ValueError("document must be a regular file under one megabyte")
        content = safe.read_text(encoding="utf-8")
        document = KnowledgeDocument(str(uuid.uuid4()), str(safe), content, time.time())
        with self._lock, self._connection() as connection:
            connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", (document.document_id, document.source, document.content, document.created_at))
        return document

    def search(self, query: str, limit: int = 10) -> list[KnowledgeDocument]:
        terms = [term for term in query.lower().split() if term]
        if not terms:
            return []
        where = " AND ".join("lower(content) LIKE ?" for _ in terms)
        args: list[object] = [f"%{term}%" for term in terms]
        args.append(max(1, min(limit, 50)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(f"SELECT * FROM documents WHERE {where} ORDER BY created_at DESC LIMIT ?", args).fetchall()
        return [KnowledgeDocument(str(row[0]), str(row[1]), str(row[2]), float(row[3])) for row in rows]

    def inspect(self, limit: int = 100) -> list[KnowledgeDocument]:
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [KnowledgeDocument(str(row[0]), str(row[1]), str(row[2]), float(row[3])) for row in rows]

