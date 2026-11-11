from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.inference.catalog import recommendations
from backend.learning.traces import TraceStore
from backend.protocols.models import ProtocolSpec, ProtocolStatus
from backend.protocols.registry import ProtocolError, ProtocolRegistry
from backend.scheduler.service import TaskScheduler
from backend.security.audit import AuditLogger


class Phase6Tests(unittest.TestCase):
    def test_protocol_lifecycle_versioning_and_conflict_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audit = AuditLogger(Path(directory) / "audit.log")
            registry = ProtocolRegistry(Path(directory) / "protocols.db", audit)
            first = registry.create(ProtocolSpec("Coding", "safe coding", "coding", procedures=("test",)))
            self.assertEqual(first.status, ProtocolStatus.PROPOSED)
            self.assertEqual(registry.approve(first.protocol_id).status, ProtocolStatus.APPROVED)
            self.assertEqual(registry.activate(first.protocol_id).status, ProtocolStatus.ACTIVE)
            second = registry.create(ProtocolSpec("Other", "other", "coding", procedures=("review",)))
            registry.approve(second.protocol_id)
            with self.assertRaises(ProtocolError): registry.activate(second.protocol_id)
            revised = registry.revise(first.protocol_id, purpose="revised safe coding")
            self.assertEqual(revised.version, 2)

    def test_trace_storage_and_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "traces.db")
            trace_id = store.start("hello", model="test")
            store.complete(trace_id, "completed", {"latency_ms": 1})
            self.assertEqual(store.analyze()["outcomes"], {"completed": 1})

    def test_scheduler_runs_one_time_task_and_can_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scheduler = TaskScheduler(AuditLogger(Path(directory) / "audit.log"))
            seen: list[str] = []
            task = scheduler.schedule("test", lambda: seen.append("ran"), delay_seconds=0.05)
            deadline = time.time() + 1
            while time.time() < deadline and not seen: time.sleep(0.02)
            self.assertEqual(seen, ["ran"])
            self.assertFalse(scheduler.cancel(task.task_id))
            scheduler.shutdown()

    def test_model_catalog_is_local_and_non_downloading(self) -> None:
        values = recommendations()
        self.assertTrue(values)
        self.assertTrue(all("fits_detected_memory" in value for value in values))


if __name__ == "__main__":
    unittest.main()
