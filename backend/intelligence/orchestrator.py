"""Central stage coordinator; it does not generate text without an inference provider."""
from __future__ import annotations

from dataclasses import dataclass

from backend.conversation.models import Message
from backend.intelligence.contracts import ContextInterpreter, IntentDetector, Reasoner


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    intent_name: str
    intent_confidence: float
    context: dict[str, object]
    plan_steps: tuple[str, ...]
    generated: bool = False


class IntelligenceOrchestrator:
    def __init__(self, intent_detector: IntentDetector, context_interpreter: ContextInterpreter,
                 reasoner: Reasoner) -> None:
        self.intent_detector = intent_detector
        self.context_interpreter = context_interpreter
        self.reasoner = reasoner

    def prepare(self, messages: tuple[Message, ...]) -> OrchestrationResult:
        latest = messages[-1].content if messages else ""
        intent = self.intent_detector.detect(latest)
        context = self.context_interpreter.interpret(messages)
        plan = self.reasoner.reason(context)
        return OrchestrationResult(intent.name, intent.confidence, context, plan.steps)

