# Next steps

Work in this order. Phase 9 must remain unstarted until Phase 8.1's exit criteria are reviewed and met.

## 1. Establish Phase 8.1 security baseline

Inspect current integrity, audit, auth/session, data-storage and tool execution behavior; turn the known gaps in [PROJECT_STATUS.md](PROJECT_STATUS.md) into a short design with threat assumptions and migration/recovery cases.

**Acceptance criteria:** design identifies assets, trust boundaries, failure modes and recovery; identifies which controls are prerequisites versus deferred; preserves local-only, deny-by-default behavior; and is reviewed before implementation.

## 2. Persist trusted code and last-known-good release state

Implement a trusted baseline that survives restart and can only be updated through an explicit reviewed operation. Define how release identity and source hashes are validated at launch.

**Acceptance criteria:** restart detects an unauthorized change; an authorized baseline update is attributable and auditable; failed or missing state fails safely with useful recovery guidance; tests use temporary repositories/data and leave host files untouched.

## 3. Add reviewed activation and rollback

Design atomic activation of reviewed code with a last-known-good fallback. Keep recovery distinct from ordinary tool execution and require explicit local confirmation.

**Acceptance criteria:** interrupted activation leaves either the previous or complete new version active; verification failure restores/retains the previous known-good version; rollback is auditable and documented; disposable integration tests cover failure points.

## 4. Harden credentials, sessions and stored data

Choose standard platform/library mechanisms for secret storage and encryption; specify key lifecycle, backups and loss/revocation behavior. Replace or augment the shared bearer token with managed expiry/revocation while retaining a simple safe local setup.

**Acceptance criteria:** secrets are not logged or exposed in status; sessions expire and can be revoked; stored sensitive data has a documented migration and recovery path; key loss does not silently destroy the only recoverable copy; tests verify redaction and revocation.

## 5. Enforce isolation for risky work and close Phase 8.1

Move eligible tool/specialist execution behind an OS process boundary with bounded filesystem/network/resource access. Disable capabilities on systems where required isolation is unavailable.

**Acceptance criteria:** isolation limits are tested on the target OS; unavailable isolation fails closed for dependent operations; cancellation and resource ceilings are effective; documented recovery and security regression checks pass. Review all Phase 8.1 roadmap exit criteria before marking the prerequisite complete.

## 6. Start Phase 9 only after the gate

Define persona/preferences and user-controlled memory lifecycle; add evaluation coverage for model-backed and deterministic fallback behavior.

**Acceptance criteria:** written Phase 9 scope is approved internally, tests cover configuration/memory controls and policy boundaries, and measured resource budgets fit the 4 GB CPU-only target. This work has not started.

## 7. Continue through Phases 10 and 11

For Phase 10, specify and gate each environment/device capability, then add inspectable approvals, cancellation and recovery. For Phase 11, complete defensive cybersecurity scope, release security/privacy review, packaging and compatibility validation.

**Acceptance criteria:** every external action is explicitly permissioned and revocable; integrations are tested against simulated providers and clearly labeled; release steps are reproducible; final test/security evidence and known limitations are published. See [MASTER_ROADMAP.md](MASTER_ROADMAP.md).

## For each implementation task

- Keep changes within the approved phase and update the relevant architecture/security docs.
- Preserve existing project history and use temporary test data for security/recovery checks.
- Run the documented focused/full verification appropriate to the change; do not claim unexercised providers or host controls as verified.
- Keep model, voice and network dependencies optional, and measure memory/CPU use on the documented hardware.
