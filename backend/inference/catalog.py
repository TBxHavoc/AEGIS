"""Small hardware-aware model catalog; entries are recommendations, not downloads."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelEntry:
    model_id: str
    family: str
    quantization: str
    estimated_ram_gb: float
    context_window: int
    license: str
    engine: str = "llama.cpp"
    quality_note: str = ""


CATALOG = (
    ModelEntry("Qwen3.5-0.8B-Q4_K_M.gguf", "Qwen3.5 0.8B", "Q4_K_M", 1.7, 2048,
               "Apache-2.0 upstream; GGUF is community-quantized",
               quality_note="Best starting balance for 4 GB; small-model reasoning remains limited."),
    ModelEntry("Qwen3-0.6B-Q4_K_M.gguf", "Qwen3 0.6B", "Q4_K_M", 1.4, 1024,
               "Apache-2.0 upstream; GGUF is community-quantized",
               quality_note="Lower RAM option, but expect weaker instruction following and reasoning."),
    ModelEntry("Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "Qwen2.5 1.5B Instruct", "Q4_K_M", 2.8, 1024,
               "Apache-2.0 upstream; GGUF is community-quantized",
               quality_note="Potentially stronger instruction model; tight fit on a 4 GB system."),
)


def hardware_profile() -> dict[str, object]:
    cpu = os.cpu_count() or 1; memory_gb = None; available_gb = None
    cpu_model = "unknown"
    try:
        memory_lines = Path("/proc/meminfo").read_text().splitlines()
        total = next(line for line in memory_lines if line.startswith("MemTotal:"))
        available = next(line for line in memory_lines if line.startswith("MemAvailable:"))
        memory_gb = round(int(total.split()[1]) / 1024 / 1024, 2)
        available_gb = round(int(available.split()[1]) / 1024 / 1024, 2)
    except (OSError, StopIteration, ValueError, IndexError):
        pass
    try:
        cpu_line = next(line for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name"))
        cpu_model = cpu_line.split(":", 1)[1].strip()
    except (OSError, StopIteration, IndexError):
        pass
    return {"cpu_count": cpu, "cpu_model": cpu_model, "memory_gb": memory_gb,
            "available_memory_gb": available_gb, "gpu": "CPU inference; integrated graphics not used"}


def recommendations() -> list[dict[str, object]]:
    profile = hardware_profile(); available = profile.get("available_memory_gb") or profile.get("memory_gb") or 4
    return [{"model_id": entry.model_id, "family": entry.family, "quantization": entry.quantization,
             "estimated_ram_gb": entry.estimated_ram_gb, "context_window": entry.context_window,
             "license": entry.license, "engine": entry.engine, "quality_note": entry.quality_note,
             "fits_detected_memory": entry.estimated_ram_gb <= float(available) * .75}
            for entry in CATALOG]
