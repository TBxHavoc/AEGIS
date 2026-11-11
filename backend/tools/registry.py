"""Central, bounded tool registry and the only tool execution interface."""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Event, RLock
from typing import Any, Callable

from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.security.audit import AuditLogger
from backend.security.guardrails import redact_secrets

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
CancelableToolHandler = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: PermissionLevel
    timeout_seconds: float
    handler: ToolHandler
    version: str = "1.0"
    risk: str = "low"
    availability: str = "available"
    requires_confirmation: bool = False
    cancellable_handler: CancelableToolHandler | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ToolRegistry:
    def __init__(self, policy: PermissionPolicy, audit: AuditLogger, max_workers: int = 2) -> None:
        if not 1 <= max_workers <= 4:
            raise ValueError("tool worker limit must be between one and four")
        self.policy, self.audit = policy, audit
        self._tools: dict[str, ToolSpec] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aegis-tool")
        self._slots = BoundedSemaphore(max_workers)
        self._run_cancellations: dict[str, Event] = {}
        self._lock = RLock()
        self.safe_mode: Any | None = None
        self._actor_calls: dict[str, list[float]] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or spec.name in self._tools:
            raise ValueError("tool name must be unique and non-empty")
        if not 0 < spec.timeout_seconds <= 300 or spec.risk not in {"low", "moderate", "high", "critical"}:
            raise ValueError("invalid tool timeout or risk classification")
        with self._lock:
            if spec.name in self._tools:
                raise ValueError("tool name must be unique and non-empty")
            self._tools[spec.name] = spec

    def list(self) -> list[dict[str, Any]]:
        with self._lock: specs = tuple(self._tools.values())
        return [{"id": spec.name, "name": spec.name, "description": spec.description,
                 "input_schema": spec.input_schema, "output_schema": spec.output_schema,
                 "permission": spec.permission.name, "risk": spec.risk, "version": spec.version,
                 "timeout_seconds": spec.timeout_seconds, "availability": spec.availability,
                 "requires_confirmation": spec.requires_confirmation}
                for spec in specs]

    def select(self, requested: str) -> ToolSpec | None:
        with self._lock: return self._tools.get(requested)

    def unregister(self, name: str) -> bool:
        with self._lock: return self._tools.pop(name, None) is not None

    def execute(self, name: str, arguments: dict[str, Any], *, actor: str = "assistant",
                confirmed: bool = False, cancellation: Any | None = None,
                request_id: str | None = None) -> ToolResult:
        spec = self.select(name)
        if spec is None:
            self.audit.record("tool.execute", actor=actor, outcome="not_found", details={"tool": name})
            return ToolResult(name, False, error="tool_not_found")
        own_cancel = None
        if request_id is not None:
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                return ToolResult(name, False, error="invalid_tool_request_id")
            own_cancel = Event()
            with self._lock:
                if request_id in self._run_cancellations:
                    return ToolResult(name, False, error="duplicate_tool_request_id")
                self._run_cancellations[request_id] = own_cancel
        class CombinedCancellation:
            def is_set(self) -> bool:
                return bool((cancellation is not None and cancellation.is_set()) or
                            (own_cancel is not None and own_cancel.is_set()))
        cancel_signal = CombinedCancellation() if cancellation is not None or own_cancel is not None else None
        try:
            now = time.monotonic()
            with self._lock:
                recent = [stamp for stamp in self._actor_calls.get(actor, ()) if now - stamp < 60]
                limit = 40 if actor.startswith("agent:") else 120
                if len(recent) >= limit:
                    self.audit.record("tool.rate_limit", actor=actor, outcome="denied", details={"tool": name})
                    return ToolResult(name, False, error="tool_invocation_rate_limit")
                recent.append(now)
                self._actor_calls[actor] = recent
            if spec.requires_confirmation and not confirmed:
                raise PermissionError("explicit tool confirmation required")
            if self.safe_mode is not None and self.safe_mode.status()["active"]:
                if spec.permission.value > PermissionLevel.READ_ONLY.value or name.startswith(("web.", "mcp.", "code.run")):
                    raise PermissionError("safe mode blocks this operation")
            self.policy.require(name, spec.permission, confirmed=confirmed)
            if spec.availability != "available":
                raise RuntimeError("tool_unavailable")
            if not isinstance(arguments, dict) or len(arguments) > 64 or len(json.dumps(arguments, allow_nan=False)) > 64_000:
                raise ValueError("tool input must be a bounded JSON object")
            _validate_schema(arguments, spec.input_schema, "input")
            slot_deadline = time.monotonic() + spec.timeout_seconds
            while not self._slots.acquire(timeout=min(.05, max(0.0, slot_deadline-time.monotonic()))):
                if cancel_signal is not None and cancel_signal.is_set():
                    self.audit.record("tool.execute", actor=actor, outcome="cancelled", details={"tool": name})
                    return ToolResult(name, False, error="tool_cancelled")
                if time.monotonic() >= slot_deadline:
                    raise TimeoutError("tool_timeout")
            slot_transferred = False
            future = None
            try:
                handler = (lambda: spec.cancellable_handler(arguments, cancel_signal)) if spec.cancellable_handler else (lambda: spec.handler(arguments))
                future = self._executor.submit(handler)
                deadline = time.monotonic() + spec.timeout_seconds
                while True:
                    if cancel_signal is not None and cancel_signal.is_set():
                        future.cancel()
                        future.add_done_callback(lambda _: self._slots.release())
                        slot_transferred = True
                        self.audit.record("tool.execute", actor=actor, outcome="cancelled", details={"tool": name})
                        return ToolResult(name, False, error="tool_cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        future.cancel()
                        future.add_done_callback(lambda _: self._slots.release())
                        slot_transferred = True
                        raise TimeoutError("tool_timeout")
                    try:
                        output = future.result(timeout=min(0.05, remaining))
                        if cancel_signal is not None and cancel_signal.is_set():
                            self.audit.record("tool.execute", actor=actor, outcome="cancelled", details={"tool": name})
                            return ToolResult(name, False, error="tool_cancelled")
                        break
                    except FutureTimeout:
                        continue
                if not isinstance(output, dict) or len(json.dumps(output, allow_nan=False)) > 256_000:
                    raise ValueError("tool output must be a bounded JSON object")
                _validate_schema(output, spec.output_schema, "output")
                resource = {key: arguments[key] for key in ("path", "source", "target") if key in arguments}
                self.audit.record("tool.execute", actor=actor, outcome="ok",
                                  details={"tool": name, "input_fields": sorted(arguments), "resource": resource})
                return ToolResult(name, True, output=output)
            finally:
                if not slot_transferred:
                    self._slots.release()
        except Exception as exc:
            error = str(exc)[:500]
            self.audit.record("tool.execute", actor=actor, outcome="denied_or_failed",
                              details={"tool": name, "error": error})
            return ToolResult(name, False, error=redact_secrets(error))
        finally:
            if request_id is not None:
                with self._lock:
                    self._run_cancellations.pop(request_id, None)

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            event = self._run_cancellations.get(request_id)
            if event is None: return False
            event.set()
        self.audit.record("tool.cancel", actor="local_user", outcome="requested", details={"request_id":request_id})
        return True

    def shutdown(self) -> None:
        with self._lock:
            for event in self._run_cancellations.values(): event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


def _validate_schema(value: Any, schema: dict[str, Any], label: str, depth: int = 0) -> None:
    """Validate the small JSON Schema subset used by built-in and connector tools."""
    if depth > 12:
        raise ValueError(f"invalid tool {label}: nesting limit exceeded")
    if not schema:
        return
    expected = schema.get("type")
    valid = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
             "string": lambda x: isinstance(x, str), "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
             "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
             "boolean": lambda x: isinstance(x, bool)}
    if expected in valid and not valid[expected](value):
        raise ValueError(f"invalid tool {label}: expected {expected}")
    if expected == "object":
        if len(value) > 1000 or any(not isinstance(key, str) for key in value):
            raise ValueError(f"invalid tool {label}: object key limit or identity violation")
        required = schema.get("required", [])
        if any(key not in value for key in required):
            raise ValueError(f"invalid tool {label}: required field missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            raise ValueError(f"invalid tool {label}: unknown field")
        for key, child in properties.items():
            if key in value:
                _validate_schema(value[key], child, label, depth + 1)
    if expected == "array":
        if len(value) > schema.get("maxItems", 1000):
            raise ValueError(f"invalid tool {label}: too many items")
        if "items" in schema:
            for item in value:
                _validate_schema(item, schema["items"], label, depth + 1)
    if expected == "string":
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"invalid tool {label}: string is too long")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"invalid tool {label}: string is empty")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"invalid tool {label}: value is not allowed")
    if expected in {"integer", "number"}:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"invalid tool {label}: non-finite number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"invalid tool {label}: number is too small")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"invalid tool {label}: number is too large")
