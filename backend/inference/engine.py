"""Provider-independent streaming inference engine with timeout and telemetry."""
from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from threading import Timer

from backend.conversation.models import CancellationToken, Message
from backend.inference.base import GenerationConfig, InferenceError, InferenceRequest, ModelMetadata, ResourceSnapshot
from backend.inference.configuration import ModelConfiguration
from backend.inference.health import InferenceHealthMonitor
from backend.inference.manager import ModelManager


class InferenceEngine:
    def __init__(self, manager: ModelManager, configuration: ModelConfiguration | None = None,
                 health: InferenceHealthMonitor | None = None) -> None:
        self.manager = manager
        self.configuration = configuration or manager.configuration
        self.health = health or InferenceHealthMonitor()

    @property
    def metadata(self) -> ModelMetadata:
        return self.manager.metadata

    def available(self) -> bool:
        return self.manager.available()

    def load(self) -> None:
        started = time.monotonic()
        self.manager.load()
        self.health.loaded(time.monotonic() - started)

    def unload(self) -> None:
        self.manager.unload()

    def resources(self) -> ResourceSnapshot:
        return self.manager.resources()

    def status(self) -> dict[str, object]:
        snapshot = self.manager.resources()
        return self.health.snapshot(self.manager.status()) | {"resources": {
            "cpu_percent": snapshot.cpu_percent, "memory_bytes": snapshot.memory_bytes,
            "memory_limit_bytes": snapshot.memory_limit_bytes,
        }}

    def _bound_request(self, request: InferenceRequest) -> InferenceRequest:
        # Character-to-token conversion is approximate; reserve output and
        # template headroom so a long in-memory transcript cannot exhaust n_ctx.
        budget = max(192, (self.configuration.context_window - self.configuration.max_tokens - 32) * 3)
        messages = list(request.messages)
        latest_index = max((i for i, message in enumerate(messages) if message.role == "user"), default=len(messages) - 1)
        latest = messages[latest_index] if messages else None
        if latest is not None and len(latest.content) > budget:
            raise InferenceError("current message exceeds the configured model context budget")
        system = messages[0] if messages and messages[0].role == "system" else None
        system_text = system.content if system else ""
        current_chars = len(latest.content) if latest else 0
        if len(system_text) + current_chars > budget:
            system_text = system_text[:max(0, budget - current_chars)]
        remaining = budget - current_chars - len(system_text)
        history = [message for i, message in enumerate(messages)
                   if message is not system and i != latest_index]
        selected: list[Message] = []
        for message in reversed(history):
            if len(message.content) > remaining:
                continue
            selected.append(message)
            remaining -= len(message.content)
        selected.reverse()
        bounded = ([replace(system, content=system_text)]
                   if system else []) + selected + ([latest] if latest else [])
        return InferenceRequest(tuple(bounded), request.request_id, request.metadata, request.generation)

    def stream(self, request: InferenceRequest, cancellation: CancellationToken) -> Iterator[str]:
        started = time.monotonic()
        timer = Timer(self.configuration.timeout_seconds, cancellation.cancel, kwargs={"reason": "timeout"})
        timer.daemon = True
        chunks: list[str] = []
        options = request.generation or GenerationConfig(
            max_tokens=self.configuration.max_tokens, temperature=self.configuration.temperature,
            top_p=self.configuration.top_p, repeat_penalty=self.configuration.repeat_penalty,
        )
        generation = GenerationConfig(min(options.max_tokens, self.configuration.max_tokens),
                                      options.temperature, options.top_p, options.repeat_penalty)
        try:
            configured = self._bound_request(InferenceRequest(request.messages, request.request_id,
                                                              request.metadata, generation))
        except InferenceError as exc:
            self.health.failed(str(exc))
            raise
        was_loaded = self.manager.status()["loaded"]
        inference_started = started
        timer.start()
        try:
            if not was_loaded:
                load_start = time.monotonic()
                self.manager.load()
                self.health.loaded(time.monotonic() - load_start)
            inference_started = time.monotonic()
            if cancellation.cancelled:
                error = "inference_timeout" if cancellation.reason == "timeout" else "cancelled"
                raise InferenceError(error)
            for chunk in self.manager.stream(configured, cancellation):
                if cancellation.cancelled:
                    break
                chunks.append(chunk)
                yield chunk
            if cancellation.cancelled:
                reason = cancellation.reason
                error = "inference_timeout" if reason == "timeout" else "cancelled"
                raise InferenceError(error)
            self.health.completed(time.monotonic() - inference_started, time.monotonic() - started,
                                  "".join(chunks), self.manager.resources())
        except InferenceError as exc:
            error = "inference_timeout" if cancellation.reason == "timeout" else str(exc)
            self.health.failed(error, cancelled=error == "cancelled")
            raise InferenceError(error) from exc
        except Exception as exc:
            self.health.failed(str(exc))
            raise InferenceError(f"inference failed: {exc}") from exc
        finally:
            timer.cancel()
