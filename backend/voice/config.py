"""Voice preferences with one global speaking-rate control."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True, slots=True)
class VoicePreferences:
    voice: str = "default"
    speaking_rate: float = 1.0
    volume: float = 1.0
    muted: bool = False
    microphone_enabled: bool = False

    def validated(self) -> "VoicePreferences":
        if not 0.5 <= self.speaking_rate <= 2.0:
            raise ValueError("speaking_rate must be between 0.5 and 2.0")
        if not 0.0 <= self.volume <= 1.0:
            raise ValueError("volume must be between 0.0 and 1.0")
        if not self.voice.strip():
            raise ValueError("voice must be non-empty")
        return self


class VoicePreferencesStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def load(self) -> VoicePreferences:
        with self._lock:
            try:
                values = json.loads(self.path.read_text(encoding="utf-8"))
                return VoicePreferences(**values).validated()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return VoicePreferences()

    def save(self, preferences: VoicePreferences) -> VoicePreferences:
        preferences = preferences.validated()
        with self._lock:
            fd, temporary = tempfile.mkstemp(prefix="voice-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(asdict(preferences), stream, indent=2, sort_keys=True)
                    stream.write("\n")
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return preferences

