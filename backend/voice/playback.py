"""Single-owner playback manager with cancellation and stale-request protection."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from threading import RLock

from backend.voice.tts import AudioArtifact, TTSError


class AudioPlayback:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("aplay") or shutil.which("paplay")
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id: str | None = None
        self._lock = RLock()

    def play(self, artifact: AudioArtifact, volume: float, request_id: str) -> None:
        with self._lock:
            self.stop()
            if not self.executable:
                raise TTSError("no local audio playback command is available")
            command = [self.executable, str(artifact.path)]
            if Path(self.executable).name == "paplay":
                command = [self.executable, f"--volume={int(volume * 65536)}", str(artifact.path)]
            self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._request_id = request_id

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
            self._request_id = None

    @property
    def active_request_id(self) -> str | None:
        with self._lock:
            return self._request_id if self._process is not None and self._process.poll() is None else None

