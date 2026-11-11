"""Local GGUF discovery and registration; this module never downloads models."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    model_id: str
    path: str
    size_bytes: int
    format: str = "GGUF"
    provider: str = "llama.cpp"
    license: str = "unknown"

    def as_dict(self) -> dict[str, object]:
        # Public API payloads should not disclose usernames or arbitrary local paths.
        public = asdict(self)
        public["filename"] = Path(self.path).name
        public.pop("path", None)
        return public


class ModelRegistry:
    def __init__(self, directories: tuple[str | Path, ...] = ()) -> None:
        self.directories = tuple(Path(directory).expanduser().resolve() for directory in directories)
        self._models: dict[str, RegisteredModel] = {}
        self._lock = RLock()

    @staticmethod
    def _identifier(path: Path) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-.")
        if not stem:
            raise ValueError("model file has no usable name")
        return stem[:120]

    def discover(self) -> list[RegisteredModel]:
        found: list[RegisteredModel] = []
        for directory in self.directories:
            try:
                paths = sorted(directory.glob("*.gguf"))
            except OSError:
                continue
            for path in paths:
                if path.is_file():
                    try:
                        found.append(self.register(path))
                    except (OSError, ValueError):
                        continue
        return found

    def register(self, path: str | Path, *, license: str = "unknown", model_id: str | None = None) -> RegisteredModel:
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file() or resolved.suffix.lower() != ".gguf":
            raise ValueError("only existing .gguf model files can be registered")
        identifier = model_id or self._identifier(resolved)
        if not identifier or len(identifier) > 120:
            raise ValueError("model_id must contain 1 to 120 characters")
        model = RegisteredModel(identifier, str(resolved), resolved.stat().st_size, license=license)
        with self._lock:
            self._models[identifier] = model
        return model

    def get(self, model_id: str) -> RegisteredModel | None:
        with self._lock:
            model = self._models.get(model_id)
        if model is None:
            return None
        path = Path(model.path)
        if not path.is_file():
            with self._lock:
                self._models.pop(model_id, None)
            return None
        return model

    def list(self) -> list[RegisteredModel]:
        with self._lock:
            identifiers = tuple(self._models)
        return [model for identifier in identifiers if (model := self.get(identifier)) is not None]
