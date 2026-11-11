"""Knowledge index tools that ingest inert text through the existing index."""
from __future__ import annotations

import hashlib
from typing import Any

from backend.knowledge.index import KnowledgeIndex
from backend.permissions.policy import PermissionLevel
from backend.tools.registry import ToolRegistry, ToolSpec


def register_knowledge_tools(registry: ToolRegistry, index: KnowledgeIndex) -> None:
    def ingest(args: dict[str, Any]) -> dict[str, Any]:
        document = index.ingest_file(args["path"])
        digest = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        duplicates = []
        for item in index.inspect(200):
            if item.document_id != document.document_id and hashlib.sha256(item.content.encode("utf-8")).hexdigest() == digest:
                duplicates.append({"document_id": item.document_id, "source": item.source})
        return {"document_id": document.document_id, "source": document.source,
                "characters": len(document.content), "sha256": digest, "duplicates": duplicates}
    registry.register(ToolSpec("knowledge.ingest", "Index an approved inert text document and report exact content duplicates.",
        {"type": "object", "properties": {"path": {"type": "string", "maxLength": 2048}},
         "required": ["path"], "additionalProperties": False}, {"type": "object"},
        PermissionLevel.WRITE_LOCAL, 5, ingest, risk="moderate", requires_confirmation=True))
