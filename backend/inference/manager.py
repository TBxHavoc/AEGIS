"""Single-model, load-on-demand manager for local GGUF files."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Callable

from backend.conversation.models import CancellationToken
from backend.inference.base import InferenceError, InferenceRequest, ModelMetadata, ResourceSnapshot
from backend.inference.configuration import ModelConfiguration
from backend.inference.llama_cpp import LlamaCppProvider
from backend.inference.registry import ModelRegistry, RegisteredModel
from backend.inference.resources import ResourceMonitor


class ModelManager:
    """Owns one active provider and unloads it before switching models."""

    def __init__(self, configuration: ModelConfiguration, registry: ModelRegistry,
                 provider_factory: Callable[[ModelConfiguration], object] | None = None) -> None:
        self.configuration = configuration
        self.registry = registry
        self._provider_factory = provider_factory or self._llama_provider
        self._provider = self._provider_factory(configuration)
        self._loaded = False
        self._lock = RLock()
        self._inference_lock = RLock()
        self._resources = ResourceMonitor()
        self._last_error: str | None = None
        if configuration.model_path:
            try:
                self.active_model = registry.register(configuration.model_path)
            except (OSError, ValueError):
                self.active_model = None
        else:
            self.active_model = None

    @staticmethod
    def _llama_provider(configuration: ModelConfiguration) -> LlamaCppProvider:
        return LlamaCppProvider(configuration.model_path, configuration.context_window, configuration.threads)

    @property
    def metadata(self) -> ModelMetadata:
        return self._provider.metadata

    def available(self) -> bool:
        return bool(self.active_model and self._provider.available())

    def resources(self) -> ResourceSnapshot:
        return self._resources.snapshot()

    @staticmethod
    def _available_memory() -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _preflight(self, model: RegisteredModel) -> None:
        available = self._available_memory()
        if available is None:
            return
        # Reserve space for the OS, KV cache, and llama.cpp runtime. This is a
        # conservative guard, not an exact prediction of peak RSS.
        kv_reserve = self.configuration.context_window * 256 * 1024
        estimated_peak = model.size_bytes + 640 * 1024 * 1024 + kv_reserve
        allowed = int(available * self.configuration.max_memory_fraction)
        if estimated_peak > allowed:
            raise InferenceError(
                "model load refused by memory guard: estimated weights/runtime/context "
                f"{estimated_peak // (1024 * 1024)} MiB exceeds the current safe budget "
                f"{allowed // (1024 * 1024)} MiB; close other applications or use a smaller context/model"
            )

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            if self.active_model is None:
                self._last_error = "no GGUF model is configured"
                raise InferenceError(self._last_error)
            try:
                self._preflight(self.active_model)
                self._provider.load()
                self._loaded = True
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                raise InferenceError(f"model load failed: {exc}") from exc

    def unload(self) -> None:
        with self._inference_lock, self._lock:
            self._provider.unload()
            self._loaded = False

    def switch_model(self, model_id: str) -> RegisteredModel:
        model = self.registry.get(model_id)
        if model is None:
            raise InferenceError("model is not registered or the file is missing")
        new_configuration = replace(self.configuration, model_path=model.path)
        new_provider = self._provider_factory(new_configuration)
        with self._lock:
            self.unload()
            self.configuration = new_configuration
            self._provider = new_provider
            self.active_model = model
            self._last_error = None
        return model

    def stream(self, request: InferenceRequest, cancellation: CancellationToken):
        while not self._inference_lock.acquire(timeout=0.1):
            if cancellation.cancelled:
                raise InferenceError(cancellation.reason)
        try:
            if cancellation.cancelled:
                raise InferenceError(cancellation.reason)
            self.load()
            if cancellation.cancelled:
                raise InferenceError(cancellation.reason)
            yield from self._provider.stream(request, cancellation)
        finally:
            self._inference_lock.release()

    def status(self) -> dict[str, object]:
        reason = None
        if not self._loaded and hasattr(self._provider, "availability_reason"):
            reason = self._provider.availability_reason()
        reason = self._last_error or reason
        return {
            "available": self.available(), "loaded": self._loaded,
            "model": self.active_model.model_id if self.active_model else "unconfigured",
            "model_file": Path(self.active_model.path).name if self.active_model else None,
            "provider": self.metadata.provider, "context_window": self.configuration.context_window,
            "threads": self.configuration.threads, "last_error": self._last_error,
            "availability_reason": reason,
        }
