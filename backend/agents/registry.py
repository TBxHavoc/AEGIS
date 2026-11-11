"""Capability registry for the nine bounded specialist workflows."""
from __future__ import annotations

from threading import RLock

from backend.agents.models import AgentSpec, AgentType


DEFAULT_SPECS = (
    AgentSpec(AgentType.RESEARCH.value, "Research", "Search local knowledge and an explicitly enabled web provider; distinguish sources and uncertainty.", "1.0", ("local_research", "web_research", "source_quality", "uncertainty"), allowed_tools=("knowledge.search", "web.search", "web.fetch"), required_tools=("knowledge.search",)),
    AgentSpec(AgentType.CODING.value, "Coding", "Inspect approved source files, run syntax checks, and request sandboxed approved tests.", "1.0", ("code_review", "debugging", "implementation_planning", "controlled_tests"), allowed_tools=("document.read", "file.metadata", "file.list", "code.syntax_check", "code.run_tests")),
    AgentSpec(AgentType.DEFENSIVE_CYBERSECURITY.value, "Defensive Cybersecurity", "Review supplied configurations and logs for defensive risks.", "1.0", ("defensive_review", "risk_identification"), allowed_tools=("document.read", "file.metadata")),
    AgentSpec(AgentType.PRODUCTIVITY.value, "Productivity", "Prioritize user-provided tasks and build bounded plans.", "1.0", ("task_planning", "prioritization")),
    AgentSpec(AgentType.BUSINESS.value, "Business", "Structure business analyses and label assumptions and estimates.", "1.0", ("business_analysis", "risk_assessment"), allowed_tools=("knowledge.search",)),
    AgentSpec(AgentType.DATA_ANALYSIS.value, "Data Analysis", "Validate, summarize, filter, group, trend, and chart bounded user-provided or approved-root datasets.", "1.0", ("data_validation", "descriptive_statistics", "grouping", "filtering", "trend_analysis", "chart_generation"), allowed_tools=("data.csv.analyze", "data.json.inspect", "data.csv.transform", "data.csv.trend", "data.csv.chart", "document.read"), required_tools=("data.csv.analyze",)),
    AgentSpec(AgentType.SYSTEM_ASSISTANCE.value, "System", "Report read-only OS, CPU, RAM, disk, process, interface and resource health.", "1.0", ("linux_diagnostics", "resource_health"), allowed_tools=("system.status", "system.diagnostics"), required_tools=("system.status",)),
    AgentSpec(AgentType.PROTOCOL_MANAGEMENT.value, "Protocol Management", "Resolve approved protocols and prepare approval-gated drafts.", "1.0", ("protocol_resolution", "draft_validation")),
    AgentSpec(AgentType.KNOWLEDGE_MANAGEMENT.value, "Knowledge Management", "Retrieve indexed knowledge and search approved local file roots.", "1.0", ("local_retrieval", "source_tracking", "file_search"), allowed_tools=("knowledge.search", "file.search", "file.find", "file.list", "document.read")),
)


class AgentRegistry:
    def __init__(self, specs: tuple[AgentSpec, ...] = DEFAULT_SPECS) -> None:
        self._specs: dict[str, AgentSpec] = {}
        self._lock = RLock()
        for spec in specs:
            self.register(spec)

    def register(self, spec: AgentSpec) -> None:
        if spec.agent_id not in {item.value for item in AgentType if item != AgentType.MAIN_ASSISTANT}:
            raise ValueError("unknown specialist identity")
        if not spec.name or not spec.capabilities or len(spec.description) > 1000:
            raise ValueError("invalid agent specification")
        with self._lock:
            if spec.agent_id in self._specs:
                raise ValueError("agent is already registered")
            self._specs[spec.agent_id] = spec

    def get(self, agent_id: str) -> AgentSpec | None:
        with self._lock:
            return self._specs.get(agent_id)

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentSpec:
        from dataclasses import replace
        with self._lock:
            spec = self._specs.get(agent_id)
            if spec is None:
                raise KeyError(agent_id)
            self._specs[agent_id] = replace(spec, enabled=bool(enabled))
            return self._specs[agent_id]

    def restrict_tools(self, agent_id: str, allowed_tools: tuple[str, ...]) -> AgentSpec:
        from dataclasses import replace
        with self._lock:
            spec = self._specs.get(agent_id)
            if spec is None:
                raise KeyError(agent_id)
            if len(set(allowed_tools)) != len(allowed_tools) or not set(allowed_tools).issubset(spec.allowed_tools):
                raise ValueError("agent tool permissions may only be narrowed")
            self._specs[agent_id] = replace(spec, allowed_tools=allowed_tools)
            return self._specs[agent_id]

    def grant_approved_tools(self, agent_id: str, requested: tuple[str, ...],
                             globally_allowed: frozenset[str]) -> AgentSpec:
        from dataclasses import replace
        with self._lock:
            spec = self._specs.get(agent_id)
            if spec is None: raise KeyError(agent_id)
            if len(set(requested)) != len(requested) or not set(requested).issubset(globally_allowed):
                raise ValueError("agents may receive only globally allowlisted tools")
            self._specs[agent_id] = replace(spec, allowed_tools=tuple(dict.fromkeys((*spec.allowed_tools, *requested))))
            return self._specs[agent_id]

    def discover(self, capability: str | None = None) -> tuple[AgentSpec, ...]:
        with self._lock:
            specs = tuple(self._specs.values())
        return tuple(spec for spec in specs if spec.enabled and (not capability or capability in spec.capabilities))

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            return [spec.as_dict() for spec in self._specs.values()]
