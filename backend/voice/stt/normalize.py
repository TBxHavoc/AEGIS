"""Conservative transcription normalization."""
from __future__ import annotations

import re


def normalize_transcription(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("transcription must be text")
    return re.sub(r"\s+", " ", text).strip()
