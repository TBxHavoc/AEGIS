"""Offline TTS provider contracts and optional Piper subprocess adapter."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.conversation.models import CancellationToken
from backend.voice.config import VoicePreferences
from backend.voice.preprocessing import prepare_for_tts, speaking_rate_for_text


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    path: Path
    request_id: str


class TTSProvider(Protocol):
    name: str

    def available(self) -> bool: ...
    def voices(self) -> tuple[str, ...]: ...
    def synthesize(self, text: str, preferences: VoicePreferences, request_id: str,
                   cancellation: CancellationToken) -> AudioArtifact: ...


class UnavailableTTSProvider:
    name = "unconfigured"

    def available(self) -> bool:
        return False

    def voices(self) -> tuple[str, ...]:
        return ()

    def synthesize(self, text: str, preferences: VoicePreferences, request_id: str,
                   cancellation: CancellationToken) -> AudioArtifact:
        raise TTSError("offline TTS is not configured; install/configure a voice model first")


class PiperProvider:
    """Piper adapter; a model path is mandatory and no model is downloaded."""
    name = "piper"

    def __init__(self, executable: str, model_path: str | Path) -> None:
        self.executable = executable
        self.model_path = Path(model_path)

    @classmethod
    def from_environment(cls) -> "PiperProvider | UnavailableTTSProvider":
        executable = os.getenv("AEGIS_PIPER_EXECUTABLE") or shutil.which("piper")
        model = os.getenv("AEGIS_PIPER_MODEL")
        if not executable or not model:
            return UnavailableTTSProvider()
        return cls(executable, model)

    def available(self) -> bool:
        return bool(shutil.which(self.executable) or Path(self.executable).exists()) and self.model_path.is_file()

    def voices(self) -> tuple[str, ...]:
        return (self.model_path.stem,) if self.available() else ()

    def command(self, preferences: VoicePreferences) -> list[str]:
        # Piper's length_scale is one global synthesis setting. It is never changed by punctuation.
        length_scale = 1.0 / preferences.speaking_rate
        return [self.executable, "--model", str(self.model_path), "--length_scale", f"{length_scale:.6f}"]

    def synthesize(self, text: str, preferences: VoicePreferences, request_id: str,
                   cancellation: CancellationToken) -> AudioArtifact:
        text = prepare_for_tts(text)
        speaking_rate_for_text(text, preferences.speaking_rate)
        if not self.available():
            raise TTSError("configured Piper executable or voice model is unavailable")
        if cancellation.cancelled:
            raise TTSError("speech synthesis cancelled")
        descriptor, output_name = tempfile.mkstemp(prefix="aegis-voice-", suffix=".wav")
        os.close(descriptor)
        output = Path(output_name)
        try:
            completed = subprocess.run(self.command(preferences) + ["--output_file", str(output)],
                                       input=text, text=True, capture_output=True, timeout=120, check=False)
            if completed.returncode != 0:
                raise TTSError(completed.stderr.strip() or "Piper synthesis failed")
            return AudioArtifact(output, request_id)
        except Exception:
            output.unlink(missing_ok=True)
            raise

