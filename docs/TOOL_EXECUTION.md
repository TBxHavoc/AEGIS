# Tool Execution

The registry is the sole execution path for local and external tools. It validates bounded input/output schemas, checks agent and tool allowlists, enforces permission and confirmation requirements, limits worker concurrency, observes cancellation, applies per-tool timeouts, and records audited outcomes. Timeout does not free capacity while a Python handler is still running. File operations, system diagnostics, data analysis, test execution, web research, and approved MCP tools are described in [TOOL_ARCHITECTURE.md](TOOL_ARCHITECTURE.md). There is no unrestricted shell tool.
