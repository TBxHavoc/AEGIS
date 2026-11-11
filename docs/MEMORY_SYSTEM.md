# Memory system

`MemoryStore` uses local SQLite and separates `conversation_history`, `short_term_context`, `long_term_personal`, `explicit`, and `retrieved_knowledge`. It supports store, retrieve, search, update, delete, inspect, expiry, category filtering, and explicit-retention controls.

Conversation history requires explicit retention consent when written to the memory store. The conversation engine keeps a bounded in-process context and does not persist it by default. Content matching common credential/private-key patterns is rejected; credentials and secrets are not stored. The API surface is intentionally user-controlled so later UI controls can inspect or delete records.

