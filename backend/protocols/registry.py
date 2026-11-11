"""SQLite-backed, audited protocol lifecycle and immutable version snapshots."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from backend.protocols.models import ProtocolSpec, ProtocolStatus
from backend.protocols.validator import validate_protocol
from backend.security.audit import AuditLogger


class ProtocolError(ValueError):
    pass


class ProtocolRegistry:
    def __init__(self, path: str | Path, audit: AuditLogger) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audit = audit
        self._lock = RLock()
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS protocols (protocol_id TEXT PRIMARY KEY, name TEXT, version INTEGER, status TEXT, scope TEXT, payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS protocols_name ON protocols(name)")
            db.execute("CREATE TABLE IF NOT EXISTS protocol_versions (protocol_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(protocol_id,version))")
            db.execute("CREATE TABLE IF NOT EXISTS protocol_compliance (report_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, protocol_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, report TEXT NOT NULL, created_at REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS protocol_compliance_task ON protocol_compliance(task_id,created_at)")
            # Backfill legacy rows once, leaving existing data intact.
            db.execute("INSERT OR IGNORE INTO protocol_versions SELECT protocol_id, version, payload, COALESCE(json_extract(payload,'$.created_at'),?) FROM protocols", (time.time(),))

    def create(self, spec: ProtocolSpec) -> ProtocolSpec:
        self._validate_for_write(spec)
        if spec.status != ProtocolStatus.PROPOSED:
            raise ProtocolError("new protocols must start as proposed drafts")
        with self._lock, self._connection() as db:
            try:
                db.execute("INSERT INTO protocols VALUES (?, ?, ?, ?, ?, ?)",
                           (spec.protocol_id, spec.name, spec.version, spec.status.value, spec.scope, json.dumps(spec.as_dict())))
                db.execute("INSERT INTO protocol_versions VALUES(?,?,?,?)",
                           (spec.protocol_id, spec.version, json.dumps(spec.as_dict()), spec.created_at))
            except sqlite3.IntegrityError as exc:
                raise ProtocolError("duplicate protocol ID or version") from exc
        self.audit.record("protocol.create", actor="user", outcome="proposed", details={"protocol_id": spec.protocol_id, "version": spec.version})
        return spec

    def get(self, protocol_id: str) -> ProtocolSpec | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT payload FROM protocols WHERE protocol_id=?", (protocol_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_version(self, protocol_id: str, version: int) -> ProtocolSpec | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT payload FROM protocol_versions WHERE protocol_id=? AND version=?", (protocol_id, version)).fetchone()
            current = db.execute("SELECT payload FROM protocols WHERE protocol_id=? AND version=?", (protocol_id, version)).fetchone()
        if current:
            return self._from_row(current)
        return self._from_row(row) if row else None

    def history(self, protocol_id: str) -> list[ProtocolSpec]:
        """Return lineage snapshots, including descendants of revisions."""
        all_specs = self.search()
        ids = {protocol_id}
        changed = True
        while changed:
            changed = False
            for item in all_specs:
                if item.revision_of in ids and item.protocol_id not in ids:
                    ids.add(item.protocol_id); changed = True
        snapshots: list[ProtocolSpec] = []
        for item in all_specs:
            if item.protocol_id in ids:
                snap = self.get_version(item.protocol_id, item.version)
                snapshots.append(snap or item)
        return sorted(snapshots, key=lambda item: (item.version, item.created_at, item.protocol_id))

    def search(self, query: str = "", *, status: ProtocolStatus | None = None,
               category: str | None = None) -> list[ProtocolSpec]:
        clauses = ["(lower(name) LIKE ? OR lower(scope) LIKE ? OR lower(payload) LIKE ?)"] if query.strip() else ["1=1"]
        args: list[object] = [f"%{query.lower()}%"] * 3 if query.strip() else []
        if status is not None:
            clauses.append("status=?"); args.append(status.value)
        if category is not None:
            clauses.append("json_extract(payload,'$.category')=?"); args.append(category)
        with self._lock, self._connection() as db:
            rows = db.execute(f"SELECT payload FROM protocols WHERE {' AND '.join(clauses)} ORDER BY name, version DESC", args).fetchall()
        return [self._from_row(row) for row in rows]

    def approve(self, protocol_id: str) -> ProtocolSpec:
        return self._transition(protocol_id, ProtocolStatus.APPROVED, {ProtocolStatus.PROPOSED, ProtocolStatus.INACTIVE})

    def activate(self, protocol_id: str) -> ProtocolSpec:
        with self._lock:
            spec = self.get(protocol_id)
            if spec is None:
                raise ProtocolError("protocol not found")
            if spec.status != ProtocolStatus.APPROVED:
                raise ProtocolError("only an approved protocol can be activated")
            conflicts = [item for item in self.search(status=ProtocolStatus.ACTIVE)
                         if item.scope.casefold() == spec.scope.casefold()
                         and item.category == spec.category
                         and self._root_id(item) != self._root_id(spec)]
            if conflicts:
                raise ProtocolError("active protocol scope conflicts with: " + ", ".join(item.name for item in conflicts))
            for old in self.search(status=ProtocolStatus.ACTIVE):
                if old.protocol_id != spec.protocol_id and self._root_id(old) == self._root_id(spec):
                    self.deactivate(old.protocol_id)
            return self._transition(protocol_id, ProtocolStatus.ACTIVE, {ProtocolStatus.APPROVED})

    def deactivate(self, protocol_id: str) -> ProtocolSpec:
        return self._transition(protocol_id, ProtocolStatus.INACTIVE, {ProtocolStatus.ACTIVE})

    def archive(self, protocol_id: str) -> ProtocolSpec:
        return self._transition(protocol_id, ProtocolStatus.ARCHIVED, {ProtocolStatus.INACTIVE, ProtocolStatus.PROPOSED})

    def restore(self, protocol_id: str) -> ProtocolSpec:
        return self._transition(protocol_id, ProtocolStatus.INACTIVE, {ProtocolStatus.ARCHIVED})

    def revise(self, protocol_id: str, **changes: Any) -> ProtocolSpec:
        current = self.get(protocol_id)
        if current is None:
            raise ProtocolError("protocol not found")
        values = current.as_dict()
        values.update(changes)
        values.pop("protocol_id", None); values.pop("status", None); values.pop("created_at", None)
        values["version"] = current.version + 1
        values["revision_of"] = current.protocol_id
        values["status"] = ProtocolStatus.PROPOSED
        values["updated_at"] = time.time()
        for key in ("preconditions", "triggers", "required_permissions", "rules", "procedures", "expected_outputs", "exceptions", "failure_handling", "verification"):
            values[key] = tuple(values.get(key, ()))
        values = {key: value for key, value in values.items() if key in ProtocolSpec.__dataclass_fields__}
        return self.create(ProtocolSpec(**values))

    def record_compliance(self, task_id: str, protocol_id: str, version: int, status: str,
                          report: dict[str, Any]) -> str:
        allowed = {"COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "VERIFICATION_UNAVAILABLE", "BLOCKED"}
        if status not in allowed:
            raise ProtocolError("invalid compliance status")
        report_id = __import__("uuid").uuid4().hex
        with self._lock, self._connection() as db:
            db.execute("INSERT INTO protocol_compliance VALUES(?,?,?,?,?,?,?)",
                       (report_id, task_id, protocol_id, version, status,
                        json.dumps(report, sort_keys=True), time.time()))
        self.audit.record("protocol.compliance", actor="assistant", outcome=status.lower(),
                          details={"task_id": task_id, "protocol_id": protocol_id, "version": version})
        return report_id

    def compliance_history(self, *, task_id: str | None = None, protocol_id: str | None = None,
                           limit: int = 100) -> list[dict[str, Any]]:
        clauses, args = [], []
        if task_id is not None: clauses.append("task_id=?"); args.append(task_id)
        if protocol_id is not None: clauses.append("protocol_id=?"); args.append(protocol_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(max(1, min(limit, 500)))
        with self._lock, self._connection() as db:
            rows = db.execute(f"SELECT report_id,task_id,protocol_id,version,status,report,created_at FROM protocol_compliance{where} ORDER BY created_at DESC LIMIT ?", args).fetchall()
        return [{"report_id": r[0], "task_id": r[1], "protocol_id": r[2], "version": r[3],
                 "status": r[4], "report": json.loads(r[5]), "created_at": r[6]} for r in rows]

    def _transition(self, protocol_id: str, target: ProtocolStatus, allowed: set[ProtocolStatus]) -> ProtocolSpec:
        with self._lock:
            spec = self.get(protocol_id)
            if spec is None or spec.status not in allowed:
                raise ProtocolError(f"invalid protocol transition to {target.value}")
            from dataclasses import replace
            updated = replace(spec, status=target, updated_at=time.time())
            with self._connection() as db:
                db.execute("UPDATE protocols SET status=?, payload=? WHERE protocol_id=?",
                           (target.value, json.dumps(updated.as_dict()), protocol_id))
            self.audit.record("protocol.transition", actor="user", outcome=target.value,
                              details={"protocol_id": protocol_id, "version": spec.version})
            return updated

    def _root_id(self, spec: ProtocolSpec) -> str:
        seen = {spec.protocol_id}
        root = spec.protocol_id
        parent = spec.revision_of
        while parent and parent not in seen:
            seen.add(parent)
            ancestor = self.get(parent)
            if ancestor is None:
                root = parent
                break
            root = ancestor.protocol_id
            parent = ancestor.revision_of
        return root

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> ProtocolSpec:
        value = json.loads(str(row[0]))
        value.setdefault("description", "")
        value.setdefault("category", "general_operational_procedures")
        value.setdefault("priority", 100)
        value.setdefault("updated_at", value.get("created_at", time.time()))
        value["status"] = ProtocolStatus(value["status"])
        for key in ("preconditions", "triggers", "required_permissions", "rules", "procedures", "expected_outputs", "exceptions", "failure_handling", "verification"):
            value[key] = tuple(value.get(key, ()))
        return ProtocolSpec(**value)

    @staticmethod
    def _validate_for_write(spec: ProtocolSpec) -> None:
        errors = validate_protocol(spec)
        if errors:
            raise ProtocolError("; ".join(errors))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
