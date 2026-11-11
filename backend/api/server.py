"""REST and WebSocket server for AEGIS Step 1."""
from __future__ import annotations

import json
import base64
import binascii
import ipaddress
import logging
import os
import re
import secrets
import socket
import threading
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from backend.api.websocket import accept_key, frame, read_frame, send_json
from backend.configuration.settings import Settings
from backend.core.events import EVENT_TYPES, RequestDeduplicator
from backend.conversation.engine import ConversationEngine
from backend.conversation.persistence import ConversationStore
from backend.conversation.session import SessionManager
from backend.inference.catalog import hardware_profile, recommendations
from backend.inference.router import ProviderRouter
from backend.inference.configuration import ModelConfiguration
from backend.inference.engine import InferenceEngine
from backend.inference.manager import ModelManager
from backend.inference.registry import ModelRegistry
from backend.memory.store import MemoryCategory, MemoryStore, SensitiveMemoryError
from backend.agents.registry import AgentRegistry
from backend.agents.specialists import SpecialistWorkflows
from backend.agents.orchestrator import MultiAgentOrchestrator
from backend.desktop.controller import DesktopController
from backend.knowledge.index import KnowledgeIndex
from backend.permissions.policy import PermissionError as PolicyPermissionError, PermissionLevel, PermissionPolicy
from backend.research.session import ResearchService
from backend.security.audit import AuditLogger
from backend.security.control import (FirewallManager, IntegrityMonitor, ResourceMonitor, SafeMode,
    SecurityComponentRegistry, sqlite_backup, sqlite_health)
from backend.security.release import ReleaseIntegrityManager
from backend.tools.builtins import register_document_tools, register_safe_tools
from backend.tools.system import register_system_tools
from backend.tools.data import register_data_tools
from backend.tools.code_execution import register_code_tools
from backend.tools.knowledge import register_knowledge_tools
from backend.research.web import DuckDuckGoHTMLProvider, register_web_tools
from backend.integrations.mcp import MCPIntegration, MCPServerConfig
from backend.integrations.device import DeviceIntegration
from backend.tools.registry import ToolRegistry, ToolResult
from backend.voice.config import VoicePreferences, VoicePreferencesStore
from backend.voice.lifecycle import VoiceService
from backend.voice.tts import PiperProvider
from backend.voice.stt.providers import WhisperCppRecognizer, VoskRecognizer
from backend.voice.stt.service import STTService
from backend.voice.stt.audio import AudioError
from backend.voice.stt.vad import VADConfig
from backend.intelligence.brain import BrainRequest, CentralBrain
from backend.intelligence.reasoning import ReasoningEngine, TaskStore
from backend.learning.traces import TraceStore
from backend.protocols.models import ProtocolSpec, ProtocolStatus
from backend.protocols.registry import ProtocolError, ProtocolRegistry
from backend.protocols.parser import ProtocolDraftParser
from backend.protocols.resolver import ProtocolResolver
from backend.scheduler.service import TaskScheduler

LOG = logging.getLogger(__name__)


class AegisServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        token = os.getenv("AEGIS_API_TOKEN", "")
        if token and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("AEGIS_API_TOKEN must be 32-256 URL-safe characters")
        token_path = Path(settings.data_dir) / "api.token"
        if not token:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            try: os.chmod(token_path.parent, 0o700)
            except OSError: pass
            try:
                token = token_path.read_text(encoding="ascii").strip()
            except FileNotFoundError:
                token = secrets.token_urlsafe(32)
                try:
                    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                        stream.write(token + "\n"); stream.flush(); os.fsync(stream.fileno())
                except FileExistsError:
                    token = token_path.read_text(encoding="ascii").strip()
            try: os.chmod(token_path, 0o600)
            except OSError: pass
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("stored AEGIS API token is malformed")
        self.api_token = token
        try:
            remote_bind = not ipaddress.ip_address(settings.host).is_loopback
        except ValueError:
            remote_bind = settings.host not in {"localhost"}
        if remote_bind and (os.getenv("AEGIS_ALLOW_REMOTE", "0") != "1" or not os.getenv("AEGIS_API_TOKEN")):
            raise ValueError("remote API binding requires AEGIS_ALLOW_REMOTE=1 and AEGIS_API_TOKEN; use a TLS-terminating trusted proxy")
        self.sessions = SessionManager(settings.max_sessions)
        self.deduplicator = RequestDeduplicator()
        self.model_configuration = ModelConfiguration.from_environment()
        self.model_registry = ModelRegistry(("models",))
        self.model_registry.discover()
        self.model_manager = ModelManager(self.model_configuration, self.model_registry)
        self.inference_engine = InferenceEngine(self.model_manager, self.model_configuration)
        self.provider_router = ProviderRouter(local=self.inference_engine)
        conversation_store = ConversationStore(Path(settings.data_dir) / "conversations.db") if settings.persist_conversations else None
        self.conversations = ConversationEngine(self.inference_engine, conversation_store,
                                                persist=settings.persist_conversations)
        self.memory = MemoryStore(Path(settings.data_dir) / "memory.db")
        self.voice = VoiceService(VoicePreferencesStore(Path(settings.data_dir) / "voice_preferences.json"),
                                  PiperProvider.from_environment())
        stt_provider = None
        if os.getenv("AEGIS_STT_PROVIDER", "").lower() == "whisper.cpp":
            stt_provider = WhisperCppRecognizer(os.getenv("AEGIS_WHISPER_MODEL"), os.getenv("AEGIS_WHISPER_EXECUTABLE", "whisper-cli"),
                                                float(os.getenv("AEGIS_STT_TIMEOUT_SECONDS", "60")))
        elif os.getenv("AEGIS_STT_PROVIDER", "").lower() == "vosk":
            stt_provider = VoskRecognizer(os.getenv("AEGIS_VOSK_MODEL"), float(os.getenv("AEGIS_STT_TIMEOUT_SECONDS", "60")))
        vad_config = VADConfig(threshold=float(os.getenv("AEGIS_VAD_THRESHOLD", "0.018")),
                               minimum_speech_ms=int(os.getenv("AEGIS_VAD_MIN_SPEECH_MS", "240")),
                               silence_ms=int(os.getenv("AEGIS_VAD_SILENCE_MS", "1100")),
                               max_recording_ms=int(os.getenv("AEGIS_VAD_MAX_RECORDING_MS", "30000")))
        self.stt = STTService(stt_provider, vad_config)
        self.audit = AuditLogger(Path(settings.data_dir) / "audit.log")
        self.safe_mode = SafeMode()
        self.firewall = FirewallManager(self.audit)
        self.resource_monitor = ResourceMonitor()
        self.security_components = SecurityComponentRegistry()
        repo_root = Path(settings.project_root).resolve() if getattr(settings, "project_root", "") else Path(__file__).resolve().parents[2]
        manifest_path = Path(settings.baseline_manifest) if getattr(settings, "baseline_manifest", "") else Path(settings.data_dir) / "trusted_baseline.json"
        self.release_integrity = ReleaseIntegrityManager(
            project_root=repo_root,
            manifest_path=manifest_path,
            audit=self.audit,
        )
        backend_dir = repo_root / "backend"
        protected_code = list(backend_dir.rglob("*.py")) if backend_dir.is_dir() else []
        protected_code.extend(path for path in (repo_root / "main.py", repo_root / ".env",
            repo_root / "frontend" / "index.html", repo_root / "config" / "logging.conf") if path.is_file())
        self.integrity = IntegrityMonitor(
            tuple(protected_code),
            release_manager=self.release_integrity,
            require_baseline=getattr(settings, "require_baseline", False),
        )
        self.security_components.register("safe_mode", self.safe_mode)
        self.security_components.register("firewall", self.firewall)
        self.security_components.register("integrity", self.integrity)
        self.security_components.register("release", self.release_integrity)
        self.security_components.register("resources", self.resource_monitor)
        self.security_components.register("audit", self.audit)
        self._rate_lock = threading.Lock()
        self._rate_events: dict[str, list[float]] = {}
        self._security_watchdog_stop = threading.Event()
        self._cpu_over_limit = 0
        self.traces = TraceStore(Path(settings.data_dir) / "traces.db")
        self.protocols = ProtocolRegistry(Path(settings.data_dir) / "protocols.db", self.audit)
        default_tools = frozenset({"system.status", "system.diagnostics", "knowledge.search", "time.now",
            "file.metadata", "document.read", "file.list", "file.search", "file.find", "file.hash",
            "data.csv.analyze", "data.json.inspect", "data.csv.transform", "data.csv.trend", "data.csv.chart",
            "code.syntax_check"})
        self.policy = PermissionPolicy.from_environment(default_tools=default_tools)
        self.security_components.register("policy", self.policy)
        protected_data = Path(settings.data_dir).expanduser().resolve()
        protected_metadata = (protected_data / "audit.log", protected_data / "api.token", protected_data / "backups")
        self.knowledge = KnowledgeIndex(Path(settings.data_dir) / "knowledge.db", settings.knowledge_root,
                                        protected_roots=protected_metadata)
        self.research = ResearchService(self.knowledge)
        self.desktop = DesktopController(self.policy, self.audit)
        self.tools = ToolRegistry(self.policy, self.audit)
        self.tools.safe_mode = self.safe_mode
        register_safe_tools(self.tools, self.status, lambda query: {"results": [
            {"source": result.source, "content": result.content} for result in self.knowledge.search(query)
        ]})
        configured_roots = tuple(item for item in os.getenv("AEGIS_FILE_ROOTS", "").split(os.pathsep) if item)
        file_roots = tuple(Path(item).expanduser().resolve() for item in configured_roots) or (Path(settings.knowledge_root).resolve(),)
        register_document_tools(self.tools, file_roots[0], file_roots[1:], protected_metadata)
        project_roots = tuple(item for item in os.getenv("AEGIS_PROJECT_ROOTS", "").split(os.pathsep) if item)
        if project_roots:
            register_code_tools(self.tools, tuple(Path(item).expanduser().resolve() for item in project_roots))
        else:
            register_code_tools(self.tools, ())
        register_system_tools(self.tools)
        register_data_tools(self.tools)
        register_knowledge_tools(self.tools, self.knowledge)
        web_provider = DuckDuckGoHTMLProvider() if os.getenv("AEGIS_WEB_PROVIDER", "").lower() == "duckduckgo" else None
        register_web_tools(self.tools, web_provider)
        self.mcp = MCPIntegration(self.tools, MCPServerConfig.from_environment())
        self.devices = DeviceIntegration()
        self.scheduler = TaskScheduler(self.audit, Path(settings.data_dir) / "scheduler.db")
        self.reasoning_store = TaskStore(Path(settings.data_dir) / "reasoning_tasks.db")
        self.protocol_resolver = ProtocolResolver(self.protocols, self.policy)
        self.reasoning = ReasoningEngine(self.inference_engine, self.tools, self.reasoning_store,
                                         protocol_resolver=self.protocol_resolver,
                                         protocol_registry=self.protocols)
        def scheduled_system_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
            resolution = self.protocol_resolver.resolve("scheduled system status snapshot", "system_operations")
            if resolution.blocked:
                raise RuntimeError("active protocol blocked scheduled system task")
            result = self.tools.execute("system.diagnostics", {}, actor="scheduler")
            if not result.ok: raise RuntimeError(result.error or "system diagnostics failed")
            return result.output
        self.scheduler.register_workflow("system.status.snapshot", scheduled_system_snapshot)
        self.agent_registry = AgentRegistry()
        self.agents = MultiAgentOrchestrator(
            self.agent_registry, SpecialistWorkflows(protocols=self.protocols, policy=self.policy),
            self.tools, self.policy, self.protocol_resolver, self.audit,
            max_parallel=int(os.getenv("AEGIS_AGENT_MAX_PARALLEL", "1")),
            timeout_seconds=float(os.getenv("AEGIS_AGENT_TIMEOUT_SECONDS", "30")))
        self.brain = CentralBrain(self.conversations, self.memory, self.tools, self.audit, self.traces,
                                  agents=self.agents, security_command=self._security_command)
        self.started_at = __import__("time").time()
        super().__init__((settings.host, settings.port), RequestHandler)
        startup_verify = self.release_integrity.verify(actor="startup")
        if startup_verify["ok"]:
            LOG.info("Trusted code baseline %s verified successfully (%d files)",
                     startup_verify.get("release_id"), startup_verify.get("checked_files", 0))
        else:
            if startup_verify.get("status") in {"mismatch", "corrupt_or_invalid_manifest"}:
                self.enter_safe_mode("startup_baseline_verification_failed: " + str(startup_verify.get("status")))
            elif startup_verify.get("status") == "missing_manifest":
                if getattr(settings, "require_baseline", False) or os.getenv("AEGIS_REQUIRE_BASELINE", "0") == "1":
                    self.enter_safe_mode("startup_baseline_missing")
                else:
                    LOG.warning("Trusted baseline manifest not found; running in unverified development mode.")
        self._security_watchdog = threading.Thread(target=self._watch_security, name="aegis-security-watch", daemon=True)
        self._security_watchdog.start()

    def _watch_security(self) -> None:
        max_rss = int(os.getenv("AEGIS_MAX_RSS_MB", "1800")) * 1024 * 1024
        data_root = Path(self.settings.data_dir)
        databases = ("memory.db", "knowledge.db", "protocols.db", "scheduler.db")
        while not self._security_watchdog_stop.wait(30):
            try:
                integrity = self.integrity.check()
                audit = self.audit.verify_integrity()
                resources = self.resource_monitor.snapshot()
                unhealthy_db = [name for name in databases if not sqlite_health(data_root / name)["ok"]]
                reasons = []
                if not integrity["ok"]: reasons.append("security_component_integrity_mismatch")
                if not audit["ok"]: reasons.append("audit_integrity_mismatch")
                if resources.get("memory_bytes") is not None and resources["memory_bytes"] > max_rss:
                    reasons.append("process_memory_limit_exceeded")
                cpu_limit = float(os.getenv("AEGIS_MAX_CPU_PERCENT", "250"))
                if resources.get("cpu_percent") is not None and resources["cpu_percent"] > cpu_limit:
                    self._cpu_over_limit += 1
                else:
                    self._cpu_over_limit = 0
                if self._cpu_over_limit >= 2: reasons.append("sustained_process_cpu_limit_exceeded")
                if resources.get("disk_free_bytes") is not None and resources["disk_free_bytes"] < int(os.getenv("AEGIS_MIN_FREE_DISK_MB", "128")) * 1024 * 1024:
                    reasons.append("low_disk_space")
                if unhealthy_db: reasons.append("database_integrity_failure")
                if reasons and not self.safe_mode.status()["active"]:
                    self.audit.record("security.monitor", actor="runtime_monitor", outcome="restricted",
                        details={"reasons": reasons, "databases": unhealthy_db,
                                 "resource_snapshot": resources})
                    self.enter_safe_mode(",".join(reasons))
            except Exception:
                # Fail closed if the monitor itself cannot establish the security state.
                self.safe_mode.enter("security_monitor_failure")

    def _security_command(self, request: str) -> tuple[str, bool] | None:
        text = request.strip().casefold().strip(" .!?")
        if text in {"enter safe mode", "activate safe mode", "enter aegis safe mode", "please enter safe mode"}:
            state = self.enter_safe_mode("explicit user text or voice request")
            return ("Safe mode is active. New tasks and external tools are blocked; active work was cancelled and voice capture and playback were stopped.", True)
        if text in {"check my firewall", "check firewall", "firewall status"}:
            result = self.firewall.inspect()
            return ("Firewall inspection: " + json.dumps(result, sort_keys=True, default=str)[:2500], False)
        if text in {"check aegis integrity", "check aegis integrity status", "verify aegis integrity"}:
            result = {"source": self.integrity.check(), "release": self.release_integrity.verify(record_audit=False), "audit": self.audit.verify_integrity()}
            return ("AEGIS integrity report: " + json.dumps(result, sort_keys=True)[:1500], False)
        if text in {"show suspicious activity", "check suspicious activity"}:
            events = self.audit.inspect(200)
            findings = [item for item in events if item.get("outcome") in {"denied", "denied_or_failed", "failed", "restricted", "blocked"}]
            return ("Recent audit entries that may warrant review (this is not malware detection): " +
                    json.dumps(findings[-20:], sort_keys=True, default=str)[:2500], False)
        if text in {"show recent security events", "show security events", "recent security events"}:
            events = [item for item in self.audit.inspect(100) if str(item.get("action", "")).startswith("security.") or
                      item.get("outcome") in {"denied", "failed", "restricted", "blocked"}]
            return ("Recent security and denied-operation audit events: " + json.dumps(events[-30:], sort_keys=True, default=str)[:2500], False)
        if text in {"run a security check", "run security check", "security check"}:
            report = {"safe_mode": self.safe_mode.status(), "integrity": self.integrity.check(),
                "audit_integrity": self.audit.verify_integrity(), "firewall": self.firewall.inspect(),
                "resources": self.resource_monitor.snapshot()}
            return ("Security check results: " + json.dumps(report, sort_keys=True, default=str)[:3000], False)
        return None

    def status(self) -> dict[str, Any]:
        provider = self.provider_router.status()
        inference = self.inference_engine.status()
        inference["local_preferred"] = True
        inference["remote_configured"] = provider["remote_configured"]
        return {"service": "aegis", "status": "ok", "model": inference["model"], "sessions": self.sessions.count(),
                "inference": inference, "voice": self.voice.status(), "stt": self.stt.health(), "tools": len(self.tools.list()),
                "agents": self.agents.status(), "protocols": len(self.protocols.search()),
                "hardware": hardware_profile(), "mcp": self.mcp.status(), "devices": self.devices.status(),
                "security": {"safe_mode": self.safe_mode.status(), "integrity": self.integrity.check()["ok"],
                             "audit_integrity": self.audit.verify_integrity()["ok"]}}

    def enter_safe_mode(self, reason: str) -> dict[str, Any]:
        self.safe_mode.enter(reason)
        if hasattr(self, "agents"):
            self.agents.cancel_all()
        if hasattr(self, "tools"):
            for request_id in tuple(getattr(self.tools, "_run_cancellations", {})):
                self.tools.cancel(request_id)
        if hasattr(self, "scheduler"):
            for task in self.scheduler.list():
                if task.status == "scheduled": self.scheduler.pause(task.task_id)
        if hasattr(self, "stt"):
            self.stt.cancel_all()
        if hasattr(self, "voice") and self.voice.status()["microphone_active"]:
            self.voice.set_microphone(False)
        if hasattr(self, "voice"):
            self.voice.stop()
        if hasattr(self, "mcp"):
            self.mcp.disconnect()
        self.audit.record("security.safe_mode", actor="local_user", outcome="entered", details={"reason": reason[:300]})
        return self.safe_mode.status()

    def recover_safe_mode(self, *, confirmed: bool = False) -> tuple[bool, str]:
        if not confirmed:
            return False, "confirmation_required"
        integrity = self.integrity.check()
        audit = self.audit.verify_integrity()
        if not integrity["ok"] or not audit["ok"]:
            return False, "integrity_checks_must_pass_before_recovery"
        recovered = self.safe_mode.recover(confirmed=True)
        self.audit.record("security.safe_mode", actor="local_user", outcome="recovered", details={})
        return recovered, "ok"

    def server_close(self) -> None:
        self._security_watchdog_stop.set()
        if hasattr(self, "_security_watchdog"):
            self._security_watchdog.join(timeout=2)
        if hasattr(self, "tools"):
            self.tools.shutdown()
        if hasattr(self, "agents"):
            self.agents.shutdown()
        if hasattr(self, "scheduler"):
            self.scheduler.shutdown()
        if hasattr(self, "inference_engine"):
            self.inference_engine.unload()
        if hasattr(self, "stt"):
            self.stt.unload()
        super().server_close()


class RequestHandler(BaseHTTPRequestHandler):
    server: AegisServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        origin = self.headers.get("Origin")
        if origin and self._same_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin and self._same_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        public_shell = self.path in {"/", "/index.html"} and self.headers.get("Upgrade", "").lower() != "websocket"
        if not public_shell and not self._security_gate(): return
        if self.headers.get("Upgrade", "").lower() == "websocket" and self.path in {"/", "/api/ws"}:
            self._websocket()
        elif self.path == "/api/health":
            self._json(200, self.server.status())
        elif self.path == "/api/status":
            self._json(200, self.server.status())
        elif self.path == "/api/security/status":
            firewall = self.server.firewall.inspect()
            self._json(200, {"components": self.server.security_components.status(),
                "authentication": {"token_configured": bool(self.server.api_token),
                    "remote_access": os.getenv("AEGIS_ALLOW_REMOTE", "0") == "1"},
                "safe_mode": self.server.safe_mode.status(),
                "integrity": self.server.integrity.check(), "audit_integrity": self.server.audit.verify_integrity(),
                "firewall": firewall, "resources": self.server.resource_monitor.snapshot(),
                "databases": {name: sqlite_health(Path(self.server.settings.data_dir) / filename)
                    for name, filename in {"memory": "memory.db", "knowledge": "knowledge.db",
                    "protocols": "protocols.db", "scheduler": "scheduler.db"}.items()}})
        elif self.path == "/api/security/firewall":
            self._json(200, self.server.firewall.inspect())
        elif self.path == "/api/security/events":
            self._json(200, {"events": self.server.audit.inspect(100)})
        elif self.path == "/api/security/report":
            self._json(200, {"status": self.server.status(), "integrity": self.server.integrity.check(),
                "audit_integrity": self.server.audit.verify_integrity(), "firewall": self.server.firewall.inspect(),
                "resources": self.server.resource_monitor.snapshot()})
        elif self.path == "/api/security/baseline":
            self._json(200, self.server.release_integrity.verify(record_audit=False))
        elif self.path.startswith("/api/memory"):
            self._memory_get()
        elif self.path == "/api/voice/status":
            self._json(200, self.server.voice.status())
        elif self.path == "/api/voice/preferences":
            self._json(200, self._voice_preferences_dict(self.server.voice.get_preferences()))
        elif self.path == "/api/tools":
            self._json(200, {"tools": self.server.tools.list()})
        elif self.path == "/api/mcp/status":
            self._json(200, self.server.mcp.status())
        elif self.path == "/api/devices/status":
            self._json(200, self.server.devices.status())
        elif urlparse(self.path).path == "/api/scheduler/tasks":
            query = parse_qs(urlparse(self.path).query)
            tasks = [self._json_safe(task) for task in self.server.scheduler.list()]
            self._json(200, {"tasks": tasks,
                             "history": self.server.scheduler.history(query.get("task_id", [None])[0])})
        elif self.path == "/api/agents":
            self._json(200, self.server.agents.status())
        elif self.path.startswith("/api/agents/runs/"):
            request_id = unquote(self.path.rsplit("/", 1)[-1])
            result = self.server.agents.get_run(request_id)
            self._json(200 if result else 404, result.as_dict() if result else {"error": "agent_run_not_found"})
        elif self.path.startswith("/api/agents/"):
            identifier = unquote(self.path.rsplit("/", 1)[-1])
            spec = self.server.agent_registry.get(identifier)
            task = self.server.agents.get(identifier)
            value = spec.as_dict() if spec else (self._task_dict(task) if task else None)
            self._json(200 if value else 404, value if value else {"error": "agent_or_task_not_found"})
        elif urlparse(self.path).path == "/api/reasoning/tasks":
            query = parse_qs(urlparse(self.path).query)
            task_id = query.get("id", [None])[0]
            task = self.server.reasoning_store.get(task_id) if task_id else None
            self._json(200, task if task_id and task else {"tasks": self.server.reasoning_store.list()})
        elif self.path == "/api/models/recommendations":
            self._json(200, {"hardware": hardware_profile(), "models": recommendations()})
        elif self.path == "/api/models":
            self._json(200, {"active": self.model_manager.status(),
                             "models": [model.as_dict() for model in self.model_registry.list()]})
        elif urlparse(self.path).path == "/api/protocols/compliance":
            query = parse_qs(urlparse(self.path).query)
            self._json(200, {"reports": self.server.protocols.compliance_history(
                task_id=query.get("task_id", [None])[0], protocol_id=query.get("protocol_id", [None])[0])})
        elif urlparse(self.path).path.startswith("/api/protocols/"):
            path = urlparse(self.path).path.rstrip("/")
            parts = [unquote(part) for part in path.split("/") if part]
            if len(parts) >= 4 and parts[3] == "compliance":
                self._json(200, {"reports": self.server.protocols.compliance_history(protocol_id=parts[2])})
            elif len(parts) >= 4 and parts[3] == "versions":
                self._json(200, {"protocol_id": parts[2], "versions": [item.as_dict() for item in self.server.protocols.history(parts[2])]})
            elif len(parts) == 4 and parts[3] == "version":
                query = parse_qs(urlparse(self.path).query)
                try: spec = self.server.protocols.get_version(parts[2], int(query.get("version", [""])[0]))
                except ValueError: spec = None
                self._json(200 if spec else 404, spec.as_dict() if spec else {"error": "protocol_version_not_found"})
            else:
                spec = self.server.protocols.get(parts[2]) if len(parts) == 3 else None
                self._json(200 if spec else 404, spec.as_dict() if spec else {"error": "protocol_not_found"})
        elif urlparse(self.path).path == "/api/protocols" or urlparse(self.path).path == "/api/protocols/search":
            query = parse_qs(urlparse(self.path).query)
            status = query.get("status", [None])[0]
            try: protocol_status = ProtocolStatus(status) if status else None
            except ValueError: self._json(400, {"error": "invalid_protocol_status"}); return
            self._json(200, {"protocols": [item.as_dict() for item in self.server.protocols.search(
                query.get("q", [""])[0], status=protocol_status, category=query.get("category", [None])[0])]})
        elif self.path == "/api/traces":
            self._json(200, {"traces": self.server.traces.list()})
        elif self.path == "/api/traces/analysis":
            self._json(200, self.server.traces.analyze())
        elif self.path.startswith("/api/knowledge/search"):
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            self._json(200, {"results": [{"source": result.source, "content": result.content} for result in self.server.knowledge.search(query)]})
        elif self.path.startswith("/api/agents/"):
            task = self.server.agents.get(unquote(self.path.rsplit("/", 1)[-1]))
            self._json(200 if task else 404, self._task_dict(task) if task else {"error": "task_not_found"})
        elif self.path in {"/", "/index.html"}:
            self._serve_frontend()
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._security_gate(mutation=True): return
        if self.server.safe_mode.status()["active"] and self.path not in {"/api/security/safe-mode/recover", "/api/security/shutdown"}:
            self._json(423, {"error": "safe_mode_active"})
            return
        if self.path == "/api/security/safe-mode":
            body = self._read_json() or {}
            status = self.server.enter_safe_mode(str(body.get("reason", "explicit user request")))
            self._json(200, status)
            return
        if self.path == "/api/security/safe-mode/recover":
            body = self._read_json() or {}
            if body.get("confirmed") is not True or not self._explicit_user_approval():
                self._json(403, {"error": "explicit_confirmation_required"}); return
            recovered, reason = self.server.recover_safe_mode(confirmed=True)
            if not recovered:
                self._json(409, {"error": reason,
                                 "integrity": self.server.integrity.check(),
                                 "audit_integrity": self.server.audit.verify_integrity()}); return
            self._json(200, {"recovered": True, "status": self.server.safe_mode.status()})
            return
        if self.path == "/api/security/firewall/proposals":
            body = self._read_json() or {}
            try:
                proposal = self.server.firewall.propose_allow(body.get("port"), body.get("protocol", "tcp"), body.get("source"))
                self._json(201, proposal)
            except (ValueError, PermissionError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path == "/api/security/firewall/apply":
            body = self._read_json() or {}
            if body.get("confirmed") is not True or not self._explicit_user_approval():
                self._json(403, {"error": "explicit_confirmation_required"}); return
            try:
                self._json(200, self.server.firewall.apply(str(body.get("proposal_id", "")), approved=True))
            except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                self._json(400, {"error": str(exc)[:300]})
            return
        if self.path == "/api/security/backup":
            body = self._read_json() or {}
            if body.get("confirmed") is not True:
                self._json(403, {"error": "explicit_confirmation_required"}); return
            try:
                source = Path(str(body.get("source", ""))).resolve()
                data_root = Path(self.server.settings.data_dir).resolve()
                source.relative_to(data_root)
                destination = data_root / "backups" / (source.name + "." + secrets.token_hex(6) + ".bak")
                self._json(201, sqlite_backup(source, destination))
            except (ValueError, OSError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)[:300]})
            return
        if self.path == "/api/security/baseline/verify":
            res = self.server.release_integrity.verify(record_audit=True, actor="api_user")
            self._json(200 if res["ok"] else 409, res)
            return
        if self.path == "/api/security/baseline/create":
            body = self._read_json() or {}
            if body.get("reviewed") is not True or not self._explicit_user_approval():
                self.server.audit.record("baseline.create", actor="local_user", outcome="denied",
                                         details={"reason": "explicit_review_required"})
                self._json(403, {"error": "explicit_review_required"})
                return
            try:
                release_id = str(body.get("release_id", "")).strip()
                result = self.server.release_integrity.create_baseline(
                    release_id,
                    reviewed=True,
                    actor="local_user",
                )
                self._json(201, result)
            except (ValueError, PermissionError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path == "/api/security/baseline/update":
            body = self._read_json() or {}
            if body.get("confirmed") is not True or body.get("reviewed") is not True or not self._explicit_user_approval():
                self.server.audit.record("baseline.update", actor="local_user", outcome="denied",
                                         details={"reason": "explicit_review_and_confirmation_required"})
                self._json(403, {"error": "explicit_review_and_confirmation_required"})
                return
            try:
                release_id = str(body.get("release_id", "")).strip()
                result = self.server.release_integrity.update_baseline(
                    release_id,
                    reviewed=True,
                    confirmed=True,
                    actor="local_user",
                )
                self._json(200, result)
            except (ValueError, PermissionError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path == "/api/mcp/connect":
            body = self._read_json() or {}
            if not self._local_client() or body.get("confirmed") is not True:
                self._json(403, {"error": "local_explicit_confirmation_required"})
                return
            if not self.server.policy.allow_network or self.server.policy.max_level < PermissionLevel.NETWORK:
                self._json(403, {"error": "network_permission_disabled"})
                return
            try:
                resolution = self.server.protocol_resolver.resolve("connect explicitly approved MCP integration", "external_integrations")
                if resolution.blocked:
                    self._json(403, {"error": "protocol_blocked", "conflicts": list(resolution.conflicts)})
                    return
                self.server.mcp.connect()
                self.server.audit.record("mcp.connect", actor="local_user", outcome="connected",
                    details={"server_id": self.server.mcp.config.server_id if self.server.mcp.config else None,
                             "tool_names": self.server.mcp.status()["tools"],
                             "protocols": [{"protocol_id": item.protocol_id, "version": item.version} for item in resolution.protocols]})
                self._json(200, self.server.mcp.status())
            except (ValueError, PermissionError, RuntimeError, OSError) as exc:
                self.server.audit.record("mcp.connect", actor="local_user", outcome="failed", details={"error": str(exc)[:500]})
                self._json(502, {"error": str(exc)[:500], "status": self.server.mcp.status()})
            return
        if self.path == "/api/mcp/approve-tools":
            body = self._read_json() or {}
            if not self._local_client() or body.get("confirmed") is not True:
                self._json(403, {"error": "local_explicit_confirmation_required"})
                return
            try:
                selected = self.server.mcp.approve_tools(body.get("tool_ids"))
                updated = replace(self.server.policy, allowed_tools=self.server.policy.allowed_tools | frozenset(selected))
                self.server.policy = updated
                self.server.tools.policy = updated
                self.server.agents.policy = updated
                self.server.protocol_resolver.policy = updated
                self.server.desktop.policy = updated
                self.server.audit.record("mcp.tools.approve", actor="local_user", outcome="approved",
                                         details={"tool_ids": selected})
                self._json(200, self.server.mcp.status())
            except (ValueError, PermissionError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)[:500]})
            return
        if self.path == "/api/mcp/disconnect":
            if not self._local_client():
                self._json(403, {"error": "local_owner_required"})
                return
            revoked = tuple(self.server.mcp.approved_tool_names)
            self.server.mcp.disconnect()
            updated = replace(self.server.policy, allowed_tools=self.server.policy.allowed_tools - frozenset(revoked))
            self.server.policy = updated
            self.server.tools.policy = updated
            self.server.agents.policy = updated
            self.server.protocol_resolver.policy = updated
            self.server.desktop.policy = updated
            self.server.audit.record("mcp.disconnect", actor="local_user", outcome="disconnected", details={})
            self._json(200, self.server.mcp.status())
            return
        if self.path == "/api/scheduler/tasks":
            body = self._read_json()
            if not self._local_client() or not body or body.get("confirmed") is not True:
                self._json(403, {"error": "local_explicit_confirmation_required"})
                return
            try:
                task = self.server.scheduler.schedule_workflow(
                    body.get("workflow_id", ""), body.get("payload", {}),
                    delay_seconds=float(body.get("delay_seconds", 0)),
                    interval_seconds=float(body.get("interval_seconds", 0)), confirmed=True,
                    max_retries=int(body.get("max_retries", 0)), owner="local_user")
                self._json(201, self._json_safe(task))
            except (ValueError, PermissionError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path.startswith("/api/scheduler/tasks/"):
            parts = [unquote(item) for item in self.path.split("/") if item]
            if len(parts) != 4:
                self._json(404, {"error": "scheduler_route_not_found"})
                return
            task_id, action = parts[2], parts[3]
            body = self._read_json() or {}
            if not self._local_client():
                self._json(403, {"error": "local_owner_required"})
                return
            confirmed = body.get("confirmed") is True
            if action == "cancel": changed = self.server.scheduler.cancel(task_id)
            elif action == "pause": changed = self.server.scheduler.pause(task_id)
            elif action == "resume": changed = self.server.scheduler.resume(task_id, confirmed=confirmed)
            else:
                self._json(404, {"error": "scheduler_action_not_found"})
                return
            self._json(200 if changed else 409, {"changed": changed, "task_id": task_id, "action": action})
            return
        if self.path == "/api/reasoning/tasks":
            body = self._read_json()
            if body is None or not isinstance(body.get("request"), str) or not body["request"].strip():
                self._json(400, {"error": "invalid_reasoning_request"})
                return
            try:
                if re.search(r"(?i)\b(?:create|draft|define|add)\s+(?:a\s+)?protocol\b", body["request"]):
                    self.server.policy.authorize_protocol_mutation("create_draft", local_client=self._local_client())
                    draft = self._protocol_spec({"instruction": body["request"]})
                    from backend.protocols.validator import validate_protocol
                    errors = validate_protocol(draft)
                    if errors:
                        self._json(422, {"clarification_required": True, "missing_or_invalid": errors,
                                         "candidate": draft.as_dict()})
                        return
                    saved = self.server.protocols.create(draft)
                    self._json(201, {"kind": "protocol_draft", "draft": saved.as_dict(),
                                     "approval_required": True})
                    return
                session_id = str(body.get("session_id", ""))
                preparation = self.server.brain.prepare(BrainRequest(session_id, secrets.token_urlsafe(8), body["request"]))
                recent = tuple(self.server.conversations.history(session_id)) if session_id else ()
                intent, plan = self.server.reasoning.plan(
                    body["request"], recent=recent, memories=preparation.memory_context,
                    instructions=self.server.model_configuration.system_prompt,
                    task_id=str(body["task_id"]) if body.get("task_id") else None)
                result = self.server.reasoning.orchestrator.execute(plan) if body.get("execute") is True else None
                response = {"intent": {"category": intent.category.value, "objective": intent.objective,
                            "subtasks": intent.subtasks, "constraints": intent.constraints,
                            "required_information": intent.required_information, "expected_output": intent.expected_output,
                            "risk_level": intent.risk_level, "required_permissions": intent.required_permissions,
                            "confidence": intent.confidence, "source": intent.source},
                            "plan": plan, "result": result}
                self._json(201, self._json_safe(response))
            except (ValueError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path.startswith("/api/reasoning/tasks/") and self.path.endswith("/cancel"):
            task_id = unquote(self.path.split("/")[-2])
            cancelled = self.server.reasoning.orchestrator.cancel(task_id)
            self._json(200 if cancelled else 409, {"cancelled": cancelled})
            return
        if self.path == "/api/models/active":
            body = self._read_json()
            if body is None or not isinstance(body.get("model_id"), str):
                self._json(400, {"error": "invalid_model_selection"})
                return
            try:
                selected = self.server.model_manager.switch_model(body["model_id"])
                self._json(200, {"model": selected.as_dict(), "active": self.server.model_manager.status()})
            except (ValueError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path == "/api/voice/test":
            self.server.voice.speak("AEGIS voice test.", f"voice-test-{secrets.token_urlsafe(8)}")
            status = self.server.voice.status()
            self._json(200 if status["available"] else 503, status)
            return
        if self.path == "/api/voice/stop":
            self.server.voice.stop()
            self._json(200, self.server.voice.status())
            return
        if self.path == "/api/voice/microphone":
            body = self._read_json()
            if body is None or not isinstance(body.get("enabled"), bool):
                self._json(400, {"error": "invalid_microphone_request"})
                return
            self.server.voice.set_microphone(body["enabled"])
            if not body["enabled"]:
                self.server.stt.cancel_all()
            self._json(200, self.server.voice.status())
            return
        if self.path == "/api/memory":
            self._memory_post()
            return
        if self.path == "/api/agents/tasks":
            body = self._read_json()
            if body is None or not isinstance(body.get("instruction"), str):
                self._json(400, {"error": "invalid_task"})
                return
            try:
                request_id = body.get("request_id") or secrets.token_urlsafe(12)
                if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                    raise ValueError("request_id must be a non-empty string of at most 128 characters")
                instruction = body["instruction"].strip()
                if not instruction or len(instruction) > self.server.settings.max_message_chars:
                    raise ValueError("instruction is empty or too long")
                selected = self.server.agents.classify(instruction)
                if not selected:
                    self._json(201, {"request_id": request_id, "status": "completed", "delegated": False,
                                     "selected_agents": [],
                                     "message": "No specialist is required; use the conversation endpoint for general requests."})
                    return
                if body.get("run", True) is not True:
                    plan = self.server.agents.plan(request_id, instruction, selected)
                    self._json(200, {"request_id": request_id, "status": "planned",
                                     "tasks": [{"task_id": item.task_id, "agent_id": item.agent_id,
                                                "dependencies": list(item.dependencies)} for item in plan]})
                    return
                session_id = str(body.get("session_id", ""))
                preparation = self.server.brain.prepare(BrainRequest(session_id, request_id, instruction))
                memories = tuple({"memory_id": memory_id, "content": content}
                                 for memory_id, content in zip(preparation.memory_ids, preparation.memory_context))
                history = self.server.conversations.history(session_id) if session_id else []
                recent = tuple({"role": item.role, "content": item.content[:1000]} for item in history[-2:])
                result = self.server.agents.run(request_id, instruction, source="api", memories=memories,
                                                recent_turns=recent, local_client=self._local_client())
                self._json(201, result.as_dict() if result else {"error": "no_specialist_selected"})
            except (ValueError, KeyError, RuntimeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path.startswith("/api/agents/tasks/") and self.path.endswith("/cancel"):
            request_id = unquote(self.path.split("/")[-2])
            cancelled = self.server.agents.cancel(request_id)
            self._json(200 if cancelled else 409, {"cancelled": cancelled})
            return
        if self.path.startswith("/api/tools/runs/") and self.path.endswith("/cancel"):
            if not self._local_client():
                self._json(403, {"error": "local_owner_required"})
                return
            request_id = unquote(self.path.split("/")[-2])
            cancelled = self.server.tools.cancel(request_id)
            self._json(202 if cancelled else 409, {"cancelled": cancelled, "request_id": request_id})
            return
        if self.path.startswith("/api/tools/"):
            body = self._read_json() or {}
            tool_name = unquote(self.path.rsplit("/", 1)[-1])
            spec = self.server.tools.select(tool_name)
            confirmed = body.get("confirmed", False) is True
            if spec and spec.permission.name != "READ_ONLY" and confirmed and not self._explicit_user_approval():
                self._json(403, {"name": tool_name, "ok": False,
                    "error": "sensitive_tool_requires_configured_user_token_and_confirmation"})
                return
            arguments = body.get("arguments", {key:value for key,value in body.items() if key not in {"confirmed", "request_id", "intent"}})
            request_intent = body.get("intent", "")
            if not isinstance(request_intent, str) or len(request_intent) > 1000:
                self._json(400, {"error": "invalid_tool_intent"})
                return
            resolution_result = self._resolve_and_execute_tool(tool_name, arguments, confirmed=confirmed,
                actor="local_user", request_id=body.get("request_id"), request_intent=request_intent)
            result = resolution_result
            self._json(200 if result.ok else 403, {"name": result.name, "ok": result.ok, "output": result.output, "error": result.error})
            return
        if self.path == "/api/knowledge/ingest":
            body = self._read_json()
            if body is None or not isinstance(body.get("path"), str):
                self._json(400, {"error": "invalid_knowledge_document"})
                return
            if not self._local_client() or body.get("confirmed") is not True:
                self._json(403, {"error": "local_explicit_confirmation_required"})
                return
            result = self._resolve_and_execute_tool("knowledge.ingest", {"path": body["path"]},
                                                    actor="local_user", confirmed=True)
            self._json(201 if result.ok else 403, {"result": result.output, "error": result.error})
            return
        if self.path in {"/api/protocols/draft/preview", "/api/protocols/draft", "/api/protocols"}:
            body = self._read_json()
            if body is None: self._json(400, {"error": "invalid_protocol"}); return
            preview = self.path.endswith("/preview")
            if not preview:
                try:
                    self.server.policy.authorize_protocol_mutation("create_draft", local_client=self._local_client())
                except PolicyPermissionError as exc:
                    self._json(403, {"error": str(exc)}); return
            try:
                spec = self._protocol_spec(body)
                from backend.protocols.validator import validate_protocol
                errors = validate_protocol(spec)
                if errors:
                    self._json(400, {"error": "invalid_protocol", "validation_errors": errors, "draft": spec.as_dict()}); return
                saved = self.server.protocols.create(spec) if not preview else spec
                self._json(200 if preview else 201, {"draft": saved.as_dict(), "approval_required": True,
                            "validation_errors": [], "preview": preview})
            except (TypeError, ValueError, ProtocolError) as exc: self._json(400, {"error": str(exc)})
            return
        if self.path.startswith("/api/protocols/"):
            parts = [unquote(part) for part in self.path.split("?")[0].strip("/").split("/")]
            protocol_id = parts[2] if len(parts) >= 3 else ""
            body = self._read_json() or {}
            action = str(body.get("action", parts[3] if len(parts) >= 4 else ""))
            try:
                self.server.policy.authorize_protocol_mutation(action, local_client=self._local_client(),
                                                               confirmed=body.get("confirmed") is True)
                if action == "approve": result = self.server.protocols.approve(protocol_id)
                elif action == "activate": result = self.server.protocols.activate(protocol_id)
                elif action == "deactivate": result = self.server.protocols.deactivate(protocol_id)
                elif action == "archive": result = self.server.protocols.archive(protocol_id)
                elif action == "restore": result = self.server.protocols.restore(protocol_id)
                elif action == "revise":
                    changes = body.get("changes", {})
                    if not isinstance(changes, dict): raise ValueError("changes must be an object")
                    result = self.server.protocols.revise(protocol_id, **changes)
                else: raise ProtocolError("unknown protocol action")
                self._json(200, result.as_dict())
            except PolicyPermissionError as exc: self._json(403, {"error": str(exc)})
            except (ProtocolError, TypeError, ValueError) as exc: self._json(400, {"error": str(exc)})
            return
        if self.path != "/api/messages":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > self.server.settings.max_message_chars * 2:
            self._json(413, {"error": "request_too_large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            message = body["message"]
            if not isinstance(message, str) or not message.strip() or len(message) > self.server.settings.max_message_chars:
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._json(400, {"error": "invalid_message"})
            return
        self._json(501, {"error": "inference_not_configured", "message": "No inference provider is configured in Step 1."})

    def do_PUT(self) -> None:
        if not self._security_gate(mutation=True): return
        if self.server.safe_mode.status()["active"]:
            self._json(423, {"error": "safe_mode_active"}); return
        if self.path.startswith("/api/agents/") and self.path.endswith("/tools/grant"):
            if not self._local_client():
                self._json(403, {"error": "local_administrator_required"})
                return
            body=self._read_json() or {}
            tools=body.get("allowed_tools")
            if body.get("confirmed") is not True or not isinstance(tools,list) or any(not isinstance(item,str) for item in tools):
                self._json(400,{"error":"explicit_confirmation_and_tool_list_required"})
                return
            agent_id=unquote(self.path.split("/")[-3])
            try:
                if any(self.server.tools.select(item) is None for item in tools):
                    raise ValueError("requested capability is not registered")
                spec=self.server.agent_registry.grant_approved_tools(agent_id,tuple(tools),self.server.policy.allowed_tools)
                self.server.audit.record("agent.tools.grant",actor="local_user",outcome="approved",
                                         details={"agent_id":agent_id,"tools":tools})
                self._json(200,spec.as_dict())
            except KeyError:
                self._json(404,{"error":"agent_not_found"})
            except ValueError as exc:
                self._json(400,{"error":str(exc)})
            return
        if self.path.startswith("/api/agents/") and self.path.endswith("/tools"):
            if not self._local_client():
                self._json(403, {"error": "local_administrator_required"})
                return
            body = self._read_json()
            tools = body.get("allowed_tools") if body else None
            if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
                self._json(400, {"error": "invalid_agent_tool_permissions"})
                return
            agent_id = unquote(self.path.split("/")[-2])
            try:
                spec = self.server.agent_registry.restrict_tools(agent_id, tuple(tools))
                self._json(200, spec.as_dict())
            except KeyError:
                self._json(404, {"error": "agent_not_found"})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if self.path.startswith("/api/agents/") and self.path.endswith("/enabled"):
            if not self._local_client():
                self._json(403, {"error": "local_administrator_required"})
                return
            body = self._read_json()
            if body is None or not isinstance(body.get("enabled"), bool):
                self._json(400, {"error": "invalid_agent_configuration"})
                return
            agent_id = unquote(self.path.split("/")[-2])
            try:
                self._json(200, self.server.agent_registry.set_enabled(agent_id, body["enabled"]).as_dict())
            except KeyError:
                self._json(404, {"error": "agent_not_found"})
            return
        if self.path == "/api/voice/preferences":
            body = self._read_json()
            if body is None:
                self._json(400, {"error": "invalid_voice_preferences"})
                return
            try:
                current = self.server.voice.get_preferences()
                saved = self.server.voice.save_preferences(VoicePreferences(
                    voice=str(body.get("voice", current.voice)),
                    speaking_rate=float(body.get("speaking_rate", current.speaking_rate)),
                    volume=float(body.get("volume", current.volume)),
                    muted=bool(body.get("muted", current.muted)),
                    microphone_enabled=bool(body.get("microphone_enabled", current.microphone_enabled)),
                ))
                self._json(200, self._voice_preferences_dict(saved))
            except (TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if not self.path.startswith("/api/memory/"):
            self._json(404, {"error": "not_found"})
            return
        memory_id = unquote(self.path.rsplit("/", 1)[-1])
        body = self._read_json()
        if body is None or not isinstance(body.get("content"), str):
            self._json(400, {"error": "invalid_memory"})
            return
        try:
            self._json(200, self._memory_dict(self.server.memory.update(memory_id, body["content"])))
        except (KeyError, SensitiveMemoryError, ValueError) as exc:
            self._json(404 if isinstance(exc, KeyError) else 400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        if not self._security_gate(mutation=True): return
        if self.server.safe_mode.status()["active"]:
            self._json(423, {"error": "safe_mode_active"}); return
        if not self.path.startswith("/api/memory/"):
            self._json(404, {"error": "not_found"})
            return
        memory_id = unquote(self.path.rsplit("/", 1)[-1])
        if self.server.memory.delete(memory_id):
            self._json(200, {"deleted": True})
        else:
            self._json(404, {"error": "memory_not_found"})

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_000_000:
                return None
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def _local_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except (ValueError, IndexError):
            return False

    def _same_origin(self, origin: str) -> bool:
        try:
            parsed = urlparse(origin)
            host = self.headers.get("Host", "")
            return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()
        except ValueError:
            return False

    def _security_gate(self, *, mutation: bool = False) -> bool:
        now = __import__("time").monotonic()
        peer = self.client_address[0]
        with self.server._rate_lock:
            events = [stamp for stamp in self.server._rate_events.get(peer, []) if now - stamp < 60]
            if len(events) >= 120:
                self.server._rate_events[peer] = events
                self._json(429, {"error": "rate_limit_exceeded"})
                return False
            events.append(now)
            self.server._rate_events[peer] = events
        origin = self.headers.get("Origin")
        if origin and not self._same_origin(origin):
            self._json(403, {"error": "origin_not_allowed"}); return False
        supplied = self.headers.get("Authorization", "")
        valid = __import__("hmac").compare_digest(supplied, "Bearer " + self.server.api_token)
        if not valid and self.headers.get("Upgrade", "").lower() == "websocket":
            offered = [item.strip() for item in self.headers.get("Sec-WebSocket-Protocol", "").split(",")]
            valid = any(__import__("hmac").compare_digest(item, "bearer." + self.server.api_token) for item in offered)
        if not valid:
            self.server.audit.record("api.auth", actor=peer, outcome="denied", details={"path": self.path[:160]})
            self._json(401, {"error": "authentication_required"}); return False
        if mutation and not self._local_client():
            self._json(403, {"error": "remote_mutation_denied"}); return False
        protocol_lifecycle = self.path.startswith("/api/protocols/") and any(
            self.path.endswith("/" + action) for action in ("approve", "activate", "deactivate", "archive", "restore"))
        privileged = (self.path.startswith(("/api/mcp/", "/api/scheduler/tasks")) or protocol_lifecycle or
                      self.path == "/api/knowledge/ingest" or self.path.endswith(("/tools/grant", "/tools", "/enabled")) or
                      self.path.startswith("/api/security/firewall/apply") or
                      self.path.startswith("/api/security/safe-mode/recover") or
                      self.path.startswith("/api/security/backup") or
                      self.path.startswith("/api/security/baseline/update") or
                      self.path.startswith("/api/security/baseline/create"))
        if mutation and privileged and not self._explicit_user_approval():
            self._json(401, {"error": "configured_local_bearer_token_required"}); return False
        return True

    def _explicit_user_approval(self) -> bool:
        return bool(__import__("hmac").compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.api_token) and self._local_client())

    def _resolve_and_execute_tool(self, name: str, arguments: dict[str, Any], *, actor: str = "local_user",
                                  confirmed: bool = False, request_id: str | None = None,
                                  request_intent: str = "") -> ToolResult:
        category = ("system_operations" if name.startswith("system.") else
                    "research" if name.startswith("web.") or name.startswith("knowledge.") else
                    "coding" if name.startswith("code.") else
                    "data_analysis" if name.startswith("data.") else
                    "file_management" if name.startswith(("file.", "document.", "directory.")) else
                    "external_integrations" if name.startswith("mcp.") else "general_operational_procedures")
        intent = f"{request_intent[:1000]} execute approved tool {name}".strip()
        resolution = self.server.protocol_resolver.resolve(intent, category)
        applied = [{"protocol_id": item.protocol_id, "version": item.version} for item in resolution.protocols]
        if resolution.blocked:
            self.server.audit.record("tool.protocol_resolution", actor=actor, outcome="blocked",
                details={"tool": name, "protocols": applied, "conflicts": resolution.conflicts,
                         "blocked_reason": resolution.blocked_reason})
            return ToolResult(name, False, output={"protocols_applied": applied,
                "conflicts": list(resolution.conflicts), "blocked_reason": resolution.blocked_reason},
                error="protocol_blocked")
        self.server.audit.record("tool.protocol_resolution", actor=actor, outcome="resolved",
                                 details={"tool": name, "protocols": applied})
        result = self.server.tools.execute(name, arguments, actor=actor, confirmed=confirmed, request_id=request_id)
        if result.ok and applied:
            result = ToolResult(name, True, {**result.output, "protocols_applied": applied})
        return result

    @staticmethod
    def _protocol_spec(body: dict[str, Any]) -> ProtocolSpec:
        instruction = body.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            from backend.protocols.parser import ProtocolDraftParser
            parsed = ProtocolDraftParser().parse(instruction)
            # Explicit structured fields may refine the deterministic draft.
            allowed = {key: body[key] for key in (
                "name", "description", "purpose", "scope", "category", "priority", "definitions",
                "preconditions", "triggers", "required_permissions", "rules", "procedures",
                "expected_outputs", "exceptions", "failure_handling", "verification", "protocol_id") if key in body}
            for key in ("preconditions", "triggers", "required_permissions", "rules", "procedures", "expected_outputs", "exceptions", "failure_handling", "verification"):
                if key in allowed: allowed[key] = RequestHandler._protocol_sequence(body, key)
            return replace(parsed, **allowed)
        return ProtocolSpec(
            name=body.get("name", ""), purpose=body.get("purpose", ""), scope=body.get("scope", ""),
            description=body.get("description", ""), category=body.get("category", "general_operational_procedures"),
            priority=body.get("priority", 100), definitions=body.get("definitions", {}),
            preconditions=RequestHandler._protocol_sequence(body, "preconditions"),
            triggers=RequestHandler._protocol_sequence(body, "triggers"),
            required_permissions=RequestHandler._protocol_sequence(body, "required_permissions"),
            rules=RequestHandler._protocol_sequence(body, "rules"),
            procedures=RequestHandler._protocol_sequence(body, "procedures"),
            expected_outputs=RequestHandler._protocol_sequence(body, "expected_outputs"),
            exceptions=RequestHandler._protocol_sequence(body, "exceptions"),
            failure_handling=RequestHandler._protocol_sequence(body, "failure_handling"),
            verification=RequestHandler._protocol_sequence(body, "verification"),
            protocol_id=body.get("protocol_id") or __import__("uuid").uuid4().hex,
        )

    @staticmethod
    def _protocol_sequence(body: dict[str, Any], field: str) -> tuple[str, ...]:
        value = body.get(field, ())
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be an array of strings")
        return tuple(value)

    def _memory_get(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        category_value = query.get("category", [None])[0]
        try:
            category = MemoryCategory(category_value) if category_value else None
            records = self.server.memory.search(query.get("q", [""])[0], category=category) if query.get("q") else self.server.memory.inspect(category=category)
        except ValueError:
            self._json(400, {"error": "invalid_memory_category"})
            return
        self._json(200, {"memories": [self._memory_dict(record) for record in records]})

    def _memory_post(self) -> None:
        body = self._read_json()
        if body is None or not isinstance(body.get("category"), str) or not isinstance(body.get("content"), str):
            self._json(400, {"error": "invalid_memory"})
            return
        try:
            record = self.server.memory.store(
                MemoryCategory(body["category"]), body["content"], source=str(body.get("source", "user")),
                retention_seconds=body.get("retention_seconds"), explicit=bool(body.get("explicit", False)),
            )
            self._json(201, self._memory_dict(record))
        except (SensitiveMemoryError, PermissionError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    @staticmethod
    def _memory_dict(record: Any) -> dict[str, Any]:
        return {"memory_id": record.memory_id, "category": record.category.value, "content": record.content,
                "source": record.source, "created_at": record.created_at, "expires_at": record.expires_at,
                "explicit": record.explicit}

    @staticmethod
    def _voice_preferences_dict(preferences: VoicePreferences) -> dict[str, Any]:
        return {"voice": preferences.voice, "speaking_rate": preferences.speaking_rate, "volume": preferences.volume,
                "muted": preferences.muted, "microphone_enabled": preferences.microphone_enabled}

    @staticmethod
    def _task_dict(task: Any) -> dict[str, Any]:
        return {"task_id": task.task_id, "agent": task.agent.value, "instruction": task.instruction,
                "status": task.status.value, "result": task.result, "error": task.error}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        from dataclasses import asdict, is_dataclass
        from enum import Enum
        if is_dataclass(value):
            return {key: RequestHandler._json_safe(item) for key, item in asdict(value).items()}
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(key): RequestHandler._json_safe(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [RequestHandler._json_safe(item) for item in value]
        return value

    def _serve_frontend(self) -> None:
        path = Path(self.server.settings.static_dir) / "index.html"
        try:
            content = path.read_bytes()
        except OSError:
            self._json(500, {"error": "frontend_unavailable"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _websocket(self) -> None:
        self.close_connection = True
        if self.server.safe_mode.status()["active"]:
            self._json(423, {"error": "safe_mode_active"})
            return
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._json(426, {"error": "websocket_upgrade_required"})
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._json(400, {"error": "missing_websocket_key"})
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key(key))
        if "aegis" in [item.strip() for item in self.headers.get("Sec-WebSocket-Protocol", "").split(",")]:
            self.send_header("Sec-WebSocket-Protocol", "aegis")
        self.end_headers()
        session = self.server.sessions.create()
        sock = self.connection
        sock.settimeout(max(30.0, self.server.model_configuration.timeout_seconds + 30.0))
        send_lock = threading.Lock()
        connection_open = threading.Event()
        connection_open.set()
        active_requests: set[str] = set()
        active_lock = threading.Lock()
        audio_enabled = threading.Event()
        self._send_ws_event(sock, send_lock, connection_open,
                            {"type": "client.connected", "request_id": "connection", "payload": {"session_id": session.session_id}})
        try:
            while True:
                incoming = read_frame(sock)
                if incoming is None:
                    break
                opcode, data = incoming
                if opcode == 8:
                    break
                if opcode == 9:
                    with send_lock:
                        sock.sendall(frame(data.decode(errors="replace"), opcode=10))
                    continue
                if opcode != 1:
                    continue
                self._handle_event(sock, session.session_id, data, send_lock, connection_open,
                                   active_requests, active_lock, audio_enabled)
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            LOG.debug("WebSocket closed: %s", exc)
        finally:
            connection_open.clear()
            with active_lock:
                outstanding = tuple(active_requests)
            for request_id in outstanding:
                self.server.conversations.cancel(request_id)
                self.server.agents.cancel(request_id)
            self.server.stt.cancel(session.session_id)
            try:
                with send_lock:
                    send_json(sock, {"type": "client.disconnected", "request_id": "connection", "payload": {"session_id": session.session_id}})
            except OSError:
                pass

    @staticmethod
    def _send_ws_event(sock: socket.socket, send_lock: threading.Lock, connection_open: threading.Event,
                       event: dict[str, Any]) -> bool:
        if not connection_open.is_set():
            return False
        try:
            with send_lock:
                if not connection_open.is_set():
                    return False
                send_json(sock, event)
            return True
        except OSError:
            connection_open.clear()
            return False

    def _handle_event(self, sock: socket.socket, session_id: str, data: bytes,
                      send_lock: threading.Lock | None = None,
                      connection_open: threading.Event | None = None,
                      active_requests: set[str] | None = None,
                      active_lock: threading.Lock | None = None,
                      audio_enabled: threading.Event | None = None) -> None:
        # Optional defaults keep this helper usable by focused transport tests.
        send_lock = send_lock or threading.Lock()
        connection_open = connection_open or threading.Event()
        connection_open.set()
        active_requests = active_requests if active_requests is not None else set()
        active_lock = active_lock or threading.Lock()
        audio_enabled = audio_enabled or threading.Event()

        def emit(event: dict[str, Any]) -> None:
            self._send_ws_event(sock, send_lock, connection_open, event)

        try:
            event = json.loads(data)
            event_type = event["type"]
            request_id = event["request_id"]
            payload = event.get("payload", {})
            if event_type not in EVENT_TYPES or not isinstance(request_id, str) or not request_id or not isinstance(payload, dict):
                raise ValueError
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            emit({"type": "system.error", "request_id": "invalid", "payload": {"error": "invalid_event"}})
            return
        if self.server.safe_mode.status()["active"]:
            emit({"type": "system.error", "request_id": request_id,
                  "payload": {"error": "safe_mode_active"}})
            return
        if self.server.deduplicator.seen_or_add(request_id):
            emit({"type": "system.error", "request_id": request_id, "payload": {"error": "duplicate_request"}})
            return
        self.server.sessions.touch(session_id)
        if event_type == "audio.session.start":
            if not self.server.voice.get_preferences().microphone_enabled:
                emit({"type": "system.error", "request_id": request_id, "payload": {"error": "microphone_disabled"}})
                return
            try:
                self.server.stt.start(session_id)
                audio_enabled.set()
                emit({"type": "system.status", "request_id": request_id, "payload": {"audio": "listening"}})
            except Exception:
                emit({"type": "system.error", "request_id": request_id, "payload": {"error": "audio_start_failed"}})
            return
        if event_type == "audio.chunk":
            encoded = payload.get("pcm16")
            if not audio_enabled.is_set() or not isinstance(encoded, str) or len(encoded) > 14000:
                emit({"type": "system.error", "request_id": request_id, "payload": {"error": "invalid_audio_chunk"}})
                return
            try:
                chunk = base64.b64decode(encoded, validate=True)
                state = self.server.stt.append(session_id, chunk)
                emit({"type": "system.status", "request_id": request_id, "payload": {"audio": state.value}})
            except (binascii.Error, AudioError) as exc:
                emit({"type": "system.error", "request_id": request_id, "payload": {"error": str(exc)}})
            return
        if event_type == "audio.session.cancel":
            audio_enabled.clear(); self.server.stt.cancel(session_id)
            emit({"type": "system.status", "request_id": request_id, "payload": {"audio": "cancelled"}})
            return
        if event_type == "audio.session.end":
            if not audio_enabled.is_set():
                emit({"type": "system.error", "request_id": request_id, "payload": {"error": "audio_not_active"}})
                return
            audio_enabled.clear()
            with active_lock: active_requests.add(request_id)
            worker = threading.Thread(target=self._transcribe_audio, args=(sock, session_id, request_id, send_lock, connection_open, active_requests, active_lock), daemon=True)
            worker.start()
            return
        if event_type == "conversation.message":
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > self.server.settings.max_message_chars:
                emit({"type": "conversation.response.failed", "request_id": request_id, "payload": {"error": "invalid_message"}})
                return
            with active_lock:
                active_requests.add(request_id)
            worker = threading.Thread(target=self._stream_conversation,
                                      args=(sock, session_id, request_id, message, send_lock,
                                            connection_open, active_requests, active_lock,
                                            "text", self._local_client()), daemon=True)
            worker.start()
        elif event_type == "system.status":
            emit({"type": "system.status", "request_id": request_id, "payload": self.server.status()})
        elif event_type in {"conversation.response.cancel", "conversation.response.interrupt"}:
            target_id = payload.get("request_id", request_id)
            with active_lock:
                is_active = isinstance(target_id, str) and target_id in active_requests
            cancelled = is_active and self.server.conversations.cancel(target_id)
            agent_cancelled = self.server.agents.cancel(target_id) if isinstance(target_id, str) else False
            self.server.stt.cancel(session_id)
            if event_type == "conversation.response.interrupt":
                self.server.voice.stop()
            emit({"type": "system.status", "request_id": request_id,
                  "payload": {"cancelled": cancelled or agent_cancelled}})

    def _transcribe_audio(self, sock: socket.socket, session_id: str, request_id: str,
                          send_lock: threading.Lock, connection_open: threading.Event,
                          active_requests: set[str], active_lock: threading.Lock) -> None:
        try:
            result = self.server.stt.finish(session_id)
            if not result.text:
                self._send_ws_event(sock, send_lock, connection_open,
                                    {"type": "conversation.response.failed", "request_id": request_id, "payload": {"error": "no_speech_detected"}})
                return
            self._stream_conversation(sock, session_id, request_id, result.text, send_lock, connection_open,
                                      active_requests, active_lock, source="voice", local_client=self._local_client())
        except Exception as exc:
            LOG.info("Voice transcription unavailable: %s", exc)
            self._send_ws_event(sock, send_lock, connection_open,
                                {"type": "conversation.response.failed", "request_id": request_id,
                                 "payload": {"error": "transcription_unavailable"}})
        finally:
            with active_lock: active_requests.discard(request_id)

    def _stream_conversation(self, sock: socket.socket, session_id: str, request_id: str, message: str,
                             send_lock: threading.Lock, connection_open: threading.Event,
                             active_requests: set[str], active_lock: threading.Lock, source: str = "text",
                             local_client: bool = False) -> None:
        try:
            for response_event in self.server.brain.stream(BrainRequest(session_id, request_id, message,
                                                                        source, local_client)):
                if not connection_open.is_set():
                    self.server.conversations.cancel(request_id)
                    break
                event_payload = dict(response_event.payload)
                if event_payload.get("error") == "model_unavailable":
                    event_payload["error"] = "inference_not_configured"
                if response_event.type == "conversation.response.completed" and not event_payload.get("suppress_voice"):
                    self.server.voice.speak(str(event_payload.get("text", "")), request_id)
                if not self._send_ws_event(sock, send_lock, connection_open,
                                           {"type": response_event.type, "request_id": response_event.request_id,
                                            "payload": event_payload}):
                    self.server.conversations.cancel(request_id)
                    break
        except Exception:
            LOG.exception("WebSocket conversation failed: %s", request_id)
            self._send_ws_event(sock, send_lock, connection_open,
                                {"type": "conversation.response.failed", "request_id": request_id,
                                 "payload": {"error": "conversation_error"}})
        finally:
            with active_lock:
                active_requests.discard(request_id)

