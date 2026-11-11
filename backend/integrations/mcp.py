"""Opt-in JSON-RPC MCP HTTP client routed back through ToolRegistry."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import re
from dataclasses import dataclass
from typing import Any

from backend.permissions.policy import PermissionLevel
from backend.tools.registry import ToolRegistry, ToolSpec


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    server_id: str
    endpoint: str
    approved: bool = False
    bearer_env: str = ""
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 20:
            raise ValueError("MCP timeout must be between one and twenty seconds")

    @classmethod
    def from_environment(cls) -> "MCPServerConfig | None":
        endpoint = os.getenv("AEGIS_MCP_ENDPOINT", "").strip()
        if not endpoint: return None
        return cls(os.getenv("AEGIS_MCP_SERVER_ID", "configured"), endpoint,
                   os.getenv("AEGIS_MCP_APPROVED", "0") == "1",
                   os.getenv("AEGIS_MCP_BEARER_ENV", ""),
                   float(os.getenv("AEGIS_MCP_TIMEOUT_SECONDS", "10")))


class MCPHTTPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.session_id: str | None = None
        self._next_id = 0
        self.connected = False
        self.tools: list[dict[str, Any]] = []
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl): return None
        self._opener = urllib.request.build_opener(NoRedirect())

    def _endpoint(self) -> str:
        parsed = urllib.parse.urlparse(self.config.endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("MCP endpoint must be an HTTP(S) URL without embedded credentials")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("unencrypted MCP endpoints are allowed only on loopback")
        if len(self.config.endpoint) > 2048 or not self.config.server_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid MCP endpoint or server id")
        return self.config.endpoint

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id: headers["Mcp-Session-Id"] = self.session_id
        if self.config.bearer_env:
            token = os.getenv(self.config.bearer_env, "")
            if not token: raise RuntimeError("configured MCP credential is unavailable")
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self._endpoint(), json.dumps(payload).encode(), headers=headers, method="POST")
        with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
            session = response.headers.get("Mcp-Session-Id", self.session_id)
            if session and (len(session) > 200 or any(ord(char) < 33 for char in session)):
                raise ValueError("invalid MCP session header")
            self.session_id = session
            if response.status == 202: return None
            raw = response.read(512_001)
            if len(raw) > 512_000: raise ValueError("MCP response exceeds size limit")
        # This compact client supports JSON response mode. SSE transports can be
        # added behind this interface without changing ToolRegistry callers.
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite MCP JSON number")))
        if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False)) > 512_000:
            raise ValueError("invalid MCP JSON-RPC response")
        if value.get("jsonrpc") != "2.0" or value.get("id") != payload.get("id"):
            raise ValueError("MCP response identity does not match request")
        if value.get("error"): raise RuntimeError("MCP server returned an error")
        return value.get("result")

    def close(self) -> None:
        if self.session_id:
            request=urllib.request.Request(self._endpoint(),headers={"Mcp-Session-Id":self.session_id},method="DELETE")
            try:
                if self.config.bearer_env:
                    token=os.getenv(self.config.bearer_env,"")
                    if token: request.add_header("Authorization","Bearer "+token)
                with self._opener.open(request,timeout=min(self.config.timeout_seconds,10)): pass
            except (OSError, urllib.error.URLError):
                pass
        self.connected=False

    def connect_and_discover(self) -> list[dict[str, Any]]:
        if not self.config.approved:
            raise PermissionError("MCP server has not been explicitly approved")
        initialized=self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "AEGIS", "version": "2.0"}}})
        if not isinstance(initialized,dict) or initialized.get("protocolVersion") not in {"2024-11-05","2025-03-26"}:
            raise ValueError("MCP server returned an unsupported protocol version")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        result = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) or {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        if not isinstance(tools, list) or len(tools) > 100:
            raise ValueError("MCP tool list exceeds its limit")
        checked=[]
        for item in tools:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str): continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", item["name"]): continue
            schema=item.get("inputSchema", {"type":"object"})
            if not isinstance(schema, dict) or len(json.dumps(schema)) > 16000 or schema.get("type") != "object": continue
            checked.append({"name": item["name"][:100], "description": str(item.get("description", ""))[:1000], "inputSchema": schema})
        self.tools=checked; self.connected=True
        return checked

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.connected or name not in {item["name"] for item in self.tools}:
            raise PermissionError("MCP tool is not discovered on the approved connection")
        self._next_id += 1
        result=self._post({"jsonrpc":"2.0","id":100+self._next_id,"method":"tools/call",
                           "params":{"name":name,"arguments":arguments}})
        if not isinstance(result, dict): raise ValueError("MCP tool returned an invalid result")
        if result.get("isError"): raise RuntimeError("MCP tool reported execution failure")
        if len(json.dumps(result, allow_nan=False)) > 256_000: raise ValueError("MCP tool result exceeds size limit")
        return result


class MCPIntegration:
    def __init__(self, registry: ToolRegistry, config: MCPServerConfig | None) -> None:
        self.registry, self.config = registry, config
        self.client: MCPHTTPClient | None = None
        self.tool_names: list[str] = []
        self.approved_tool_names: set[str] = set()

    def connect(self) -> list[dict[str, Any]]:
        if self.config is None: raise RuntimeError("MCP server is not configured")
        if self.client and self.client.connected: raise RuntimeError("MCP server is already connected")
        client=MCPHTTPClient(self.config)
        discovered=client.connect_and_discover()
        for item in discovered:
            name=f"mcp.{self.config.server_id}.{item['name']}"
            if self.registry.select(name): continue
            schema=item["inputSchema"]
            self.registry.register(ToolSpec(name, item["description"] or "Approved MCP tool.", schema,
                {"type":"object"}, PermissionLevel.NETWORK, self.config.timeout_seconds,
                lambda args, tool=item["name"], instance=client: instance.call_tool(tool,args),
                risk="moderate", requires_confirmation=True))
            self.tool_names.append(name)
        self.client=client
        return self.registry.list()

    def approve_tools(self, names: list[str]) -> tuple[str, ...]:
        if not self.client or not self.client.connected:
            raise RuntimeError("MCP server is not connected")
        if not isinstance(names, list) or not names or len(names) > 100 or len(set(names)) != len(names):
            raise ValueError("choose one to one hundred unique discovered tool IDs")
        if not set(names).issubset(self.tool_names):
            raise ValueError("MCP tool approval includes an undiscovered tool")
        self.approved_tool_names.update(names)
        return tuple(names)

    def disconnect(self) -> None:
        if self.client: self.client.close()
        for name in self.tool_names: self.registry.unregister(name)
        self.tool_names.clear(); self.approved_tool_names.clear(); self.client=None

    def status(self) -> dict[str, Any]:
        return {"configured": self.config is not None, "approved": bool(self.config and self.config.approved),
                "connected": bool(self.client and self.client.connected), "tools": list(self.tool_names),
                "approved_tools": sorted(self.approved_tool_names),
                "transport": "streamable_http_json_only" if self.config else None}
