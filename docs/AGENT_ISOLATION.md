# Agent isolation

Specialists remain in-process workflows, not separate OS identities or containers. They have per-agent tool allowlists, deterministic policy rechecks, one active root slot by default (at most two configured), bounded task context, a configured task timeout, per-agent tool rate limits, and cancellation propagation. The common tool registry limits worker count and tool run time. The code test runner uses bubblewrap, read-only source snapshots, disabled networking, resource limits, and a reduced environment when available.

Python thread cancellation is cooperative and cannot safely terminate arbitrary native code. Timeouts mark a run failed and propagate cancellation, but a non-cooperative thread may continue; its capacity remains occupied. No unrestricted shell or arbitrary script execution is available. Safe mode stops new agent requests and signals existing roots to cancel. No claim of OS-level isolation for general specialist handlers is made.
