# Multi-agent orchestration

## Request lifecycle

Typed messages and voice transcripts share the same `CentralBrain` entry point:

```text
user request → intent classification → protocol resolution → policy validation
→ specialist selection and dependency plan → bounded execution → verification
→ protocol compliance report → CentralBrain synthesis → one response
```

General conversation does not launch a specialist. Current classification covers explicit research, code review, cybersecurity, productivity, business, CSV/data, system status, protocol, and local knowledge requests. A few mixed cases create two tasks; market research precedes business planning, and code review precedes defensive review. Local research plus data analysis can run independently.

## Limits and collaboration

By default, specialists run sequentially (`AEGIS_AGENT_MAX_PARALLEL=1`). The configured value is restricted to 1 or 2; each workflow has a timeout (`AEGIS_AGENT_TIMEOUT_SECONDS`, 1–300 seconds), and only two root requests can execute at once. A plan has at most three specialist tasks. Task IDs are rooted in the original request, and cycle/unknown dependency checks run before execution. The internal message bus bounds each request to 64 messages and its retained history. Duplicate assignment/delegation requests are rejected.

An executing specialist may request a short response from a sibling already present in that root plan. This is a message only: it cannot add a new specialist or schedule recursive work. A result is returned only after that sibling has completed. Shared context is limited to the relevant request, up to two recent turns, up to five retrieved memory excerpts, resolved protocols, and declared dependency results.

Cancellation signals the root and task events; workflows must cooperate by checking their cancellation event. Timeout and cancellation are surfaced as failures/partial results, never successful completion. There is no automatic retry loop.

## Protocols, tools, and verification

The existing protocol resolver runs for every planned specialist category. Only active protocols are supplied, with ID, version, rules, and procedures. Resolver conflicts or permission requirements above policy block execution. Unapproved protocols cannot govern a run. The result records protocol versions and reports partial compliance when procedure evidence is unavailable.

Specialists invoke only their configured tools through `AgentContext.invoke_tool`, which checks the agent allowlist and existing `PermissionPolicy` before calling `ToolRegistry`. Each task receives live descriptions and schemas for its authorized registered tools. Tool calls carry an `agent:<id>` audit actor. Local administration can disable agents or narrow tool access. `PUT /api/agents/{agent_id}/tools/grant` can add only globally allowlisted registered tools after explicit local confirmation; agents cannot grant themselves capabilities. Current conversational workflows use read-only tools; no shell tool is exposed.

`ResultVerifier` checks identity, status, summary, confidence and structure, warns on findings without evidence, and detects contradictory values for the same key. It does not establish truth by itself. CentralBrain receives bounded specialist output as untrusted reference data, then the configured shared model synthesizes one response. When no provider is configured, the existing inference-unavailable response behavior remains in effect.

## HTTP surface

- `GET /api/agents`: enabled agent definitions and active execution status.
- `GET /api/agents/{agent_id}`: specialist definition; `GET /api/agents/{task_id}` also supports a task lookup.
- `GET /api/agents/runs/{request_id}`: completed run result.
- `POST /api/agents/tasks` with `{"instruction":"Check my system status"}`: classify and execute through the same brain/orchestration path; simple unclassified requests return `delegated: false` and do not launch a specialist.
- `POST /api/agents/tasks/{request_id}/cancel`: cooperative cancellation.
- Local-only `PUT /api/agents/{agent_id}/enabled` and `/tools`: disable/enable or narrow the tool list.
- Local-only `PUT /api/agents/{agent_id}/tools/grant` with `allowed_tools` and `confirmed:true`: add capabilities already present in the global tool allowlist.

The orchestrator settings are `AEGIS_AGENT_MAX_PARALLEL` (default `1`, maximum `2`) and `AEGIS_AGENT_TIMEOUT_SECONDS` (default `30`, maximum `300`).

## Capability status

These workflows are lightweight and deterministic. Research uses local knowledge and optional configured web search; coding can read approved files but does not edit files or automatically run tests; security analysis is heuristic and passive; productivity and business outputs are drafts; data analysis uses bounded registered CSV/JSON tools; system assistance is read-only; protocol management can create proposed drafts but cannot approve them. No arbitrary shell or script execution, external calendar, or separate specialist model is wired in.
