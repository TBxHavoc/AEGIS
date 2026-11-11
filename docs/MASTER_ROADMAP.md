# Master roadmap

This roadmap consolidates the remaining work after Phase 8. It preserves the phase sequence: finish Phase 8.1 security hardening before starting Phase 9. Phases 9–11 are planned and **have not started**. Existing project history remains in [ROADMAP.md](ROADMAP.md).

## Prerequisite — Phase 8.1: security hardening

Close the deployment and recovery gaps identified in [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md) and [INTEGRITY_AND_RECOVERY.md](INTEGRITY_AND_RECOVERY.md).

- Persist a trusted code baseline and a last-known-good release identity across restarts; define how the baseline is established and updated through a reviewed operation.
- Design and implement a reviewed code-change activation path with integrity verification, atomic activation, rollback, and an auditable recovery procedure.
- Add server-managed session identity, expiry and revocation instead of relying solely on one shared browser token. Preserve local-only defaults.
- Select a platform keyring/encryption approach for secrets and sensitive persisted data; document migration, backup and key-loss behavior. Do not introduce custom cryptography.
- Isolate risky tools and specialist work at the process/OS boundary where supported; make unsupported isolation explicit and fail closed for operations that require it.
- Add security regression coverage and verify recovery on disposable data. Review privileged operations without changing host firewall policy as part of normal tests.

**Exit criteria:** the trusted baseline survives restart; reviewed activation either commits fully or restores the previous known-good version; credentials/sessions can be revoked; protected data has a documented and tested encryption lifecycle; risky execution has enforced isolation or is unavailable; recovery and audit evidence pass documented checks. Record any platform-specific exclusions before Phase 9.

## Phase 9 — Intelligence and personality

Build on the optional local inference engine and bounded memory only after Phase 8.1's exit gate.

- Define a stable AEGIS persona, tone, user preferences, and boundaries as explicit configuration with deterministic defaults.
- Improve memory with user-controlled inspection, correction, deletion, retention and provenance; keep retrieved memory bounded and distinguish it from verified facts.
- Improve request understanding, planning and result validation through provider-independent interfaces. Preserve deterministic fallbacks and label uncertainty when no model is available.
- Evaluate model-backed responses and plans with a small repeatable local evaluation set, including refusal/policy adherence, factual calibration, cancellation, latency and memory use.
- Support the constrained CPU/RAM target with measured model/context profiles; avoid assuming GPU support or remote services.

**Exit criteria:** persona/preferences are configurable and covered by tests; memory lifecycle controls work and are auditable; model and fallback paths are clearly distinguishable; evaluation results and resource measurements are recorded for supported hardware; no model output bypasses permissions or tool policy.

## Phase 10 — Environment and autonomy

Expand interaction with the user's environment only through narrow, visible and permissioned integrations.

- Specify supported device/environment providers and capability contracts before implementing a provider. Keep disconnected state safe and useful.
- Add user-visible autonomy levels, task previews, approval boundaries, cancellation, time/resource limits, and durable audit records for scheduled or multi-step work.
- Improve task state and recovery so interrupted workflows can be inspected, resumed or discarded safely; require renewed confirmation for consequential actions.
- Add integration health, least-privilege credentials and revocation; external services remain opt-in and their returned content is untrusted.
- Validate behavior with simulated providers and, where available, explicitly configured test devices; document what was actually exercised.

**Exit criteria:** each environment action maps to a declared capability and policy; users can inspect, approve, cancel and revoke; restart/recovery behavior is tested; no unattended consequential action occurs without a clearly configured policy and visible audit trail.

## Phase 11 — Cybersecurity and final release

Complete defensive security features and release readiness while retaining the project's authorization and least-privilege boundaries.

- Define supported defensive cybersecurity use cases, authorization assumptions and prohibited actions in a reviewed threat model.
- Add defensive asset/configuration checks and explainable findings only through approved local or explicitly authorized sources; include evidence and confidence.
- Complete security review, dependency/license inventory, data-flow and privacy review, backup/recovery review, and release threat-model update.
- Prepare versioning, migration/rollback guidance, packaging, installation and operational documentation, compatibility matrix and release checklist.
- Run the documented suite and security regression suite on supported environments; publish a clear list of real versus simulated capabilities and known issues.

**Exit criteria:** release candidate passes documented tests and security review; supported installation/recovery steps are reproducible; data handling and permissions are documented; all integrations and model requirements are accurately labeled; unresolved high-severity issues block release.

## Hardware and sequencing constraints

The primary target is Ubuntu 26.04.1 LTS on an Intel Pentium Gold 4415U with 4 GB RAM and integrated graphics. Prefer CPU-only, low-concurrency paths, bounded contexts and small workloads. Measure resource use before recommending local models or always-on services. Sequence is **Phase 8.1 → Phase 9 → Phase 10 → Phase 11**; do not begin Phase 9 until Phase 8.1 exit criteria are reviewed and met.
