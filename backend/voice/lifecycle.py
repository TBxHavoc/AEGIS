"""Voice orchestration: one response, one request ID, one playback owner."""
from __future__ import annotations

from threading import RLock

from backend.conversation.models import CancellationToken
from backend.voice.capture import MicrophoneCapture
from backend.voice.config import VoicePreferences, VoicePreferencesStore
from backend.voice.playback import AudioPlayback
from backend.voice.recognition import LocalSpeechService
from backend.voice.state import VoiceStateStore
from backend.voice.tts import TTSProvider, TTSError


class VoiceService:
    def __init__(self, preferences: VoicePreferencesStore, tts: TTSProvider,
                 playback: AudioPlayback | None = None, speech: LocalSpeechService | None = None) -> None:
        self.preferences = preferences
        self.tts = tts
        self.playback = playback or AudioPlayback()
        self.speech = speech or LocalSpeechService(microphone=MicrophoneCapture())
        self.state = VoiceStateStore()
        self._active: dict[str, CancellationToken] = {}
        self._completed: set[str] = set()
        self._lock = RLock()

    def get_preferences(self) -> VoicePreferences:
        return self.preferences.load()

    def save_preferences(self, values: VoicePreferences) -> VoicePreferences:
        saved = self.preferences.save(values)
        self.state.update(muted=saved.muted, microphone_active=saved.microphone_enabled)
        if saved.muted:
            self.stop()
        return saved

    def speak(self, text: str, request_id: str) -> None:
        with self._lock:
            if request_id in self._active or request_id in self._completed:
                return
            self.stop()
            token = CancellationToken()
            self._active[request_id] = token
        preferences = self.get_preferences()
        if preferences.muted:
            self._finish(request_id)
            return
        self.state.update(synthesizing=True, active_request_id=request_id, error=None)
        try:
            artifact = self.tts.synthesize(text, preferences, request_id, token)
            if token.cancelled:
                artifact.path.unlink(missing_ok=True)
                return
            self.playback.play(artifact, preferences.volume, request_id)
            self.state.update(synthesizing=False, speaking=True)
        except (TTSError, OSError, ValueError) as exc:
            self.state.update(synthesizing=False, speaking=False, error=str(exc))
        finally:
            self._finish(request_id)

    def stop(self) -> None:
        with self._lock:
            for token in self._active.values():
                token.cancel()
            self._active.clear()
            self.playback.stop()
            self.state.update(synthesizing=False, speaking=False, active_request_id=None)

    def set_microphone(self, enabled: bool) -> None:
        if enabled:
            self.speech.start_microphone()
        else:
            self.speech.stop_microphone()
        preferences = self.get_preferences()
        self.save_preferences(VoicePreferences(preferences.voice, preferences.speaking_rate, preferences.volume,
                                               preferences.muted, enabled))

    def status(self) -> dict[str, object]:
        current = self.state.get()
        preferences = self.get_preferences()
        speaking = self.playback.active_request_id is not None
        if current.speaking != speaking:
            self.state.update(speaking=speaking)
        return {"provider": self.tts.name, "available": self.tts.available(), "voices": self.tts.voices(),
                "speaking": speaking, "synthesizing": current.synthesizing,
                "microphone_active": self.speech.microphone.active, "muted": preferences.muted,
                "error": current.error}

    def _finish(self, request_id: str) -> None:
        with self._lock:
            self._active.pop(request_id, None)
            self._completed.add(request_id)
            if self.state.get().active_request_id == request_id:
                self.state.update(active_request_id=None, synthesizing=False)

