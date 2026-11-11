"""Append-only interaction traces and lightweight outcome analysis."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any


class TraceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._lock = RLock()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS traces (trace_id TEXT PRIMARY KEY, request TEXT, agent TEXT, model TEXT, outcome TEXT, started_at REAL, ended_at REAL, payload TEXT)")

    def start(self, request: str, *, agent: str = "main_assistant", model: str = "unconfigured", payload: dict[str, Any] | None = None) -> str:
        trace_id = str(uuid.uuid4())
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (trace_id, request, agent, model, "running", time.time(), None, json.dumps(payload or {})))
        return trace_id

    def complete(self, trace_id: str, outcome: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("UPDATE traces SET outcome=?, ended_at=?, payload=? WHERE trace_id=?", (outcome, time.time(), json.dumps(payload or {}), trace_id))

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT trace_id, request, agent, model, outcome, started_at, ended_at, payload FROM traces ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        return [{"trace_id": row[0], "request": row[1], "agent": row[2], "model": row[3], "outcome": row[4], "started_at": row[5], "ended_at": row[6], "payload": json.loads(row[7])} for row in rows]

    def analyze(self) -> dict[str, Any]:
        rows = self.list(1000)
        outcomes: dict[str, int] = {}
        for row in rows: outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        return {"count": len(rows), "outcomes": outcomes}
