# Specialist agents

The registry exposes nine bounded specialists. They are deterministic workflows, not nine independent AI models. The shared configured inference provider may synthesize their outputs through `CentralBrain`.

| Agent | Current capability | Current boundary |
| --- | --- | --- |
| Research | Searches approved local knowledge and, when configured and authorized, web search plus one bounded page retrieval with source URLs. | Web content is untrusted; source quality and facts are not independently verified. |
| Coding | Reviews supplied code or reads an explicitly named file under an approved root; syntax/test tools are available through the registry. | Does not modify files. Test execution requires explicit local confirmation, project-root configuration, and bubblewrap. |
| Defensive cybersecurity | Applies bounded text heuristics to a user-provided configuration or log excerpt. | No scanning, network access, or system inspection. |
| Productivity | Turns a request into a short draft task list and priorities. | Does not access calendars, reminders, or external services. |
| Business | Produces a structured planning outline and labels assumptions. | No live market data or financial advice/modeling engine. |
| Data analysis | Uses registered tools for CSV/JSON inspection, numeric summaries, filtering/group counts, endpoint trend summaries, and SVG bar charts. | In-memory inputs only; bounded row/column/size limits; no arbitrary code or automatic source modification. |
| System assistance | Calls read-only AEGIS status and host diagnostics for OS, CPU, memory, disk, process samples, interfaces, and uptime. | Does not inspect services through systemctl or perform changes. |
| Protocol management | Retrieves active protocols and can prepare a proposed protocol draft through existing parser/registry checks. | Drafts require local authorization and never self-approve or activate. |
| Knowledge management | Retrieves indexed knowledge and finds/lists files only under approved roots; confirmed local ingestion is available through the knowledge tool. | No automatic bulk indexing; retrieved text remains untrusted reference data. |

Each agent has a versioned `AgentSpec`, capabilities, required permission level, and configured tool allowlist. The orchestrator supplies the live schemas for that allowlist to the task context. Disable controls remove an agent from selection. Local administration can narrow access; granting a new capability requires explicit confirmation and that tool must already be globally allowlisted. All actual tool calls use `ToolRegistry`, `PermissionPolicy`, input validation, execution timeouts, and audit logging.

An agent result is structured and carries a verification status. Missing evidence is surfaced as a warning; contradictory findings lower the run verification status. The verifier does not perform independent fact checking or execute coding/data results. See [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md).
