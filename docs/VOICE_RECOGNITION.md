# Voice recognition

AEGIS captures audio only after the user enables the microphone button. The browser requests permission with `getUserMedia`, converts mono input to 16 kHz signed PCM16, and streams small base64 encoded chunks over the existing `/api/ws` connection. The server accepts chunks only during an enabled voice session, validates format and size, applies energy based VAD, and passes completed audio to the configured local provider. Transcribed text enters `CentralBrain` as a normal `source="voice"` request, retaining the same session history, memory retrieval, tools, permissions, protocol resolution, audit, and cancellation path as typed input. TTS remains governed by the existing voice preferences.

The UI keeps one microphone button and the existing visualizer. It does not render transcripts or chat history. It displays processing and playback phases, disables and closes the capture stream when voice mode is switched off, and exposes the existing text composer in that state. Browser echo cancellation and noise suppression are requested. While playback is active the client gates input and requires a louder sustained signal to barge in. This reduces TTS feedback, but is not full duplex acoustic echo cancellation.

## Providers and resource guidance

`WhisperCppRecognizer` invokes a locally installed `whisper-cli` executable and an existing local GGML/GGUF whisper.cpp model. It requires no Python package and starts one CLI process per transcription. CPU-only whisper.cpp builds are appropriate for the Pentium Gold 4415U; GPU support is not assumed. Upstream model files occupy about 75 MiB (`tiny.en`), 142 MiB (`base.en`), and 466 MiB (`small.en`) on disk; reported RAM depends on build and inference settings. The recommended first choice for this 4 GB machine is `tiny.en`, with at least 1 GB available before loading; `base.en` is a reasonable next step if headroom remains. `small.en` needs materially more headroom and can compete with the OS and language model. Avoid medium/large on this machine. The model-size figures come from the [whisper.cpp model list](https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md); runtime estimates here are conservative operating guidance, not measurements on this host. Actual speed and accuracy depend on build and audio; none have been benchmarked here.

`VoskRecognizer` is an optional alternative requiring the `vosk` Python package and a separately obtained model directory. Its small US English model is 40 MB on disk and is designed for lightweight devices; accuracy is more limited than larger Vosk models. The model sizes and published benchmark figures are listed by [Vosk](https://alphacephei.com/vosk/models). The adapter checks available memory against twice the local model file size before loading. Provider instances load lazily and can be unloaded. Only one provider is configured at a time.

The built-in VAD uses PCM frame RMS with configurable threshold, 240 ms minimum speech by default, 1100 ms silence hangover, and a hard 30 second segment ceiling. Short pauses remain in one turn. Set `AEGIS_VAD_THRESHOLD`, `AEGIS_VAD_MIN_SPEECH_MS`, `AEGIS_VAD_SILENCE_MS`, and `AEGIS_VAD_MAX_RECORDING_MS` to change these values. Silero VAD was considered but is not implemented because it requires a separate runtime/model; the energy VAD has no package dependency and can be replaced behind `VoiceActivityDetector`.

## Configure

No dependency or model is installed or downloaded automatically. For whisper.cpp, install/build it yourself and obtain a compatible local English model, then set:

```sh
export AEGIS_STT_PROVIDER='whisper.cpp'
export AEGIS_WHISPER_EXECUTABLE=/path/to/whisper-cli
export AEGIS_WHISPER_MODEL=/path/to/ggml-tiny.en.bin
export AEGIS_STT_TIMEOUT_SECONDS=60
export AEGIS_VAD_THRESHOLD=0.018
export AEGIS_VAD_MIN_SPEECH_MS=240
export AEGIS_VAD_SILENCE_MS=1100
export AEGIS_VAD_MAX_RECORDING_MS=30000
```

Or install Vosk and obtain an English model separately:

```sh
export AEGIS_STT_PROVIDER=vosk
export AEGIS_VOSK_MODEL=/path/to/vosk-model-small-en-us
```

Restart AEGIS and inspect `/api/status` for `stt.available`. Microphone permission is granted through the browser prompt. The site must be served from localhost or HTTPS for browser microphone access. Use the microphone control to explicitly enable or disable capture. Disabling it stops browser tracks immediately and sends a cancellation event.

## WebSocket audio events

All messages use the existing `{type, request_id, payload}` envelope. `audio.session.start` opens a bounded audio session; `audio.chunk` contains `payload.pcm16` as base64 mono PCM16 at 16 kHz (at most 500 ms per chunk); `audio.session.end` triggers transcription and the standard conversation response events; and `audio.session.cancel` discards the in-progress recording. Each event needs a unique `request_id`. Audio is capped at 30 seconds per turn, 500 ms per chunk, and 64 KiB per WebSocket frame. Audio is held only in bounded process memory and temporary WAV input for whisper.cpp is removed with its temporary directory. Raw audio is not logged or persisted. No external service receives audio.

## Testing and limitations

Run `python3 -m unittest tests.test_voice_recognition`. These tests use generated PCM samples and unconfigured provider adapters; they do not access a microphone, load a model, or verify recognition quality. The browser capture path and end-to-end microphone behavior have not been exercised on physical hardware. English is the initial target, but language metadata is currently unknown unless a future provider returns it; multilingual accuracy is not claimed. Energy VAD can struggle with quiet speech and noisy rooms. The browser uses `ScriptProcessorNode`, which is widely supported but deprecated. WebSocket payloads use base64 JSON for compatibility with the existing event protocol.
