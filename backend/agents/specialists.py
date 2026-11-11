"""Conservative specialist workflows that share AEGIS tools and local services."""
from __future__ import annotations

import csv
import io
import re
from threading import Event
from typing import Any

from backend.agents.context import AgentContext
from backend.agents.models import AgentResult, AgentSpec, AgentTask, AgentType
from backend.protocols.models import ProtocolStatus
from backend.protocols.parser import ProtocolDraftParser
from backend.protocols.validator import validate_protocol


class SpecialistWorkflows:
    def __init__(self, *, protocols: Any, policy: Any) -> None:
        self.protocols, self.policy = protocols, policy

    def execute(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        if cancel.is_set():
            return AgentResult(task.task_id, spec.agent_id, "cancelled", "Task cancelled.", errors=("task_cancelled",))
        methods = {
            AgentType.RESEARCH.value: self._research,
            AgentType.KNOWLEDGE_MANAGEMENT.value: self._knowledge,
            AgentType.SYSTEM_ASSISTANCE.value: self._system,
            AgentType.CODING.value: self._coding,
            AgentType.DEFENSIVE_CYBERSECURITY.value: self._cyber,
            AgentType.PRODUCTIVITY.value: self._productivity,
            AgentType.BUSINESS.value: self._business,
            AgentType.DATA_ANALYSIS.value: self._data,
            AgentType.PROTOCOL_MANAGEMENT.value: self._protocol,
        }
        result = methods[spec.agent_id](spec, task, context, cancel)
        return result

    @staticmethod
    def _result(spec: AgentSpec, task: AgentTask, summary: str, *, findings=(), evidence=(), actions=(),
                warnings=(), errors=(), confidence=.45, status="completed") -> AgentResult:
        return AgentResult(task.task_id, spec.agent_id, status, summary, tuple(findings), tuple(evidence),
                           tuple(actions), tuple(errors), tuple(warnings), confidence)

    @staticmethod
    def _tool(spec: AgentSpec, context: AgentContext, name: str, args: dict[str, Any], task: AgentTask) -> tuple[Any, tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        if name not in spec.allowed_tools or context.invoke_tool is None:
            return None, (), ("tool_not_available_to_agent",)
        result = context.invoke_tool(name, args)
        action = ({"tool": name, "ok": result.ok, "error": result.error},)
        if not result.ok:
            return None, (), action
        evidence = tuple({"source": item.get("source", "local tool"), "excerpt": str(item.get("content", item))[:500]}
                         for item in result.output.get("results", []) if isinstance(item, dict))
        if not evidence and result.output:
            evidence = ({"source": "AEGIS registered tool", "excerpt": str(result.output)[:500]},)
        return result.output, evidence, action

    def _research(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        output, evidence, actions = self._tool(spec, context, "knowledge.search", {"query": context.user_request}, task)
        findings = tuple({"claim": str(item.get("content", ""))[:500], "evidence_refs": (str(item.get("source", "local knowledge")),)}
                         for item in (output or {}).get("results", [])[:10])
        all_evidence = list(evidence); all_actions = list(actions); warnings = []
        web_count = 0
        if "web.search" in spec.allowed_tools and context.invoke_tool:
            try:
                searched = context.invoke_tool("web.search", {"query": context.user_request[:300], "limit": 5})
                all_actions.append({"tool": "web.search", "ok": searched.ok, "error": searched.error})
                if searched.ok:
                    for item in searched.output.get("results", [])[:5]:
                        url = str(item.get("url", ""))
                        if not url.startswith("https://"):
                            continue
                        web_count += 1
                        all_evidence.append({"source": url, "title": str(item.get("title", ""))[:300],
                                             "excerpt": str(item.get("snippet", ""))[:500], "trust": "untrusted_web_content"})
                        findings += ({"claim": str(item.get("title", "Web result"))[:300], "evidence_refs": (url,),
                                      "claim_type": "search_result_title_not_verified_fact"},)
                    if web_count:
                        try:
                            page = context.invoke_tool("web.fetch", {"url": all_evidence[-web_count]["source"]})
                            all_actions.append({"tool": "web.fetch", "ok": page.ok, "error": page.error})
                            if page.ok:
                                all_evidence.append({"source": page.output.get("url"), "excerpt": page.output.get("text", "")[:500],
                                                     "trust": "untrusted_web_content"})
                        except Exception as exc:
                            warnings.append("web_page_fetch_failed")
                else:
                    warnings.append("web_search_failed")
            except Exception:
                warnings.append("web_research_not_authorized_or_unavailable")
        if not (output or {}).get("results"):
            warnings.append("no_approved_local_sources_found")
        if not web_count:
            warnings.append("web_research_unavailable_or_not_authorized")
        if web_count:
            warnings.append("web_content_is_untrusted_and_search_titles_are_not_verified_facts")
        local_count = len((output or {}).get("results", []))
        status = "completed" if findings else "partial"
        summary = f"Collected {local_count} local knowledge result(s) and {web_count} web search result(s)."
        return self._result(spec, task, summary, findings=findings, evidence=tuple(all_evidence), actions=tuple(all_actions),
                            warnings=tuple(dict.fromkeys(warnings)), confidence=.5 if findings else .2, status=status)

    def _knowledge(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        if context.invoke_tool and re.search(r"\b(find|locate|search)\b.{0,50}\b(files?|documents?)\b|\blist (?:the )?(?:files|directory)\b", context.user_request, re.I):
            list_request = bool(re.search(r"\blist (?:the )?(?:files|directory)\b", context.user_request, re.I))
            file_match = re.search(r"(?:file|document)(?:\s+named)?\s+([\w.-]+\.[A-Za-z0-9]{1,8})", context.user_request, re.I)
            absolute_match = re.search(r"(?P<path>/(?:[^\s'\"`]+))", context.user_request)
            query = file_match.group(1) if file_match else absolute_match.group("path").rsplit("/",1)[-1] if absolute_match else re.sub(r"(?i)\b(find|locate|search|for|this|file|document|please)\b", "", context.user_request).strip()
            tool_name = "file.list" if list_request else "file.find"
            arguments = {} if list_request else {"query": query[:200]}
            result = context.invoke_tool(tool_name, arguments)
            if result.ok:
                matches = result.output.get("matches", result.output.get("entries", []))
                findings = tuple({"claim": "approved-root file match", "path": item.get("path", item.get("name")),
                                  "evidence_refs": ("file-system-tool",)} for item in matches[:100])
                return self._result(spec, task, f"Found {len(matches)} approved-root file result(s).",
                    findings=findings, evidence=({"source":"approved-root file listing","count":len(matches)},),
                    actions=({"tool":tool_name,"ok":True},), warnings=() if findings else ("no_matching_files_found",),
                    confidence=.9 if findings else .4, status="completed" if findings else "partial")
            return self._result(spec, task, "Approved-root file search could not run.",
                actions=({"tool":tool_name,"ok":False,"error":result.error},),
                warnings=("file_root_or_tool_policy_denied_access",), status="partial")
        result = self._research(spec, task, context, cancel)
        return AgentResult(result.task_id, result.agent_id, result.status,
                           "Knowledge retrieval completed. " + result.summary, result.findings,
                           result.evidence, result.actions_performed, result.errors, result.warnings,
                           result.confidence, result.verification_status, result.protocols_applied)

    def _system(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        output, evidence, actions = self._tool(spec, context, "system.status", {}, task)
        if output is None:
            return self._result(spec, task, "System health check could not run.", actions=actions,
                                warnings=("system_status_tool_unavailable",), status="partial")
        diagnostic, diag_evidence, diag_actions = self._tool(spec, context, "system.diagnostics", {}, task)
        diagnostic_summary = ({key: diagnostic.get(key) for key in ("os", "cpu_model", "cpu_count", "memory_bytes", "disk_root", "uptime_seconds")}
                               if diagnostic else None)
        return self._result(spec, task, "Read-only AEGIS and host diagnostics collected.",
                            findings=({"claim": "system status", "value": str(output.get("status", "unknown")),
                                       "host_diagnostics": diagnostic_summary,
                                       "evidence_refs": ("AEGIS registered system.status",)},),
                            evidence=evidence + diag_evidence, actions=actions + diag_actions, confidence=.9,
                            warnings=() if diagnostic else ("host_diagnostics_unavailable",))

    def _coding(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        snippets = re.findall(r"```(?:[\w+-]+)?\s*([\s\S]*?)```", context.user_request)
        source_ref = "user-supplied-code"
        file_match = re.search(r"(?:file|path)\s*[:=]?\s*([\w./~ -]+\.[A-Za-z0-9]{1,8})|(?P<absolute>/(?:[^\s'\"`]+))", context.user_request, re.I)
        if not snippets and file_match and context.invoke_tool and "document.read" in spec.allowed_tools:
            path = (file_match.group("absolute") or file_match.group(1)).strip().strip("'\"")
            read = context.invoke_tool("document.read", {"path": path})
            if read.ok:
                snippets = [str(read.output.get("content", ""))]
                source_ref = str(read.output.get("path", path))
            else:
                return self._result(spec, task, "The requested source file could not be read through approved file tools.",
                                    actions=({"tool": "document.read", "ok": False, "error": read.error},),
                                    warnings=("approved_file_root_or_tool_policy_denied_access",), status="partial")
        if snippets:
            snippet = "\n".join(snippets)[:6000]
            findings = [{"claim": "User supplied code was inspected for basic structural risks.",
                         "evidence_refs": (source_ref,)}]
            if re.search(r"\b(?:eval|exec)\s*\(", snippet):
                findings.append({"claim": "Dynamic code execution is present and deserves review.",
                                 "evidence_refs": (source_ref,)})
            return self._result(spec, task, "Reviewed the supplied or approved-read source snippet with bounded static checks.",
                                findings=findings, evidence=({"source": source_ref, "excerpt": snippet[:500]},),
                                warnings=("no_files_modified_tests_or_commands_run",), confidence=.4)
        return self._result(spec, task, "Prepared a coding review workflow; no code or project files were supplied.",
                            warnings=("no_repository_access_or_code_execution_performed",), confidence=.2, status="partial")

    def _cyber(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        matches = re.findall(r"(?i).{0,60}\b(?:password|secret|token|private key|0\.0\.0\.0|chmod 777|sudo|world writable)\b.{0,60}", context.user_request)
        if not matches:
            return self._result(spec, task, "No concrete configuration or log evidence was provided for a defensive review.",
                                warnings=("authorized_targets_only_no_network_scans",), status="partial", confidence=.2)
        evidence = tuple({"source": "user-supplied-text", "excerpt": item.strip()} for item in matches[:10])
        findings = tuple({"claim": "Potentially sensitive or permissive configuration marker requires review.",
                          "evidence_refs": (f"user-supplied-text:{index}",)} for index, _ in enumerate(matches[:10]))
        return self._result(spec, task, f"Flagged {len(findings)} supplied text marker(s) for manual defensive review.",
                            findings=findings, evidence=evidence, warnings=("heuristic_only_not_a_security_scan",), confidence=.3, status="partial")

    def _productivity(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        items = tuple(part.strip(" .") for part in re.split(r"[,;\n]+|\bthen\b", context.user_request, flags=re.I) if part.strip())[:12]
        findings = tuple({"claim": item, "priority": index + 1, "evidence_refs": ("user-request",)} for index, item in enumerate(items))
        return self._result(spec, task, f"Prepared a {len(items)}-item draft action list; no calendar or reminder was changed.",
                            findings=findings, evidence=({"source": "user-request", "excerpt": context.user_request[:500]},),
                            warnings=("no_external_schedule_or_reminder_action_taken",), confidence=.4)

    def _business(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        assumptions = re.findall(r"(?i)\b(?:assume|assuming|estimate|estimated|approximately)\b[^,.!?;]*", context.user_request)
        return self._result(spec, task, "Prepared a business analysis outline; numerical estimates need supplied data or sourced assumptions.",
                            findings=({"claim": "analysis requires explicit assumptions and evidence", "assumptions": assumptions,
                                       "evidence_refs": ("user-request",)},),
                            evidence=({"source": "user-request", "excerpt": context.user_request[:500]},),
                            warnings=("no_market_data_or_financial_model_was_generated",), confidence=.35)

    def _data(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        match = re.search(r"```(?:csv)?\s*([\s\S]*?)```", context.user_request, re.I)
        json_match = re.search(r"```json\s*([\s\S]*?)```", context.user_request, re.I)
        file_source = None
        file_action: tuple[dict[str, Any], ...] = ()
        if not match and not json_match and context.invoke_tool and "document.read" in spec.allowed_tools:
            file_match = re.search(r"(?:file|path)\s*[:=]?\s*([\w./~ -]+\.(?:csv|json))|(?P<absolute>/(?:[^\s'\"`]+\.(?:csv|json)))", context.user_request, re.I)
            if file_match:
                path = (file_match.group("absolute") or file_match.group(1)).strip().strip("'\"")
                read = context.invoke_tool("document.read", {"path": path})
                if read.ok:
                    file_source = str(read.output.get("path", path))
                    file_action = ({"tool":"document.read","ok":True},)
                    contents = str(read.output.get("content", ""))
                    if path.lower().endswith(".json"): json_match = re.match(r"([\s\S]*)", contents)
                    else: match = re.match(r"([\s\S]*)", contents)
                else:
                    return self._result(spec, task, "The dataset could not be read through approved file tools.",
                        actions=({"tool":"document.read","ok":False,"error":read.error},), status="partial",
                        warnings=("approved_file_root_or_policy_denied_access",))
        if not match and json_match and context.invoke_tool and "data.json.inspect" in spec.allowed_tools:
            try:
                result = context.invoke_tool("data.json.inspect", {"content": json_match.group(1).strip()})
                if result.ok:
                    return self._result(spec, task, "Inspected the bounded JSON input without executing embedded content.",
                        findings=({"claim": "JSON structure inspected", "value": result.output,
                                   "evidence_refs": (file_source or "user-supplied-json",)},),
                        evidence=({"source": file_source or "user-supplied-json", "characters": len(json_match.group(1))},),
                        actions=file_action + ({"tool": "data.json.inspect", "ok": True},), confidence=.75)
            except Exception:
                pass
        if not match:
            return self._result(spec, task, "No CSV data block was supplied; provide a bounded table for analysis.",
                                warnings=("no_dataset_available",), confidence=.2, status="partial")
        raw = match.group(1).strip()
        if len(raw) > 10000:
            return self._result(spec, task, "Dataset exceeds the 10 KB agent input limit.", errors=("dataset_too_large",), status="failed")
        if not context.invoke_tool or "data.csv.analyze" not in spec.allowed_tools:
            return self._result(spec, task, "The registered CSV analysis tool is unavailable.",
                                errors=("data_tool_unavailable",), status="failed")
        tool_name = "data.csv.analyze"
        arguments: dict[str, Any] = {"content": raw}
        try:
            rows = list(csv.reader(io.StringIO(raw)))
            headers = rows[0] if rows else []
            normalized = [item.casefold() for item in headers]
            if re.search(r"\b(chart|plot|graph)\b", context.user_request, re.I) and len(headers) >= 2:
                tool_name = "data.csv.chart"
                numeric = next((index for index in range(1, len(headers))
                                if any(_is_number(row[index]) for row in rows[1:] if index < len(row))), 1)
                arguments.update(label_column=headers[0], value_column=headers[numeric])
            elif re.search(r"\btrend\b", context.user_request, re.I):
                date_index = next((index for index, name in enumerate(normalized) if "date" in name or "time" in name), None)
                value_index = next((index for index in range(len(headers)) if index != date_index
                                    and any(_is_number(row[index]) for row in rows[1:] if index < len(row))), None)
                if date_index is not None and value_index is not None:
                    tool_name = "data.csv.trend"
                    arguments.update(date_column=headers[date_index], value_column=headers[value_index])
            elif re.search(r"\bgroup\b", context.user_request, re.I):
                by = re.search(r"\bby\s+([\w -]{1,100})", context.user_request, re.I)
                column = next((header for header in headers if by and header.casefold() == by.group(1).strip().casefold()), headers[0] if headers else "")
                if column:
                    tool_name = "data.csv.transform"; arguments.update(operation="group_count", column=column)
        except (csv.Error, IndexError):
            pass
        tool_result = context.invoke_tool(tool_name, arguments)
        if not tool_result.ok:
            return self._result(spec, task, "CSV analysis was denied or failed in the registered data tool.",
                                errors=(tool_result.error or "data_analysis_failed",), status="failed")
        data = tool_result.output
        if tool_name != "data.csv.analyze":
            return self._result(spec, task, f"Completed the requested {tool_name.rsplit('.',1)[-1]} operation through a registered tool.",
                findings=({"claim": "bounded dataset operation completed", "value": data,
                           "evidence_refs": (file_source or "user-supplied-csv",)},),
                evidence=({"source": file_source or "user-supplied-csv", "operation": tool_name},),
                actions=file_action + ({"tool": tool_name, "ok": True},), confidence=.75)
        return self._result(spec, task, f"Analyzed {data['row_count']} CSV rows using the registered data tool.",
            findings=({"claim": "validated descriptive statistics", "value": data["columns"],
                       "evidence_refs": (file_source or "user-supplied-csv",)},),
            evidence=({"source": file_source or "user-supplied-csv", "row_count": data["row_count"],
                       "columns": data["column_names"]},),
            actions=file_action + ({"tool": "data.csv.analyze", "ok": True},),
            warnings=tuple(data.get("limitations", ())), confidence=.8)
    def _protocol(self, spec: AgentSpec, task: AgentTask, context: AgentContext, cancel: Event) -> AgentResult:
        if re.search(r"(?i)\b(?:create|draft|define|add)\s+(?:a\s+)?protocol\b", context.user_request):
            try:
                self.policy.authorize_protocol_mutation("create_draft", local_client=context.local_client)
                candidate = ProtocolDraftParser().parse(context.user_request)
                errors = validate_protocol(candidate)
                if errors:
                    return self._result(spec, task, "Protocol draft needs clarification before it can be saved.",
                                        findings=({"claim": "draft validation", "missing_or_invalid": errors,
                                                   "candidate": candidate.as_dict(), "evidence_refs": ("user-request",)},),
                                        warnings=("draft_not_saved",), confidence=.7, status="partial")
                saved = self.protocols.create(candidate)
                return self._result(spec, task, "Created a proposed protocol draft; approval and activation remain separate user actions.",
                                    findings=({"claim": "protocol draft created", "protocol_id": saved.protocol_id,
                                               "version": saved.version, "evidence_refs": ("protocol-registry",)},),
                                    evidence=({"source": "protocol-registry", "excerpt": f"{saved.name} ({saved.protocol_id})"},),
                                    actions=({"action": "create_draft", "approval_required": True},), confidence=.9)
            except (ValueError, RuntimeError, PermissionError) as exc:
                return self._result(spec, task, "Protocol draft was not saved.", errors=(str(exc),), status="failed")
        active = self.protocols.search(status=ProtocolStatus.ACTIVE)
        relevant = [item for item in active if any(word in (context.user_request + " " + item.scope + " " + " ".join(item.triggers)).casefold()
                                                    for word in re.findall(r"[a-z0-9]+", context.user_request.casefold()) if len(word) > 3)]
        evidence = tuple({"source": "protocol-registry", "excerpt": f"{item.name} v{item.version}: {item.scope}"} for item in relevant[:10])
        findings = tuple({"claim": item.name, "protocol_id": item.protocol_id, "version": item.version,
                          "evidence_refs": ("protocol-registry",)} for item in relevant[:10])
        return self._result(spec, task, f"Retrieved {len(findings)} potentially relevant active protocol(s).",
                            findings=findings, evidence=evidence,
                            warnings=() if findings else ("no_matching_active_protocol_found",), confidence=.6 if findings else .3,
                            status="completed" if findings else "partial")


def _is_number(value: str) -> bool:
    try:
        number = float(value)
        return number == number and abs(number) != float("inf")
    except (TypeError, ValueError):
        return False
