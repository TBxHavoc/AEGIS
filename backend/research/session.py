"""Research sessions over local indexed sources; no network access by default."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from backend.knowledge.index import KnowledgeIndex


@dataclass(slots=True)
class ResearchSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""
    history: list[dict[str, object]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class ResearchService:
    def __init__(self, index: KnowledgeIndex) -> None:
        self.index = index
        self.sessions: dict[str, ResearchSession] = {}

    def start(self, query: str) -> ResearchSession:
        if not query.strip():
            raise ValueError("research query must be non-empty")
        session = ResearchSession(query=query)
        results = self.index.search(query)
        session.history.append({"query": query, "sources": [result.source for result in results], "results": len(results)})
        self.sessions[session.session_id] = session
        return session

