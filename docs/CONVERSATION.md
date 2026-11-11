# Conversation

`ConversationEngine` owns one logical turn. It records the user message, assembles a bounded context window, asks the selected provider for a stream, emits zero or more chunks, and emits exactly one terminal event: `conversation.response.completed` or `conversation.response.failed`.

Cancellation is cooperative through `CancellationToken`; a caller can cancel or interrupt by request ID. Session history is always available in process. SQLite session/message persistence is opt-in with `AEGIS_PERSIST_CONVERSATIONS=1`, so ordinary conversations are not permanently saved by default. Persistence failures are surfaced as provider/engine errors rather than being silently presented as successful responses.

## Specialist routing API

Typed requests and voice transcripts enter the same `CentralBrain` pipeline. `GET /api/agents` lists enabled specialist definitions and resource status; `GET /api/agents/{agent_id}` returns a definition and `GET /api/agents/runs/{request_id}` returns a completed run. `POST /api/agents/tasks` accepts an `instruction` and optional `session_id`, classifies it, and returns either a completed `delegated: false` response for general requests or a run with selected specialists and verified result metadata. `POST /api/agents/tasks/{request_id}/cancel` requests cooperative cancellation. Local configuration can narrow tool access; adding globally allowlisted capabilities requires explicit local confirmation through `PUT /api/agents/{agent_id}/tools/grant`. See [MULTI_AGENT_ORCHESTRATION.md](MULTI_AGENT_ORCHESTRATION.md) for limits and request examples.

## Tool and integration API

- `GET /api/tools` discovers registered tools and schemas. `POST /api/tools/{tool_id}` accepts `{"arguments":{...},"intent":"user goal","request_id":"optional-id","confirmed":true}`. Sensitive tool calls with confirmation are loopback-only; caller-assigned executions can be cancelled with `POST /api/tools/runs/{request_id}/cancel`. Direct tool execution resolves active protocols before invoking the registry.
- `POST /api/knowledge/ingest` requires a loopback caller and `confirmed:true`, and then uses the allowlisted `knowledge.ingest` registry tool.
- `GET /api/scheduler/tasks` lists persisted tasks/history. `POST /api/scheduler/tasks` schedules a registered workflow and requires loopback plus `confirmed:true`; pause/cancel are loopback-only, and resume of persisted workflows also needs confirmation.
- `GET /api/mcp/status` reports configuration/connection; `POST /api/mcp/connect` requires a configured, approved endpoint, enabled network policy, loopback and `confirmed:true`. `POST /api/mcp/disconnect` closes it. No MCP connection is made on startup.
- `GET /api/devices/status` reports the device integration foundation as unavailable until a provider and pairing flow exist.

Tool IDs, allowlists, file-root configuration, network defaults, and execution boundaries are described in [TOOL_ARCHITECTURE.md](TOOL_ARCHITECTURE.md).

