"""Small deterministic parser for explicit protocol drafts; no model claims."""
from __future__ import annotations

import re
import uuid

from backend.protocols.models import ProtocolSpec


class ProtocolDraftParser:
    _categories = {
        "software development": "software_development", "coding": "software_development",
        "research": "research", "cybersecurity": "cybersecurity", "business": "business_operations",
        "data analysis": "data_analysis", "system administration": "system_administration",
        "communication": "communication", "productivity": "personal_productivity",
        "device management": "device_management",
    }

    def parse(self, instruction: str) -> ProtocolSpec:
        text = instruction.strip()
        if not text:
            raise ValueError("protocol instruction is required")
        if len(text) > 20000:
            raise ValueError("protocol instruction exceeds 20000 characters")
        match = re.search(r"(?i)\bprotocol\s+([A-Za-z0-9_-]+)(?:\s*[-—:]\s*([^\n.]+))?", text)
        supplied_id = match.group(1) if match else None
        name_hint = match.group(2).strip(" —:-") if match and match.group(2) else ""
        topic = re.search(r"(?i)\bfor\s+([A-Za-z][A-Za-z0-9 _-]{1,60})", text)
        scope_text = topic.group(1).strip(" .,:;") if topic else name_hint
        normalized = scope_text.lower()
        category = next((value for label, value in self._categories.items() if label in normalized), "general_operational_procedures")
        if not scope_text:
            scope_text = category.replace("_", " ")
        title = name_hint or scope_text.title() + " Protocol"
        purpose_match = re.search(r"(?i)\bpurpose\s*:\s*([^\n]+)", text)
        purpose = purpose_match.group(1).strip() if purpose_match else self._purpose(text, scope_text)
        rules_match = re.search(r"(?is)\brules\s*:\s*(.*)$", text)
        rule_text = rules_match.group(1).strip() if rules_match else text
        rules = (rule_text.rstrip(" ."),) if rule_text else ()
        procedures = self._procedures(text)
        triggers = (scope_text,) if scope_text else ()
        return ProtocolSpec(name=title, purpose=purpose, scope=scope_text, category=category,
                            description=text, rules=rules, procedures=procedures, triggers=triggers,
                            protocol_id=supplied_id or str(uuid.uuid4()))

    @staticmethod
    def _purpose(text: str, scope: str) -> str:
        ensure = re.search(r"(?i)\b(?:ensure|so that|to make sure)\s+(.+?)(?:[.!?]|$)", text)
        if ensure:
            return ensure.group(1).strip()
        return f"Define the required operating procedure for {scope}."

    @staticmethod
    def _procedures(text: str) -> tuple[str, ...]:
        numbered = re.findall(r"(?m)^\s*\d+[.)]\s*(.+?)\s*$", text)
        if numbered:
            return tuple(item.strip() for item in numbered if item.strip())
        action_line = re.search(r"(?i)\b(?:must be|must|should be|required to)\s+(.+?)(?:\bbefore\b|[.!?]|$)", text)
        if action_line:
            pieces = re.split(r",|\s+and\s+|\s+then\s+", action_line.group(1))
            steps = tuple("Complete: " + part.strip() for part in pieces if part.strip())
            if steps:
                return steps
        return ()
