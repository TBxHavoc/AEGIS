"""Input/path/command guardrails. These are deny-by-default helpers."""
from __future__ import annotations

import re
import shlex
from pathlib import Path


SECRET_PATTERNS = (
    re.compile(r"(?i)(\"?(?:api[_ -]?key|client[_ -]?secret|secret|password|passwd|token|access[_ -]?key)\"?\s*:\s*)\"[^\"\n]*\""),
    re.compile(r"(?i)(?:api[_ -]?key|client[_ -]?secret|secret|password|passwd|token|access[_ -]?key)\s*=\s*[^\s,;]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL),
)


def redact_secrets(text: str) -> str:
    redacted = text
    redacted = SECRET_PATTERNS[0].sub(r'\1"[REDACTED]"', redacted)
    for pattern in SECRET_PATTERNS[1:]:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def validate_path(path: str | Path, allowed_root: str | Path, *, must_exist: bool = False) -> Path:
    root = Path(allowed_root).resolve()
    candidate = Path(path).expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        raise PermissionError("path is outside the allowed root")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def validate_command(command: str, allowed_programs: frozenset[str] = frozenset()) -> tuple[str, ...]:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be non-empty")
    if any(operator in command for operator in (";", "&&", "||", "|", ">", "<", "`", "$", "\n")):
        raise PermissionError("shell operators are not allowed")
    parts = tuple(shlex.split(command))
    if not parts or (allowed_programs and parts[0] not in allowed_programs):
        raise PermissionError("program is not allowlisted")
    if parts[0] in {"sudo", "su", "rm", "mkfs", "dd", "chmod", "chown", "apt", "apt-get", "pip", "systemctl"}:
        raise PermissionError("privileged or destructive command is denied")
    return parts

