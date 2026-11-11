# AEGIS 2.0

AEGIS 2.0 is a low-resource local-first personal-agent foundation with a minimal voice-first interface, dependency-free HTTP/WebSocket transport, bounded central orchestration, optional llama.cpp/GGUF inference, privacy-aware SQLite memory, specialized agent boundaries, safe tools, security guardrails, and local knowledge retrieval.

## Run

```sh
/usr/bin/python3 main.py
```

Open <http://127.0.0.1:8765/>. The interface is voice-first: the microphone control requests browser permission only after explicit activation, while text input appears when microphone mode is disabled. Inference and voice output remain unconfigured until providers/models are deliberately installed; the backend reports those states instead of fabricating responses. Conversation persistence is disabled by default.

## Validate

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

Step 4 adds bounded agent tasks, a deny-by-default tool registry, audit/security guardrails, local knowledge retrieval, research sessions, and a disabled-by-default desktop boundary. Phase 5 adds the central brain, optional llama.cpp integration, resource monitoring, evaluation records, and safe document/time tools. This phase adds approval-gated protocol management, interaction traces, a model catalog, and bounded scheduling. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INTELLIGENCE_CORE.md](docs/INTELLIGENCE_CORE.md), [docs/AGENT_ORCHESTRATION.md](docs/AGENT_ORCHESTRATION.md), [docs/LOCAL_MODEL_INTEGRATION.md](docs/LOCAL_MODEL_INTEGRATION.md), [docs/REASONING_AND_PLANNING.md](docs/REASONING_AND_PLANNING.md), [docs/LEARNING_SYSTEM.md](docs/LEARNING_SYSTEM.md), [docs/TRACE_LEARNING.md](docs/TRACE_LEARNING.md), [docs/PROTOCOL_INTELLIGENCE.md](docs/PROTOCOL_INTELLIGENCE.md), [docs/SCHEDULER.md](docs/SCHEDULER.md), [docs/TOOL_EXECUTION.md](docs/TOOL_EXECUTION.md), [docs/RESOURCE_LIMITS.md](docs/RESOURCE_LIMITS.md), [docs/AGENT_SYSTEM.md](docs/AGENT_SYSTEM.md), [docs/TOOL_REGISTRY.md](docs/TOOL_REGISTRY.md), [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md), [docs/KNOWLEDGE_SYSTEM.md](docs/KNOWLEDGE_SYSTEM.md), [docs/DESKTOP_AUTOMATION.md](docs/DESKTOP_AUTOMATION.md), [docs/CONVERSATION.md](docs/CONVERSATION.md), [docs/INFERENCE.md](docs/INFERENCE.md), [docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md), [docs/VOICE_SYSTEM.md](docs/VOICE_SYSTEM.md), [docs/VOICE_BEHAVIOUR.md](docs/VOICE_BEHAVIOUR.md), [docs/VOICE_TESTING.md](docs/VOICE_TESTING.md), [docs/OPENJARVIS_INTEGRATION.md](docs/OPENJARVIS_INTEGRATION.md), [docs/OPENJARVIS_PHASE6.md](docs/OPENJARVIS_PHASE6.md), [docs/WEBSOCKET_PROTOCOL.md](docs/WEBSOCKET_PROTOCOL.md), [docs/TESTING.md](docs/TESTING.md), and [docs/ROADMAP.md](docs/ROADMAP.md).

<img width="3925" height="3006" alt="01-system-overview" src="https://github.com/user-attachments/assets/78d8d0d5-8c23-468d-8428-3cb19a4404e5" />
<img width="4933" height="2629" alt="02-component-detail" src="https://github.com/user-attachments/assets/19168f93-99ac-4e51-8480-bd2eb197f943" />
<img width="5192" height="6296" alt="03-request-flow" src="https://github.com/user-attachments/assets/40aecc34-a25f-480d-9713-69f421ce241b" />
<img width="5304" height="1867" alt="04-security-flow" src="https://github.com/user-attachments/assets/487c0fb0-00d2-4bf9-8303-fec55b9fb4f0" />
