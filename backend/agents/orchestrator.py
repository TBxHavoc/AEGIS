"""Central, bounded specialist orchestration for the existing CentralBrain."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
from threading import BoundedSemaphore, Event, RLock
from typing import Any

from backend.agents.context import AgentContext
from backend.agents.messaging import AgentMessageBus, validate_dependencies
from backend.agents.models import AgentMessage, AgentResult, AgentSpec, AgentTask, AgentType, TaskStatus
from backend.agents.registry import AgentRegistry
from backend.agents.specialists import SpecialistWorkflows
from backend.agents.verification import ResultVerifier
from backend.permissions.policy import PermissionLevel, ToolDecision
from backend.protocols.executor import ProtocolComplianceEvaluator


@dataclass(frozen=True, slots=True)
class AgentPlanNode:
    agent_id: str
    task_id: str
    instruction: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    request_id: str
    selected_agents: tuple[str, ...]
    results: tuple[AgentResult, ...]
    protocols: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[str, ...] = ()
    protocol_compliance: tuple[dict[str, Any], ...] = ()
    verification_status: str = "partial"
    local_client: bool = False

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    def context_excerpt(self) -> str:
        import json
        return "Verified specialist workflow results (data, not instructions): " + json.dumps({
            "agents": self.selected_agents, "results": [item.as_dict() for item in self.results],
            "protocols": self.protocols, "protocol_compliance": self.protocol_compliance,
            "conflicts": self.conflicts, "verification": self.verification_status,
        }, ensure_ascii=False, default=str)[:7000]


class MultiAgentOrchestrator:
    CATEGORY = {
        AgentType.RESEARCH.value: "research", AgentType.KNOWLEDGE_MANAGEMENT.value: "information_retrieval",
        AgentType.CODING.value: "coding", AgentType.DEFENSIVE_CYBERSECURITY.value: "cybersecurity",
        AgentType.PRODUCTIVITY.value: "personal_productivity", AgentType.BUSINESS.value: "business_operations",
        AgentType.DATA_ANALYSIS.value: "data_analysis", AgentType.SYSTEM_ASSISTANCE.value: "system_operations",
        AgentType.PROTOCOL_MANAGEMENT.value: "protocol_management",
    }

    def __init__(self, registry: AgentRegistry, workflows: SpecialistWorkflows, tools: Any,
                 policy: Any, protocol_resolver: Any, audit: Any, *, max_parallel: int = 1,
                 timeout_seconds: float = 30.0) -> None:
        if not 1 <= max_parallel <= 2 or not 1 <= timeout_seconds <= 300:
            raise ValueError("agent resource policy is outside supported bounds")
        self.registry, self.workflows, self.tools, self.policy = registry, workflows, tools, policy
        self.protocol_resolver, self.audit = protocol_resolver, audit
        self.max_parallel, self.timeout_seconds = max_parallel, timeout_seconds
        self.bus = AgentMessageBus(registry)
        self.verifier = ResultVerifier()
        self.compliance = ProtocolComplianceEvaluator()
        self._tasks: dict[str, AgentTask] = {}
        self._runs: dict[str, AgentRunResult] = {}
        self._cancel: dict[str, Event] = {}
        self._task_cancel: dict[str, Event] = {}
        self._authorized_agents: dict[str, frozenset[str]] = {}
        self._completed_results: dict[str, AgentResult] = {}
        self._completed_roots: set[str] = set()
        self._slots = BoundedSemaphore(max_parallel)
        self._root_slots = BoundedSemaphore(2)
        self._lock = RLock()

    def classify(self, request: str) -> tuple[str, ...]:
        text = request.casefold()
        selected: list[str] = []
        def add(agent: AgentType) -> None:
            identifier = agent.value
            if identifier not in selected and self.registry.get(identifier) and self.registry.get(identifier).enabled:
                selected.append(identifier)
        market = bool(re.search(r"\b(market|industry) research\b", text))
        code_security = bool(re.search(r"\b(code|coding|repository|source code)\b", text) and re.search(r"\b(security|vulnerab|secure)", text))
        mixed_research_data = bool(re.search(r"\bresearch\b", text) and re.search(r"\b(analy[sz]e|dataset|csv|statistics)\b", text))
        if market:
            add(AgentType.RESEARCH); add(AgentType.BUSINESS)
        elif code_security:
            add(AgentType.CODING); add(AgentType.DEFENSIVE_CYBERSECURITY)
        elif mixed_research_data:
            add(AgentType.RESEARCH); add(AgentType.DATA_ANALYSIS)
        elif re.search(r"\b(protocol|procedure|workflow policy)\b", text): add(AgentType.PROTOCOL_MANAGEMENT)
        elif re.search(r"\b(csv|json|dataset|statistics|data analysis|analy[sz]e this data|chart this data|trend analysis)\b", text): add(AgentType.DATA_ANALYSIS)
        elif re.search(r"\b(system status|cpu|memory usage|linux diagnostics|check my system|disk space)\b", text): add(AgentType.SYSTEM_ASSISTANCE)
        elif re.search(r"\b(find|locate|search)\b.{0,50}\b(files?|documents?)\b|\blist (?:the )?(?:files|directory)\b", text): add(AgentType.KNOWLEDGE_MANAGEMENT)
        elif re.search(r"\b(vulnerability|cybersecurity|security configuration|security review|review these logs)\b", text): add(AgentType.DEFENSIVE_CYBERSECURITY)
        elif re.search(r"\b(code review|review this code|debug|implement|coding agent|codebase)\b", text): add(AgentType.CODING)
        elif re.search(r"\b(schedule|prioriti[sz]e|to-?do|reminder|organize my tasks|productivity)\b", text): add(AgentType.PRODUCTIVITY)
        elif re.search(r"\b(business|revenue|financial model|business plan|operations analysis)\b", text): add(AgentType.BUSINESS)
        elif re.search(r"\b(knowledge base|indexed documents|organize knowledge|retrieve knowledge)\b", text): add(AgentType.KNOWLEDGE_MANAGEMENT)
        elif re.search(r"\b(research|investigate|compare sources|find sources)\b", text): add(AgentType.RESEARCH)
        return tuple(selected)

    def should_delegate(self, request: str) -> bool:
        return bool(self.classify(request))

    def plan(self, request_id: str, request: str, selected: tuple[str, ...] | None = None) -> tuple[AgentPlanNode, ...]:
        selected = selected if selected is not None else self.classify(request)
        if len(selected) > 3:
            selected = selected[:3]
        nodes = []
        dependent_pair = selected in ((AgentType.RESEARCH.value, AgentType.BUSINESS.value),
                                      (AgentType.CODING.value, AgentType.DEFENSIVE_CYBERSECURITY.value))
        previous = None
        for index, agent_id in enumerate(selected):
            spec = self.registry.get(agent_id)
            if spec is None or not spec.enabled:
                raise ValueError("selected agent is unavailable")
            dependencies = (previous,) if previous and (dependent_pair or index > 1) else ()
            node = AgentPlanNode(agent_id, f"{request_id}:{agent_id}", request, dependencies)
            nodes.append(node)
            previous = node.task_id
        graph = {node.task_id: tuple(dep for dep in node.dependencies) for node in nodes}
        validate_dependencies(graph)
        return tuple(nodes)

    def run(self, request_id: str, request: str, *, source: str = "text",
            memories: tuple[dict[str, str], ...] = (), recent_turns: tuple[dict[str, str], ...] = (),
            local_client: bool = False) -> AgentRunResult | None:
        nodes = self.plan(request_id, request)
        if not nodes:
            return None
        if not self._root_slots.acquire(timeout=0.1):
            return AgentRunResult(request_id, (), (), verification_status="failed", local_client=local_client)
        with self._lock:
            if request_id in self._completed_roots or request_id in self._cancel:
                self._root_slots.release()
                raise ValueError("duplicate or active agent request id")
            root_cancel = Event()
            self._cancel[request_id] = root_cancel
            self._authorized_agents[request_id] = frozenset(node.agent_id for node in nodes)
        try:
            resolutions = [(node, self.protocol_resolver.resolve(request, self.CATEGORY.get(node.agent_id, "")))
                           for node in nodes] if self.protocol_resolver else []
            protocol_specs: dict[str, Any] = {}
            protocols_by_agent: dict[str, tuple[dict[str, Any], ...]] = {}
            for node, resolution in resolutions:
                protocol_specs.update({item.protocol_id: item for item in resolution.protocols})
                protocols_by_agent[node.agent_id] = tuple(self._protocol_data(item) for item in resolution.protocols)
            protocols = tuple(self._protocol_data(item) for item in protocol_specs.values())
            blocked = [resolution for _, resolution in resolutions if resolution.blocked]
            conflicts = tuple(message for item in blocked for message in item.conflicts)
            reasons = tuple(item.blocked_reason for item in blocked if item.blocked_reason)
            if blocked:
                explanation = "; ".join((*reasons, *conflicts)) or "protocol requirements block this task"
                reports = tuple({"protocol_id": value["protocol_id"], "version": value["version"],
                                 "status": "BLOCKED", "explanation": explanation} for value in protocols)
                result = AgentRunResult(request_id, tuple(node.agent_id for node in nodes), (), protocols,
                                        conflicts, reports, "failed", local_client)
                with self._lock: self._runs[request_id] = result
                return result
            output_by_id: dict[str, AgentResult] = {}
            pending = list(nodes)
            while pending and not root_cancel.is_set():
                ready = [node for node in pending if all(dep in output_by_id for dep in node.dependencies)]
                if not ready:
                    raise ValueError("agent dependency graph stalled")
                batch = ready[:self.max_parallel]
                prior_by_node = {node.task_id: tuple(output_by_id[dep] for dep in node.dependencies) for node in batch}
                pool = ThreadPoolExecutor(max_workers=min(self.max_parallel, len(batch)),
                                          thread_name_prefix="aegis-agent")
                futures = {node: pool.submit(self._execute_node, request_id, request, source, node,
                    prior_by_node[node.task_id], protocols_by_agent.get(node.agent_id, ()), memories, recent_turns, local_client, root_cancel)
                    for node in batch}
                for node, future in futures.items():
                    try: result = future.result(timeout=self.timeout_seconds + 2)
                    except FutureTimeout:
                        root_cancel.set()
                        result = AgentResult(node.task_id, node.agent_id, "timed_out", "Agent orchestration exceeded its time limit.", errors=("agent_timeout",))
                    output_by_id[node.task_id] = result
                    pending.remove(node)
                pool.shutdown(wait=False, cancel_futures=True)
            output = tuple(output_by_id[node.task_id] for node in nodes if node.task_id in output_by_id)
            protocol_reports = tuple(self.compliance.evaluate(protocol_specs[key], ()).as_dict()
                                     for key in protocol_specs)
            conflicts_found = self.verifier.conflicts(output)
            status = "verified" if output and all(item.verification_status == "verified" for item in output) and not conflicts_found else "partial"
            if root_cancel.is_set() or any(item.status in {"failed", "cancelled", "timed_out"} for item in output):
                status = "failed"
            self.bus.send(AgentMessage.create(root_request_id=request_id, sender="central_brain",
                recipient="central_brain", kind="progress", task_id=request_id,
                payload={"completed_agents": len(output), "total_agents": len(nodes), "status": status}))
            result = AgentRunResult(request_id, tuple(node.agent_id for node in nodes), output, protocols,
                                    conflicts_found, protocol_reports, status, local_client)
            with self._lock:
                self._runs[request_id] = result
                if len(self._runs) > 512: self._runs.pop(next(iter(self._runs)))
            return result
        finally:
            with self._lock:
                self._cancel.pop(request_id, None)
                self._authorized_agents.pop(request_id, None)
                self._completed_roots.add(request_id)
                if len(self._completed_roots) > 2048:
                    self._completed_roots.pop()
            self._root_slots.release()

    def _execute_node(self, request_id: str, request: str, source: str, node: AgentPlanNode,
                      prior: tuple[AgentResult, ...], protocols: tuple[dict[str, Any], ...],
                      memories: tuple[dict[str, str], ...], recent_turns: tuple[dict[str, str], ...],
                      local_client: bool, root_cancel: Event) -> AgentResult:
        spec = self.registry.get(node.agent_id)
        if spec is None:
            return AgentResult(node.task_id, node.agent_id, "failed", "Agent is not registered.", errors=("agent_not_registered",))
        task = AgentTask(task_id=node.task_id, agent=AgentType(node.agent_id), instruction=node.instruction,
                         root_request_id=request_id, parent_task_id=request_id if node.dependencies else None,
                         depends_on=node.dependencies, depth=1 if node.dependencies else 0)
        task_cancel = Event()
        with self._lock:
            self._tasks[task.task_id] = task
            self._task_cancel[task.task_id] = task_cancel
        context = AgentContext(request_id, request, source, recent_turns[:2], memories[:5], protocols,
                               prior, local_client, lambda name, args: self._invoke_checked(spec, name, args, task_cancel, root_cancel),
                               lambda target, question: self.request_agent(node.task_id, target, question),
                               lambda: self.bus.inbox(request_id, node.agent_id),
                               tuple({"id": tool.name, "description": tool.description,
                                      "input_schema": tool.input_schema, "output_schema": tool.output_schema,
                                      "permission": tool.permission.name, "risk": tool.risk,
                                      "availability": tool.availability}
                                     for name in spec.allowed_tools if (tool := self.tools.select(name)) is not None))
        task.context = context.as_data()
        try:
            self.bus.send(AgentMessage.create(root_request_id=request_id, sender="central_brain",
                recipient=node.agent_id, kind="task.assignment", task_id=node.task_id,
                payload={"instruction": request[:4000], "source": source},
                context_refs=tuple(item["memory_id"] for item in memories[:5] if item.get("memory_id"))))
            denied = self._permission_error(spec)
            if denied:
                result = AgentResult(node.task_id, node.agent_id, "failed", "Agent permissions do not satisfy the required tools.", errors=(denied,))
            elif any(item.status not in {"completed", "partial"} for item in prior):
                result = AgentResult(node.task_id, node.agent_id, "cancelled", "A dependency did not complete.", errors=("dependency_failed",))
            elif root_cancel.is_set():
                task_cancel.set()
                result = AgentResult(node.task_id, node.agent_id, "cancelled", "Task cancelled.", errors=("task_cancelled",))
            elif not self._slots.acquire(timeout=self.timeout_seconds):
                result = AgentResult(node.task_id, node.agent_id, "timed_out", "No execution slot became available.", errors=("resource_limit_timeout",))
            else:
                slot_transferred = False
                try:
                    task.status = TaskStatus.RUNNING
                    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aegis-specialist")
                    future = worker.submit(self.workflows.execute, spec, task, context, task_cancel)
                    try:
                        result = future.result(timeout=self.timeout_seconds)
                    except FutureTimeout:
                        task_cancel.set()
                        # Keep the global execution slot occupied until a
                        # non-cooperative workflow actually exits.
                        future.add_done_callback(lambda _: self._slots.release())
                        slot_transferred = True
                        result = AgentResult(node.task_id, node.agent_id, "timed_out",
                                             "Specialist exceeded its time limit.", errors=("agent_timeout",))
                    finally:
                        worker.shutdown(wait=False, cancel_futures=True)
                finally:
                    if not slot_transferred:
                        self._slots.release()
            result = self.verifier.verify(result, node.task_id, node.agent_id)
            applied = tuple({"protocol_id": protocol["protocol_id"], "version": protocol["version"],
                             "name": protocol["name"]} for protocol in protocols)
            result = replace(result, protocols_applied=applied)
            task.status = (TaskStatus.COMPLETED if result.status in {"completed", "partial"} else
                           TaskStatus.CANCELLED if result.status == "cancelled" else
                           TaskStatus.TIMED_OUT if result.status == "timed_out" else TaskStatus.FAILED)
            task.result = result.as_dict()
            with self._lock:
                self._completed_results[task.task_id] = result
                if len(self._completed_results) > 512:
                    self._completed_results.pop(next(iter(self._completed_results)))
            self.bus.send(AgentMessage.create(root_request_id=request_id, sender=node.agent_id,
                recipient="central_brain", kind="task.result", task_id=node.task_id,
                payload={"status": result.status, "verification": result.verification_status,
                         "summary": result.summary[:1000]}))
            self.audit.record("agent.orchestrate", actor=node.agent_id, outcome=result.status,
                              details={"root_request_id": request_id, "task_id": node.task_id,
                                       "source": source, "protocol_ids": [item["protocol_id"] for item in protocols]})
            return result
        except Exception as exc:
            task.status = TaskStatus.FAILED; task.error = str(exc)
            return AgentResult(node.task_id, node.agent_id, "failed", "Specialist workflow failed.", errors=(str(exc),))
        finally:
            with self._lock:
                self._task_cancel.pop(task.task_id, None)

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(request_id)
            if event is None:
                return False
            event.set()
            for task_id, task_event in self._task_cancel.items():
                task = self._tasks.get(task_id)
                if task and task.root_request_id == request_id:
                    task_event.set()
                    try:
                        self.bus.send(AgentMessage.create(root_request_id=request_id, sender="central_brain",
                            recipient=task.agent.value, kind="cancel", task_id=task_id,
                            payload={"reason": "user_cancelled"}))
                    except ValueError:
                        pass
            for task in self._tasks.values():
                if task.root_request_id == request_id and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                    task.status = TaskStatus.CANCELLED
        self.audit.record("agent.cancel", actor="user", outcome="requested", details={"request_id": request_id})
        return True

    def cancel_all(self) -> int:
        """Cancel active roots and child tasks during emergency shutdown."""
        with self._lock:
            signals = tuple(self._cancel.values()) + tuple(self._task_cancel.values())
        for signal in signals:
            signal.set()
        return len(signals)

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def request_agent(self, sender_task_id: str, recipient_agent_id: str,
                      question: str) -> AgentResult | None:
        """Request a bounded result from a sibling already authorized in this run.

        This never creates or schedules new work: the target must be in the root
        plan, and only an already completed result can be returned.
        """
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError("agent request must contain a short question")
        with self._lock:
            sender_task = self._tasks.get(sender_task_id)
            if sender_task is None or not sender_task.root_request_id:
                raise ValueError("request sender is not an active agent task")
            root_id = sender_task.root_request_id
            allowed = self._authorized_agents.get(root_id, frozenset())
            if recipient_agent_id not in allowed or recipient_agent_id == sender_task.agent.value:
                raise ValueError("recipient is not an authorized sibling agent")
            target_id = f"{root_id}:{recipient_agent_id}"
            self.bus.send(AgentMessage.create(root_request_id=root_id, sender=sender_task.agent.value,
                recipient=recipient_agent_id, kind="agent.request", task_id=sender_task_id,
                payload={"question": question.strip()}))
            return self._completed_results.get(target_id)

    def get_run(self, request_id: str) -> AgentRunResult | None:
        with self._lock:
            return self._runs.get(request_id)

    def status(self) -> dict[str, Any]:
        with self._lock:
            tasks = tuple(self._tasks.values())
        return {"registered": len(self.registry.discover()), "available": len(self.registry.discover()),
                "active_tasks": sum(item.status == TaskStatus.RUNNING for item in tasks),
                "max_parallel": self.max_parallel, "timeout_seconds": self.timeout_seconds,
                "agents": self.registry.list()}

    def _permission_error(self, spec: AgentSpec) -> str | None:
        for permission in spec.required_permissions:
            try: level = PermissionLevel[permission]
            except KeyError: return "invalid_agent_permission_configuration"
            if level > self.policy.max_level: return "agent_permission_exceeds_policy"
        for name in spec.allowed_tools:
            tool = self.tools.select(name)
            if tool is None:
                return "agent_tool_unavailable"
        for name in spec.required_tools:
            tool = self.tools.select(name)
            if tool is None or self.policy.check(name, tool.permission) != ToolDecision.ALLOW:
                return "agent_tool_not_authorized"
        return None

    def _invoke(self, spec: AgentSpec, name: str, args: dict[str, Any]):
        if name not in spec.allowed_tools:
            raise PermissionError("agent is not configured for this tool")
        tool = self.tools.select(name)
        if tool is None:
            raise PermissionError("tool is unavailable")
        self.policy.require(name, tool.permission)
        return self.tools.execute(name, args, actor=f"agent:{spec.agent_id}")

    def _invoke_checked(self, spec: AgentSpec, name: str, args: dict[str, Any],
                        task_cancel: Event, root_cancel: Event):
        if task_cancel.is_set() or root_cancel.is_set():
            raise PermissionError("agent task was cancelled")
        class CombinedCancellation:
            def is_set(self) -> bool:
                return task_cancel.is_set() or root_cancel.is_set()
        if name not in spec.allowed_tools:
            raise PermissionError("agent is not configured for this tool")
        tool = self.tools.select(name)
        if tool is None:
            raise PermissionError("tool is unavailable")
        self.policy.require(name, tool.permission)
        return self.tools.execute(name, args, actor=f"agent:{spec.agent_id}", cancellation=CombinedCancellation())

    @staticmethod
    def _protocol_data(protocol: Any) -> dict[str, Any]:
        return {"protocol_id": protocol.protocol_id, "version": protocol.version, "name": protocol.name,
                "category": protocol.category, "scope": protocol.scope, "rules": list(protocol.rules),
                "procedures": list(protocol.procedures), "required_permissions": list(protocol.required_permissions),
                "status": protocol.status.value}

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancel.values(): event.set()
            for event in self._task_cancel.values(): event.set()
            self._cancel.clear()
