"""Provider-independent local speech-recognition contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.voice.capture import MicrophoneCapture


class RecognitionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    text: str
    confidence: float | None = None
    language: str | None = None
    provider: str = "unknown"


class SpeechRecognizer(Protocol):
    def available(self) -> bool: ...
    def transcribe(self, audio: bytes, cancellation: object) -> RecognitionResult: ...
    def health(self) -> dict[str, object]: ...
    def unload(self) -> None: ...


class UnavailableSpeechRecognizer:
    def available(self) -> bool:
        return False

    def transcribe(self, audio: bytes, cancellation: object) -> RecognitionResult:
        raise RecognitionError("local speech recognition is not configured")

    def health(self) -> dict[str, object]:
        return {"provider": "unconfigured", "available": False}

    def unload(self) -> None:
        return None


class LocalSpeechService:
    def __init__(self, recognizer: SpeechRecognizer | None = None, microphone: MicrophoneCapture | None = None) -> None:
        self.recognizer = recognizer or UnavailableSpeechRecognizer()
        self.microphone = microphone or MicrophoneCapture()

    def start_microphone(self) -> None:
        self.microphone.start()

    def stop_microphone(self) -> None:
        self.microphone.stop()

    def status(self) -> dict[str, object]:
        return {"microphone_active": self.microphone.active, "recognition_available": self.recognizer.available()}

