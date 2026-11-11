"""Scope, trigger, priority, conflict, and permission aware protocol resolution."""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.protocols.models import ProtocolSpec, ProtocolStatus
from backend.protocols.registry import ProtocolRegistry


@dataclass(frozen=True, slots=True)
class ProtocolResolution:
    protocols: tuple[ProtocolSpec, ...] = ()
    conflicts: tuple[str, ...] = ()
    overridden: tuple[str, ...] = ()
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts or self.blocked_reason)


class ProtocolResolver:
    CATEGORY_MAP = {
        "coding": "software_development", "multi_step": "general_operational_procedures",
        "research": "research", "information_retrieval": "research",
        "data_analysis": "data_analysis", "system_operations": "system_administration",
        "business_operations": "business_operations", "protocol_management": "general_operational_procedures",
    }

    def __init__(self, registry: ProtocolRegistry, policy: PermissionPolicy) -> None:
        self.registry, self.policy = registry, policy

    def resolve(self, objective: str, category: str = "") -> ProtocolResolution:
        candidates = self.registry.search(status=ProtocolStatus.ACTIVE)
        words = set(re.findall(r"[a-z0-9]+", objective.casefold()))
        expected_category = self.CATEGORY_MAP.get(category, category)
        matched: list[tuple[int, ProtocolSpec]] = []
        for protocol in candidates:
            scope_words = set(re.findall(r"[a-z0-9]+", protocol.scope.casefold()))
            trigger_words = {word for trigger in protocol.triggers for word in re.findall(r"[a-z0-9]+", trigger.casefold())}
            category_match = bool(expected_category and protocol.category == expected_category)
            scope_match = bool(scope_words and scope_words <= words)
            trigger_match = bool(trigger_words & words)
            if category_match or scope_match or trigger_match:
                score = (100 if category_match else 0) + (20 if scope_match else 0) + min(10, len(trigger_words & words))
                matched.append((score, protocol))
        matched.sort(key=lambda pair: (-pair[1].priority, -pair[0], pair[1].protocol_id))

        preconditioned = [protocol for _, protocol in matched if protocol.preconditions]
        if preconditioned:
            requirements = "; ".join(f"{p.name}: {', '.join(p.preconditions)}" for p in preconditioned)
            return ProtocolResolution(tuple(protocol for _, protocol in matched), blocked_reason=(
                "protocol preconditions cannot be verified automatically: " + requirements))

        required = [(protocol, permission) for _, protocol in matched for permission in protocol.required_permissions]
        for protocol, permission in required:
            try:
                level = PermissionLevel[permission.upper()]
            except KeyError:
                return ProtocolResolution(tuple(item for _, item in matched), blocked_reason=(
                    f"protocol {protocol.protocol_id} has an unrecognized permission requirement"))
            if level > self.policy.max_level or (level == PermissionLevel.NETWORK and not self.policy.allow_network):
                return ProtocolResolution(tuple(item for _, item in matched), blocked_reason=(
                    f"protocol {protocol.protocol_id} requires {permission}, which exceeds current permission policy"))

        applicable: list[ProtocolSpec] = []
        overridden: list[str] = []
        conflicts: list[str] = []
        for _, candidate in matched:
            conflicting = next((chosen for chosen in applicable if self._conflicting_rules(chosen, candidate)), None)
            if conflicting is None:
                applicable.append(candidate)
                continue
            high = conflicting.priority
            low = candidate.priority
            if high == low:
                conflicts.append(f"equal-priority protocols conflict: {conflicting.protocol_id} and {candidate.protocol_id}")
            else:
                # list is sorted high-to-low; the lower-priority protocol is not applied.
                overridden.append(candidate.protocol_id)
        return ProtocolResolution(tuple(protocol for _, protocol in matched) if conflicts else tuple(applicable),
                                  tuple(conflicts), tuple(overridden))

    @staticmethod
    def _conflicting_rules(first: ProtocolSpec, second: ProtocolSpec) -> bool:
        positive, negative = [], []
        positive_re = re.compile(r"(?i)\b(?:must|shall|always|should|required to)\s+(.+)")
        negative_re = re.compile(r"(?i)\b(?:must not|shall not|never|do not|should not)\s+(.+)")
        for protocol in (first, second):
            for rule in protocol.rules + protocol.procedures:
                neg = negative_re.search(rule)
                pos = positive_re.search(rule)
                if neg:
                    negative.append(ProtocolResolver._key(neg.group(1)))
                elif pos:
                    positive.append(ProtocolResolver._key(pos.group(1)))
        return any(a == b or (a and b and (a in b or b in a)) for a in positive for b in negative)

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(word for word in re.findall(r"[a-z0-9]+", value.casefold()) if word not in {"the", "a", "an", "to", "be"})
