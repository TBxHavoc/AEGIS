"""Validated runtime settings for local and future inference providers."""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = "You are AEGIS, a helpful, honest personal assistant. Be concise and say when you are uncertain."


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    model_path: str | None = None
    context_window: int = 1024
    threads: int = 2
    timeout_seconds: float = 120.0
    max_tokens: int = 256
    temperature: float = 0.2
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    max_memory_fraction: float = 0.75
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def __post_init__(self) -> None:
        if not 128 <= self.context_window <= 32768:
            raise ValueError("context_window must be between 128 and 32768")
        if not 1 <= self.threads <= 64:
            raise ValueError("threads must be between 1 and 64")
        if not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 1 and 1800")
        if not 1 <= self.max_tokens <= min(8192, self.context_window - 64):
            raise ValueError("max_tokens must leave at least 64 context tokens for the prompt")
        if not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("generation temperature or top_p is invalid")
        if not 1 <= self.repeat_penalty <= 2:
            raise ValueError("repeat_penalty must be between 1 and 2")
        if not 0.25 <= self.max_memory_fraction <= 0.9:
            raise ValueError("max_memory_fraction must be between 0.25 and 0.9")

    @classmethod
    def from_environment(cls) -> "ModelConfiguration":
        return cls(
            model_path=os.getenv("AEGIS_MODEL_PATH") or None,
            context_window=int(os.getenv("AEGIS_MODEL_CONTEXT", "1024")),
            threads=int(os.getenv("AEGIS_MODEL_THREADS", "2")),
            timeout_seconds=float(os.getenv("AEGIS_INFERENCE_TIMEOUT", "120")),
            max_tokens=int(os.getenv("AEGIS_MODEL_MAX_TOKENS", "256")),
            temperature=float(os.getenv("AEGIS_MODEL_TEMPERATURE", "0.2")),
            top_p=float(os.getenv("AEGIS_MODEL_TOP_P", "0.9")),
            repeat_penalty=float(os.getenv("AEGIS_MODEL_REPEAT_PENALTY", "1.1")),
            max_memory_fraction=float(os.getenv("AEGIS_MODEL_MEMORY_FRACTION", "0.75")),
            system_prompt=os.getenv("AEGIS_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        )
