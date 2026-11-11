"""Bounded per-session audio assembly and local transcription service."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from backend.voice.stt.audio import AudioError, validate_pcm16
from backend.voice.stt.providers import SpeechRecognizer, Transcription
from backend.voice.stt.vad import VADConfig, VADState, VoiceActivityDetector

MAX_AUDIO_BYTES = 16000 * 2 * 30
MAX_CHUNK_BYTES = 16000 * 2 // 2


@dataclass
class AudioSession:
    vad: VoiceActivityDetector = field(default_factory=VoiceActivityDetector)
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)

    def append(self, chunk: bytes) -> VADState:
        if self.cancelled.is_set():
            raise AudioError("audio session cancelled")
        if len(chunk) > MAX_CHUNK_BYTES:
            raise AudioError("audio chunk exceeds 500 ms")
        if self.size + len(chunk) > MAX_AUDIO_BYTES:
            raise AudioError("audio session exceeds 30 seconds")
        validate_pcm16(chunk)
        self.chunks.append(chunk)
        self.size += len(chunk)
        return self.vad.process(chunk)

    def audio(self) -> bytes:
        return validate_pcm16(b"".join(self.chunks))


class STTService:
    def __init__(self, provider: SpeechRecognizer | None = None, vad_config: VADConfig = VADConfig()) -> None:
        self.provider = provider
        self.vad_config = vad_config
        self._active: dict[str, AudioSession] = {}
        self._transcribing: dict[str, AudioSession] = {}
        self._lock = threading.RLock()

    def start(self, session_id: str) -> None:
        with self._lock:
            old = self._active.pop(session_id, None)
            if old:
                old.cancelled.set()
            self._active[session_id] = AudioSession(VoiceActivityDetector(self.vad_config))

    def append(self, session_id: str, chunk: bytes) -> VADState:
        with self._lock:
            session = self._active.get(session_id)
            if session is None:
                raise AudioError("audio session is not active")
            return session.append(chunk)

    def finish(self, session_id: str) -> Transcription:
        with self._lock:
            session = self._active.pop(session_id, None)
            if session is not None:
                self._transcribing[session_id] = session
        if session is None:
            raise RuntimeError("local speech recognition is not configured")
        if self.provider is None:
            with self._lock:
                self._transcribing.pop(session_id, None)
            raise RuntimeError("local speech recognition is not configured")
        try:
            return self.provider.transcribe(session.audio(), session.cancelled)
        finally:
            with self._lock:
                self._transcribing.pop(session_id, None)

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            session = self._active.pop(session_id, None) or self._transcribing.get(session_id)
        if session:
            session.cancelled.set()
            return True
        return False

    def cancel_all(self) -> None:
        with self._lock:
            sessions = tuple(self._active.values()) + tuple(self._transcribing.values())
            self._active.clear()
        for session in sessions:
            session.cancelled.set()

    def health(self) -> dict[str, object]:
        result = self.provider.health() if self.provider else {"provider": "unconfigured", "available": False}
        result["vad"] = {"threshold": self.vad_config.threshold, "minimum_speech_ms": self.vad_config.minimum_speech_ms,
                         "silence_ms": self.vad_config.silence_ms, "max_recording_ms": self.vad_config.max_recording_ms}
        return result

    def unload(self) -> None:
        if self.provider:
            self.provider.unload()
