"""Independent structural and evidence checks for specialist outputs."""
from __future__ import annotations

from dataclasses import replace

from backend.agents.models import AgentResult


class ResultVerifier:
    def verify(self, result: AgentResult, expected_task_id: str, expected_agent_id: str) -> AgentResult:
        errors = list(result.errors)
        contract_errors: list[str] = []
        warnings = list(result.warnings)
        if result.task_id != expected_task_id or result.agent_id != expected_agent_id:
            contract_errors.append("result_identity_mismatch")
        if result.status not in {"completed", "partial", "failed", "cancelled", "timed_out"}:
            contract_errors.append("invalid_result_status")
        if not isinstance(result.summary, str) or not result.summary.strip() or len(result.summary) > 4000:
            contract_errors.append("invalid_result_summary")
        if not 0.0 <= result.confidence <= 1.0:
            contract_errors.append("confidence_out_of_range")
        for finding in result.findings:
            if not isinstance(finding, dict) or len(str(finding)) > 4000:
                contract_errors.append("invalid_finding")
                break
            if finding.get("claim") and not finding.get("evidence_refs"):
                warnings.append("finding_without_evidence")
        if contract_errors:
            errors.extend(contract_errors)
            return replace(result, status="failed", errors=tuple(dict.fromkeys(errors)),
                           verification_status="failed", warnings=tuple(dict.fromkeys(warnings)))
        status = "verified" if result.findings and result.evidence else "partial"
        if result.status in {"failed", "cancelled", "timed_out"}:
            status = "failed" if result.status != "cancelled" else "cancelled"
        elif result.warnings or not result.evidence:
            status = "partial"
        return replace(result, verification_status=status, warnings=tuple(dict.fromkeys(warnings)))

    def conflicts(self, results: tuple[AgentResult, ...]) -> tuple[str, ...]:
        claims: dict[str, tuple[str, str]] = {}
        conflicts = []
        for result in results:
            for finding in result.findings:
                key, value = finding.get("key"), finding.get("value")
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                previous = claims.get(key)
                if previous and previous[0] != value:
                    conflicts.append(f"conflicting findings for {key}: {previous[0]} ({previous[1]}) vs {value} ({result.agent_id})")
                claims[key] = (value, result.agent_id)
        return tuple(conflicts)
