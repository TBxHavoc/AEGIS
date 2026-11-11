# Agent system

`AgentManager` provides finite tasks for the main assistant, research, coding, productivity, data analysis, system assistance, and defensive cybersecurity roles. Tasks have IDs, routed agent types, status, result/error fields, timeouts, cancellation events, and audit records.

The legacy `AgentManager` remains a finite compatibility task runner. Phase 6 adds nine deterministic specialist workflows, and Phase 7 connects their approved capabilities through the common registry. There is no unrestricted autonomous loop, self-replication, privileged escalation, or implicit tool access. See [SPECIALIST_AGENTS.md](SPECIALIST_AGENTS.md) for current capability boundaries.

