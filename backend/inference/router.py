"""Local-first provider selection without requiring cloud credentials."""
from __future__ import annotations

from backend.inference.base import InferenceProvider
from backend.inference.providers import UnavailableProvider


class ProviderRouter:
    def __init__(self, local: InferenceProvider | None = None, remote: InferenceProvider | None = None) -> None:
        self.local = local
        self.remote = remote

    def select(self) -> InferenceProvider:
        if self.local is not None and self.local.available():
            return self.local
        if self.remote is not None and self.remote.available():
            return self.remote
        return UnavailableProvider()

    def status(self) -> dict[str, object]:
        provider = self.select()
        resources = provider.resources()
        return {"available": provider.available(), "provider": provider.metadata.provider, "model": provider.metadata.model_id,
                "context_window": provider.metadata.context_window, "local_preferred": True,
                "remote_configured": self.remote is not None, "resources": {
                    "memory_bytes": resources.memory_bytes, "memory_limit_bytes": resources.memory_limit_bytes,
                }}

