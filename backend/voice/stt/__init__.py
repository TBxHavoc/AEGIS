"""Local-first, replaceable speech-to-text components."""
from backend.voice.stt.audio import AudioError, validate_pcm16
from backend.voice.stt.providers import SpeechRecognizer, WhisperCppRecognizer, VoskRecognizer
from backend.voice.stt.vad import VADConfig, VADState, VoiceActivityDetector

__all__ = ["AudioError", "validate_pcm16", "SpeechRecognizer", "WhisperCppRecognizer",
           "VoskRecognizer", "VADConfig", "VADState", "VoiceActivityDetector"]
