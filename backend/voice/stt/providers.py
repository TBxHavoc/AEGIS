"""Optional local STT engines. Both remain unloaded until explicitly configured."""
from __future__ import annotations

import json
import time
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

from backend.voice.recognition import RecognitionResult, SpeechRecognizer
from backend.voice.stt.audio import validate_pcm16
from backend.voice.stt.normalize import normalize_transcription

Transcription = RecognitionResult


def _cancelled(token: object) -> bool:
    return bool(getattr(token, "cancelled", False) or getattr(token, "is_set", lambda: False)())


def _memory_available_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class WhisperCppRecognizer:
    name = "whisper.cpp"

    def __init__(self, model: str | Path | None = None, executable: str = "whisper-cli", timeout: float = 60.0) -> None:
        self.model = Path(model).expanduser() if model else None
        self.executable = shutil.which(executable) or (executable if Path(executable).is_file() else None)
        self.timeout = timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.executable and self.model and self.model.is_file())

    def health(self) -> dict[str, object]:
        return {"provider": self.name, "available": self.available(), "model_configured": bool(self.model),
                "model_exists": bool(self.model and self.model.is_file()), "loaded": False}

    def unload(self) -> None:
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
            self._process = None

    def transcribe(self, audio: bytes, cancellation: object) -> Transcription:
        validate_pcm16(audio)
        if not self.available():
            raise RuntimeError("configure whisper.cpp and a local model to enable transcription")
        available = _memory_available_bytes()
        if available is not None:
            estimated = max(1024**3, self.model.stat().st_size * 3)
            if available < estimated:
                raise RuntimeError("insufficient available memory for the configured whisper.cpp model")
        if _cancelled(cancellation):
            raise RuntimeError("transcription cancelled")
        with tempfile.TemporaryDirectory(prefix="aegis-stt-") as directory:
            wav_path = Path(directory) / "input.wav"
            with wave.open(str(wav_path), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(audio)
            command = [str(self.executable), "-m", str(self.model), "-f", str(wav_path), "-nt", "--no-gpu"]
            with self._lock:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self._process = process
            try:
                deadline = time.monotonic() + self.timeout
                while True:
                    if _cancelled(cancellation):
                        process.kill(); process.communicate(); raise RuntimeError("transcription cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill(); process.communicate(); raise RuntimeError("transcription timed out")
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                self._process = None
            if _cancelled(cancellation):
                raise RuntimeError("transcription cancelled")
            if process.returncode:
                raise RuntimeError("whisper.cpp transcription failed")
            text = normalize_transcription(stdout.decode(errors="replace"))
            return RecognitionResult(text, None, None, self.name)


class VoskRecognizer:
    name = "Vosk"

    def __init__(self, model_path: str | Path | None = None, timeout: float = 60.0) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.timeout = timeout
        self._model = None

    def available(self) -> bool:
        try:
            import vosk  # noqa: F401
            return bool(self.model_path and self.model_path.is_dir())
        except ImportError:
            return False

    def health(self) -> dict[str, object]:
        return {"provider": self.name, "available": self.available(), "model_configured": bool(self.model_path),
                "loaded": self._model is not None}

    def unload(self) -> None:
        self._model = None

    def transcribe(self, audio: bytes, cancellation: object) -> Transcription:
        validate_pcm16(audio)
        if not self.available():
            raise RuntimeError("install Vosk and configure a local model to enable transcription")
        if _cancelled(cancellation):
            raise RuntimeError("transcription cancelled")
        import vosk
        if self._model is None:
            try:
                model_bytes = sum(item.stat().st_size for item in self.model_path.rglob("*") if item.is_file())
            except OSError as exc:
                raise RuntimeError("could not inspect Vosk model resources") from exc
            available = _memory_available_bytes()
            if available is not None and available < model_bytes * 2:
                raise RuntimeError("insufficient available memory for the configured Vosk model")
            self._model = vosk.Model(str(self.model_path))
        recognizer = vosk.KaldiRecognizer(self._model, 16000)
        import time
        deadline = time.monotonic() + self.timeout
        for offset in range(0, len(audio), 8000):
            if _cancelled(cancellation):
                raise RuntimeError("transcription cancelled")
            if time.monotonic() >= deadline:
                raise RuntimeError("transcription timed out")
            recognizer.AcceptWaveform(audio[offset:offset + 8000])
        if _cancelled(cancellation):
            raise RuntimeError("transcription cancelled")
        result = json.loads(recognizer.FinalResult())
        text = normalize_transcription(result.get("text", ""))
        return RecognitionResult(text, result.get("confidence"), None, self.name)
