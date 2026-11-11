# Integrity and recovery

At startup AEGIS records SHA-256 baselines for backend Python source files. The lightweight watchdog checks these hashes, the audit chain, SQLite `quick_check` for known stores, and process RSS every 30 seconds. A mismatch or failed check records a security event and enters safe mode. This baseline is in memory: it detects changes after startup, not a coordinated source replacement before launch.

To create a consistent DB backup, use the confirmed local `/api/security/backup` endpoint with a source under the configured AEGIS data directory. Each backup has a unique name and is not overwritten. AEGIS verifies it with `quick_check` and returns its SHA-256 digest. On corruption, the monitor preserves the original and enters safe mode. Stop AEGIS, copy the damaged file aside, verify a backup, then restore it manually; do not overwrite the only copy. AEGIS currently has no automatic restore operation or persisted last-known-good code release.

Safe-mode recovery is explicit and requires the configured API token. Recovery is refused while source hashes or audit-chain verification fail. Review recent events and repair/restore the affected files first. Recovery does not resolve the underlying incident automatically.
