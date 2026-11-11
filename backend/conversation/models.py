"""Conversation value objects and cooperative cancellation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Literal

Role = Literal["user", "assistant", "system", "tool"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    message_id: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamEvent:
    type: str
    request_id: str
    payload: dict[str, Any]


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()
        self._reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        self._reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

