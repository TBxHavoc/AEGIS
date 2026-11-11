"""Safe desktop automation boundary; real actions are disabled in the foundation release."""
from __future__ import annotations

from threading import Event

from backend.permissions.policy import PermissionError, PermissionLevel, PermissionPolicy
from backend.security.audit import AuditLogger


class DesktopController:
    def __init__(self, policy: PermissionPolicy, audit: AuditLogger) -> None:
        self.policy = policy
        self.audit = audit

    def inspect_screen(self) -> dict[str, object]:
        self.audit.record("desktop.inspect_screen", actor="assistant", outcome="unavailable")
        return {"available": False, "message": "desktop inspection is not configured"}

    def interact(self, action: str, arguments: dict[str, object], cancellation: Event | None = None) -> dict[str, object]:
        self.audit.record("desktop.interact", actor="assistant", outcome="denied", details={"action": action})
        self.policy.require("desktop.interact", PermissionLevel.DESTRUCTIVE)
        raise PermissionError("desktop interaction requires explicit user authorization and is disabled")

