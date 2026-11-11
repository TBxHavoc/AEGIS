# Reasoning, planning, and orchestration (Phase 3)

## Status

Phase 3 adds structured request understanding, bounded context construction, a validated plan format, and a permission-gated task orchestrator. It does not provide general-purpose autonomous execution. Without an available configured local model, request understanding uses transparent keyword and sentence heuristics. The deterministic path cannot reliably infer nuanced intent, hidden constraints, or multi-step dependencies.

When the existing Phase 2 `InferenceEngine` reports a model available, `ModelBackedUnderstanding` uses that provider contract to request JSON intent fields. Invalid or unavailable model output falls back to the deterministic classifier. The model can propose descriptions; every tool name is revalidated against the local registry and every invocation passes through `ToolRegistry` and `PermissionPolicy`. Model-generated commands are never executed.

## Components

- `backend/intelligence/reasoning.py`: intent categories and extraction, bounded `ContextBuilder`, optional model-backed classifier, deterministic planner, typed task/step/results, validation, orchestration, verification, cancellation, and SQLite task history.
- `POST /api/reasoning/tasks`: accepts `{ "request": "...", "session_id": "optional", "execute": false }`. It returns intent and a validated plan. Set `execute` to `true` to invoke only tools named in the plan through the permission-controlled registry. Plan-only is the default.
- `GET /api/reasoning/tasks`: lists recent persisted plans and results. `GET /api/reasoning/tasks?id=...` reads one record. `POST /api/reasoning/tasks/{task_id}/cancel` cancels a currently executing plan between steps.

The context builder accepts only caller-selected recent conversation, up to five relevant memory excerpts, registered tool descriptions, model availability metadata, and system instructions. It enforces a character bound before passing context to the existing inference engine, which applies its own token estimate and bound. The HTTP endpoint obtains memories through the existing brain's category-filtered retrieval, never by dumping the database.

## Execution and verification

Plans require unique step IDs, earlier-step dependencies, registered tools, bounded timeouts, and retry counts from zero through three. A dependent step is skipped as failed when its dependency did not succeed. Tool execution uses the registry's permission policy and timeout. Only read-only tools may request retries, and currently only timeout errors are retried. A plan may include descriptive steps without a tool; these remain unexecuted and produce partial status rather than fabricated completion.

Verification is deliberately narrow. Registered tool steps can verify that a successful result is a non-empty object. This checks result shape, not whether an external or semantic objective is true. Steps without a suitable criterion return `verification_unavailable`; they are not reported as independently verified. Outcomes include success, partial success, failure, verification unavailable, and cancelled.

Task records, plans, durations, retries, outputs, and verification details are stored in `data/reasoning_tasks.db`. Tool audit events continue to be stored by the existing `AuditLogger`. Cancellation is cooperative between steps; an in-flight synchronous handler cannot be forcibly interrupted beyond its registered timeout.

Phase 4 resolves matching active protocols before planning. Only relevant protocol content is included in context. Plans store the exact protocol IDs and versions, append ordered required procedure steps, and record a compliance report. Equal-priority conflicts, unresolved protocol preconditions, or permissions above the existing policy produce a blocked plan. Protocol procedures currently have no specialist executors, so plans do not claim compliance merely because their steps were listed. See [PROTOCOLS.md](PROTOCOLS.md).

## Capability classification

| Capability | Status | Limit |
| --- | --- | --- |
| Deterministic intent categories and field extraction | WORKING | Keyword/sentence heuristics, low confidence, English-oriented. |
| Model-backed intent extraction | FOUNDATION ONLY | Uses Phase 2 provider when available; no real model configured or verified in this checkout. |
| Bounded conversation/memory/tool context | WORKING | Caller supplies selected memories and recent history; character limit is approximate for tokens. |
| Dependency-aware plan validation | WORKING | Dependencies must reference earlier steps; no parallel executor. |
| Registered tool execution and permission enforcement | WORKING | Only current registered tools; current planner maps a small set of explicit request patterns. |
| Cancellation, timeouts, bounded retries | PARTIALLY WORKING | Cancellation between steps; only timed-out read-only steps retry; worker threads may finish after timeout. |
| Independent/semantic result verification | PARTIALLY WORKING | Non-empty result shape only; semantic and external-state verification unavailable. |
| Alternative plan generation and escalation | NOT IMPLEMENTED | Failures are explained in task results; no alternate-plan search or interactive approval flow. |
| Specialist-agent delegation | FOUNDATION ONLY | No fake agents or delegation implementation added. |

No inference model, model runtime, or large dependency was installed or downloaded for this phase.
