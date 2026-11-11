# Development handover

## Development environment

- OS: Ubuntu 26.04.1 LTS, x86_64.
- CPU: Intel Pentium Gold 4415U, 2 physical cores / 4 threads.
- Memory/graphics: 4 GB RAM, integrated graphics; plan for CPU-only operation and limited memory headroom.
- Python: `/usr/bin/python3` and `python3` report Python 3.14.4.
- Repository: run commands from the project root. The app defaults to `127.0.0.1:8765`.

## Dependencies and installation status

Base runtime and development dependencies are Python standard library only (`requirements/base.txt`, `requirements/dev.txt`). `requirements.txt` points to the base requirements. The optional inference extra is `llama-cpp-python` from `requirements/inference.txt`; it is not required by the base app. Piper, whisper.cpp and Vosk are also optional external providers. Model files are not included or downloaded automatically.

The documented checkout state has no optional model/provider configured. Do not infer that optional packages or model files are present. This handover did not install packages, download models, or create a virtual environment. Check local package/provider availability before enabling an optional integration.

## Setup and launch

From the project root:

```sh
/usr/bin/python3 main.py
```

Open `http://127.0.0.1:8765` in a browser. The default local server generates a random bearer token at `data/api.token` with restrictive permissions; the UI prompts for it and retains it in tab-scoped session storage. Read the token locally when prompted by the UI. Do not copy it into source control, logs, or shared documentation. Starting the application may create runtime data under `data/`.

Optional configuration can be exported in the shell or copied from `.env.example` into a local environment-loading workflow. The application settings are environment-driven; do not assume `.env` is loaded automatically. Restart the process after changing configuration.

## Configuration reference

See [.env.example](../.env.example) for the full template and defaults. Common settings:

| Purpose | Variables |
| --- | --- |
| Bind, logging, request limits | `AEGIS_HOST`, `AEGIS_PORT`, `AEGIS_LOG_LEVEL`, `AEGIS_MAX_MESSAGE_CHARS`, `AEGIS_MAX_SESSIONS` |
| Data, history, knowledge | `AEGIS_DATA_DIR`, `AEGIS_PERSIST_CONVERSATIONS`, `AEGIS_HISTORY_LIMIT`, `AEGIS_KNOWLEDGE_ROOT` |
| Local model | `AEGIS_MODEL_PATH`, `AEGIS_MODEL_CONTEXT`, `AEGIS_MODEL_THREADS`, `AEGIS_INFERENCE_TIMEOUT`, `AEGIS_MODEL_MAX_TOKENS`, `AEGIS_MODEL_TEMPERATURE`, `AEGIS_MODEL_TOP_P`, `AEGIS_MODEL_REPEAT_PENALTY`, `AEGIS_MODEL_MEMORY_FRACTION`, optional `AEGIS_SYSTEM_PROMPT` |
| Speech output | `AEGIS_PIPER_EXECUTABLE`, `AEGIS_PIPER_MODEL` |
| Speech input | `AEGIS_STT_PROVIDER`, `AEGIS_WHISPER_EXECUTABLE`, `AEGIS_WHISPER_MODEL`, `AEGIS_VOSK_MODEL`, `AEGIS_STT_TIMEOUT_SECONDS`, `AEGIS_VAD_*` |
| Tools and integrations | `AEGIS_FILE_ROOTS`, `AEGIS_PROJECT_ROOTS`, `AEGIS_MAX_PERMISSION`, `AEGIS_ALLOW_NETWORK`, `AEGIS_ALLOWED_TOOLS`, `AEGIS_WEB_PROVIDER`, `AEGIS_MCP_*` |
| API and remote bind | `AEGIS_API_TOKEN`, `AEGIS_ALLOW_REMOTE`; remote binding requires explicit enablement and an environment token, and remote mutations remain denied. |
| Resource watchdog | `AEGIS_MAX_RSS_MB`, `AEGIS_MAX_CPU_PERCENT`, `AEGIS_MIN_FREE_DISK_MB` |

Review [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md) before changing bind address, token, roots, network policy, or permission level.

## Model and voice providers

### Model inference

The only real inference provider implemented is optional local llama.cpp via `llama-cpp-python`, loading a user-provided GGUF lazily. Neither the runtime nor a model is included by the base installation. Configure `AEGIS_MODEL_PATH` to an existing file and use modest context/thread/token limits. The 4 GB Pentium system has limited headroom; current model sizing and CPU-only guidance are in [MODEL_ENGINE.md](MODEL_ENGINE.md). The examples there are optional and were not executed for this handover. Real generation and performance on this machine remain unverified.

### Speech

Piper TTS requires an installed executable and local voice model configured with `AEGIS_PIPER_EXECUTABLE` and `AEGIS_PIPER_MODEL`. STT supports local whisper.cpp (`AEGIS_STT_PROVIDER=whisper.cpp`, executable and model paths) or Vosk (`AEGIS_STT_PROVIDER=vosk`, model directory, plus its Python package). Providers and model assets are not bundled. Browser microphone use requires local/HTTPS origin and explicit user activation. No audio provider or physical microphone was verified here; details are in [VOICE_RECOGNITION.md](VOICE_RECOGNITION.md).

## Test and documentation commands

```sh
/usr/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -v
/usr/bin/python3 -m compileall -q backend main.py tests
git diff --check
```

The latest recorded full test result before this handover was 110 passing tests; this task did not rerun tests. Consult [TESTING.md](TESTING.md) for coverage boundaries. Tests rely on mocks for some providers/integrations and do not prove live model, speech, web, external MCP or firewall behavior.

## Important locations

- `main.py`, `backend/api/`: entry point, HTTP and WebSocket server.
- `backend/conversation/`, `backend/intelligence/`, `backend/agents/`: shared request path, reasoning and bounded specialists.
- `backend/inference/`, `backend/memory/`, `backend/voice/`: model contracts, memory and voice providers.
- `backend/tools/`, `backend/integrations/`: permissioned tool execution and optional connectors.
- `backend/security/`: safe mode, integrity, audit, protected paths, backups and firewall controls.
- `frontend/`: browser UI.
- `tests/`: unit and loopback integration suite.
- `requirements/`, `.env.example`: dependency groups and configuration template.
- `docs/`: architecture and operational references; `ROADMAP.md` records prior phases, and `MASTER_ROADMAP.md` consolidates future phases.
- `data/`: runtime databases, audit records and API token; treat as sensitive runtime data and do not commit it.

## Known limitations and recovery

Read [PROJECT_STATUS.md](PROJECT_STATUS.md), [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md), and [INTEGRITY_AND_RECOVERY.md](INTEGRITY_AND_RECOVERY.md) before operational changes. The audit chain is unkeyed; there is no encrypted-at-rest/keyring support, persisted trusted code baseline, automatic database restore, or server-managed per-user session revocation. Safe mode is an application control, not host isolation. Threads cancel cooperatively. UFW inspection may require root and AEGIS will not elevate itself. Optional inference/voice providers are absent unless separately installed and configured.

For database corruption, preserve the original, stop AEGIS, make a separate copy, verify a known-good backup with the documented SQLite checks, and restore manually only after review. Do not overwrite the only copy. For integrity or audit failures, retain evidence and follow safe-mode recovery gates; do not bypass them to resume service. For a lost API token, stop the service and follow the token-generation/configuration path in `backend/security/` and the security docs; protect any replacement token as a secret.

## Resume point

Resume with the **Phase 8.1 security-hardening prerequisite**, beginning with persistent trusted-code/last-known-good release state and a reviewed activation/rollback design. Complete and review Phase 8.1 exit criteria before any Phase 9 work. See [NEXT_STEPS.md](NEXT_STEPS.md) for ordered tasks.
