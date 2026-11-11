"""Explicit unavailable provider; adapters are added in later steps."""
from __future__ import annotations

from collections.abc import Iterator

from backend.conversation.models import CancellationToken
from backend.inference.base import InferenceRequest, ModelMetadata, ResourceSnapshot


class UnavailableProvider:
    metadata = ModelMetadata("none", "unconfigured", local=True)

    def available(self) -> bool:
        return False

    def stream(self, request: InferenceRequest, cancellation: CancellationToken) -> Iterator[str]:
        if False:
            yield ""

    def load(self) -> None:
        return None

    def unload(self) -> None:
        return None

    def resources(self) -> ResourceSnapshot:
        return ResourceSnapshot()

