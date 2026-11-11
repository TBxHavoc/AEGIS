# Permanent protocol intelligence (Phase 4)

Phase 6 specialist work resolves applicable active protocols before execution. The selected protocol ID/version and bounded rules/procedures enter specialist context; resolver conflicts or permission requirements above policy block the run. The specialist result records the protocol versions it received, while protocol compliance is reported as partial when there is no procedure-level execution evidence. Protocol instructions never grant tool permissions. The Protocol Management specialist can prepare a proposed draft after local-client and policy checks, but cannot approve or activate it. See [MULTI_AGENT_ORCHESTRATION.md](MULTI_AGENT_ORCHESTRATION.md).

Phase 7 also resolves protocols for direct `/api/tools/{tool_id}` execution and scheduled system snapshots. A blocking resolution prevents the operation; successful direct requests record applicable protocol versions in audit details and response metadata. Tool handlers still obey the independent permission policy; protocol text cannot authorize an otherwise denied tool. Semantic procedure compliance remains limited. See [TOOL_ARCHITECTURE.md](TOOL_ARCHITECTURE.md).

## Purpose and limits

Protocols are user-defined operational guidance stored separately from conversational memory. They are structured records, not executable scripts or security authority. A protocol cannot grant permissions, bypass operating-system or application controls, bypass approval, or cause arbitrary Python or shell execution. Tool calls still pass through the normal allowlist and permission policy.

The deterministic draft parser handles explicit instructions and numbered/action lists. It does not provide general natural-language understanding. Incomplete drafts return validation issues for clarification. No model or large dependency is needed; model-backed complex parsing is not implemented or claimed.

## Data and versioning

`ProtocolSpec` includes a unique protocol ID, name, description, category, priority, version, purpose, scope, definitions, preconditions, triggers, required permission levels, rules, ordered procedures, expected outputs, exceptions, failure handling, verification criteria, approval status, timestamps, and a parent revision link. Categories include common AEGIS domains and accept custom labels.

SQLite stores the current protocol state in `protocols`, immutable content snapshots in `protocol_versions`, and task compliance results in `protocol_compliance`. Existing `data/protocols.db` rows are retained; startup backfills missing version snapshots. A revision creates a new protocol ID linked to its parent and starts as a proposed draft. Approval never edits the snapshot content. Activating an approved revision deactivates its active ancestor in that lineage. History walks revision descendants and returns stored snapshots.

Lifecycle: proposed draft → approved → active → inactive → archived. Archived drafts may be restored to inactive and then approved/activated through the normal transitions. Every transition and compliance result is audited.

## Draft, approval, and API

All protocol mutation endpoints require a loopback client. Draft creation is allowed locally; approval, activation, deactivation, archive, restore, and revision also require `{"confirmed":true}`. This follows the current application's local-owner model; the backend does not yet have user accounts or remote authentication. Read endpoints are available wherever the server is reachable.

Examples:

```http
POST /api/protocols/draft/preview
{"instruction":"Create Protocol 001 for software development. Every code change must be tested, reviewed, documented and verified before acceptance."}
```

Preview returns a structured candidate and validation result without storage. `POST /api/protocols/draft` stores the candidate as proposed. Structured clients can also `POST /api/protocols` with fields such as `name`, `purpose`, `scope`, `category`, and `procedures`. No create endpoint can set an approved or active state.

Lifecycle actions use `POST /api/protocols/{id}/approve`, `/activate`, `/deactivate`, `/archive`, `/restore`, or `/revise`, each with `{"confirmed":true}`. Revision uses `{"confirmed":true,"changes":{"purpose":"..."}}`; the result is a new proposed version and must be separately approved and activated.

Read APIs:

- `GET /api/protocols?q=...&status=...&category=...` lists/searches protocols.
- `GET /api/protocols/{id}` retrieves the current version.
- `GET /api/protocols/{id}/versions` retrieves its revision lineage.
- `GET /api/protocols/{id}/version?version=N` retrieves a specific snapshot.
- `GET /api/protocols/compliance?task_id=...&protocol_id=...` or `GET /api/protocols/{id}/compliance` retrieves compliance reports.

The reasoning endpoint recognizes explicit “create/draft/define/add protocol” requests and returns a stored draft only. Other task requests use `POST /api/reasoning/tasks`; its plan includes matching active protocol IDs and versions. Plans are not executed unless the caller explicitly sets `execute: true`.

## Resolution and conflict handling

Only active protocols are candidates. The resolver uses task category, scope terms, and triggers; it never loads every protocol into context. It orders applicable protocols by stored priority. Opposing directives with equal priority block the plan. A lower-priority conflicting protocol is recorded in `protocol_overrides` and omitted from the applied set. Requirements that exceed the existing permission policy block the plan. Because preconditions lack a reliable semantic evaluator, a matching protocol with preconditions also blocks execution instead of silently assuming they are satisfied.

The applicable protocol summary is included in bounded reasoning context. Its instructions remain subordinate to system instructions and the existing permission policy. Protocol procedures are attached as ordered plan steps tagged with the exact protocol ID and version.

## Compliance

The orchestrator records per-procedure status and verification state and persists a report for each attached protocol. Status values are `COMPLIANT`, `PARTIALLY_COMPLIANT`, `NON_COMPLIANT`, `VERIFICATION_UNAVAILABLE`, and `BLOCKED`. In the current AEGIS build, most protocol procedures have no corresponding registered executor, so they remain incomplete and the report is partial. Structural tool result checks do not establish semantic completion. If all procedures succeed but protocol-level verification criteria cannot be independently evaluated, the report says `VERIFICATION_UNAVAILABLE`.

## Capability classification

| Capability | Status | Limit |
| --- | --- | --- |
| Structured protocol records and schema/security validation | WORKING | Custom category strings are accepted; complex semantic validation is unavailable. |
| Deterministic natural-language draft parsing | PARTIALLY WORKING | Handles explicit protocol requests, numbered procedures, and simple action lists; ambiguous inputs need clarification. |
| SQLite persistence and immutable version snapshots | WORKING | Revisions use new version-instance IDs linked to a parent; history follows lineage. |
| Approval and lifecycle management | WORKING | Requires loopback access and explicit confirmation for lifecycle changes. No remote account authentication exists. |
| Scope/category/trigger resolution and priority | PARTIALLY WORKING | Keyword matching and simple directive-conflict detection; nuanced semantic relevance is not guaranteed. |
| Permission-aware resolution | WORKING | Refuses declared permission requirements above the current policy; protocols cannot grant permissions. |
| Reasoning-plan attachment and procedure tracking | PARTIALLY WORKING | Procedures appear as plan steps but have no specialist execution handlers. |
| Compliance persistence and status reporting | WORKING | Reports attempted/verified structure; semantic verification remains unavailable. |
| Automatic model-backed protocol parsing | NOT IMPLEMENTED | No real model-based protocol extraction or conflict reasoning is claimed. |
