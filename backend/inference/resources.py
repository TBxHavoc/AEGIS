"""Low-overhead resource observations for bounded local inference."""
from __future__ import annotations

import os
from pathlib import Path

from backend.inference.base import ResourceSnapshot


class ResourceMonitor:
    def snapshot(self) -> ResourceSnapshot:
        memory_bytes = None
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    memory_bytes = int(line.split()[1]) * 1024
                    break
        except (OSError, ValueError, IndexError):
            pass
        limit = None
        try:
            limit = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (OSError, ValueError):
            pass
        return ResourceSnapshot(memory_bytes=memory_bytes, memory_limit_bytes=limit)
