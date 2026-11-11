from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from backend.agents.manager import AgentManager
from backend.agents.models import AgentType, TaskStatus
from backend.desktop.controller import DesktopController
from backend.knowledge.index import KnowledgeIndex
from backend.permissions.policy import ConfirmationRequired, PermissionLevel, PermissionPolicy
from backend.security.audit import AuditLogger
from backend.security.guardrails import redact_secrets, validate_command, validate_path
from backend.tools.registry import ToolRegistry, ToolSpec


class Step4Tests(unittest.TestCase):
    def test_tool_registry_is_allowlisted_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = AuditLogger(Path(directory) / "audit.log")
            registry = ToolRegistry(PermissionPolicy(), audit)
            registry.register(ToolSpec("safe.echo", "test", {"type": "object"}, {"type": "object"},
                                       PermissionLevel.READ_ONLY, 1, lambda args: {"value": args.get("value")}))
            self.assertFalse(registry.execute("shell", {}).ok)
            self.assertFalse(registry.execute("safe.echo", {"value": "no"}).ok)
            registry.policy = PermissionPolicy(allowed_tools=frozenset({"safe.echo"}))
            result = registry.execute("safe.echo", {"value": "yes"})
            self.assertTrue(result.ok)
            self.assertEqual(audit.inspect()[-1]["outcome"], "ok")
            registry.shutdown()

    def test_permission_confirmation_and_guardrails(self) -> None:
        policy = PermissionPolicy(allowed_tools=frozenset({"danger"}), max_level=PermissionLevel.READ_ONLY)
        with self.assertRaises(ConfirmationRequired):
            policy.require("danger", PermissionLevel.DESTRUCTIVE)
        with tempfile.TemporaryDirectory() as directory:
            safe = validate_path(Path(directory) / "file.txt", directory)
            self.assertTrue(str(safe).startswith(directory))
            with self.assertRaises(PermissionError):
                validate_path("/etc/passwd", directory)
        with self.assertRaises(PermissionError):
            validate_command("echo hi; rm -rf /", frozenset({"echo"}))
        with self.assertRaises(PermissionError):
            validate_command("sudo reboot", frozenset({"sudo"}))
        self.assertNotIn("secret-value", redact_secrets("token=secret-value"))

    def test_agent_routing_cancellation_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = AuditLogger(Path(directory) / "audit.log")
            manager = AgentManager(audit)
            self.assertEqual(manager.route("research sources"), AgentType.RESEARCH)
            task = manager.create("do bounded work")
            manager.handlers[task.agent] = lambda task, cancel: {"ok": True}
            completed = manager.run(task.task_id)
            self.assertEqual(completed.status, TaskStatus.COMPLETED)
            slow = manager.create("slow work")
            manager.handlers[slow.agent] = lambda task, cancel: (time.sleep(0.2), {"ok": True})[1]
            timed = manager.run(slow.task_id, timeout_seconds=0.01)
            self.assertEqual(timed.status, TaskStatus.TIMED_OUT)
            cancelled = manager.create("cancel me")
            self.assertTrue(manager.cancel(cancelled.task_id))
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
            manager.shutdown()

    def test_knowledge_retrieval_is_local_and_source_tracked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "documents"
            root.mkdir()
            source = root / "notes.md"
            source.write_text("Pentium local knowledge", encoding="utf-8")
            index = KnowledgeIndex(Path(directory) / "knowledge.db", root)
            document = index.ingest_file(source)
            self.assertEqual(index.search("local")[0].source, str(source.resolve()))
            self.assertEqual(index.inspect()[0].document_id, document.document_id)
            with self.assertRaises(PermissionError):
                index.ingest_file("/etc/hosts")

    def test_desktop_actions_are_disabled_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = AuditLogger(Path(directory) / "audit.log")
            desktop = DesktopController(PermissionPolicy(), audit)
            self.assertFalse(desktop.inspect_screen()["available"])
            with self.assertRaises(Exception):
                desktop.interact("click", {})
            self.assertEqual(audit.inspect()[-1]["outcome"], "denied")


if __name__ == "__main__":
    unittest.main()

