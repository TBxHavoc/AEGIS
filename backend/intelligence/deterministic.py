"""Transparent deterministic helpers, not an LLM or reasoning claim."""
from __future__ import annotations

from backend.conversation.models import Message
from backend.intelligence.contracts import Intent, Plan


class DeterministicIntentDetector:
    source = "deterministic-heuristic"

    def detect(self, message: str) -> Intent:
        lowered = message.strip().lower()
        if lowered.endswith("?"):
            name = "question"
        elif lowered.startswith(("please ", "can you ", "could you ")):
            name = "request"
        else:
            name = "statement"
        return Intent(name, 0.5, self.source)


class DeterministicContextInterpreter:
    source = "deterministic-context-window"

    def interpret(self, messages: tuple[Message, ...]) -> dict[str, object]:
        return {"message_count": len(messages), "roles": tuple(message.role for message in messages), "source": self.source}


class UnavailableReasoner:
    def reason(self, context: dict[str, object]) -> Plan:
        return Plan((), "inference-unavailable")

