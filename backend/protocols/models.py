"""Protocol value objects and lifecycle states."""
from __future__ import annotations

import time
import uuid
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProtocolStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


PROTOCOL_CATEGORIES = frozenset({
    "software_development", "research", "cybersecurity", "business_operations",
    "data_analysis", "system_administration", "communication", "personal_productivity",
    "device_management", "general_operational_procedures",
})


@dataclass(frozen=True, slots=True)
class ProtocolSpec:
    name: str
    purpose: str
    scope: str
    description: str = ""
    category: str = "general_operational_procedures"
    priority: int = 100
    definitions: dict[str, str] = field(default_factory=dict)
    preconditions: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    procedures: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    failure_handling: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    protocol_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: int = 1
    status: ProtocolStatus = ProtocolStatus.PROPOSED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    revision_of: str | None = None

    def validate(self) -> list[str]:
        errors: list[str] = []
        for field_name in ("name", "purpose", "scope", "category"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field_name} is required")
            elif len(value) > 1000:
                errors.append(f"{field_name} is too long")
        if not isinstance(self.protocol_id, str) or not self.protocol_id.strip():
            errors.append("protocol_id is required")
        elif len(self.protocol_id) > 128:
            errors.append("protocol_id is too long")
        if not isinstance(self.version, int) or not isinstance(self.priority, int) or self.version < 1 or not 0 <= self.priority <= 1000:
            errors.append("version or priority is outside the allowed range")
        if not isinstance(self.status, ProtocolStatus):
            errors.append("status is invalid")
        if not isinstance(self.created_at, (int, float)) or not isinstance(self.updated_at, (int, float)) or not math.isfinite(self.created_at) or not math.isfinite(self.updated_at):
            errors.append("timestamps must be finite numbers")
        if not isinstance(self.description, str) or len(self.description) > 10000:
            errors.append("description must be text no longer than 10000 characters")
        if not isinstance(self.definitions, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.definitions.items()):
            errors.append("definitions must map strings to strings")
        for field_name in ("preconditions", "triggers", "required_permissions", "rules", "procedures", "expected_outputs", "exceptions", "failure_handling", "verification"):
            value = getattr(self, field_name)
            if not isinstance(value, tuple) or any(not isinstance(item, str) or not item.strip() for item in value):
                errors.append(f"{field_name} must be a tuple of non-empty strings")
            elif len(value) > 100 or any(len(item) > 4000 for item in value):
                errors.append(f"{field_name} exceeds protocol size limits")
        if not isinstance(self.procedures, tuple) or not self.procedures:
            errors.append("at least one procedure is required")
        elif all(isinstance(item, str) for item in self.procedures) and len(set(self.procedures)) != len(self.procedures):
            errors.append("procedures must be unique")
        return errors

    def as_dict(self) -> dict[str, Any]:
        return {"protocol_id": self.protocol_id, "name": self.name, "version": self.version,
                "description": self.description, "purpose": self.purpose, "scope": self.scope,
                "category": self.category, "priority": self.priority, "definitions": self.definitions,
                "preconditions": list(self.preconditions), "triggers": list(self.triggers),
                "required_permissions": list(self.required_permissions), "rules": list(self.rules),
                "procedures": list(self.procedures), "expected_outputs": list(self.expected_outputs),
                "exceptions": list(self.exceptions), "failure_handling": list(self.failure_handling),
                "verification": list(self.verification), "status": self.status.value,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "revision_of": self.revision_of}
