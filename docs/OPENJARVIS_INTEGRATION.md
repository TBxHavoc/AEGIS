# OpenJarvis research and integration boundary

Research sources reviewed:

- [OpenJarvis repository](https://github.com/open-jarvis/OpenJarvis)
- [OpenJarvis documentation](https://open-jarvis.github.io/OpenJarvis/)
- [Architecture overview](https://open-jarvis.github.io/OpenJarvis/architecture/overview/)
- [Intelligence](https://open-jarvis.github.io/OpenJarvis/architecture/intelligence/)
- [Engine](https://open-jarvis.github.io/OpenJarvis/architecture/engine/)
- [Tools and memory](https://open-jarvis.github.io/OpenJarvis/architecture/memory/)
- [Learning](https://open-jarvis.github.io/OpenJarvis/architecture/learning/)
- [Security](https://open-jarvis.github.io/OpenJarvis/architecture/security/)

## Findings

OpenJarvis presents a local-first personal-AI stack with shared primitives, interchangeable engines, agents, tools, memory, evaluation/telemetry, and a learning loop. Its documented source layout uses registries and separate packages for engine, intelligence, agents, tools/storage, learning, security, and server concerns. The architecture describes a thread-safe synchronous EventBus as connective tissue; its engine layer discovers healthy providers and lists models, while its OpenAI-compatible adapters support streaming and health checks. Its memory documentation includes SQLite/FTS5 persistent sparse retrieval and optional FAISS/ColBERT/BM25/hybrid backends. Its learning layer stores interaction traces and can update routing policies. Its security layer composes scanners around inference and supports warn/redact/block modes with audit records.

## Reusable ideas adopted

- Provider-independent inference contracts and local-first routing.
- Explicit registries/boundaries instead of a monolithic assistant class.
- SQLite as the low-resource persistence baseline.
- Request/event identifiers, bounded history, and composable security boundaries.

## Deliberately not copied

No OpenJarvis source code, dependency, model, installer, or dataset was downloaded or reused. AEGIS currently has no OpenJarvis runtime dependency. The repository identifies its license as Apache-2.0; if future AEGIS code copies any source, the relevant copyright notices and license text must be preserved and reviewed before merging.

