# Threat model

## Assets

Host files and services, AEGIS source/configuration, user-approved files, SQLite stores, credentials, audit history, microphone/playback state, and scheduled work.

## Threat actors and failures

The model may return malicious instructions or mistaken tool arguments; a specialist may loop or exceed resources; an external document or MCP/web result may contain hostile content; a local HTTP origin may attempt cross-site requests; files may contain symlinks or traversal paths; process crashes may leave databases incomplete; an unprivileged AEGIS process may lack permission to inspect firewall state.

## Controls

The model cannot alter the deterministic policy. Tools are allowlisted, schema-validated, rate-limited and bounded. File tools require configured roots and reject protected names and symlink traversal. Test execution runs only in the Phase 7 bubblewrap snapshot. External network providers remain opt-in. API origin checks, optional bearer authentication, loopback-only default bind, request bounds and remote mutation denial reduce exposure. Safe mode, integrity checks, audit chaining and conservative SQLite backup/health checks provide containment and evidence.

## Assumptions and residual risk

The application runs as the user's OS account and cannot defend against that account, root, kernel compromise, full-process memory inspection, disk rollback, or an attacker who can replace the program and its local hash baseline before startup. SHA-256 audit chaining is not keyed. API access uses a shared bearer token stored at mode 0600 in `data/api.token` unless provided through `AEGIS_API_TOKEN`; possession of the OS account grants access to it. The browser stores the bearer token in tab-scoped storage; per-user server-issued/revocable sessions are not implemented. Third-party MCP/web endpoints and DNS/network behavior remain external trust boundaries. Data-at-rest encryption and OS keyring support are not implemented. Firewall state is not verified if UFW denies read permission. No claim of malware detection or complete security is made.
