"""Microphone lifecycle boundary; no privileged command execution is performed."""
from __future__ import annotations

from threading import RLock


class MicrophoneError(RuntimeError):
    pass


class MicrophoneCapture:
    def __init__(self) -> None:
        self._active = False
        self._lock = RLock()

    def start(self) -> None:
        with self._lock:
            self._active = True

    def stop(self) -> None:
        with self._lock:
            self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

