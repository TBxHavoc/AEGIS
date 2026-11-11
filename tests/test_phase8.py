from __future__ import annotations

import sqlite3
import tempfile
import http.client
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.security.audit import AuditLogger
from backend.security.control import (FirewallManager, ProtectedPathError, ProtectedPaths,
                                      SafeMode, IntegrityMonitor, sqlite_backup, sqlite_health)
from backend.security.guardrails import redact_secrets
from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.tools.builtins import register_document_tools
from backend.tools.registry import ToolRegistry


class Phase8SecurityTests(unittest.TestCase):
    def test_protected_path_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "approved"; root.mkdir()
            blocked = root / ".aws"; blocked.mkdir()
            outside = Path(tmp) / "outside"; outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            policy = ProtectedPaths()
            with self.assertRaises(PermissionError): policy.validate(blocked / "credentials", (root,))
            with self.assertRaises(PermissionError): policy.validate(root / ".." / "outside", (root,))
            with self.assertRaises(PermissionError): policy.validate(root / "escape" / "file", (root,))
            with self.assertRaises(PermissionError): policy.validate(root / "memory.db", (root,))

    def test_aegis_source_is_read_only_to_file_tools(self):
        project = Path(__file__).parents[1].resolve()
        target = project / "backend" / "__phase8_must_not_create.py"
        audit = AuditLogger(Path(tempfile.mkdtemp()) / "audit.log")
        policy = PermissionPolicy(max_level=PermissionLevel.WRITE_LOCAL, allowed_tools=frozenset({"file.create"}))
        registry = ToolRegistry(policy, audit)
        try:
            register_document_tools(registry, project)
            result = registry.execute("file.create", {"path": str(target), "content": "unsafe"}, confirmed=True)
            self.assertFalse(result.ok); self.assertFalse(target.exists())
        finally:
            registry.shutdown()

    def test_audit_redaction_and_integrity_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.log"; audit = AuditLogger(path)
            audit.record("login", actor="user", outcome="ok", details={"password": "hunter2", "token": "a.long.secret"})
            raw = path.read_text()
            self.assertNotIn("hunter2", raw); self.assertNotIn("a.long.secret", raw)
            self.assertTrue(audit.verify_integrity()["ok"])
            path.write_text(raw.replace('"outcome":"ok"', '"outcome":"tampered"'))
            self.assertFalse(audit.verify_integrity()["ok"])
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", redact_secrets("AKIAABCDEFGHIJKLMNOP"))
        self.assertNotIn("PRIVATE MATERIAL", redact_secrets("-----BEGIN RSA PRIVATE KEY-----\nPRIVATE MATERIAL\n-----END RSA PRIVATE KEY-----"))

    def test_safe_mode_requires_confirmation_to_recover(self):
        safe = SafeMode(); safe.enter("integrity mismatch")
        self.assertTrue(safe.status()["active"])
        self.assertFalse(safe.recover(confirmed=False))
        self.assertTrue(safe.recover(confirmed=True))
        self.assertFalse(safe.status()["active"])

    def test_firewall_proposals_are_validated_and_apply_requires_confirmation(self):
        audit = AuditLogger(Path(tempfile.mkdtemp()) / "audit.log")
        firewall = FirewallManager(audit, executable="/mock/ufw")
        with self.assertRaises(ValueError): firewall.propose_allow(22)
        with self.assertRaises(ValueError): firewall.propose_allow(70000)
        proposal = firewall.propose_allow(8443, source="192.0.2.0/24")
        self.assertTrue(proposal["requires_approval"])
        with self.assertRaises(PermissionError): firewall.apply(proposal["id"], approved=False)
        with patch("backend.security.control.subprocess.run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = "Status: active"; run.return_value.stderr = ""
            result = firewall.apply(proposal["id"], approved=True)
            self.assertTrue(result["applied"])
            self.assertEqual(run.call_count, 3)  # one additive command, then read-only verification
            self.assertEqual(run.call_args_list[0].args[0], ["/mock/ufw", "allow", "from", "192.0.2.0/24", "to", "any", "port", "8443", "proto", "tcp"])

    def test_database_backup_is_consistent_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"; target = Path(tmp) / "backup.db"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE t(value TEXT)"); db.execute("INSERT INTO t VALUES('preserve')")
            self.assertTrue(sqlite_health(source)["ok"])
            result = sqlite_backup(source, target)
            self.assertTrue(result["verified"]); self.assertTrue(sqlite_health(target)["ok"])
            with self.assertRaises(ValueError): sqlite_backup(source, target)
            with sqlite3.connect(target) as db: self.assertEqual(db.execute("SELECT value FROM t").fetchone()[0], "preserve")

    def test_integrity_monitor_detects_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protected.py"; path.write_text("safe")
            monitor = IntegrityMonitor((path,)); self.assertTrue(monitor.check()["ok"])
            path.write_text("changed")
            self.assertFalse(monitor.check()["ok"])

    def test_security_voice_text_commands_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = AegisServer(Settings(port=0, data_dir=str(Path(tmp) / "data"),
                knowledge_root=str(Path(tmp) / "knowledge"), static_dir=str(Path(tmp) / "frontend")))
            try:
                report = server._security_command("Check AEGIS integrity")
                self.assertIn("AEGIS integrity report", report[0])
                emergency = server._security_command("Enter safe mode")
                self.assertTrue(emergency[1]); self.assertTrue(server.safe_mode.status()["active"])
                self.assertIsNone(server._security_command("please enter safe mode now and check"))
            finally:
                server.server_close()

    def test_api_and_websocket_require_configured_bearer_token(self):
        test_token = "t" * 40
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AEGIS_API_TOKEN": test_token}):
            server = AegisServer(Settings(port=0, data_dir=str(Path(tmp) / "data"),
                knowledge_root=str(Path(tmp) / "knowledge"), static_dir=str(Path(tmp) / "frontend")))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                conn.request("GET", "/api/security/status")
                self.assertEqual(conn.getresponse().status, 401); conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                conn.request("GET", "/api/security/status", headers={"Authorization": "Bearer " + test_token})
                response = conn.getresponse(); self.assertEqual(response.status, 200); response.read(); conn.close()
                sock = __import__("socket").create_connection(("127.0.0.1", server.server_address[1]), timeout=3)
                sock.sendall((f"GET /api/ws HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n").encode())
                self.assertIn(b"401", sock.recv(1024)); sock.close()
                sock = __import__("socket").create_connection(("127.0.0.1", server.server_address[1]), timeout=3)
                sock.sendall((f"GET /api/ws HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: aegis, bearer.{test_token}\r\n\r\n").encode())
                handshake = sock.recv(1024)
                self.assertIn(b"101 Switching Protocols", handshake)
                self.assertIn(b"Sec-WebSocket-Protocol: aegis", handshake)
                sock.sendall(b"\x88\x00"); sock.close()
            finally:
                server.shutdown(); thread.join(timeout=2); server.server_close()


if __name__ == "__main__": unittest.main()
