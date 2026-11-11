# Project status

**Snapshot:** 2026-11-10. This is a documentation handover; no implementation or test run was performed for this snapshot.

## Current architecture

`main.py` loads validated environment settings and runs a dependency-light threaded HTTP/WebSocket server. The browser UI is served from `frontend/`; the server exposes REST and WebSocket APIs. Requests flow through the shared conversation engine and `CentralBrain`, with bounded sessions, optional SQLite persistence, local memory retrieval, deterministic reasoning/planning, protocol resolution and specialist workflows. Tools are registered centrally and filtered through permission, confirmation, rate, timeout, cancellation and safe-mode controls. Optional providers are local llama.cpp inference, Piper speech synthesis, whisper.cpp/Vosk recognition, web search and MCP. Security services cover authentication, protected paths, audit, integrity checks, backups, safe mode and firewall inspection/proposals. See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md), and [TOOL_ARCHITECTURE.md](TOOL_ARCHITECTURE.md).

## Implemented

- Phases 1–8 are recorded as complete in [ROADMAP.md](ROADMAP.md): core server/UI, model-provider contract, local memory, reasoning/planning, persistent protocols, voice interfaces, nine bounded specialist definitions, controlled tools/integrations, and Phase 8 security controls.
- Local-first model discovery and lazy llama.cpp adapter; model use is optional and unavailable without the runtime and a compatible local GGUF file.
- Typed and browser microphone requests share the conversation path. Local STT/TTS adapters exist, but require separately installed providers and models.
- Filesystem tools are root-scoped; network, MCP, scheduled work, code-related tools and elevated permissions are opt-in and gated.
- HTTP and WebSocket API access requires a bearer token. The default token is generated in `data/api.token`; remote binding has additional explicit gates.
- Phase 8 includes safe mode, source/audit/database checks, protected paths, audit-chain records, confirmed SQLite backups, and additive UFW proposals.

## Incomplete features and known limitations

- Phase 8.1 hardening remains a prerequisite: persistent trusted-code baseline/last-known-good release, reviewed code activation and rollback, stronger server-managed session/revocation, encryption/keyring design, and process isolation for long-running or untrusted work.
- Phases 9–11 have not started. Their scope is in [MASTER_ROADMAP.md](MASTER_ROADMAP.md).
- No live model inference, microphone capture, transcription, or speech synthesis has been verified on this host. No model files are configured or bundled.
- Reasoning, protocols and specialist workflows use deterministic fallbacks; model-backed extraction and specialist collaboration are not independently trained or separately modeled. Protocol compliance evidence and semantic interpretation are limited.
- Browser audio uses deprecated `ScriptProcessorNode`; energy VAD and half-duplex playback gating are basic and may perform poorly in noise. English quality is not benchmarked.
- MCP and web integrations default off. No external MCP service is configured. Device integration is an interface only; no Android provider is connected.
- UFW status could not be read without root, so the host firewall's active state/rules remain unverified. AEGIS does not elevate privileges or change host firewall rules. Listener inspection does not establish process ownership.
- Watchdog checks are application-scoped and cannot detect general malware, attribute host CPU, or kill arbitrary processes. Python worker cancellation is cooperative. Safe mode is not OS isolation.
- Audit SHA-256 chaining detects ordinary edits but is unkeyed and does not protect against a privileged attacker rewriting the full chain. Legacy records are not authenticated. There is no encryption at rest or automatic database restore.
- Model memory/speed estimates are approximations. The 4 GB system has little headroom; model loading is synchronous and cannot be forcibly interrupted.

## Test status

The most recent recorded full-suite result before this handover was **110 tests passing** (about 7.4 seconds), with compile checks and `git diff --check` also reported passing. This task did not rerun tests; treat that result as the last recorded baseline, not a fresh verification. The documented commands are in [TESTING.md](TESTING.md). Tests include standard-library unit and loopback integration coverage; they do not establish real provider quality or live external-service behavior.

## Actual versus mocked

| Area | Status in this checkout/environment |
| --- | --- |
| HTTP/WebSocket server, deterministic request handling, local SQLite paths, policy gates | Implemented locally; exercised by unit/loopback tests. |
| llama.cpp model loading and output | Adapter plumbing is tested with mocks; no runtime/model configured, so real generation is unavailable and unverified. |
| Voice | Browser capture and provider adapters are implemented; tests use generated PCM and unavailable/mocked providers. No physical microphone, STT model, or TTS model was exercised. |
| Specialist agents/reasoning | Bounded deterministic workflows and mocked inference contracts; not separate autonomous models. |
| MCP | Tests use a loopback mock MCP server; no external MCP server is configured. |
| Web research | Parsing and network-policy paths are tested; live search is not claimed and is off by default. |
| Sandbox | Bubblewrap test-runner path uses a real local sandbox where available; this is not general isolation for all tools/agents. |
| Firewall/security | Proposal validation uses a mocked command runner. Read-only UFW inspection was attempted and denied for lack of root; no firewall changes were made. Security subsystem behavior is otherwise covered by local tests, not an external security audit. |
| Backups/recovery | SQLite backup/verification is exercised on temporary databases; restore is a documented manual process and has not been performed on production data. |

## Hardware constraints

Development target: **Ubuntu 26.04.1 LTS, Intel Pentium Gold 4415U, 4 GB RAM, integrated graphics** (x86_64; CPU-only model execution). The processor has two physical cores/four threads; avoid GPU assumptions and heavy concurrent workloads. Start with low context and small local models only after checking available memory. Hardware details and estimates are documented in [MODEL_ENGINE.md](MODEL_ENGINE.md); no model benchmark has been performed here.
