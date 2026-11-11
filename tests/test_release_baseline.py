"""Comprehensive tests for AEGIS Phase 8.1 Task 1: Trusted Code Baseline & Release State."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.security.audit import AuditLogger
from backend.security.control import ProtectedPaths
from backend.security.release import (
    DEFAULT_BASELINE_FILENAME,
    MANIFEST_FORMAT_VERSION,
    ReleaseIntegrityManager,
    ReleaseManifest,
    scan_project_source_files,
    sha256_file,
)
from backend.tools.builtins import register_document_tools
from backend.tools.registry import ToolRegistry


class ReleaseBaselineTests(unittest.TestCase):
    """Unit and integration tests for release baseline subsystem."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.backend = self.root / "backend"
        self.backend.mkdir(parents=True)
        (self.backend / "core.py").write_text("print('core')", encoding="utf-8")
        sub = self.backend / "sub"
        sub.mkdir()
        (sub / "mod.py").write_text("print('mod')", encoding="utf-8")
        (self.root / "main.py").write_text("print('main')", encoding="utf-8")

        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True)
        self.manifest_path = self.data_dir / DEFAULT_BASELINE_FILENAME
        self.audit_path = self.data_dir / "audit.log"
        self.audit = AuditLogger(self.audit_path)

        self.manager = ReleaseIntegrityManager(
            project_root=self.root,
            manifest_path=self.manifest_path,
            audit=self.audit,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_initial_trusted_baseline_creation(self) -> None:
        """Requirement: Initial trusted baseline creation."""
        with self.assertRaises(PermissionError):
            self.manager.create_baseline("v0.8.1", reviewed=False)

        with self.assertRaises(ValueError):
            self.manager.create_baseline("", reviewed=True)

        result = self.manager.create_baseline("v0.8.1", reviewed=True, actor="admin_alice")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["release_id"], "v0.8.1")
        self.assertEqual(result["file_count"], 3)
        self.assertTrue(self.manifest_path.is_file())

        stat = self.manifest_path.stat()
        self.assertEqual(stat.st_mode & 0o777, 0o600)

        manifest = self.manager.load_manifest()
        self.assertEqual(manifest.format_version, MANIFEST_FORMAT_VERSION)
        self.assertEqual(manifest.release_id, "v0.8.1")
        self.assertGreater(manifest.created_at, 0)
        self.assertEqual(len(manifest.files), 3)
        self.assertIn("backend/core.py", manifest.files)
        self.assertIn("backend/sub/mod.py", manifest.files)
        self.assertIn("main.py", manifest.files)
        self.assertTrue(manifest.verify_seal())

    def test_successful_verification_after_restart(self) -> None:
        """Requirement: The baseline must survive application restarts and verify successfully."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        # Simulate fresh process restart by creating a new manager instance
        restarted_manager = ReleaseIntegrityManager(
            project_root=self.root,
            manifest_path=self.manifest_path,
            audit=self.audit,
        )
        self.assertTrue(restarted_manager.has_manifest())

        report = restarted_manager.verify()
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["release_id"], "v0.8.1")
        self.assertEqual(report["checked_files"], 3)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["modified"], [])
        self.assertEqual(report["unexpected"], [])

    def test_modified_file_detection(self) -> None:
        """Requirement: Detect modified application source files."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        (self.backend / "core.py").write_text("print('tampered')", encoding="utf-8")

        report = self.manager.verify()
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["modified"], ["backend/core.py"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unexpected"], [])
        self.assertIn("recovery", report)

    def test_missing_file_detection(self) -> None:
        """Requirement: Detect missing application source files."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        (self.backend / "sub" / "mod.py").unlink()

        report = self.manager.verify()
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["missing"], ["backend/sub/mod.py"])
        self.assertEqual(report["modified"], [])
        self.assertEqual(report["unexpected"], [])
        self.assertIn("recovery", report)

    def test_unexpected_source_file_detection(self) -> None:
        """Requirement: Detect unexpected application source files."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        (self.backend / "rogue.py").write_text("print('backdoor')", encoding="utf-8")

        report = self.manager.verify()
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["unexpected"], ["backend/rogue.py"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["modified"], [])
        self.assertIn("recovery", report)

    def test_corrupt_manifest_detection(self) -> None:
        """Requirement: Fail safely when manifest is corrupt or invalid."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        # 1. Invalid JSON
        self.manifest_path.write_text("{invalid json", encoding="utf-8")
        report = self.manager.verify()
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "corrupt_or_invalid_manifest")
        self.assertIn("recovery", report)

        # 2. Tampered hash with invalid seal
        valid = self.manager.create_baseline("v0.8.1", reviewed=True)
        raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        raw_manifest["files"]["backend/core.py"] = "0" * 64
        self.manifest_path.write_text(json.dumps(raw_manifest), encoding="utf-8")

        report2 = self.manager.verify()
        self.assertFalse(report2["ok"])
        self.assertEqual(report2["status"], "corrupt_or_invalid_manifest")
        self.assertIn("seal verification failed", report2["error"])

        # 3. Missing manifest
        self.manifest_path.unlink()
        report3 = self.manager.verify()
        self.assertFalse(report3["ok"])
        self.assertEqual(report3["status"], "missing_manifest")

    def test_unauthorized_baseline_update_rejection(self) -> None:
        """Requirement: Baseline updates must require explicit reviewed and confirmed operations."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        # Denied without reviewed=True
        with self.assertRaises(PermissionError):
            self.manager.update_baseline("v0.8.2", reviewed=False, confirmed=True)

        # Denied without confirmed=True
        with self.assertRaises(PermissionError):
            self.manager.update_baseline("v0.8.2", reviewed=True, confirmed=False)

        # Denied with invalid release_id
        with self.assertRaises(ValueError):
            self.manager.update_baseline("", reviewed=True, confirmed=True)

    def test_explicit_authorized_baseline_update(self) -> None:
        """Requirement: Explicit authorized baseline update updates manifest and passes verify."""
        self.manager.create_baseline("v0.8.1", reviewed=True)

        (self.backend / "core.py").write_text("print('new feature')", encoding="utf-8")
        mismatch = self.manager.verify()
        self.assertFalse(mismatch["ok"])

        # Perform explicit authorized update
        res = self.manager.update_baseline("v0.8.2", reviewed=True, confirmed=True, actor="lead_developer")
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "updated")
        self.assertEqual(res["release_id"], "v0.8.2")
        self.assertEqual(res["previous_release_id"], "v0.8.1")

        verified = self.manager.verify()
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["release_id"], "v0.8.2")
        self.assertEqual(verified["modified"], [])

    def test_audit_event_recording(self) -> None:
        """Requirement: Record baseline creation, verification failures and authorized updates."""
        self.manager.create_baseline("v0.8.1", reviewed=True, actor="admin_init")

        (self.backend / "core.py").write_text("print('changed')", encoding="utf-8")
        self.manager.verify(record_audit=True, actor="audit_watcher")

        self.manager.update_baseline("v0.8.2", reviewed=True, confirmed=True, actor="admin_update")

        try:
            self.manager.update_baseline("v0.8.3", reviewed=False, confirmed=True, actor="unauthorized_caller")
        except PermissionError:
            pass

        events = self.audit.inspect(50)
        actions = [item.get("action") for item in events]
        self.assertIn("baseline.create", actions)
        self.assertIn("baseline.verify", actions)
        self.assertIn("baseline.update", actions)

        create_ev = next(e for e in events if e.get("action") == "baseline.create")
        self.assertEqual(create_ev["actor"], "admin_init")
        self.assertEqual(create_ev["outcome"], "created")
        self.assertEqual(create_ev["details"]["release_id"], "v0.8.1")

        verify_ev = next(e for e in events if e.get("action") == "baseline.verify" and e.get("outcome") == "failed")
        self.assertEqual(verify_ev["details"]["status"], "mismatch")
        self.assertEqual(verify_ev["details"]["modified"], ["backend/core.py"])

        update_ev = next(e for e in events if e.get("action") == "baseline.update" and e.get("outcome") == "updated")
        self.assertEqual(update_ev["actor"], "admin_update")
        self.assertEqual(update_ev["details"]["release_id"], "v0.8.2")

        denied_ev = next(e for e in events if e.get("action") == "baseline.update" and e.get("outcome") == "denied")
        self.assertEqual(denied_ev["actor"], "unauthorized_caller")

        integrity = self.audit.verify_integrity()
        self.assertTrue(integrity["ok"])

    def test_protected_paths_protects_manifest(self) -> None:
        """Requirement: Manifest protected against ordinary unauthorized modification via ProtectedPaths."""
        policy = ProtectedPaths()
        with self.assertRaises(PermissionError):
            policy.validate(self.manifest_path, (self.root,))

        with self.assertRaises(PermissionError):
            policy.validate(self.root / "data" / "trusted_baseline.json", (self.root,))

        with self.assertRaises(PermissionError):
            policy.validate(self.root / "data" / "release_manifest.json", (self.root,))

        # File tools cannot create or edit baseline manifest
        tool_policy = PermissionPolicy(
            max_level=PermissionLevel.WRITE_LOCAL,
            allowed_tools=frozenset({"file.create", "file.edit"}),
        )
        registry = ToolRegistry(tool_policy, self.audit)
        try:
            register_document_tools(registry, self.root)
            result = registry.execute(
                "file.create",
                {"path": str(self.manifest_path), "content": "malicious content"},
                confirmed=True,
            )
            self.assertFalse(result.ok)
        finally:
            registry.shutdown()

    def test_startup_enters_safemode_on_modified_files(self) -> None:
        """Requirement: Startup verification fails safely into safe mode when source files are modified."""
        self.manager.create_baseline("v0.8.1", reviewed=True)
        # Modify a file on disk
        (self.backend / "core.py").write_text("print('tampered')", encoding="utf-8")

        settings = Settings(
            port=0,
            data_dir=str(self.data_dir),
            knowledge_root=str(self.root / "knowledge"),
            static_dir=str(self.root / "frontend"),
            baseline_manifest=str(self.manifest_path),
            project_root=str(self.root),
        )
        server = AegisServer(settings)
        try:
            self.assertTrue(server.safe_mode.status()["active"])
            self.assertIn("startup_baseline_verification_failed", server.safe_mode.status()["reason"])

            # Recovery is refused because integrity fails
            rec, reason = server.recover_safe_mode(confirmed=False)
            self.assertFalse(rec)
            self.assertEqual(reason, "confirmation_required")
            rec2, reason2 = server.recover_safe_mode(confirmed=True)
            self.assertFalse(rec2)
            self.assertEqual(reason2, "integrity_checks_must_pass_before_recovery")
            self.assertTrue(server.safe_mode.status()["active"])
        finally:
            server.server_close()

    def test_startup_enters_safemode_on_corrupt_manifest(self) -> None:
        """Requirement: Startup verification fails safely into safe mode when manifest is corrupt."""
        self.manifest_path.write_text("{corrupt manifest content", encoding="utf-8")

        settings = Settings(
            port=0,
            data_dir=str(self.data_dir),
            knowledge_root=str(self.root / "knowledge"),
            static_dir=str(self.root / "frontend"),
            baseline_manifest=str(self.manifest_path),
            project_root=str(self.root),
        )
        server = AegisServer(settings)
        try:
            self.assertTrue(server.safe_mode.status()["active"])
            self.assertIn("corrupt_or_invalid_manifest", server.safe_mode.status()["reason"])
        finally:
            server.server_close()

    def test_server_baseline_api_endpoints(self) -> None:
        """Requirement: REST endpoints for baseline status, verification, creation, and update."""
        token = "t" * 40
        settings = Settings(
            port=0,
            data_dir=str(self.data_dir),
            knowledge_root=str(self.root / "knowledge"),
            static_dir=str(self.root / "frontend"),
            baseline_manifest=str(self.manifest_path),
            project_root=str(self.root),
        )
        server = AegisServer(settings)
        server.api_token = token
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method: str, path: str, body: dict | None = None, auth: bool = True) -> tuple[int, dict]:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = f"Bearer {token}"
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp.status, json.loads(data.decode("utf-8")) if data else {}

        try:
            # 1. Baseline status before creation
            st, body = request("GET", "/api/security/baseline")
            self.assertEqual(st, 200)
            self.assertFalse(body["ok"])
            self.assertEqual(body["status"], "missing_manifest")

            # 2. Create baseline via API
            st, body = request("POST", "/api/security/baseline/create", {"release_id": "v0.8.1", "reviewed": True})
            self.assertEqual(st, 201)
            self.assertTrue(body["ok"])
            self.assertEqual(body["release_id"], "v0.8.1")

            # 3. Baseline verify via API
            st, body = request("POST", "/api/security/baseline/verify")
            self.assertEqual(st, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["status"], "verified")

            # 4. Modify a file and verify via API
            (self.backend / "core.py").write_text("print('api_change')", encoding="utf-8")
            st, body = request("POST", "/api/security/baseline/verify")
            self.assertEqual(st, 409)
            self.assertFalse(body["ok"])
            self.assertEqual(body["status"], "mismatch")

            # 5. Unauthorized update rejection
            st, body = request("POST", "/api/security/baseline/update", {"release_id": "v0.8.2", "confirmed": False})
            self.assertEqual(st, 403)

            # 6. Authorized update via API
            st, body = request("POST", "/api/security/baseline/update",
                               {"release_id": "v0.8.2", "confirmed": True, "reviewed": True})
            self.assertEqual(st, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["release_id"], "v0.8.2")

            # 7. Baseline verify passes after authorized update
            st, body = request("POST", "/api/security/baseline/verify")
            self.assertEqual(st, 200)
            self.assertTrue(body["ok"])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
