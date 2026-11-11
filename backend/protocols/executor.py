"""Protocol procedure tracking and honest compliance classification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True, slots=True)
class ComplianceReport:
    protocol_id: str
    version: int
    status: str
    procedures: tuple[dict[str, Any], ...]
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return {"protocol_id": self.protocol_id, "version": self.version, "status": self.status,
                "procedures": list(self.procedures), "explanation": self.explanation}


class ProtocolComplianceEvaluator:
    def evaluate(self, protocol: Any, steps: tuple[Any, ...], *, blocked: bool = False) -> ComplianceReport:
        by_id = {step.step_id: step for step in steps}
        procedure_results = []
        states = []
        for index, procedure in enumerate(protocol.procedures):
            step_id = f"protocol-{protocol.protocol_id}-p{index + 1}"
            result = by_id.get(step_id)
            state = "not_attempted" if result is None else (
                "not_attempted_dependency" if result.error == "dependency_failed" else result.status.value)
            verified = bool(result and state == "succeeded" and result.verification.value == "success")
            procedure_results.append({"procedure": procedure, "status": state, "verified": verified,
                                      "error": result.error if result else None})
            states.append((result, verified))
        if blocked:
            status, explanation = "BLOCKED", "Protocol resolution or permission policy blocked execution."
        elif not states:
            status, explanation = "VERIFICATION_UNAVAILABLE", "Protocol has no verifiable procedures."
        elif all(result is not None and verified for result, verified in states):
            if protocol.verification:
                status, explanation = "VERIFICATION_UNAVAILABLE", "Procedure steps completed, but protocol verification criteria need semantic evaluation."
            else:
                status, explanation = "COMPLIANT", "Every required protocol procedure completed and passed verification."
        elif any(result is not None and result.status.value == "failed" and result.error != "dependency_failed" for result, _ in states):
            status, explanation = "NON_COMPLIANT", "At least one required protocol procedure failed."
        elif all(result is not None and result.status.value == "succeeded" for result, _ in states):
            status, explanation = "VERIFICATION_UNAVAILABLE", "Procedures completed, but independent verification was unavailable."
        else:
            status, explanation = "PARTIALLY_COMPLIANT", "Some required protocol procedures were not completed or verified."
        return ComplianceReport(protocol.protocol_id, protocol.version, status, tuple(procedure_results), explanation)
