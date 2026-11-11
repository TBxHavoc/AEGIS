"""Interfaces for intelligence stages; implementations must declare their nature."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.conversation.models import Message


@dataclass(frozen=True, slots=True)
class Intent:
    name: str
    confidence: float
    source: str = "unavailable"


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[str, ...]
    source: str = "unavailable"


@dataclass(frozen=True, slots=True)
class ToolChoice:
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


class IntentDetector(Protocol):
    def detect(self, message: str) -> Intent: ...


class ContextInterpreter(Protocol):
    def interpret(self, messages: tuple[Message, ...]) -> dict[str, object]: ...


class Reasoner(Protocol):
    def reason(self, context: dict[str, object]) -> Plan: ...


class Planner(Protocol):
    def plan(self, intent: Intent, context: dict[str, object]) -> Plan: ...


class TaskDecomposer(Protocol):
    def decompose(self, plan: Plan) -> tuple[str, ...]: ...


class ResponseGenerator(Protocol):
    def generate(self, messages: tuple[Message, ...]) -> str: ...


class ToolSelector(Protocol):
    def select(self, intent: Intent) -> tuple[ToolChoice, ...]: ...


class ToolResultInterpreter(Protocol):
    def interpret(self, result: object) -> str: ...

