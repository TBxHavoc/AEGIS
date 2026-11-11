"""Bounded agent task value objects."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AgentType(StrEnum):
    MAIN_ASSISTANT = "main_assistant"
    RESEARCH = "research"
    CODING = "coding"
    PRODUCTIVITY = "productivity"
    DATA_ANALYSIS = "data_analysis"
    BUSINESS = "business"
    SYSTEM_ASSISTANCE = "system_assistance"
    DEFENSIVE_CYBERSECURITY = "defensive_cybersecurity"
    PROTOCOL_MANAGEMENT = "protocol_management"
    KNOWLEDGE_MANAGEMENT = "knowledge_management"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(slots=True)
class AgentTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent: AgentType = AgentType.MAIN_ASSISTANT
    instruction: str = ""
    status: TaskStatus = TaskStatus.QUEUED
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    root_request_id: str = ""
    parent_task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    depth: int = 0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_id: str
    name: str
    description: str
    version: str
    capabilities: tuple[str, ...]
    required_permissions: tuple[str, ...] = ("READ_ONLY",)
    allowed_tools: tuple[str, ...] = ()
    enabled: bool = True
    required_tools: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AgentResult:
    task_id: str
    agent_id: str
    status: str
    summary: str
    findings: tuple[dict[str, Any], ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()
    actions_performed: tuple[dict[str, Any], ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confidence: float = 0.0
    verification_status: str = "unverified"
    protocols_applied: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AgentMessage:
    message_id: str
    root_request_id: str
    sender_agent_id: str
    recipient_agent_id: str
    message_type: str
    task_id: str
    payload: dict[str, Any]
    context_refs: tuple[str, ...] = ()

    TYPES = frozenset({"task.assignment", "task.result", "agent.request", "progress", "cancel", "failure"})

    @classmethod
    def create(cls, *, root_request_id: str, sender: str, recipient: str, kind: str,
               task_id: str, payload: dict[str, Any], context_refs: tuple[str, ...] = ()) -> "AgentMessage":
        if kind not in cls.TYPES or not root_request_id or not task_id:
            raise ValueError("invalid agent message type or request identity")
        if not sender or not recipient or len(payload) > 32 or len(context_refs) > 20:
            raise ValueError("agent message exceeds schema limits")
        if len(str(payload)) > 12000:
            raise ValueError("agent message payload exceeds size limit")
        required = {"task.assignment": {"instruction", "source"},
                    "task.result": {"status", "verification", "summary"},
                    "agent.request": {"question"}, "progress": {"completed_agents", "total_agents", "status"},
                    "cancel": {"reason"}, "failure": {"error"}}[kind]
        if not required.issubset(payload) or any(not isinstance(key, str) for key in payload):
            raise ValueError("agent message payload does not match its schema")
        if any(not isinstance(value, (str, int, float, bool, list, tuple, dict, type(None))) for value in payload.values()):
            raise ValueError("agent message contains an unsupported value")
        return cls(str(uuid.uuid4()), root_request_id, sender, recipient, kind, task_id, payload, context_refs)

