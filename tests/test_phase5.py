from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.conversation.engine import ConversationEngine
from backend.inference.llama_cpp import LlamaCppProvider
from backend.intelligence.brain import BrainRequest, CentralBrain
from backend.learning.records import LearningRecords
from backend.memory.store import MemoryCategory, MemoryStore
from backend.permissions.policy import PermissionPolicy
from backend.security.audit import AuditLogger
from backend.tools.builtins import register_safe_tools
from backend.tools.registry import ToolRegistry


class Phase5Tests(unittest.TestCase):
    def test_unconfigured_local_model_is_honest_and_lazy(self) -> None:
        provider = LlamaCppProvider(None)
        self.assertFalse(provider.available())
        self.assertEqual(provider.metadata.provider, "llama.cpp")
        self.assertIsNone(provider._model)

    def test_brain_preparation_retrieves_memory_and_selects_safe_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "memory.db")
            memory.store(MemoryCategory.EXPLICIT, "The system status is important", explicit=True)
            audit = AuditLogger(Path(directory) / "audit.log")
            policy = PermissionPolicy(allowed_tools=frozenset({"system.status"}))
            tools = ToolRegistry(policy, audit)
            register_safe_tools(tools, lambda: {"status": "ok"}, lambda query: {"results": []})
            brain = CentralBrain(ConversationEngine(None), memory, tools, audit)
            result = brain.prepare(BrainRequest("session", "request", "What is the system status?"))
            self.assertEqual(result.selected_tool, "system.status")
            self.assertTrue(result.memory_ids)
            tools.shutdown()

    def test_learning_records_are_approved_data_not_model_training(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records = LearningRecords(Path(directory) / "learning.db")
            identifier = records.record("evaluation", {"prompt": "x"}, {"result": "y"}, approved=True)
            self.assertEqual(records.inspect()[0]["id"], identifier)
            self.assertTrue(records.inspect()[0]["approved"])

    def test_frontend_is_voice_first_without_chat_history_panel(self) -> None:
        html = Path("frontend/index.html").read_text(encoding="utf-8")
        self.assertIn('id="microphone"', html)
        self.assertIn('id="composer" hidden', html)
        self.assertNotIn('id="messages"', html)
        self.assertIn('conversation.message', html)


if __name__ == "__main__":
    unittest.main()
