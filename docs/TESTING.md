# Testing

Run:

```sh
/usr/bin/python3 -W error::ResourceWarning -m unittest discover -s tests -v
/usr/bin/python3 -m compileall -q backend main.py tests
```

Tests are standard-library unit and loopback integration tests. Phase 7 coverage includes approved-root and symlink file controls, atomic backed-up replacement, tool schemas/limits/timeouts/cancellation, read-only system diagnostics, bounded CSV/JSON transforms and charts, knowledge ingestion, a real local bubblewrap sandbox with network denial and read-only snapshots, a loopback mock MCP server, and persistent scheduler restore/approval. The MCP server is mocked; external MCP services are not configured. Web parsing and network-policy gates are tested, but no live web search is claimed. Existing tests cover unavailable inference/voice, agent cancellation, protocol resolution, audit redaction, knowledge retrieval, and desktop denial. Real audio synthesis, microphone capture, and model inference remain untested because no providers/models are configured.

Phase 8 adds temporary-directory tests for protected path traversal and symlinks, secret redaction, hash-chain tampering, integrity changes, safe-mode recovery gates, firewall proposal validation and approval (with a mocked command runner), consistent non-overwriting SQLite backups, deterministic voice/text security command routing, and bearer authentication for REST/WebSocket requests. Tests do not change host firewall rules. Firewall inspection was performed read-only; this environment denied UFW status without root.

