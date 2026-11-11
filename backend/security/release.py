"""Persistent, tamper-evident trusted code baseline and release integrity subsystem."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

MANIFEST_FORMAT_VERSION = "1.0"
DEFAULT_BASELINE_FILENAME = "trusted_baseline.json"
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass
class ReleaseManifest:
    """Persistent, tamper-evident manifest of trusted source code files."""
    format_version: str
    release_id: str
    created_at: float
    files: dict[str, str]
    seal: str = ""

    def canonical_bytes(self) -> bytes:
        data = {
            "created_at": self.created_at,
            "files": {k: self.files[k] for k in sorted(self.files.keys())},
            "format_version": self.format_version,
            "release_id": self.release_id,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_seal(self, secret: str | None = None) -> str:
        payload = self.canonical_bytes()
        if secret:
            return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def verify_seal(self, secret: str | None = None) -> bool:
        if not self.seal:
            return False
        expected = self.compute_seal(secret)
        return hmac.compare_digest(self.seal, expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "release_id": self.release_id,
            "created_at": self.created_at,
            "files": {k: self.files[k] for k in sorted(self.files.keys())},
            "seal": self.seal or self.compute_seal(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], secret: str | None = None) -> "ReleaseManifest":
        if not isinstance(data, dict):
            raise ValueError("manifest data must be a JSON object")
        version = str(data.get("format_version", ""))
        if version != MANIFEST_FORMAT_VERSION:
            raise ValueError(f"unsupported manifest format_version: {version!r} (expected {MANIFEST_FORMAT_VERSION!r})")
        release_id = str(data.get("release_id", ""))
        if not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise ValueError(f"invalid release_id: {release_id!r}")
        created_at = data.get("created_at")
        if not isinstance(created_at, (int, float)) or created_at <= 0:
            raise ValueError("created_at must be a positive timestamp")
        raw_files = data.get("files")
        if not isinstance(raw_files, dict):
            raise ValueError("files must be a mapping of path to sha256")
        files: dict[str, str] = {}
        for path_str, digest in raw_files.items():
            if not isinstance(path_str, str) or not isinstance(digest, str):
                raise ValueError("files keys and values must be strings")
            if not re.fullmatch(r"[0-9a-f]{64}", digest.lower()):
                raise ValueError(f"invalid sha256 digest for {path_str}: {digest}")
            files[path_str] = digest.lower()
        seal = str(data.get("seal", ""))
        manifest = cls(
            format_version=version,
            release_id=release_id,
            created_at=float(created_at),
            files=files,
            seal=seal,
        )
        if not manifest.verify_seal(secret):
            raise ValueError("manifest integrity seal verification failed")
        return manifest


def sha256_file(path: Path) -> str:
    """Compute SHA-256 digest of a single file in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_project_source_files(
    project_root: Path,
    tracked_dirs: tuple[str, ...] = ("backend",),
    tracked_files: tuple[str, ...] = ("main.py",),
) -> dict[str, str]:
    """Scan tracked application source files and compute their SHA-256 digests."""
    project_root = project_root.resolve()
    result: dict[str, str] = {}

    for dir_name in tracked_dirs:
        dir_path = (project_root / dir_name).resolve()
        if not dir_path.is_dir():
            continue
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", ".pytest_cache"}]
            for filename in sorted(files):
                if not filename.endswith(".py"):
                    continue
                file_path = Path(root) / filename
                if file_path.is_file():
                    rel = file_path.relative_to(project_root).as_posix()
                    result[rel] = sha256_file(file_path)

    for filename in tracked_files:
        file_path = (project_root / filename).resolve()
        if file_path.is_file():
            rel = file_path.relative_to(project_root).as_posix()
            result[rel] = sha256_file(file_path)

    return {k: result[k] for k in sorted(result.keys())}


class ReleaseIntegrityManager:
    """Manages persistent trusted-code baseline verification, creation, and updates."""

    def __init__(
        self,
        project_root: str | Path,
        manifest_path: str | Path,
        audit: Any = None,
        secret: str | None = None,
        tracked_dirs: tuple[str, ...] = ("backend",),
        tracked_files: tuple[str, ...] = ("main.py",),
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.manifest_path = Path(manifest_path).resolve()
        self.audit = audit
        self.secret = secret
        self.tracked_dirs = tracked_dirs
        self.tracked_files = tracked_files

    def has_manifest(self) -> bool:
        return self.manifest_path.is_file()

    def load_manifest(self) -> ReleaseManifest:
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"baseline manifest not found at {self.manifest_path}")
        try:
            content = self.manifest_path.read_text(encoding="utf-8")
            data = json.loads(content)
            return ReleaseManifest.from_dict(data, secret=self.secret)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"malformed or corrupt manifest: {exc}") from exc

    def _write_manifest(self, manifest: ReleaseManifest) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.manifest_path.parent, 0o700)
        except OSError:
            pass
        encoded = json.dumps(manifest.to_dict(), indent=2, sort_keys=True)
        tmp_path = self.manifest_path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.manifest_path)
            try:
                os.chmod(self.manifest_path, 0o600)
            except OSError:
                pass
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def create_baseline(
        self,
        release_id: str,
        *,
        actor: str = "local_admin",
        reviewed: bool = False,
    ) -> dict[str, Any]:
        """Create initial persistent baseline. Requires explicit reviewed=True."""
        if not reviewed:
            if self.audit:
                self.audit.record(
                    "baseline.create",
                    actor=actor,
                    outcome="denied",
                    details={"reason": "explicit_review_required", "release_id": release_id},
                )
            raise PermissionError("Baseline creation requires explicit local review (reviewed=True)")
        if not release_id or not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise ValueError("Invalid release_id: must be 1-128 alphanumeric characters, hyphens, underscores, or dots")

        current_files = scan_project_source_files(
            self.project_root,
            tracked_dirs=self.tracked_dirs,
            tracked_files=self.tracked_files,
        )
        if not current_files:
            raise ValueError("No application source files found to track")

        manifest = ReleaseManifest(
            format_version=MANIFEST_FORMAT_VERSION,
            release_id=release_id,
            created_at=time.time(),
            files=current_files,
        )
        manifest.seal = manifest.compute_seal(self.secret)
        self._write_manifest(manifest)

        if self.audit:
            self.audit.record(
                "baseline.create",
                actor=actor,
                outcome="created",
                details={
                    "release_id": release_id,
                    "format_version": MANIFEST_FORMAT_VERSION,
                    "file_count": len(current_files),
                    "manifest_path": str(self.manifest_path),
                },
            )

        return {
            "ok": True,
            "status": "created",
            "release_id": release_id,
            "file_count": len(current_files),
            "manifest_path": str(self.manifest_path),
        }

    def update_baseline(
        self,
        release_id: str,
        *,
        actor: str = "local_admin",
        reviewed: bool = False,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Update existing baseline. Requires explicit reviewed=True and confirmed=True."""
        if not reviewed or not confirmed:
            reason = "explicit_review_and_confirmation_required"
            if self.audit:
                self.audit.record(
                    "baseline.update",
                    actor=actor,
                    outcome="denied",
                    details={"reason": reason, "release_id": release_id},
                )
            raise PermissionError(f"Baseline update rejected: {reason}")
        if not release_id or not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise ValueError("Invalid release_id: must be 1-128 alphanumeric characters, hyphens, underscores, or dots")

        prev_id = None
        if self.has_manifest():
            try:
                prev_manifest = self.load_manifest()
                prev_id = prev_manifest.release_id
            except Exception:
                prev_id = "corrupt_or_unreadable"

        current_files = scan_project_source_files(
            self.project_root,
            tracked_dirs=self.tracked_dirs,
            tracked_files=self.tracked_files,
        )
        if not current_files:
            raise ValueError("No application source files found to track")

        manifest = ReleaseManifest(
            format_version=MANIFEST_FORMAT_VERSION,
            release_id=release_id,
            created_at=time.time(),
            files=current_files,
        )
        manifest.seal = manifest.compute_seal(self.secret)
        self._write_manifest(manifest)

        if self.audit:
            self.audit.record(
                "baseline.update",
                actor=actor,
                outcome="updated",
                details={
                    "release_id": release_id,
                    "previous_release_id": prev_id,
                    "format_version": MANIFEST_FORMAT_VERSION,
                    "file_count": len(current_files),
                    "manifest_path": str(self.manifest_path),
                },
            )

        return {
            "ok": True,
            "status": "updated",
            "release_id": release_id,
            "previous_release_id": prev_id,
            "file_count": len(current_files),
            "manifest_path": str(self.manifest_path),
        }

    def verify(self, *, record_audit: bool = True, actor: str = "runtime") -> dict[str, Any]:
        """Verify current project files against persistent baseline manifest."""
        if not self.manifest_path.exists():
            res = {
                "ok": False,
                "status": "missing_manifest",
                "manifest_path": str(self.manifest_path),
                "error": "trusted baseline manifest not found",
                "recovery": "Create baseline using: python3 -m backend.security.release create --release-id <release_id> --reviewed",
            }
            if record_audit and self.audit:
                self.audit.record("baseline.verify", actor=actor, outcome="failed", details=res)
            return res

        try:
            manifest = self.load_manifest()
        except Exception as exc:
            res = {
                "ok": False,
                "status": "corrupt_or_invalid_manifest",
                "manifest_path": str(self.manifest_path),
                "error": str(exc),
                "recovery": "Manifest is corrupt or invalid. Restore from verified backup or recreate baseline after review: python3 -m backend.security.release create --release-id <release_id> --reviewed",
            }
            if record_audit and self.audit:
                self.audit.record("baseline.verify", actor=actor, outcome="failed", details=res)
            return res

        current_files = scan_project_source_files(
            self.project_root,
            tracked_dirs=self.tracked_dirs,
            tracked_files=self.tracked_files,
        )
        manifest_files = manifest.files

        missing = sorted([f for f in manifest_files if f not in current_files or not (self.project_root / f).is_file()])
        unexpected = sorted([f for f in current_files if f not in manifest_files])
        modified = sorted([f for f in manifest_files if f in current_files and current_files[f] != manifest_files[f]])

        if missing or unexpected or modified:
            res = {
                "ok": False,
                "status": "mismatch",
                "release_id": manifest.release_id,
                "format_version": manifest.format_version,
                "created_at": manifest.created_at,
                "checked_files": len(manifest_files),
                "missing": missing,
                "modified": modified,
                "unexpected": unexpected,
                "recovery": "Investigate changes with git status / diff. If unauthorized, revert files. If authorized, update baseline: python3 -m backend.security.release update --release-id <release_id> --reviewed --confirm",
            }
            if record_audit and self.audit:
                self.audit.record("baseline.verify", actor=actor, outcome="failed", details=res)
            return res

        res = {
            "ok": True,
            "status": "verified",
            "release_id": manifest.release_id,
            "format_version": manifest.format_version,
            "created_at": manifest.created_at,
            "checked_files": len(manifest_files),
            "missing": [],
            "modified": [],
            "unexpected": [],
        }
        if record_audit and self.audit:
            self.audit.record(
                "baseline.verify",
                actor=actor,
                outcome="verified",
                details={"release_id": manifest.release_id, "checked_files": len(manifest_files)},
            )
        return res

    def status(self) -> dict[str, Any]:
        return self.verify(record_audit=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="AEGIS Trusted Code Baseline & Release Integrity")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_p = subparsers.add_parser("create", help="Create initial trusted baseline")
    create_p.add_argument("--release-id", required=True, help="Release identifier (e.g. v0.8.1)")
    create_p.add_argument("--reviewed", action="store_true", help="Confirm baseline has been reviewed")
    create_p.add_argument("--manifest", help="Custom manifest path")
    create_p.add_argument("--root", help="Custom project root directory")

    verify_p = subparsers.add_parser("verify", help="Verify current source files against baseline")
    verify_p.add_argument("--manifest", help="Custom manifest path")
    verify_p.add_argument("--root", help="Custom project root directory")

    update_p = subparsers.add_parser("update", help="Update trusted baseline to current files")
    update_p.add_argument("--release-id", required=True, help="New release identifier")
    update_p.add_argument("--reviewed", action="store_true", help="Confirm changes have been reviewed")
    update_p.add_argument("--confirm", action="store_true", help="Explicit confirmation to update baseline")
    update_p.add_argument("--manifest", help="Custom manifest path")
    update_p.add_argument("--root", help="Custom project root directory")

    status_p = subparsers.add_parser("status", help="Show current baseline status")
    status_p.add_argument("--manifest", help="Custom manifest path")
    status_p.add_argument("--root", help="Custom project root directory")

    args = parser.parse_args()

    project_root = Path(args.root).resolve() if getattr(args, "root", None) else Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest).resolve() if getattr(args, "manifest", None) else project_root / "data" / DEFAULT_BASELINE_FILENAME
    audit_path = project_root / "data" / "audit.log"
    from backend.security.audit import AuditLogger
    audit = AuditLogger(audit_path)

    mgr = ReleaseIntegrityManager(
        project_root=project_root,
        manifest_path=manifest_path,
        audit=audit,
    )

    try:
        if args.command == "create":
            res = mgr.create_baseline(args.release_id, reviewed=args.reviewed, actor="cli_admin")
            print(json.dumps(res, indent=2))
        elif args.command == "verify":
            res = mgr.verify(record_audit=True, actor="cli_verify")
            print(json.dumps(res, indent=2))
            if not res["ok"]:
                sys.exit(1)
        elif args.command == "update":
            res = mgr.update_baseline(args.release_id, reviewed=args.reviewed, confirmed=args.confirm, actor="cli_admin")
            print(json.dumps(res, indent=2))
        elif args.command == "status":
            res = mgr.status()
            print(json.dumps(res, indent=2))
            if not res["ok"]:
                sys.exit(1)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
