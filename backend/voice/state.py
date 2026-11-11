"""Observable voice subsystem state."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class VoiceState:
    speaking: bool = False
    synthesizing: bool = False
    microphone_active: bool = False
    muted: bool = False
    active_request_id: str | None = None
    error: str | None = None


class VoiceStateStore:
    def __init__(self) -> None:
        self._state = VoiceState()
        self._lock = RLock()

    def get(self) -> VoiceState:
        with self._lock:
            return self._state

    def update(self, **changes: object) -> VoiceState:
        with self._lock:
            values = {field: getattr(self._state, field) for field in self._state.__dataclass_fields__}
            values.update(changes)
            self._state = VoiceState(**values)
            return self._state

