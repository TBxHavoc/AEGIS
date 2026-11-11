"""Small in-process inference health and latency counters."""
from __future__ import annotations

from threading import Lock
from typing import Any

from backend.inference.base import ResourceSnapshot


class InferenceHealthMonitor:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests = 0
        self._errors = 0
        self._cancellations = 0
        self._last_error: str | None = None
        self._last_load_seconds: float | None = None
        self._last_inference_seconds: float | None = None
        self._last_request_seconds: float | None = None
        self._last_tokens_estimate = 0
        self._last_tokens_per_second: float | None = None
        self._last_memory_bytes: int | None = None

    def loaded(self, seconds: float) -> None:
        with self._lock:
            self._last_load_seconds = max(0.0, seconds)

    def completed(self, seconds: float, request_seconds: float, text: str, resources: ResourceSnapshot) -> None:
        tokens = max(0, round(len(text) / 4))
        with self._lock:
            self._requests += 1
            self._last_inference_seconds = max(0.0, seconds)
            self._last_request_seconds = max(0.0, request_seconds)
            self._last_tokens_estimate = tokens
            self._last_tokens_per_second = tokens / seconds if seconds > 0 else None
            self._last_memory_bytes = resources.memory_bytes
            self._last_error = None

    def failed(self, error: str, *, cancelled: bool = False) -> None:
        with self._lock:
            self._errors += 1
            self._cancellations += int(cancelled)
            self._last_error = error

    def snapshot(self, manager_status: dict[str, Any]) -> dict[str, object]:
        with self._lock:
            return {
                **manager_status, "requests": self._requests, "errors": self._errors,
                "cancellations": self._cancellations, "last_error": self._last_error or manager_status.get("last_error"),
                "model_load_seconds": self._last_load_seconds,
                "last_inference_seconds": self._last_inference_seconds,
                "last_request_seconds": self._last_request_seconds,
                "last_tokens_estimate": self._last_tokens_estimate,
                "last_tokens_per_second_estimate": self._last_tokens_per_second,
                "memory_bytes": self._last_memory_bytes,
            }
