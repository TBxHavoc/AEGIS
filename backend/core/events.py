"""Event names and duplicate request tracking."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any

EVENT_TYPES = frozenset({
    "client.connected", "client.disconnected", "conversation.message",
    "conversation.response.started", "conversation.response.chunk",
    "conversation.response.completed", "conversation.response.failed",
    "conversation.response.cancel", "conversation.response.interrupt",
    "audio.session.start", "audio.chunk", "audio.session.end", "audio.session.cancel",
    "system.status", "system.error",
})


@dataclass(frozen=True, slots=True)
class Event:
    type: str
    request_id: str
    payload: dict[str, Any]


class RequestDeduplicator:
    def __init__(self, capacity: int = 2048) -> None:
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._capacity = capacity
        self._lock = Lock()

    def seen_or_add(self, request_id: str) -> bool:
        with self._lock:
            if request_id in self._seen:
                return True
            self._seen.add(request_id)
            self._order.append(request_id)
            while len(self._order) > self._capacity:
                self._seen.remove(self._order.popleft())
            return False

