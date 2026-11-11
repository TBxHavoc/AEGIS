# Agent Orchestration (Phase 6)

`CentralBrain` routes relevant requests to the bounded `MultiAgentOrchestrator`. The registry contains nine deterministic specialist workflows; tasks use validated messages, bounded context, dependency checks, approved protocols, existing tool permissions, and independent structural result verification. Typed and voice requests share this route.

These are lightweight workflows, not separate model instances. The existing shared model, when configured, writes the final response. Current limits and capability boundaries are documented in [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md), [SPECIALIST_AGENTS.md](SPECIALIST_AGENTS.md), and [MULTI_AGENT_ORCHESTRATION.md](MULTI_AGENT_ORCHESTRATION.md). There are no unrestricted autonomous loops.
