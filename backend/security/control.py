"""Independent deterministic security controls for runtime and host operations."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROTECTED_NAMES = frozenset({".git", ".agents", ".codex", ".aws", ".ssh", ".gnupg",
    ".mozilla", "google-chrome", "chromium", "brave-browser", "bravesoftware", "firefox",
    "keyrings", ".password-store", "keepassxc"})
PROTECTED_SYSTEM = tuple(Path(p) for p in ("/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/root"))


class ProtectedPathError(PermissionError):
    pass


class ProtectedPaths:
    """Canonical path policy shared by all file modification handlers."""
    def __init__(self, protected: tuple[str | Path, ...] = ()) -> None:
        self.protected = tuple(Path(p).expanduser().absolute() for p in (*PROTECTED_SYSTEM, *protected))

    def validate(self, path: str | Path, roots: tuple[str | Path, ...] = (), *, allow_missing: bool = True) -> Path:
        raw = Path(path).expanduser()
        # Check existing ancestors before resolving, so an in-root symlink cannot redirect access.
        absolute = raw if raw.is_absolute() else Path.cwd() / raw
        cursor = absolute
        while not cursor.exists() and cursor != cursor.parent:
            cursor = cursor.parent
        if cursor.is_symlink():
            raise ProtectedPathError("symlink traversal is denied")
        resolved = absolute.resolve(strict=not allow_missing)
        if any(part.casefold() in PROTECTED_NAMES for part in resolved.parts):
            raise ProtectedPathError("protected project or credential path")
        lowered = tuple(part.casefold() for part in resolved.parts)
        if any(part in {".env", "audit.log", "api.token", "authorized_keys", "known_hosts", "cookies", "login data", "web data", "key4.db",
                        "trusted_baseline.json", "release_manifest.json"}
               or part.startswith(".env.") for part in lowered):
            raise ProtectedPathError("sensitive configuration, audit, baseline manifest, or credential path")
        if resolved.suffix.casefold() in {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}:
            raise ProtectedPathError("database and recovery files are protected")
        project_root = Path(__file__).resolve().parents[2]
        try:
            project_parts = tuple(part.casefold() for part in resolved.relative_to(project_root).parts)
        except ValueError:
            project_parts = ()
        if len(project_parts) >= 2 and project_parts[0] == "data" and "backups" in project_parts[1:]:
            raise ProtectedPathError("AEGIS database backups are protected")
        if any(lowered[index:index + 2] == ("backend", "security") for index in range(max(0, len(lowered) - 1))):
            raise ProtectedPathError("AEGIS security components are protected")
        if any(lowered[index:index + 3] == ("backend", "permissions", "policy.py") for index in range(max(0, len(lowered) - 2))):
            raise ProtectedPathError("AEGIS permission policy is protected")
        for protected in self.protected:
            try:
                resolved.relative_to(protected.resolve())
                raise ProtectedPathError("path is protected")
            except ValueError:
                pass
        if roots:
            allowed = False
            for root in roots:
                try:
                    resolved.relative_to(Path(root).expanduser().resolve())
                    allowed = True
                    break
                except ValueError:
                    if resolved == Path(root).expanduser().resolve():
                        allowed = True
                        break
            if not allowed:
                raise ProtectedPathError("path is outside approved roots")
        return resolved


class SafeMode:
    """Thread-safe read-only restriction switch, independent of model state."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = False
        self._reason = ""
        self._since: float | None = None

    def enter(self, reason: str) -> None:
        with self._lock:
            self._active, self._reason, self._since = True, str(reason)[:500], time.time()

    def recover(self, *, confirmed: bool) -> bool:
        if not confirmed:
            return False
        with self._lock:
            self._active, self._reason, self._since = False, "", None
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"active": self._active, "reason": self._reason, "since": self._since,
                    "mode": "read_only" if self._active else "normal"}

    def require_writable(self) -> None:
        if self._active:
            raise PermissionError("AEGIS safe mode blocks mutating and external operations")


class SecurityComponentRegistry:
    """Small extension point for independently replaceable security services."""
    def __init__(self) -> None:
        self._components: dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(self, name: str, component: Any) -> None:
        if not name or len(name) > 80:
            raise ValueError("invalid security component name")
        with self._lock:
            if name in self._components:
                raise ValueError("security component already registered")
            self._components[name] = component

    def status(self) -> dict[str, Any]:
        with self._lock:
            items = tuple(self._components.items())
        result = {}
        for name, component in items:
            try:
                result[name] = component.status() if callable(getattr(component, "status", None)) else {"available": True}
            except Exception:
                result[name] = {"available": False, "error": "status_unavailable"}
        return result


class IntegrityMonitor:
    def __init__(self, paths: tuple[str | Path, ...] = (), release_manager: Any = None, require_baseline: bool = False) -> None:
        self.paths = tuple(Path(path).resolve() for path in paths)
        self.baseline = {str(p): self._hash(p) for p in self.paths if p.is_file()}
        self.release_manager = release_manager
        self.require_baseline = require_baseline

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def check(self) -> dict[str, Any]:
        if self.release_manager is not None:
            if self.release_manager.has_manifest() or self.require_baseline:
                return self.release_manager.verify()
        changes = []
        for name, expected in self.baseline.items():
            path = Path(name)
            try:
                actual = self._hash(path) if path.is_file() else "missing"
            except OSError:
                actual = "unreadable"
            if actual != expected:
                changes.append({"path": name, "status": actual if actual in {"missing", "unreadable"} else "changed"})
        return {"ok": not changes, "checked_files": len(self.baseline), "changes": changes}

    def status(self) -> dict[str, Any]:
        return self.check()


class FirewallManager:
    """Read-only UFW inspection and safe additive rule proposals. Applying is separate."""
    def __init__(self, audit: Any, executable: str | None = None) -> None:
        self.audit = audit
        self.executable = executable or shutil.which("ufw")
        self._proposals: dict[str, dict[str, Any]] = {}

    def inspect(self) -> dict[str, Any]:
        if not self.executable:
            return {"available": False, "provider": "ufw", "status": "unavailable",
                    "rules": [], "listeners": self._listeners(), "exposed_services": [],
                    "message": "UFW is not installed; no changes were made."}
        try:
            status = subprocess.run([self.executable, "status", "verbose"], capture_output=True,
                                    text=True, timeout=4, check=False)
            numbered = subprocess.run([self.executable, "status", "numbered"], capture_output=True,
                                      text=True, timeout=4, check=False)
            if status.returncode not in (0, 1):
                return {"available": True, "provider": "ufw", "status": "inspection_error",
                        "error": status.stderr[:500], "listeners": self._listeners(), "rules": []}
            if "need to be root" in (status.stdout + status.stderr).lower():
                return {"available": True, "provider": "ufw", "status": "permission_denied",
                        "error": "UFW state requires elevated read permission; actual firewall state is unverified.",
                        "listeners": self._listeners(), "rules": []}
            lines = [line.strip() for line in numbered.stdout.splitlines() if line.strip()]
            state = "enabled" if "Status: active" in status.stdout else "inactive" if "Status: inactive" in status.stdout else "unknown"
            listeners = self._listeners()
            exposed = [item for item in listeners if item.get("address") in {"0.0.0.0", "::", "*"}]
            return {"available": True, "provider": "ufw", "status": state,
                    "rules": lines[:200], "listeners": listeners, "exposed_services": exposed}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": True, "provider": "ufw", "status": "inspection_error",
                    "error": str(exc)[:300], "rules": [], "listeners": self._listeners(), "exposed_services": []}

    def status(self) -> dict[str, Any]:
        value = self.inspect()
        return {key: value.get(key) for key in ("available", "provider", "status")}

    @staticmethod
    def _listeners() -> list[dict[str, Any]]:
        found = []
        try:
            for protocol, suffix, state in (("tcp", "tcp", "0A"), ("tcp", "tcp6", "0A"),
                                             ("udp", "udp", "07"), ("udp", "udp6", "07")):
                for line in Path(f"/proc/net/{suffix}").read_text().splitlines()[1:]:
                    cols = line.split()
                    if len(cols) < 10 or cols[3] != state:
                        continue
                    address, port = cols[1].split(":")
                    try:
                        raw = bytes.fromhex(address)
                        if len(raw) == 4: raw = raw[::-1]
                        elif len(raw) == 16: raw = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
                        addr = str(ipaddress.ip_address(raw))
                    except (ValueError, OSError):
                        addr = address
                    found.append({"protocol": protocol, "address": addr, "port": int(port, 16)})
        except OSError:
            pass
        return found[:200]

    def propose_allow(self, port: int, protocol: str = "tcp", source: str | None = None) -> dict[str, Any]:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if protocol not in {"tcp", "udp"}:
            raise ValueError("protocol must be tcp or udp")
        if port == 22:
            raise ValueError("SSH firewall changes are not supported by this workflow")
        if source:
            source = str(ipaddress.ip_network(source, strict=False))
        command = ["ufw", "allow", f"{port}/{protocol}"]
        if source:
            command = ["ufw", "allow", "from", source, "to", "any", "port", str(port), "proto", protocol]
        proposal = {"id": hashlib.sha256(json.dumps([command, time.time_ns()]).encode()).hexdigest()[:20],
                    "command": command, "impact": f"Adds an inbound {protocol} allow rule on port {port}"
                    + (f" limited to {source}" if source else " for any source")
                    + ". Existing UFW rules are retained. SSH, DNS, routes, and interfaces are not changed.",
                    "requires_approval": True}
        self._proposals[proposal["id"]] = proposal
        return proposal

    def apply(self, proposal_id: str, *, approved: bool) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if not approved:
            raise PermissionError("explicit approval is required")
        if not proposal:
            raise ValueError("firewall proposal not found")
        if not self.executable:
            raise RuntimeError("UFW is unavailable")
        argv = [self.executable, *proposal["command"][1:]]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        self.audit.record("firewall.apply", actor="local_user", outcome="command_succeeded" if result.returncode == 0 else "command_failed",
                          details={"proposal_id": proposal_id, "returncode": result.returncode})
        verified = self.inspect()
        return {"applied": result.returncode == 0, "verified_status": verified["status"],
                "verification": verified, "message": (result.stdout or result.stderr)[:1000]}


class ResourceMonitor:
    """Cheap on-demand resource snapshot; deliberately no daemon or process killer."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previous: tuple[float, int] | None = None

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {"process_id": os.getpid(), "cpu_percent": None, "memory_bytes": None,
                                  "disk_free_bytes": None, "alerts": []}
        try:
            fields = Path("/proc/self/stat").read_text().split()
            ticks = int(fields[13]) + int(fields[14]); now = time.monotonic()
            result["cpu_ticks"] = ticks
            with self._lock:
                if self._previous:
                    elapsed, previous_ticks = now - self._previous[0], self._previous[1]
                    if elapsed > 0:
                        result["cpu_percent"] = round(max(0.0, min(400.0,
                            ((ticks - previous_ticks) / os.sysconf("SC_CLK_TCK")) / elapsed * 100)), 1)
                self._previous = (now, ticks)
            result["memory_bytes"] = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            result["disk_free_bytes"] = shutil.disk_usage(Path.cwd()).free
        except (OSError, ValueError, IndexError):
            pass
        return result

    def status(self) -> dict[str, Any]:
        return self.snapshot()


def sqlite_health(path: str | Path) -> dict[str, Any]:
    """Read-only SQLite integrity check; never attempts destructive repair."""
    import sqlite3
    candidate = Path(path)
    if not candidate.exists():
        return {"ok": True, "status": "not_created"}
    try:
        uri = f"file:{candidate.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            result = db.execute("PRAGMA quick_check").fetchone()[0]
        return {"ok": result == "ok", "status": result}
    except (sqlite3.DatabaseError, OSError):
        return {"ok": False, "status": "corrupt_or_unreadable", "recovery": "preserve the original file and restore a verified backup manually"}


def sqlite_backup(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Consistent SQLite backup to a new path; refuses to overwrite any existing file."""
    import sqlite3
    src, dst = Path(source), Path(destination)
    if not src.is_file() or dst.exists():
        raise ValueError("source must exist and backup destination must be new")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True, timeout=5) as source_db:
        with sqlite3.connect(dst, timeout=5) as target_db:
            source_db.backup(target_db)
            check = target_db.execute("PRAGMA quick_check").fetchone()[0]
    if check != "ok":
        raise RuntimeError("backup verification failed; original data was preserved")
    os.chmod(dst, 0o600)
    digest = hashlib.sha256(dst.read_bytes()).hexdigest()
    return {"path": str(dst), "sha256": digest, "verified": True}
