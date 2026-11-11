"""Provider-independent inference contracts."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from backend.conversation.models import CancellationToken, Message


class InferenceError(RuntimeError):
    """Expected provider failure, reported without fabricating an answer."""


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    provider: str
    model_id: str
    parameter_count: str = "unknown"
    context_window: int = 0
    local: bool = True


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    max_tokens: int = 256
    temperature: float = 0.2
    top_p: float = 0.9
    repeat_penalty: float = 1.1


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    messages: tuple[Message, ...]
    request_id: str
    metadata: dict[str, str] = field(default_factory=dict)
    generation: GenerationConfig | None = None


class ModelProvider(Protocol):
    metadata: ModelMetadata

    def available(self) -> bool: ...
    def stream(self, request: InferenceRequest, cancellation: CancellationToken) -> Iterator[str]: ...
    def load(self) -> None: ...
    def unload(self) -> None: ...
    def resources(self) -> ResourceSnapshot: ...


# Backwards-compatible name used by the conversation and router layers.
InferenceProvider = ModelProvider

