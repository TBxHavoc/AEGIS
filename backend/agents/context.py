"""Small, explicit context packet handed to one specialist task."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.agents.models import AgentResult
from backend.agents.models import AgentMessage
from backend.tools.registry import ToolResult


@dataclass(frozen=True, slots=True)
class AgentContext:
    root_request_id: str
    user_request: str
    source: str
    recent_turns: tuple[dict[str, str], ...] = ()
    memories: tuple[dict[str, str], ...] = ()
    protocols: tuple[dict[str, Any], ...] = ()
    prior_results: tuple[AgentResult, ...] = ()
    local_client: bool = False
    invoke_tool: Callable[[str, dict[str, Any]], ToolResult] | None = None
    request_agent: Callable[[str, str], AgentResult | None] | None = None
    receive_messages: Callable[[], tuple[AgentMessage, ...]] | None = None
    available_tools: tuple[dict[str, Any], ...] = ()

    def as_data(self) -> dict[str, Any]:
        return {"source": self.source, "recent_turns": list(self.recent_turns),
                "memories": list(self.memories), "protocols": list(self.protocols),
                "prior_results": [item.as_dict() for item in self.prior_results],
                "available_tools": list(self.available_tools)}
