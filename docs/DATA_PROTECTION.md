# Data protection

Audit writes redact common assignment-form API keys, bearer tokens, AWS access-key IDs, JWT-like strings and PEM private-key blocks before persistence. Database health uses SQLite `PRAGMA quick_check` in read-only mode. The confirmed backup endpoint uses SQLite's online backup API, writes a new mode-0600 file under `data/backups`, verifies it, and refuses to overwrite an existing destination. Recovery never deletes or repairs the source; preserve it and restore a verified backup manually.

Audit directory/file permissions are tightened to 0700/0600 when the platform permits. Audit records include a SHA-256 chain; historical pre-chain records are readable but unauthenticated. This is tamper-evident, not keyed tamper-proof storage.

Secrets in ordinary model memory or arbitrary database fields are not comprehensively classified. There is no keyring adapter, encrypted database, or encryption-at-rest claim in this release. Keep credentials in environment injection or a future OS-backed secret store, restrict OS account access, and do not place secrets in prompts or ordinary memory. SQLite transactions remain owned by each existing subsystem; backup snapshots are transactionally consistent through SQLite's backup API.
