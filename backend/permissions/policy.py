"""Permission levels, allowlists, and explicit confirmation gates."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class PermissionLevel(IntEnum):
    READ_ONLY = 10
    WRITE_LOCAL = 20
    NETWORK = 30
    DESTRUCTIVE = 40
    PRIVILEGED = 50


class PermissionError(RuntimeError):
    pass


class ConfirmationRequired(PermissionError):
    pass


class ToolDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    max_level: PermissionLevel = PermissionLevel.READ_ONLY
    allowed_tools: frozenset[str] = frozenset({"system.status", "knowledge.search"})
    allow_network: bool = False

    def status(self) -> dict[str, object]:
        return {"available": True, "max_level": self.max_level.name,
                "allow_network": self.allow_network, "allowlist_size": len(self.allowed_tools)}

    @classmethod
    def from_environment(cls, *, default_tools: frozenset[str]) -> "PermissionPolicy":
        import os
        allowed = frozenset(item.strip() for item in os.getenv("AEGIS_ALLOWED_TOOLS", ",".join(sorted(default_tools))).split(",") if item.strip())
        level_name = os.getenv("AEGIS_MAX_PERMISSION", "READ_ONLY").upper()
        try:
            maximum = PermissionLevel[level_name]
        except KeyError as exc:
            raise ValueError("AEGIS_MAX_PERMISSION must be READ_ONLY, WRITE_LOCAL, NETWORK, DESTRUCTIVE, or PRIVILEGED") from exc
        return cls(max_level=maximum, allowed_tools=allowed,
                   allow_network=os.getenv("AEGIS_ALLOW_NETWORK", "0") == "1")

    def check(self, tool_name: str, level: PermissionLevel, *, confirmed: bool = False) -> ToolDecision:
        if tool_name not in self.allowed_tools:
            return ToolDecision.DENY
        if level > self.max_level:
            if level >= PermissionLevel.DESTRUCTIVE and not confirmed:
                return ToolDecision.CONFIRM
            if level < PermissionLevel.DESTRUCTIVE:
                return ToolDecision.DENY
        if level == PermissionLevel.NETWORK and not self.allow_network:
            return ToolDecision.DENY
        return ToolDecision.ALLOW

    def require(self, tool_name: str, level: PermissionLevel, *, confirmed: bool = False) -> None:
        decision = self.check(tool_name, level, confirmed=confirmed)
        if decision == ToolDecision.CONFIRM:
            raise ConfirmationRequired(f"explicit confirmation required for {tool_name}")
        if decision != ToolDecision.ALLOW:
            raise PermissionError(f"tool not permitted: {tool_name}")

    @staticmethod
    def authorize_protocol_mutation(action: str, *, local_client: bool, confirmed: bool = False) -> None:
        """Allow only named local owner actions; lifecycle mutations require confirmation."""
        allowed = {"create_draft", "revise", "approve", "activate", "deactivate", "archive", "restore"}
        if action not in allowed or not local_client:
            raise PermissionError("protocol mutation is not authorized")
        if action != "create_draft" and not confirmed:
            raise ConfirmationRequired(f"explicit confirmation required for protocol {action}")

