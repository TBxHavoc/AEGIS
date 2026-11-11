"""Unimplemented device integration contracts; no automatic pairing or access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class DeviceProvider(Protocol):
    provider_id: str
    def status(self, device_id: str) -> dict[str, Any]: ...
    def notify(self, device_id: str, message: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DeviceConnection:
    device_id: str
    provider_id: str
    paired: bool = False
    authorized_capabilities: tuple[str, ...] = ()


class DeviceIntegration:
    """Foundation only. A concrete provider and local pairing flow are required."""
    def __init__(self) -> None:
        self._connections: dict[str, DeviceConnection] = {}

    def status(self) -> dict[str, Any]:
        return {"available": False, "paired_devices": [],
                "reason": "no_device_provider_or_pairing_flow_configured"}
