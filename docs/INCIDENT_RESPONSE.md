# Incident response

1. Read `GET /api/security/status` and `/api/security/events`; preserve returned details and relevant source/database files.
2. If a task or component is misbehaving, enter safe mode with `POST /api/security/safe-mode`. This cancels agent/tool work, pauses scheduled tasks, stops audio/microphone activity and disconnects MCP.
3. Review source integrity, audit integrity, database health, resource values, firewall status, and configured integrations. Do not infer malware from a listener or resource spike alone.
4. Preserve corrupted data before recovery. Create/verify a new SQLite backup where possible; AEGIS never deletes the original on a health failure.
5. Correct the cause using normal OS administration. Firewall proposals are reviewed and applied separately; no automatic host changes occur.
6. Recover from safe mode only after integrity checks pass, with the local API token and explicit confirmation. Recovery is audited.

The emergency route is deterministic and does not call the LLM. If the API process itself is unresponsive, use the operating system service/process manager to stop it; AEGIS cannot shut down a hung or compromised kernel/process independently.
