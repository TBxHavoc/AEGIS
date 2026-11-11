"""Read-only Linux and host diagnostics without shelling out."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from backend.permissions.policy import PermissionLevel
from backend.tools.registry import ToolRegistry, ToolSpec


def system_diagnostics() -> dict[str, Any]:
    memory: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                memory[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        memory = {}
    uptime = None
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    cpu_model = platform.processor() or None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    disk = shutil.disk_usage("/")
    try: interfaces = sorted(path.name for path in Path("/sys/class/net").iterdir())
    except OSError: interfaces = []
    processes = []
    proc_root = Path("/proc")
    if proc_root.exists():
        for entry in proc_root.iterdir():
            if entry.name.isdigit():
                try:
                    processes.append({"pid": int(entry.name), "name": (entry / "comm").read_text().strip()[:120]})
                except OSError:
                    continue
                if len(processes) >= 100:
                    break
    return {"os": platform.platform()[:300], "hostname": socket.gethostname()[:128],
            "cpu_model": cpu_model, "cpu_count": os.cpu_count(), "memory_bytes": memory,
            "disk_root": {"total": disk.total, "used": disk.used, "free": disk.free},
            "uptime_seconds": uptime, "network_interfaces": interfaces,
            "processes_sample": processes, "service_manager_checks": "unavailable_without_approved_provider",
            "collected_at": time.time()}


def register_system_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec("system.diagnostics", "Read OS, CPU, memory, disk, process sample, network interface and uptime information.",
                               {"type": "object", "additionalProperties": False}, {"type": "object"},
                               PermissionLevel.READ_ONLY, 3, lambda args: system_diagnostics(), version="1.0"))
