"""Bounded central request lifecycle for the AEGIS assistant."""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from backend.conversation.engine import ConversationEngine
from backend.conversation.models import StreamEvent
from backend.intelligence.deterministic import DeterministicIntentDetector
from backend.memory.store import MemoryCategory, MemoryStore
from backend.security.audit import AuditLogger
from backend.tools.registry import ToolRegistry, ToolResult
from backend.learning.traces import TraceStore


@dataclass(frozen=True, slots=True)
class BrainRequest:
    session_id: str
    request_id: str
    text: str
    source: str = "text"
    local_client: bool = False


@dataclass(frozen=True, slots=True)
class BrainPreparation:
    intent: str
    confidence: float
    memory_ids: tuple[str, ...]
    selected_tool: str | None
    memory_context: tuple[str, ...] = ()


class CentralBrain:
    """Coordinates stages while leaving actual language generation to a provider."""

    def __init__(self, conversations: ConversationEngine, memory: MemoryStore,
                 tools: ToolRegistry, audit: AuditLogger, traces: TraceStore | None = None,
                 agents: object | None = None, security_command: object | None = None) -> None:
        self.conversations = conversations
        self.memory = memory
        self.tools = tools
        self.audit = audit
        self.intent = DeterministicIntentDetector()
        self.traces = traces
        self.agents = agents
        self.security_command = security_command

    def prepare(self, request: BrainRequest) -> BrainPreparation:
        detected = self.intent.detect(request.text)
        query = self._memory_query(request.text)
        memories = []
        if query:
            for category in (MemoryCategory.EXPLICIT, MemoryCategory.LONG_TERM_PERSONAL,
                             MemoryCategory.SHORT_TERM_CONTEXT, MemoryCategory.RETRIEVED_KNOWLEDGE):
                memories.extend(self.memory.search(query, category=category, limit=5))
        memories.sort(key=lambda item: item.created_at, reverse=True)
        memories = memories[:5]
        selected = self._select_tool(request.text)
        preparation = BrainPreparation(detected.name, detected.confidence,
                                       tuple(item.memory_id for item in memories), selected,
                                       tuple(item.content for item in memories))
        self.audit.record("brain.prepare", actor="assistant", outcome="ok", details={
            "request_id": request.request_id, "intent": detected.name,
            "memory_count": len(memories), "tool": selected,
        })
        return preparation

    def stream(self, request: BrainRequest) -> Iterator[StreamEvent]:
        if self.security_command is not None:
            response = self.security_command(request.text)
            if response is not None:
                self.audit.record("security.voice_or_text_command", actor="user", outcome="handled_deterministically",
                                  details={"request_id": request.request_id, "command": request.text[:120]})
                yield StreamEvent("conversation.response.started", request.request_id,
                                  {"session_id": request.session_id, "phase": "security_check"})
                yield StreamEvent("conversation.response.completed", request.request_id,
                                  {"text": response[0], "security_command": True,
                                   "suppress_voice": response[1]})
                return
        preparation = self.prepare(request)
        agent_run = None
        memories = preparation.memory_context
        agent_context = ""
        delegated = self.agents is not None and self.agents.should_delegate(request.text)
        if delegated:
            yield StreamEvent("conversation.response.started", request.request_id,
                              {"session_id": request.session_id, "phase": "specialist_orchestration"})
        if delegated:
            history = self.conversations.history(request.session_id) if request.session_id else []
            recent = tuple({"role": item.role, "content": item.content[:1000]} for item in history[-2:])
            memory_refs = tuple({"memory_id": identifier, "content": value[:500]}
                                for identifier, value in zip(preparation.memory_ids, preparation.memory_context))
            agent_run = self.agents.run(request.request_id, request.text, source=request.source,
                                        memories=memory_refs, recent_turns=recent,
                                        local_client=request.local_client)
            if agent_run is not None:
                if any(item.get("status") == "BLOCKED" for item in agent_run.protocol_compliance):
                    yield StreamEvent("conversation.response.failed", request.request_id,
                                      {"error": "protocol_blocked", "protocol_compliance": agent_run.protocol_compliance,
                                       "conflicts": agent_run.conflicts})
                    return
                agent_context = agent_run.context_excerpt()
        provider = self.conversations.provider
        model_id = provider.metadata.model_id if provider is not None else "unconfigured"
        trace_id = self.traces.start(request.text, model=model_id,
                                      payload={"source": request.source, "intent": preparation.intent,
                                              "memory_ids": preparation.memory_ids, "tool": preparation.selected_tool,
                                              "selected_agents": agent_run.selected_agents if agent_run else (),
                                              "agent_verification": agent_run.verification_status if agent_run else None}) if self.traces else None
        outcome = "failed"
        try:
            configuration = getattr(self.conversations.provider, "configuration", None)
            system_prompt = getattr(configuration, "system_prompt", None)
            for event in self.conversations.stream(request.session_id, request.request_id, request.text,
                                                   memories=memories, system_prompt=system_prompt,
                                                   agent_context=agent_context):
                if delegated and event.type == "conversation.response.started":
                    continue
                if event.type in {"conversation.response.completed", "conversation.response.failed"}:
                    outcome = "completed" if event.type.endswith("completed") else str(event.payload.get("error", "failed"))
                yield event
        finally:
            if trace_id and self.traces:
                self.traces.complete(trace_id, outcome, {"request_id": request.request_id})

    def execute_selected_tool(self, request: BrainRequest, *, confirmed: bool = False) -> ToolResult | None:
        name = self._select_tool(request.text)
        if name is None:
            return None
        return self.tools.execute(name, self._tool_arguments(name, request.text), actor="assistant", confirmed=confirmed)

    @staticmethod
    def _memory_query(text: str) -> str:
        stop_words = {"a", "an", "and", "are", "can", "could", "for", "how", "is", "of", "please", "the", "to", "what", "you"}
        terms = [term for term in re.findall(r"[\w-]+", text.lower()) if term not in stop_words]
        return " ".join(terms)

    @staticmethod
    def _select_tool(text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"\b(status|health|cpu|memory usage)\b", lowered):
            return "system.status"
        if re.search(r"\b(search|find|look up)\b", lowered):
            return "knowledge.search"
        return None

    @staticmethod
    def _tool_arguments(name: str, text: str) -> dict[str, object]:
        return {"query": text} if name == "knowledge.search" else {}
