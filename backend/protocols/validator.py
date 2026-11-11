"""Schema and security validation for user-defined protocol data."""
from __future__ import annotations

from backend.permissions.policy import PermissionLevel
from backend.protocols.models import ProtocolSpec


def validate_protocol(spec: ProtocolSpec) -> list[str]:
    errors = spec.validate()
    valid_levels = {level.name for level in PermissionLevel}
    for permission in spec.required_permissions:
        if not isinstance(permission, str):
            continue
        if permission.upper() not in valid_levels:
            errors.append(f"unknown permission requirement: {permission}")
    # Protocol content is declarative. Refuse common attempts to smuggle executable
    # commands into structured execution/verification fields.
    forbidden = ("shell:", "python:", "exec(", "eval(", "subprocess", "os.system")
    for field_name in ("rules", "procedures", "failure_handling", "verification"):
        for entry in getattr(spec, field_name):
            if isinstance(entry, str) and any(token in entry.lower() for token in forbidden):
                errors.append(f"executable content is not permitted in {field_name}")
    return errors
