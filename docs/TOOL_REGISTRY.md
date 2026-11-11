# Tool registry

`ToolRegistry` is the single action execution boundary. `ToolSpec` identifies a versioned capability, schemas, permission, risk, availability, timeout, confirmation gate, and handler. Discovery is `GET /api/tools`; `POST /api/tools/{id}` executes a registered tool. No agent, model, or integration invokes a registered handler outside the registry.

The registry validates bounded inputs and outputs, enforces the configured allowlist and permission policy, limits concurrency, observes cancellation, preserves capacity after non-cooperative timeouts, and records audited outcomes. Agents receive only their declared capabilities and use a scoped callback. MCP tools are dynamically discovered only after explicit local connection approval and are still registered here.

Default policy is read-only with network disabled. See [TOOL_ARCHITECTURE.md](TOOL_ARCHITECTURE.md) for config and the current catalog, and [SECURITY_MODEL.md](SECURITY_MODEL.md) for permission semantics.
