"""Configurable energy VAD with hangover so ordinary pauses do not split speech."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.voice.stt.audio import pcm_rms


class VADState(str, Enum):
    SILENCE = "silence"
    SPEECH = "speech"
    SPEECH_ENDED = "speech_ended"


@dataclass(frozen=True, slots=True)
class VADConfig:
    threshold: float = 0.018
    minimum_speech_ms: int = 240
    silence_ms: int = 1100
    max_recording_ms: int = 30000

    def __post_init__(self) -> None:
        if not 0.001 <= self.threshold <= 0.5 or not 50 <= self.minimum_speech_ms <= 5000:
            raise ValueError("invalid VAD threshold or minimum speech duration")
        if not 200 <= self.silence_ms <= 10000 or not 1000 <= self.max_recording_ms <= 30000:
            raise ValueError("invalid VAD silence or maximum duration")


class VoiceActivityDetector:
    def __init__(self, config: VADConfig = VADConfig()) -> None:
        self.config = config
        self.reset()

    def reset(self) -> None:
        self.elapsed_ms = 0
        self.speech_ms = 0
        self.silence_ms = 0
        self.started = False

    def process(self, pcm: bytes, sample_rate: int = 16000) -> VADState:
        duration = len(pcm) * 500 / sample_rate
        self.elapsed_ms += duration
        voiced = pcm_rms(pcm) >= self.config.threshold
        if voiced:
            self.started = True
            self.speech_ms += duration
            self.silence_ms = 0
            return VADState.SPEECH
        if not self.started:
            return VADState.SILENCE
        self.silence_ms += duration
        if self.elapsed_ms >= self.config.max_recording_ms or self.silence_ms >= self.config.silence_ms:
            if self.speech_ms >= self.config.minimum_speech_ms:
                return VADState.SPEECH_ENDED
            self.reset()
        return VADState.SPEECH
