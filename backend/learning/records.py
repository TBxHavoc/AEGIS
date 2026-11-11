"""Small SQLite-backed records for controlled evaluation and feedback."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from threading import RLock


class LearningRecords:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, kind TEXT, input_json TEXT, output_json TEXT, approved INTEGER, created_at REAL)")

    def record(self, kind: str, input_data: dict[str, object], output_data: dict[str, object], approved: bool = False) -> str:
        record_id = str(uuid.uuid4())
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)",
                       (record_id, kind, json.dumps(input_data), json.dumps(output_data), int(approved), time.time()))
        return record_id

    def inspect(self, limit: int = 100) -> list[dict[str, object]]:
        with self._lock, sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id, kind, input_json, output_json, approved, created_at FROM evaluations ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        return [{"id": row[0], "kind": row[1], "input": json.loads(row[2]), "output": json.loads(row[3]),
                 "approved": bool(row[4]), "created_at": row[5]} for row in rows]
