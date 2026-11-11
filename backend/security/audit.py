"""Append-only local audit log with secret redaction."""
from __future__ import annotations

import json
import hashlib
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any

from backend.security.guardrails import redact_secrets


class AuditLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
            if self.path.exists(): os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._lock = RLock()

    def status(self) -> dict[str, Any]:
        integrity = self.verify_integrity()
        return {"available": True, "integrity_ok": integrity["ok"], "path": str(self.path)}

    def record(self, action: str, *, actor: str, outcome: str, details: dict[str, Any] | None = None) -> None:
        payload = {"timestamp": time.time(), "action": action, "actor": actor, "outcome": outcome,
                   "details": details or {}}
        with self._lock:
            previous = "0" * 64
            if self.path.exists():
                try:
                    for line in self.path.read_text(encoding="utf-8").splitlines():
                        item = json.loads(line)
                        previous = item.get("_integrity", {}).get("sha256", previous)
                except (OSError, ValueError):
                    # Preserve the existing file; verification will report malformed history.
                    previous = "0" * 64
            safe_payload = json.loads(redact_secrets(json.dumps(payload, sort_keys=True, default=str)))
            canonical = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256((previous + canonical).encode()).hexdigest()
            safe_payload["_integrity"] = {"previous": previous, "sha256": digest}
            encoded = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            try: os.chmod(self.path, 0o600)
            except OSError: pass

    def inspect(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-max(1, min(int(limit), 1000)):]
        return [json.loads(redact_secrets(line)) for line in lines if line]

    def verify_integrity(self) -> dict[str, Any]:
        """Validate line structure and the hash chain for records written by this version."""
        previous = "0" * 64
        checked = 0
        try:
            if not self.path.exists(): return {"ok": True, "records": 0, "legacy_records": 0}
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line: continue
                item = json.loads(line)
                if "_integrity" not in item:
                    # Pre-chain history stays readable but is explicitly not authenticated.
                    checked += 1
                    continue
                chain = item.pop("_integrity")
                canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
                expected = hashlib.sha256((previous + canonical).encode()).hexdigest()
                if chain.get("previous") != previous or chain.get("sha256") != expected:
                    return {"ok": False, "records": checked, "tamper_line": checked + 1}
                previous = chain["sha256"]
                checked += 1
            return {"ok": True, "records": checked, "legacy_records": sum(1 for line in self.path.read_text(encoding="utf-8").splitlines()
                if line and "_integrity" not in json.loads(line))}
        except (OSError, ValueError, TypeError, KeyError):
            return {"ok": False, "records": checked, "error": "audit_unreadable_or_malformed"}

