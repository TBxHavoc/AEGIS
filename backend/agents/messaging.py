"""Bounded, identity-checked internal message transport."""
from __future__ import annotations

from collections import deque
from threading import RLock

from backend.agents.models import AgentMessage
from backend.agents.registry import AgentRegistry


class AgentMessageBus:
    def __init__(self, registry: AgentRegistry, max_messages_per_request: int = 64) -> None:
        self.registry = registry
        self.max_messages_per_request = max_messages_per_request
        self._messages: deque[AgentMessage] = deque(maxlen=2048)
        self._counts: dict[str, int] = {}
        self._seen: set[tuple[str, str, str]] = set()
        self._request_order: deque[str] = deque()
        self._lock = RLock()

    def send(self, message: AgentMessage) -> None:
        if message.sender_agent_id != "central_brain" and self.registry.get(message.sender_agent_id) is None:
            raise ValueError("message sender is not a registered agent")
        if message.recipient_agent_id != "central_brain" and self.registry.get(message.recipient_agent_id) is None:
            raise ValueError("message recipient is not a registered agent")
        key = (message.root_request_id, message.sender_agent_id, message.task_id)
        with self._lock:
            count = self._counts.get(message.root_request_id, 0)
            if count >= self.max_messages_per_request:
                raise ValueError("agent message budget exceeded")
            if key in self._seen and message.message_type in {"task.assignment", "agent.request"}:
                raise ValueError("duplicate or circular agent request")
            self._seen.add(key)
            if message.root_request_id not in self._counts:
                self._request_order.append(message.root_request_id)
            self._counts[message.root_request_id] = count + 1
            self._messages.append(message)
            while len(self._request_order) > 512:
                expired = self._request_order.popleft()
                self._counts.pop(expired, None)
                self._seen = {item for item in self._seen if item[0] != expired}

    def for_request(self, root_request_id: str) -> tuple[AgentMessage, ...]:
        with self._lock:
            return tuple(item for item in self._messages if item.root_request_id == root_request_id)

    def inbox(self, root_request_id: str, recipient_agent_id: str) -> tuple[AgentMessage, ...]:
        with self._lock:
            return tuple(item for item in self._messages
                         if item.root_request_id == root_request_id
                         and item.recipient_agent_id == recipient_agent_id)


def validate_dependencies(dependencies: dict[str, tuple[str, ...]]) -> None:
    if len(dependencies) > 9:
        raise ValueError("agent plan exceeds the nine-specialist limit")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("circular agent dependency detected")
        if node in visited:
            return
        if node not in dependencies:
            raise ValueError("agent dependency references an unknown task")
        visiting.add(node)
        for dependency in dependencies[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for item in dependencies:
        visit(item)
