"""Text preprocessing that deliberately preserves intended words and punctuation."""
from __future__ import annotations


def prepare_for_tts(text: str) -> str:
    """Return the exact assistant text; never add an address, filler, or SSML breaks."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("TTS text must be non-empty text")
    return text


def speaking_rate_for_text(text: str, configured_rate: float) -> float:
    """Return the single configured rate, independent of punctuation."""
    prepare_for_tts(text)
    if not 0.5 <= configured_rate <= 2.0:
        raise ValueError("configured rate out of range")
    return configured_rate

