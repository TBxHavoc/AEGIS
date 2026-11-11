# Security architecture (Phase 8)

AEGIS security decisions are deterministic and outside model inference. The tool registry applies the policy allowlist, maximum permission, confirmation, bounded schema, invocation rate, concurrency, timeout, cancellation and safe-mode checks on every registered tool call. Specialists also have scoped capabilities and bounded concurrency. A model response is never accepted as the security decision itself.

`backend/security/` contains shared protected-path, safe-mode, integrity, firewall, resource-monitor, SQLite health/backup, and component registry services. API routes expose compact status, firewall inspection/proposals, recent audit events, safe mode and recovery. Same-origin validation, request size bounds, rate limiting and remote-bind checks are applied at the HTTP layer.

The shared typed/voice `CentralBrain` recognizes “run a security check”, “check my firewall”, “show me suspicious activity”, “check AEGIS integrity”, “enter safe mode”, and “show recent security events”. These requests route to deterministic local checks; the model does not authorize or invent security state. Safe-mode voice output is suppressed after capture/playback shutdown.

## Runtime behavior

- Safe mode blocks mutations, code execution and network tools, rejects new HTTP and WebSocket work, cancels agents/tools, pauses scheduled tasks, stops microphone capture and playback, and disconnects MCP. Read-only local status remains available. Recovery requires a configured API bearer token plus explicit confirmation, and the audit and source integrity checks must pass.
- A low-cost 30-second watchdog checks security source hashes, audit chain integrity, SQLite quick checks and AEGIS process resident memory. On a detected mismatch or check failure it records evidence and enters safe mode. The monitor does not identify malware or kill arbitrary host processes.
- The scheduler restores durable jobs paused. Agent roots, tools, schemas, timeouts and concurrency are bounded. Python thread cancellation is cooperative; a handler that ignores cancellation can continue until its own process/runtime timeout and retains its worker slot.
- Audit records redact common token/key/password forms, use mode 0600 files in a mode 0700 directory when supported, fsync each append, and form a SHA-256 chain. This detects edits but is not a signature or protection against a privileged attacker who can rewrite the entire file. Legacy pre-chain entries are reported as legacy, not authenticated.

## API configuration

By default the service binds to `127.0.0.1` and creates a random API token in `data/api.token` with mode 0600 (the parent directory is restricted to mode 0700 where supported). Read it locally with `cat data/api.token`; the public HTML shell prompts for it and the browser keeps it in tab-scoped `sessionStorage`. Alternatively set `AEGIS_API_TOKEN` to a URL-safe 32-256 character token. All API and WebSocket requests require `Authorization: Bearer <token>` (WebSockets use the `bearer.<token>` subprotocol). Sensitive operations also require explicit confirmation. Remote binding is refused unless both `AEGIS_ALLOW_REMOTE=1` and an environment token are present; remote state changes remain denied by this API. A trusted TLS proxy and network policy are required before considering remote read access. Never put the token in logs or source control.

The server does not issue/revoke user sessions. The local token is a shared single-user credential, not per-user identity. Loopback binding alone is not treated as authentication.

## Limits

There is no OS keyring integration or encryption at rest in this phase. No custom cryptography is used. Firewall reads can require root and then report `permission_denied`; AEGIS does not elevate itself. Safe mode is an application restriction, not a host firewall or process sandbox. See the focused documents in this directory and [THREAT_MODEL.md](THREAT_MODEL.md).
