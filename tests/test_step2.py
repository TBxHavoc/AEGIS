from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.conversation.context import ContextAssembler
from backend.conversation.engine import ConversationEngine
from backend.conversation.models import CancellationToken
from backend.conversation.persistence import ConversationStore
from backend.inference.base import InferenceRequest, ModelMetadata, ResourceSnapshot
from backend.inference.providers import UnavailableProvider
from backend.inference.router import ProviderRouter
from backend.memory.store import MemoryCategory, MemoryStore, SensitiveMemoryError


class FakeProvider:
    metadata = ModelMetadata("fake-local", "test-model", parameter_count="test", context_window=128)

    def __init__(self, chunks: tuple[str, ...] = ("hello", " world")) -> None:
        self.chunks = chunks

    def available(self) -> bool:
        return True

    def stream(self, request: InferenceRequest, cancellation: CancellationToken):
        for chunk in self.chunks:
            if cancellation.cancelled:
                return
            yield chunk

    def load(self) -> None: return None
    def unload(self) -> None: return None
    def resources(self) -> ResourceSnapshot: return ResourceSnapshot()


class BrokenProvider(FakeProvider):
    def stream(self, request: InferenceRequest, cancellation: CancellationToken):
        raise RuntimeError("provider exploded")
        yield ""


class Step2Tests(unittest.TestCase):
    def test_context_assembly_is_bounded(self) -> None:
        context = ContextAssembler(max_messages=2, max_characters=9).assemble([], "new")
        self.assertEqual(context.messages[-1].content, "new")
        self.assertLessEqual(context.character_count, 9)

    def test_conversation_stream_has_one_terminal_response_and_history(self) -> None:
        engine = ConversationEngine(FakeProvider())
        events = list(engine.stream("s1", "r1", "question"))
        self.assertEqual([event.type for event in events], [
            "conversation.response.started", "conversation.response.chunk",
            "conversation.response.chunk", "conversation.response.completed",
        ])
        self.assertEqual([message.role for message in engine.history("s1")], ["user", "assistant"])

    def test_cancellation(self) -> None:
        engine = ConversationEngine(FakeProvider())
        generator = engine.stream("s1", "r1", "question")
        next(generator)
        self.assertTrue(engine.cancel("r1"))
        self.assertEqual(next(generator).payload["error"], "cancelled")

    def test_unavailable_model_is_honest(self) -> None:
        events = list(ConversationEngine(UnavailableProvider()).stream("s1", "r1", "question"))
        self.assertEqual(events[-1].type, "conversation.response.failed")
        self.assertEqual(events[-1].payload["error"], "model_unavailable")

    def test_provider_failure_recovers_as_terminal_error(self) -> None:
        events = list(ConversationEngine(BrokenProvider()).stream("s1", "r1", "question"))
        self.assertEqual(events[-1].type, "conversation.response.failed")
        self.assertEqual(events[-1].payload["error"], "conversation_error")

    def test_provider_router_prefers_local_and_has_unavailable_state(self) -> None:
        self.assertEqual(ProviderRouter(local=FakeProvider()).select().metadata.provider, "fake-local")
        self.assertFalse(ProviderRouter().select().available())

    def test_conversation_persistence_is_opt_in_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversations.db")
            engine = ConversationEngine(FakeProvider(), store=store, persist=True)
            list(engine.stream("s1", "r1", "remember this"))
            restored = ConversationEngine(FakeProvider(), store=store, persist=True)
            self.assertEqual([message.content for message in restored.history("s1")], ["remember this", "hello world"])

    def test_memory_crud_search_retention_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "memory.db")
            record = memory.store(MemoryCategory.EXPLICIT, "My favorite color is blue", explicit=True)
            self.assertEqual(memory.retrieve(record.memory_id).content, record.content)
            self.assertEqual(len(memory.search("favorite blue")), 1)
            updated = memory.update(record.memory_id, "My favorite color is green")
            self.assertIn("green", updated.content)
            self.assertTrue(memory.delete(record.memory_id))
            self.assertIsNone(memory.retrieve(record.memory_id))
            with self.assertRaises(PermissionError):
                memory.store(MemoryCategory.CONVERSATION_HISTORY, "private chat")
            with self.assertRaises(SensitiveMemoryError):
                memory.store(MemoryCategory.EXPLICIT, "api_key=not-for-storage", explicit=True)


if __name__ == "__main__":
    unittest.main()

