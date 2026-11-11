# Voice system

Step 3 separates microphone capture, recognition, text preprocessing, TTS, playback, preferences, lifecycle, and observable state under `backend/voice/`.

The selected architecture is an optional Piper adapter. Piper is a fast local neural TTS engine, but the current OHF-Voice repository is GPL-3.0 and individual voice/model licenses must be reviewed before use. AEGIS does not download Piper, its runtime, or any voice model. Configure `AEGIS_PIPER_EXECUTABLE` and `AEGIS_PIPER_MODEL` only after an explicit installation decision.

For this 4 GB CPU-only Pentium Gold 4415U, Piper is the practical naturalness target if a small voice model is installed. eSpeak-NG is much lighter and multilingual but has lower naturalness; RHVoice is a multilingual offline alternative; Mimic 3 is neural and local but has an AGPL-3.0 project license and a larger runtime footprint. The comparison is based on project documentation and licensing, not a benchmark on this machine. See [VOICE_BEHAVIOUR.md](VOICE_BEHAVIOUR.md) for the non-negotiable rate and punctuation rules.

