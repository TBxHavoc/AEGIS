"""Validation and normalization for mono signed 16-bit PCM audio."""
from __future__ import annotations

import array
import math
import sys


class AudioError(ValueError):
    pass


def validate_pcm16(audio: bytes, sample_rate: int = 16000, *, max_seconds: float = 30.0) -> bytes:
    if sample_rate != 16000:
        raise AudioError("audio sample rate must be 16000 Hz")
    if not isinstance(audio, bytes) or not audio or len(audio) % 2:
        raise AudioError("audio must contain non-empty mono PCM16 samples")
    if len(audio) > int(sample_rate * 2 * max_seconds):
        raise AudioError("audio exceeds the configured duration limit")
    return audio


def pcm_rms(audio: bytes) -> float:
    if len(audio) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(audio)
    if sys.byteorder != "little":
        samples.byteswap()
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
