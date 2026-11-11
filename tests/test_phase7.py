from __future__ import annotations

import json
import http.client
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from backend.integrations.mcp import MCPIntegration, MCPServerConfig
from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.knowledge.index import KnowledgeIndex
from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.research.web import DuckDuckGoHTMLProvider, _TextExtractor, register_web_tools
from backend.scheduler.service import TaskScheduler
from backend.security.audit import AuditLogger
from backend.tools.builtins import register_document_tools
from backend.tools.code_execution import register_code_tools
from backend.tools.data import register_data_tools
from backend.tools.knowledge import register_knowledge_tools
from backend.tools.registry import ToolRegistry, ToolSpec
from backend.tools.system import register_system_tools
from backend.protocols.models import ProtocolSpec


class ToolFixture:
    def __init__(self, root: Path, *, allowed: set[str] | None = None,
                 max_level: PermissionLevel = PermissionLevel.READ_ONLY,
                 allow_network: bool = False) -> None:
        self.root = root
        self.audit = AuditLogger(root / "audit.log")
        self.policy = PermissionPolicy(max_level, frozenset(allowed or set()), allow_network)
        self.registry = ToolRegistry(self.policy, self.audit, max_workers=2)

    def close(self): self.registry.shutdown()


class Phase7Tests(unittest.TestCase):
    def test_file_tools_enforce_roots_symlinks_protected_paths_and_confirmed_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "approved"; root.mkdir()
            outside = Path(directory) / "outside.txt"; outside.write_text("no", encoding="utf-8")
            (root / ".git").mkdir()
            (root / "linked.txt").symlink_to(outside)
            allowed = {"file.metadata", "document.read", "file.list", "file.create", "file.replace", "file.delete"}
            fx = ToolFixture(Path(directory), allowed=allowed, max_level=PermissionLevel.DESTRUCTIVE)
            register_document_tools(fx.registry, root)
            try:
                self.assertFalse(fx.registry.execute("document.read", {"path": str(outside)}).ok)
                self.assertFalse(fx.registry.execute("document.read", {"path": str(root / "linked.txt")}).ok)
                self.assertFalse(fx.registry.execute("document.read", {"path": str(root / ".git")}).ok)
                created = fx.registry.execute("file.create", {"path": str(root / "notes.txt"), "content": "first"}, confirmed=True)
                self.assertTrue(created.ok)
                self.assertFalse(fx.registry.execute("file.replace", {"path": str(root / "notes.txt"), "content": "second"}).ok)
                replaced = fx.registry.execute("file.replace", {"path": str(root / "notes.txt"), "content": "second"}, confirmed=True)
                self.assertTrue(replaced.ok)
                self.assertEqual((root / "notes.txt.aegis-backup").read_text(), "first")
                self.assertEqual((root / "notes.txt").read_text(), "second")
                self.assertTrue(any(row["action"] == "tool.execute" for row in fx.audit.inspect()))
            finally: fx.close()

    def test_registry_validates_inputs_output_limits_and_timeout_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fx = ToolFixture(Path(directory), allowed={"ok", "slow"})
            fx.registry.register(ToolSpec("ok", "test", {"type":"object","required":["value"],"properties":{"value":{"type":"integer"}},"additionalProperties":False},
                {"type":"object","required":["ok"]}, PermissionLevel.READ_ONLY, .2, lambda args: {"ok": True}))
            fx.registry.register(ToolSpec("slow", "test", {"type":"object"}, {"type":"object"},
                PermissionLevel.READ_ONLY, .05, lambda args: (time.sleep(.2) or {"ok":True})))
            try:
                self.assertFalse(fx.registry.execute("ok", {"value":"1"}).ok)
                self.assertFalse(fx.registry.execute("ok", {"value":1,"extra":2}).ok)
                self.assertTrue(fx.registry.execute("ok", {"value":1}).ok)
                self.assertEqual(fx.registry.execute("slow", {}).error, "tool_timeout")
            finally: fx.close()

    def test_tool_cancellation_is_reported_and_capacity_is_released_after_worker_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fx=ToolFixture(Path(directory),allowed={"slow"})
            fx.registry.register(ToolSpec("slow","test",{"type":"object"},{"type":"object"},
                PermissionLevel.READ_ONLY,.5,lambda args:(time.sleep(.2) or {"ok":True})))
            cancelled=threading.Event(); results=[]
            thread=threading.Thread(target=lambda:results.append(fx.registry.execute("slow",{},cancellation=cancelled)))
            try:
                thread.start(); time.sleep(.05); cancelled.set(); thread.join(1)
                self.assertFalse(thread.is_alive()); self.assertEqual(results[0].error,"tool_cancelled")
                self.assertTrue(fx.registry.execute("slow",{}).ok)
            finally: fx.close()

    def test_tool_request_ids_support_external_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fx=ToolFixture(Path(directory),allowed={"wait"})
            def handler(args,cancel):
                while not cancel.is_set(): time.sleep(.01)
                return {"cancelled":True}
            fx.registry.register(ToolSpec("wait","test",{"type":"object"},{"type":"object"},
                PermissionLevel.READ_ONLY,2,lambda args:{},cancellable_handler=handler))
            results=[]; thread=threading.Thread(target=lambda:results.append(fx.registry.execute("wait",{},request_id="external-cancel")))
            try:
                thread.start(); time.sleep(.05); self.assertTrue(fx.registry.cancel("external-cancel")); thread.join(1)
                self.assertEqual(results[0].error,"tool_cancelled")
                self.assertFalse(fx.registry.cancel("external-cancel"))
            finally: fx.close()

    def test_system_data_and_knowledge_tools_return_bounded_real_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); doc=root / "note.md"; doc.write_text("verified local note",encoding="utf-8")
            fx=ToolFixture(root, allowed={"system.diagnostics","data.csv.analyze","data.json.inspect","data.csv.transform","data.csv.chart","knowledge.ingest"}, max_level=PermissionLevel.WRITE_LOCAL)
            index=KnowledgeIndex(root / "knowledge.db",root)
            register_system_tools(fx.registry); register_data_tools(fx.registry); register_knowledge_tools(fx.registry,index)
            try:
                host=fx.registry.execute("system.diagnostics",{})
                self.assertTrue(host.ok); self.assertIn("memory_bytes",host.output)
                csv_text="team,value\na,2\nb,4\na,6"
                stats=fx.registry.execute("data.csv.analyze",{"content":csv_text})
                self.assertTrue(stats.ok); self.assertEqual(stats.output["columns"]["value"]["mean"],4)
                grouped=fx.registry.execute("data.csv.transform",{"content":csv_text,"operation":"group_count","column":"team"})
                self.assertEqual(grouped.output["row_count"],2)
                chart=fx.registry.execute("data.csv.chart",{"content":csv_text,"label_column":"team","value_column":"value"})
                self.assertIn("<svg",chart.output["svg"])
                denied=fx.registry.execute("knowledge.ingest",{"path":str(doc)})
                self.assertFalse(denied.ok)
                ingested=fx.registry.execute("knowledge.ingest",{"path":str(doc)},confirmed=True)
                self.assertTrue(ingested.ok); self.assertIn("document_id",ingested.output)
            finally: fx.close()

    def test_sandboxed_tests_are_read_only_and_network_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root / "project"; (project / "tests").mkdir(parents=True)
            (project/".env").write_text("SECRET=do-not-copy",encoding="utf-8")
            (project / "tests" / "test_safe.py").write_text(
                "import unittest, socket, os\nclass T(unittest.TestCase):\n def test_network(self):\n  with self.assertRaises(OSError): socket.create_connection(('1.1.1.1',443),timeout=.1)\n def test_source_is_read_only(self):\n  with self.assertRaises(OSError): open('/workspace/should_not_exist','w')\n def test_credentials_are_excluded(self):\n  self.assertFalse(os.path.exists('/workspace/.env'))\n",encoding="utf-8")
            fx=ToolFixture(root,allowed={"code.run_tests"})
            register_code_tools(fx.registry,(project,))
            try:
                spec=fx.registry.select("code.run_tests")
                if spec.availability != "available": self.skipTest("bubblewrap unavailable")
                result=fx.registry.execute("code.run_tests",{"project":str(project),"runner":"unittest","target":"tests"},confirmed=True)
                self.assertTrue(result.ok,result.error)
                self.assertEqual(result.output["exit_code"],0,result.output.get("output"))
                self.assertFalse(result.output["network_enabled"])
                self.assertFalse((project / "tests" / "__pycache__").exists())
            finally: fx.close()

    def test_web_extractor_discards_scripts_and_configured_tool_is_network_gated(self) -> None:
        parser=_TextExtractor(); parser.feed("<html><script>ignore()</script><p>Public fact</p></html>")
        self.assertEqual(" ".join(parser.parts),"Public fact")
        from backend.research.web import _validate_url
        with self.assertRaises(ValueError): _validate_url("https://127.0.0.1/")
        with tempfile.TemporaryDirectory() as directory:
            fx=ToolFixture(Path(directory),allowed={"web.search"},max_level=PermissionLevel.NETWORK,allow_network=False)
            class FakeProvider:
                provider_id="fake"
                def search(self,query,limit=5): return [{"title":"Result","url":"https://example.com","snippet":"untrusted"}]
                def fetch(self,url): return {"url":url,"text":"untrusted","trust":"untrusted_web_content"}
            register_web_tools(fx.registry,FakeProvider())
            try:
                result=fx.registry.execute("web.search",{"query":"test"})
                self.assertFalse(result.ok)
                self.assertIn("not permitted",result.error)
            finally: fx.close()

    def test_mcp_server_requires_explicit_approval_and_calls_through_registry(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method=payload["method"]
                if method == "notifications/initialized":
                    self.send_response(202); self.end_headers(); return
                if method == "initialize": result={"protocolVersion":"2025-03-26","capabilities":{"tools":{}}}
                elif method == "tools/list": result={"tools":[{"name":"echo","description":"echo","inputSchema":{"type":"object","properties":{"value":{"type":"string"}},"required":["value"],"additionalProperties":False}}]}
                else: result={"content":[{"type":"text","text":payload["params"]["arguments"]["value"]}]}
                body=json.dumps({"jsonrpc":"2.0","id":payload.get("id"),"result":result}).encode()
                self.send_response(200); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        server=HTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                fx=ToolFixture(Path(directory),allowed={"mcp.fixture.echo"},max_level=PermissionLevel.NETWORK,allow_network=True)
                cfg=MCPServerConfig("fixture",f"http://127.0.0.1:{server.server_port}/mcp",True)
                integration=MCPIntegration(fx.registry,cfg)
                try:
                    discovered=integration.connect()
                    self.assertTrue(any(tool["name"]=="mcp.fixture.echo" for tool in discovered))
                    denied=fx.registry.execute("mcp.fixture.echo",{"value":"hello"})
                    self.assertFalse(denied.ok)
                    result=fx.registry.execute("mcp.fixture.echo",{"value":"hello"},confirmed=True)
                    self.assertTrue(result.ok,result.error)
                    self.assertEqual(result.output["content"][0]["text"],"hello")
                finally: fx.close()
            with tempfile.TemporaryDirectory() as directory:
                fx=ToolFixture(Path(directory))
                try:
                    bad=MCPIntegration(fx.registry,MCPServerConfig("x",f"http://127.0.0.1:{server.server_port}/",False))
                    with self.assertRaises(PermissionError): bad.connect()
                finally: fx.close()
        finally:
            server.shutdown(); thread.join(1); server.server_close()

    def test_persistent_scheduled_workflows_restore_paused_and_need_confirmation_to_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); audit=AuditLogger(root/"audit.log"); db=root/"scheduler.db"; seen=[]
            scheduler=TaskScheduler(audit,db); scheduler.register_workflow("snapshot",lambda payload: seen.append(payload["value"]) or {"value":payload["value"]})
            with self.assertRaises(PermissionError): scheduler.schedule_workflow("snapshot",{"value":1})
            task=scheduler.schedule_workflow("snapshot",{"value":2},confirmed=True)
            deadline=time.time()+2
            while time.time()<deadline and task.status!="completed": time.sleep(.02)
            self.assertEqual(seen,[2]); scheduler.shutdown()
            restored=TaskScheduler(audit,db); restored.register_workflow("snapshot",lambda payload: seen.append(payload["value"]) or payload)
            try:
                recurring=restored.schedule_workflow("snapshot",{"value":3},delay_seconds=3600,interval_seconds=3600,confirmed=True)
                restored.shutdown()
                resumed=TaskScheduler(audit,db); resumed.register_workflow("snapshot",lambda payload: seen.append(payload["value"]) or payload)
                try:
                    persisted=next(item for item in resumed.list() if item.task_id==recurring.task_id)
                    self.assertEqual(persisted.status,"paused")
                    self.assertFalse(resumed.resume(persisted.task_id))
                    self.assertTrue(resumed.resume(persisted.task_id,confirmed=True))
                    self.assertTrue(resumed.cancel(persisted.task_id))
                    self.assertTrue(resumed.history(task.task_id))
                finally: resumed.shutdown()
            finally: pass

    def test_direct_tool_api_resolves_protocols_and_reports_blocked_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            server=AegisServer(Settings(port=0,data_dir=str(root/"data"),knowledge_root=str(root/"knowledge"),
                                        static_dir=str(root),persist_conversations=False))
            protocol=server.protocols.create(ProtocolSpec("Privileged status gate","block diagnostic tool",
                "system diagnostics",category="system_operations",triggers=("system.status",),
                required_permissions=("PRIVILEGED",),procedures=("Only a privileged operator may inspect status.",)))
            server.protocols.approve(protocol.protocol_id); server.protocols.activate(protocol.protocol_id)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                connection=http.client.HTTPConnection("127.0.0.1",server.server_address[1],timeout=3)
                connection.request("POST","/api/tools/system.status",json.dumps({"arguments":{}}),
                    {"Content-Type":"application/json", "Authorization":"Bearer "+server.api_token})
                response=connection.getresponse(); body=json.loads(response.read()); connection.close()
                self.assertEqual(response.status,403)
                self.assertEqual(body["error"],"protocol_blocked")
                self.assertTrue(body["output"]["protocols_applied"])
                self.assertFalse(any(row["action"]=="tool.execute" and row["details"].get("tool")=="system.status"
                                     for row in server.audit.inspect()))
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__": unittest.main()
