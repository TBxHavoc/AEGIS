"""Validated, environment-backed application settings."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    max_message_chars: int = 4000
    max_sessions: int = 32
    static_dir: str = "frontend"
    data_dir: str = "data"
    persist_conversations: bool = False
    conversation_history_limit: int = 20
    knowledge_root: str = "data/knowledge_documents"
    tool_timeout_seconds: float = 5.0
    require_baseline: bool = False
    baseline_manifest: str = ""
    project_root: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        level = os.getenv("AEGIS_LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("AEGIS_LOG_LEVEL is invalid")
        return cls(
            host=os.getenv("AEGIS_HOST", "127.0.0.1"),
            port=_int_env("AEGIS_PORT", 8765, 1, 65535),
            log_level=level,
            max_message_chars=_int_env("AEGIS_MAX_MESSAGE_CHARS", 4000, 1, 100000),
            max_sessions=_int_env("AEGIS_MAX_SESSIONS", 32, 1, 10000),
            static_dir=os.getenv("AEGIS_STATIC_DIR", "frontend"),
            data_dir=os.getenv("AEGIS_DATA_DIR", "data"),
            persist_conversations=os.getenv("AEGIS_PERSIST_CONVERSATIONS", "0") == "1",
            conversation_history_limit=_int_env("AEGIS_HISTORY_LIMIT", 20, 1, 1000),
            knowledge_root=os.getenv("AEGIS_KNOWLEDGE_ROOT", "data/knowledge_documents"),
            tool_timeout_seconds=float(os.getenv("AEGIS_TOOL_TIMEOUT", "5")),
            require_baseline=os.getenv("AEGIS_REQUIRE_BASELINE", "0") == "1",
            baseline_manifest=os.getenv("AEGIS_BASELINE_MANIFEST", ""),
            project_root=os.getenv("AEGIS_PROJECT_ROOT", ""),
        )

