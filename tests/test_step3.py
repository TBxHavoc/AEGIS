from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.conversation.models import CancellationToken
from backend.voice.config import VoicePreferences, VoicePreferencesStore
from backend.voice.lifecycle import VoiceService
from backend.voice.preprocessing import prepare_for_tts, speaking_rate_for_text
from backend.voice.recognition import LocalSpeechService
from backend.voice.tts import AudioArtifact, PiperProvider, UnavailableTTSProvider


class MockTTS:
    name = "mock"

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.calls: list[tuple[str, str, float]] = []

    def available(self) -> bool: return True
    def voices(self) -> tuple[str, ...]: return ("mock",)

    def synthesize(self, text: str, preferences: VoicePreferences, request_id: str, cancellation: CancellationToken) -> AudioArtifact:
        self.calls.append((text, request_id, preferences.speaking_rate))
        path = Path(self.directory) / f"{request_id}.wav"
        path.write_bytes(b"mock audio")
        return AudioArtifact(path, request_id)


class MockPlayback:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.stopped = 0

    def play(self, artifact: AudioArtifact, volume: float, request_id: str) -> None:
        self.played.append(request_id)

    def stop(self) -> None:
        self.stopped += 1


class Step3Tests(unittest.TestCase):
    def test_text_preservation_and_punctuation_independent_rate(self) -> None:
        phrases = [
            "One, two, three, four.", "A long sentence with, multiple commas, stays consistent.",
            "Short.", "Is this a question?", "Excellent!", "Version 3.14, released in 2026.", "Dr. Smith arrived.",
        ]
        self.assertEqual(prepare_for_tts(phrases[1]), phrases[1])
        self.assertNotIn("boss", prepare_for_tts(phrases[1]).lower())
        self.assertEqual([speaking_rate_for_text(phrase, 1.0) for phrase in phrases], [1.0] * len(phrases))

    def test_piper_uses_one_global_rate_setting(self) -> None:
        provider = PiperProvider("piper", "/tmp/model.onnx")
        preferences = VoicePreferences(speaking_rate=1.25)
        first = provider.command(preferences)
        second = provider.command(preferences)
        self.assertEqual(first, second)
        self.assertIn("0.800000", first)

    def test_preferences_persist_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = VoicePreferencesStore(Path(directory) / "voice.json")
            saved = store.save(VoicePreferences("amy", 1.1, 0.4, True, True))
            self.assertEqual(store.load(), saved)
            with self.assertRaises(ValueError):
                store.save(VoicePreferences(speaking_rate=3.0))

    def test_voice_service_deduplicates_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tts = MockTTS(directory)
            playback = MockPlayback()
            service = VoiceService(VoicePreferencesStore(Path(directory) / "voice.json"), tts, playback)
            service.speak("exact words, no filler.", "r1")
            service.speak("exact words, no filler.", "r1")
            service.speak("new response.", "r2")
            self.assertEqual([call[1] for call in tts.calls], ["r1", "r2"])
            service.stop()
            self.assertGreaterEqual(playback.stopped, 1)

    def test_unavailable_tts_and_recognition_are_explicit(self) -> None:
        tts = UnavailableTTSProvider()
        self.assertFalse(tts.available())
        speech = LocalSpeechService()
        speech.start_microphone()
        self.assertTrue(speech.status()["microphone_active"])
        speech.stop_microphone()
        self.assertFalse(speech.status()["microphone_active"])


if __name__ == "__main__":
    unittest.main()

