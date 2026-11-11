# Phase 1: Architecture Assessment and Integration Plan

## Current foundation

AEGIS is a standard-library Python application with a voice-first static frontend. `main.py` starts the threaded HTTP server in `backend/api/server.py`; the server owns REST/WebSocket transport and wires the conversation, inference, memory, tools, agents, protocols, learning, knowledge, scheduler, voice, audit, and permissions services. The frontend and WebSocket event protocol are already established and should remain the integration boundary.

The repository includes modular packages for conversation/context, provider contracts and a lazy llama.cpp adapter, central request preparation, SQLite-backed memory/protocol/trace/evaluation data, agent task routing, a deny-by-default tool registry, local knowledge indexing, scheduling, voice lifecycle/TTS, security, and desktop boundaries. There are extensive existing docs and 33 standard-library unit/loopback integration tests.

## Assessment

| Area | Phase 1 finding |
| --- | --- |
| Frontend and transport | **Working foundation.** Voice-first controls and HTTP/WebSocket lifecycle exist. Preserve the current interface and event names. |
| Local model management | **Foundation only.** A lazy llama.cpp adapter, local-first router, model catalog, resource snapshot, and explicit unavailable state exist. No model/runtime is configured or installed, and hardware benchmarks have not been performed. |
| Central brain | **Partial foundation.** `CentralBrain` detects heuristic intent, retrieves memory IDs, selects a small set of tool names, and records traces. It then delegates text generation to the conversation engine. Retrieved memories are not yet supplied to model context; selected tools are not automatically planned/executed/verified. |
| Planning and task execution | **Partial/foundation only.** Contracts and bounded/cancellable agent task records exist. Agent handlers are unavailable placeholders; there is no functioning multi-step planner or specialist execution. |
| Protocol registry | **Working persistence/lifecycle foundation.** SQLite supports structured proposals, approval, activation, deactivation, archive, revisions, search, and audit. Matching protocols are not yet automatically retrieved/applied by the brain, and the HTTP interface is narrower than the lifecycle API. |
| Tools, permissions, audit | **Partial.** A small set of safe local/read-only tools is registered and permission/audit boundaries exist. No general calculator, sandbox, web provider, MCP, or broad system interaction is wired. |
| Research and knowledge | **Local-only foundation.** Approved local text/code ingestion and retrieval are available. There is no configured web research adapter, source comparison workflow, or claim of web search. |
| Learning | **Foundation only.** Traces, feedback/evaluation records, and basic analysis are persisted. Automated comparative evaluation and useful performance dashboards are not established; model weights and code are not modified. |
| Voice | **Lifecycle foundation.** Optional Piper TTS and unavailable-provider behavior are implemented behind adapters. No TTS/STT provider or voice model is installed/configured, so real synthesis/recognition has not been verified. |
| Scheduler and desktop | **Partial.** A bounded scheduler exists, but should be checked for restart-persistent jobs and durable execution history. Desktop access is intentionally disabled; no real computer actions are enabled. |

## Constraints and issues to carry forward

- Do not change the frontend, WebSocket contract, or current user edits as part of the initial integration.
- No model, large dependency, dataset, paid API, or external service is configured. Local inference should be benchmarked only after the user chooses/approves a model/runtime installation.
- The current test suite passes (33 tests), but the test process emits ignored `ResourceWarning` messages for unclosed SQLite connections. This is a cleanup issue to investigate when modifying those persistence paths; it does not change the suite's exit status.
- The docs currently describe major subsystems more completely than their live integrations. In particular, agent handlers, automatic protocol application, web research, real voice, and model inference must not be represented as working until end-to-end verification exists.
- The current `docs/OPENJARVIS_INTEGRATION.md` cites a stale/noncanonical set of URLs. The requested Stanford project is [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis), documented at [open-jarvis.github.io/OpenJarvis](https://open-jarvis.github.io/OpenJarvis/); its current repository identifies an Apache-2.0 license. The `stanford-oval/openjarvis` URL in the brief does not resolve to that project. Existing architecture notes should be corrected before any OpenJarvis-inspired integration. No source was copied or downloaded during this assessment.

## Integration plan

1. **Model management and inference:** define model lifecycle/status/limits and configuration, make provider loading failures actionable, and verify resource reporting. Benchmark the current machine and recommend a model before any installation. Keep model installation opt-in.
2. **Central brain:** connect intent, bounded context (including retrieved memory), task classification/planning, agent/tool routing, permission checks, execution, verification, progress/cancellation/timeouts, and response/voice output while preserving existing transport events.
3. **Protocol registry integration:** finish protocol schema/history and API coverage, then add applicable-protocol retrieval, explicit conflict handling, usage/compliance records, and a proposal/approval path that never silently activates revisions.
4. **Specialists and tools:** implement one meaningful capability at a time behind typed contracts and permissioned registry tools; add focused tests before claiming each specialist works. Avoid unrestricted shell access.
5. **Knowledge and research:** improve local document coverage/provenance first; add web research only through an explicitly configured provider and source-backed results.
6. **Learning and measurement:** close SQLite lifecycle issues, persist approved feedback/evaluations/traces, and report measurable tool/task latency and success without automatic changes to code, policies, prompts, or model weights.
7. **Voice, scheduler, and connectors:** preserve voice lifecycle and interface; validate an optional CPU-friendly TTS/STT provider only after approval. Make scheduled work durable and keep computer interaction read-only/disabled until narrowly permissioned.
8. **Integration and documentation:** update capability classifications and installation guidance from verified behavior, correct OpenJarvis research citations, and run the complete available suite after integrated changes.

Each later major phase should remain a separately reviewable change. Any step requiring substantial new dependencies, downloads, or an external service is deferred for the user's approval as requested in the implementation brief.

## Phase 1 verification

- `/usr/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -v` — **33 tests passed**; ignored SQLite connection `ResourceWarning` messages appeared during interpreter shutdown.
- `/usr/bin/python3 -m compileall -q backend main.py tests` — **passed**.
- No model, package, dataset, or external service was installed or contacted by the application.
