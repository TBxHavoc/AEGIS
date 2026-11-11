"""Finite, cancellable agent task manager with no autonomous loop."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event, RLock
from typing import Callable

from backend.agents.models import AgentTask, AgentType, TaskStatus
from backend.security.audit import AuditLogger


class AgentManager:
    def __init__(self, audit: AuditLogger, max_workers: int = 2) -> None:
        self.audit = audit
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aegis-agent")
        self._tasks: dict[str, AgentTask] = {}
        self._cancel: dict[str, Event] = {}
        self._lock = RLock()
        self.handlers: dict[AgentType, Callable[[AgentTask, Event], dict[str, object]]] = {
            agent: self._unavailable for agent in AgentType
        }

    def create(self, instruction: str, agent: AgentType = AgentType.MAIN_ASSISTANT) -> AgentTask:
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("task instruction must be non-empty")
        task = AgentTask(agent=agent, instruction=instruction)
        with self._lock:
            self._tasks[task.task_id] = task
            self._cancel[task.task_id] = Event()
        return task

    def route(self, instruction: str) -> AgentType:
        lowered = instruction.lower()
        if any(word in lowered for word in ("research", "source", "investigate")):
            return AgentType.RESEARCH
        if any(word in lowered for word in ("code", "python", "debug")):
            return AgentType.CODING
        if any(word in lowered for word in ("schedule", "todo", "productivity")):
            return AgentType.PRODUCTIVITY
        if any(word in lowered for word in ("business", "revenue", "operations", "financial analysis")):
            return AgentType.BUSINESS
        if any(word in lowered for word in ("csv", "spreadsheet", "data analysis", "statistics")):
            return AgentType.DATA_ANALYSIS
        if any(word in lowered for word in ("security", "vulnerability", "cybersecurity", "log analysis")):
            return AgentType.DEFENSIVE_CYBERSECURITY
        if any(word in lowered for word in ("system status", "cpu", "memory usage", "linux")):
            return AgentType.SYSTEM_ASSISTANCE
        if any(word in lowered for word in ("protocol", "standard", "procedure")):
            return AgentType.PROTOCOL_MANAGEMENT
        if any(word in lowered for word in ("knowledge base", "document index", "knowledge management")):
            return AgentType.KNOWLEDGE_MANAGEMENT
        return AgentType.MAIN_ASSISTANT

    def run(self, task_id: str, timeout_seconds: float = 10.0) -> AgentTask:
        task = self._tasks[task_id]
        task.status = TaskStatus.RUNNING
        self.audit.record("agent.run", actor=task.agent.value, outcome="started", details={"task_id": task_id})
        future: Future[dict[str, object]] = self._executor.submit(self.handlers[task.agent], task, self._cancel[task_id])
        try:
            task.result = future.result(timeout=timeout_seconds)
            task.status = TaskStatus.CANCELLED if self._cancel[task_id].is_set() else TaskStatus.COMPLETED
        except FutureTimeout:
            self._cancel[task_id].set()
            task.status = TaskStatus.TIMED_OUT
            task.error = "agent_timeout"
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        self.audit.record("agent.run", actor=task.agent.value, outcome=task.status.value, details={"task_id": task_id})
        return task

    def cancel(self, task_id: str) -> bool:
        event = self._cancel.get(task_id)
        if event is None:
            return False
        event.set()
        task = self._tasks[task_id]
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.CANCELLED
        self.audit.record("agent.cancel", actor="user", outcome="requested", details={"task_id": task_id})
        return True

    def get(self, task_id: str) -> AgentTask | None:
        return self._tasks.get(task_id)

    @staticmethod
    def _unavailable(task: AgentTask, cancel: Event) -> dict[str, object]:
        return {"available": False, "message": "This agent has no configured inference or tools."}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

