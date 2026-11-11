"""Optional llama.cpp Python adapter.

The adapter is deliberately lazy: importing AEGIS never imports llama-cpp-python,
and no model is downloaded or loaded unless AEGIS_MODEL_PATH is configured.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from threading import RLock

from backend.conversation.models import CancellationToken
from backend.inference.base import GenerationConfig, InferenceError, InferenceRequest, ModelMetadata, ResourceSnapshot
from backend.inference.resources import ResourceMonitor


class LlamaCppProvider:
    name = "llama.cpp"

    def __init__(self, model_path: str | None, context_window: int = 1024, threads: int = 2,
                 *, memory_map: bool = True) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        model_id = self.model_path.name if self.model_path else "unconfigured"
        self.metadata = ModelMetadata(self.name, model_id, parameter_count="local GGUF", context_window=context_window, local=True)
        self.context_window = context_window
        self.threads = max(1, threads)
        self.memory_map = memory_map
        self._model = None
        self._lock = RLock()
        self._monitor = ResourceMonitor()

    @classmethod
    def from_environment(cls) -> "LlamaCppProvider":
        return cls(os.getenv("AEGIS_MODEL_PATH"), int(os.getenv("AEGIS_MODEL_CONTEXT", "1024")),
                   int(os.getenv("AEGIS_MODEL_THREADS", "2")))

    def available(self) -> bool:
        return self.availability_reason() is None

    def availability_reason(self) -> str | None:
        if self.model_path is None:
            return "no GGUF model is configured (set AEGIS_MODEL_PATH)"
        if not self.model_path.is_file():
            return "configured GGUF model file is missing"
        if self._import_llama() is None:
            return "optional llama-cpp-python runtime is not installed"
        return None

    def _import_llama(self):
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
            return Llama
        except ImportError:
            return None

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not self.model_path or not self.model_path.is_file():
                raise InferenceError("configured local model file is missing")
            llama = self._import_llama()
            if llama is None:
                raise InferenceError("llama-cpp-python is not installed")
            self._model = llama(model_path=str(self.model_path), n_ctx=self.context_window,
                                n_threads=self.threads, use_mmap=self.memory_map,
                                n_batch=64, n_ubatch=32, n_gpu_layers=0, verbose=False)

    def unload(self) -> None:
        with self._lock:
            self._model = None

    def resources(self) -> ResourceSnapshot:
        return self._monitor.snapshot()

    def stream(self, request: InferenceRequest, cancellation: CancellationToken) -> Iterator[str]:
        if not self.available():
            raise InferenceError("local llama.cpp inference is unavailable")
        self.load()
        messages = [{"role": message.role, "content": message.content} for message in request.messages]
        generation = request.generation or GenerationConfig()
        try:
            result = self._model.create_chat_completion(
                messages=messages, max_tokens=generation.max_tokens,
                temperature=generation.temperature, top_p=generation.top_p,
                repeat_penalty=generation.repeat_penalty, stream=True,
            )
            for item in result:
                if cancellation.cancelled:
                    return
                text = item.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if text:
                    yield str(text)
        except Exception as exc:
            raise InferenceError(f"local llama.cpp inference failed: {exc}") from exc
