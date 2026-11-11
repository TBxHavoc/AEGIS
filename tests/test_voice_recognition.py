from __future__ import annotations

import array
import unittest

from backend.voice.stt.audio import AudioError, validate_pcm16
from backend.voice.stt.normalize import normalize_transcription
from backend.voice.stt.providers import WhisperCppRecognizer, VoskRecognizer
from backend.voice.stt.providers import Transcription
from backend.voice.stt.service import AudioSession, STTService
from backend.voice.stt.vad import VADConfig, VADState, VoiceActivityDetector


def pcm(samples: int, value: int) -> bytes:
    return array.array("h", [value] * samples).tobytes()


class VoiceRecognitionTests(unittest.TestCase):
    def test_audio_validation_rejects_empty_odd_and_oversized_audio(self) -> None:
        for data in (b"", b"\x00", b"\x00\x00" * (16000 * 31)):
            with self.subTest(size=len(data)), self.assertRaises(AudioError):
                validate_pcm16(data)

    def test_vad_holds_short_pauses_and_ends_after_configured_silence(self) -> None:
        vad = VoiceActivityDetector(VADConfig(threshold=.01, minimum_speech_ms=200, silence_ms=700))
        self.assertEqual(vad.process(pcm(3200, 4000)), VADState.SPEECH)
        self.assertEqual(vad.process(pcm(3200, 0)), VADState.SPEECH)
        self.assertEqual(vad.process(pcm(3200, 4000)), VADState.SPEECH)
        self.assertEqual(vad.process(pcm(11200, 0)), VADState.SPEECH_ENDED)

    def test_audio_session_bounds_chunks_and_cancels(self) -> None:
        session = AudioSession()
        self.assertEqual(session.append(pcm(8000, 2000)), VADState.SPEECH)
        session.cancelled.set()
        with self.assertRaises(AudioError):
            session.append(pcm(800, 1000))

    def test_stt_provider_adapters_are_optional_and_lazy(self) -> None:
        whisper = WhisperCppRecognizer()
        vosk = VoskRecognizer()
        self.assertFalse(whisper.available())
        self.assertFalse(vosk.available())
        self.assertFalse(whisper.health()["loaded"])
        self.assertFalse(vosk.health()["loaded"])

    def test_service_rejects_chunks_without_an_explicit_session(self) -> None:
        service = STTService()
        with self.assertRaises(AudioError):
            service.append("missing", pcm(800, 1000))
        service.start("browser-session")
        self.assertEqual(service.append("browser-session", pcm(800, 1000)), VADState.SPEECH)
        self.assertTrue(service.cancel("browser-session"))

    def test_mock_provider_transcription_returns_text_and_metadata(self) -> None:
        class MockProvider:
            name = "mock"
            def available(self) -> bool: return True
            def health(self) -> dict[str, object]: return {"provider": self.name, "available": True}
            def unload(self) -> None: pass
            def transcribe(self, audio: bytes, cancellation: object) -> Transcription:
                self.audio = audio
                return Transcription("hello there", confidence=.91, language="en", provider=self.name)

        provider = MockProvider()
        service = STTService(provider)
        service.start("session")
        service.append("session", pcm(3200, 3000))
        result = service.finish("session")
        self.assertEqual(result.text, "hello there")
        self.assertEqual(result.language, "en")
        self.assertEqual(result.confidence, .91)
        self.assertEqual(len(provider.audio), 6400)

    def test_transcription_normalization_only_collapses_whitespace(self) -> None:
        self.assertEqual(normalize_transcription("  Turn   left, please.\n"), "Turn left, please.")

    def test_websocket_and_frontend_voice_controls_are_wired(self) -> None:
        from backend.core.events import EVENT_TYPES
        from pathlib import Path
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        self.assertTrue({"audio.session.start", "audio.chunk", "audio.session.end", "audio.session.cancel"}.issubset(EVENT_TYPES))
        self.assertIn("getUserMedia", html)
        self.assertIn('track.stop()', html)
        self.assertIn('wsSend("audio.session.cancel")', html)
        self.assertIn('wsSend("conversation.response.interrupt"', html)
        self.assertIn("echoCancellation:true", html)
        self.assertIn("ttsActive", html)


if __name__ == "__main__":
    unittest.main()
