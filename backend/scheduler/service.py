"""Bounded in-process scheduler with optional durable approved workflows."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.security.audit import AuditLogger


@dataclass(slots=True)
class ScheduledTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    interval_seconds: float = 0.0
    owner: str = "user"
    status: str = "scheduled"
    next_run: float = field(default_factory=time.time)
    retries: int = 0
    max_retries: int = 0
    workflow_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None


class TaskScheduler:
    def __init__(self, audit: AuditLogger, database_path: str | Path | None = None) -> None:
        self.audit = audit
        self.database_path = Path(database_path) if database_path else None
        self._tasks: dict[str, ScheduledTask] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._workflows: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._stop = threading.Event(); self._lock = threading.RLock()
        if self.database_path:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY, name TEXT, interval REAL, owner TEXT, status TEXT, next_run REAL, retries INTEGER, max_retries INTEGER, workflow_id TEXT, payload TEXT, last_error TEXT)")
                connection.execute("CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, timestamp REAL, status TEXT, output TEXT)")
                for row in connection.execute("SELECT task_id,name,interval,owner,next_run,retries,max_retries,workflow_id,payload,last_error FROM tasks WHERE workflow_id IS NOT NULL"):
                    task = ScheduledTask(row[0], row[1], row[2], row[3], "paused", row[4], row[5], row[6], row[7], json.loads(row[8]), row[9])
                    self._tasks[task.task_id] = task
                connection.execute("UPDATE tasks SET status='paused' WHERE workflow_id IS NOT NULL")
        self._thread = threading.Thread(target=self._loop, name="aegis-scheduler", daemon=True); self._thread.start()

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def register_workflow(self, workflow_id: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not workflow_id or len(workflow_id) > 100:
            raise ValueError("invalid workflow identity")
        with self._lock:
            self._workflows[workflow_id] = handler
            for task in self._tasks.values():
                if task.workflow_id == workflow_id:
                    self._handlers[task.task_id] = lambda item=task, fn=handler: fn(item.payload)

    def schedule_workflow(self, workflow_id: str, payload: dict[str, Any], *, delay_seconds: float = 0,
                          interval_seconds: float = 0, confirmed: bool = False,
                          max_retries: int = 0, owner: str = "user") -> ScheduledTask:
        if not confirmed:
            raise PermissionError("explicit approval is required before scheduling")
        if not isinstance(workflow_id, str) or workflow_id not in self._workflows:
            raise ValueError("workflow is not registered")
        if not isinstance(payload, dict) or len(json.dumps(payload, default=str)) > 8000:
            raise ValueError("workflow payload exceeds its limit")
        if not 0 <= delay_seconds <= 31_536_000 or not 0 <= interval_seconds <= 31_536_000 or not 0 <= max_retries <= 3:
            raise ValueError("schedule timing or retry count is outside supported bounds")
        task = ScheduledTask(name=workflow_id, interval_seconds=interval_seconds, owner=owner,
                             next_run=time.time() + delay_seconds, max_retries=max_retries,
                             workflow_id=workflow_id, payload=payload)
        with self._lock:
            if len(self._tasks) >= 256: raise RuntimeError("scheduler task limit reached")
            self._tasks[task.task_id] = task
            self._handlers[task.task_id] = lambda: self._workflows[workflow_id](payload)
            self._persist(task)
        self.audit.record("scheduler.approve", actor=owner, outcome="scheduled", details={"task_id": task.task_id, "workflow_id": workflow_id})
        return task

    def schedule(self, name: str, handler: Callable[[], None], *, delay_seconds: float = 0, interval_seconds: float = 0,
                 owner: str = "user", max_retries: int = 0) -> ScheduledTask:
        """In-process compatibility for trusted internal call sites only."""
        if not name.strip() or delay_seconds < 0 or interval_seconds < 0:
            raise ValueError("invalid scheduled task")
        task = ScheduledTask(name=name, interval_seconds=interval_seconds, owner=owner,
                             next_run=time.time() + delay_seconds, max_retries=max_retries)
        with self._lock:
            if len(self._tasks) >= 256: raise RuntimeError("scheduler task limit reached")
            self._tasks[task.task_id] = task; self._handlers[task.task_id] = handler
        return task

    def list(self) -> list[ScheduledTask]:
        with self._lock: return list(self._tasks.values())

    def history(self, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if self.database_path is None: return []
        with self._db() as connection:
            rows = connection.execute("SELECT task_id,timestamp,status,output FROM history WHERE (? IS NULL OR task_id=?) ORDER BY id DESC LIMIT ?",
                                      (task_id, task_id, max(1, min(limit, 500)))).fetchall()
        return [{"task_id": row[0], "timestamp": row[1], "status": row[2], "output": json.loads(row[3])} for row in rows]

    def pause(self, task_id: str) -> bool:
        return self._set_status(task_id, "paused")

    def resume(self, task_id: str, *, confirmed: bool = False) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.workflow_id and not confirmed: return False
        return self._set_status(task_id, "scheduled")

    def cancel(self, task_id: str) -> bool:
        return self._set_status(task_id, "cancelled")

    def _set_status(self, task_id: str, status: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in {"completed", "cancelled", "failed"}: return False
            task.status = status
            self._persist(task)
        self.audit.record("scheduler.task", actor="user", outcome=status, details={"task_id": task_id}); return True

    def _persist(self, task: ScheduledTask) -> None:
        if not self.database_path or not task.workflow_id: return
        with self._db() as connection:
            connection.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (task.task_id, task.name, task.interval_seconds, task.owner, task.status, task.next_run,
                 task.retries, task.max_retries, task.workflow_id, json.dumps(task.payload), task.last_error))

    def _record_history(self, task: ScheduledTask, status: str, output: Any) -> None:
        if self.database_path:
            with self._db() as connection:
                connection.execute("INSERT INTO history(task_id,timestamp,status,output) VALUES(?,?,?,?)",
                    (task.task_id, time.time(), status, json.dumps(output, default=str)[:10000]))
        self.audit.record("scheduler.execute", actor=task.owner, outcome=status,
                          details={"task_id": task.task_id, "workflow_id": task.workflow_id, "output": str(output)[:1000]})

    def _loop(self) -> None:
        while not self._stop.wait(.25):
            for task in self.list():
                if task.status != "scheduled" or task.next_run > time.time(): continue
                handler = self._handlers.get(task.task_id)
                if handler is None:
                    task.status = "paused"; task.last_error = "workflow_handler_unavailable"; self._persist(task); continue
                try:
                    output = handler(); task.retries = 0; task.last_error = None
                    task.status = "scheduled" if task.interval_seconds else "completed"
                    self._record_history(task, "completed", output)
                except Exception as exc:
                    task.retries += 1; task.last_error = str(exc)[:500]
                    task.status = "scheduled" if task.retries <= task.max_retries else "failed"
                    self._record_history(task, "failed", {"error": task.last_error})
                if task.status == "scheduled": task.next_run = time.time() + task.interval_seconds
                self._persist(task)

    def shutdown(self) -> None:
        self._stop.set(); self._thread.join(timeout=2)
