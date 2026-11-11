"""Small built-in tools with canonical allow-root enforcement."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.permissions.policy import PermissionLevel
from backend.security.control import ProtectedPaths
from backend.tools.registry import ToolRegistry, ToolSpec

PROTECTED = {".git", ".agents", ".codex", ".aws", ".ssh", ".gnupg"}
SENSITIVE_NAMES = {".env", "id_rsa", "id_ed25519", "authorized_keys", "known_hosts",
                   "cookies", "login data", "web data", "key4.db", "logins.json", "signons.sqlite"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml", ".py", ".csv", ".html", ".css", ".js", ".toml", ".ini"}
MAX_FILE_BYTES = 1_000_000


def register_safe_tools(registry: ToolRegistry, status_handler: Any, search_handler: Any) -> None:
    registry.register(ToolSpec("system.status", "Read AEGIS service status.", {"type": "object"},
                               {"type": "object"}, PermissionLevel.READ_ONLY, 2.0,
                               lambda args: status_handler()))
    registry.register(ToolSpec("knowledge.search", "Search indexed local knowledge.",
                               {"type": "object", "properties": {"query": {"type": "string", "maxLength": 500}},
                                "required": ["query"], "additionalProperties": False},
                               {"type": "object"}, PermissionLevel.READ_ONLY, 3.0,
                               lambda args: search_handler(args.get("query", ""))))
    registry.register(ToolSpec("time.now", "Read the current UTC time.", {"type": "object"}, {"type": "object"},
                               PermissionLevel.READ_ONLY, 1.0,
                               lambda args: {"utc": datetime.now(timezone.utc).isoformat()}))


def register_document_tools(registry: ToolRegistry, allowed_root: str | Path,
                            additional_roots: tuple[str | Path, ...] = (),
                            protected_roots: tuple[str | Path, ...] = ()) -> None:
    roots = tuple(Path(root).expanduser().resolve() for root in (allowed_root, *additional_roots))
    protected_policy = ProtectedPaths(protected_roots)

    aegis_root = Path(__file__).resolve().parents[2]

    def checked(raw: Any, *, must_exist: bool = False, directory: bool | None = None,
                for_write: bool = False) -> tuple[Path, Path]:
        if not isinstance(raw, str) or not raw or len(raw) > 2048:
            raise ValueError("path must be a non-empty bounded string")
        supplied = Path(raw).expanduser()
        candidate = supplied if supplied.is_absolute() else roots[0] / supplied
        lexical = Path(os.path.abspath(candidate))
        protected_policy.validate(lexical, roots, allow_missing=True)
        if for_write and (lexical == aegis_root or aegis_root in lexical.parents):
            relative_to_aegis = lexical.relative_to(aegis_root)
            if not relative_to_aegis.parts or relative_to_aegis.parts[0].casefold() in {
                    "backend", "frontend", "docs", "scripts", "config", "requirements.txt", "requirements"} or lexical == aegis_root / "main.py":
                raise PermissionError("AEGIS source and security configuration require the controlled change workflow")
        root = next((item for item in roots if lexical == item or item in lexical.parents), None)
        if root is None:
            raise PermissionError("path is outside approved directories")
        if for_write:
            owner_check = lexical if lexical.exists() else lexical.parent
            while not owner_check.exists() and owner_check != owner_check.parent:
                owner_check = owner_check.parent
            metadata = owner_check.stat()
            if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
                raise PermissionError("write target is not owned by the AEGIS user")
            if metadata.st_mode & 0o002:
                raise PermissionError("write target is world writable")
        relative = lexical.relative_to(root)
        if any(part.casefold() in PROTECTED for part in relative.parts):
            raise PermissionError("protected project metadata paths are inaccessible")
        if any(part.casefold() in SENSITIVE_NAMES or part.casefold().startswith(".env.")
               or part.casefold().endswith((".pem", ".key", ".p12", ".pfx")) for part in relative.parts):
            raise PermissionError("sensitive credential paths are inaccessible")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("symlink paths are not permitted")
        resolved = lexical.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise PermissionError("canonical path is outside approved directories")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        if directory is True and resolved.exists() and not resolved.is_dir():
            raise ValueError("path is not a directory")
        if directory is False and resolved.exists() and not resolved.is_file():
            raise ValueError("path is not a regular file")
        return resolved, root

    def metadata(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path"), must_exist=True, directory=False)
        stat = path.stat()
        return {"path": str(path), "size": stat.st_size, "modified": stat.st_mtime,
                "is_file": True, "suffix": path.suffix.lower()}

    def read_document(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path"), must_exist=True, directory=False)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("document type is not approved for text reading")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file exceeds the one megabyte read limit")
        content = path.read_text(encoding="utf-8")
        return {"path": str(path), "content": content[:12000], "truncated": len(content) > 12000}

    def list_directory(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path", str(roots[0])), must_exist=True, directory=True)
        entries = []
        for item in sorted(path.iterdir(), key=lambda value: value.name.casefold()):
            if item.name.casefold() in PROTECTED or item.is_symlink():
                continue
            entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
            if len(entries) >= 500:
                break
        return {"path": str(path), "entries": entries, "truncated": len(entries) >= 500}

    def search_files(args: dict[str, Any]) -> dict[str, Any]:
        base, _ = checked(args.get("path", str(roots[0])), must_exist=True, directory=True)
        query = args.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        matches = []
        scanned, max_scan = 0, 3000
        for folder, dirs, filenames in os.walk(base, followlinks=False):
            dirs[:] = [name for name in dirs if name.casefold() not in PROTECTED
                       and not (Path(folder) / name).is_symlink()]
            for filename in filenames:
                path = Path(folder) / filename
                scanned += 1
                if scanned > max_scan:
                    break
                if path.suffix.lower() not in TEXT_SUFFIXES or path.is_symlink():
                    continue
                try:
                    if path.stat().st_size > 200_000:
                        continue
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if query.casefold() in content.casefold():
                    matches.append({"path": str(path), "excerpt": content[:500]})
                    if len(matches) >= 100:
                        break
            if scanned > max_scan or len(matches) >= 100:
                break
        return {"matches": matches, "files_scanned": min(scanned, max_scan), "truncated": scanned > max_scan}

    def find_files(args: dict[str, Any]) -> dict[str, Any]:
        base, _ = checked(args.get("path", str(roots[0])), must_exist=True, directory=True)
        query = args.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("file name query must contain 1 to 200 characters")
        matches=[]; scanned=0; max_scan=10_000
        for folder, dirs, filenames in os.walk(base, followlinks=False):
            dirs[:]=[name for name in dirs if name.casefold() not in PROTECTED and not (Path(folder)/name).is_symlink()]
            for filename in filenames:
                scanned += 1
                path=Path(folder)/filename
                if path.is_symlink(): continue
                if query.casefold() in filename.casefold():
                    matches.append({"path":str(path),"name":filename})
                    if len(matches)>=100: break
                if scanned>=max_scan: break
            if scanned>=max_scan or len(matches)>=100: break
        return {"matches":matches,"files_scanned":scanned,"truncated":scanned>=max_scan}

    def create_file(args: dict[str, Any]) -> dict[str, Any]:
        path, root = checked(args.get("path"), for_write=True)
        content = args.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("file content exceeds the one megabyte limit")
        if path.exists():
            raise FileExistsError("file already exists; use the explicit replace operation")
        return _atomic_write(path, root, content)

    def replace_file(args: dict[str, Any]) -> dict[str, Any]:
        path, root = checked(args.get("path"), must_exist=True, directory=False, for_write=True)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("existing file exceeds the one megabyte replacement limit")
        content = args.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("file content exceeds the one megabyte limit")
        backup = path.with_name(path.name + ".aegis-backup")
        if backup.exists():
            raise FileExistsError("backup file already exists; preserve it before replacing again")
        shutil.copy2(path, backup)
        result = _atomic_write(path, root, content)
        result["backup"] = str(backup)
        return result

    def mkdir(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path"), for_write=True)
        path.mkdir(parents=True, exist_ok=False)
        return {"path": str(path), "created": True}

    def move(args: dict[str, Any]) -> dict[str, Any]:
        source, root = checked(args.get("source"), must_exist=True, directory=False, for_write=True)
        target, target_root = checked(args.get("target"), for_write=True)
        if target_root != root or target.exists():
            raise ValueError("move target must be new and within the same approved root")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return {"source": str(source), "target": str(target), "moved": True}

    def copy_file(args: dict[str, Any]) -> dict[str, Any]:
        source, root = checked(args.get("source"), must_exist=True, directory=False)
        target, target_root = checked(args.get("target"), directory=False, for_write=True)
        if target_root != root or target.exists() or source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("copy target must be new within the source root and file size limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return {"source": str(source), "target": str(target), "copied": True}

    def delete_file(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path"), must_exist=True, for_write=True)
        if path.is_dir():
            raise ValueError("recursive directory deletion is not supported")
        path.unlink()
        return {"path": str(path), "deleted": True}

    def file_hash(args: dict[str, Any]) -> dict[str, Any]:
        path, _ = checked(args.get("path"), must_exist=True, directory=False)
        if path.stat().st_size > 100_000_000:
            raise ValueError("file exceeds the 100 MB hash limit")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(block)
        return {"path": str(path), "algorithm": "sha256", "digest": digest.hexdigest()}

    object_in = {"type": "object", "additionalProperties": False}
    specs = (
        ("file.metadata", "Inspect metadata under an approved root.", {"path": {"type": "string", "maxLength": 2048}}, ["path"], PermissionLevel.READ_ONLY, 1, metadata, "low"),
        ("document.read", "Read a bounded UTF-8 text document under an approved root.", {"path": {"type": "string", "maxLength": 2048}}, ["path"], PermissionLevel.READ_ONLY, 3, read_document, "low"),
        ("file.list", "List a bounded set of entries in an approved directory.", {"path": {"type": "string", "maxLength": 2048}}, [], PermissionLevel.READ_ONLY, 2, list_directory, "low"),
        ("file.search", "Search text content in approved directories.", {"path": {"type": "string", "maxLength": 2048}, "query": {"type": "string", "maxLength": 200, "minLength": 1}}, ["query"], PermissionLevel.READ_ONLY, 5, search_files, "low"),
        ("file.find", "Find file names under approved directories without following symlinks.", {"path": {"type": "string", "maxLength": 2048}, "query": {"type": "string", "maxLength": 200, "minLength": 1}}, ["query"], PermissionLevel.READ_ONLY, 5, find_files, "low"),
        ("file.hash", "Calculate a SHA-256 digest for a bounded approved file.", {"path": {"type": "string", "maxLength": 2048}}, ["path"], PermissionLevel.READ_ONLY, 5, file_hash, "low"),
        ("file.create", "Create a new bounded text file atomically.", {"path": {"type": "string", "maxLength": 2048}, "content": {"type": "string", "maxLength": MAX_FILE_BYTES}}, ["path", "content"], PermissionLevel.WRITE_LOCAL, 5, create_file, "moderate"),
        ("file.replace", "Replace a file after creating a one-time backup.", {"path": {"type": "string", "maxLength": 2048}, "content": {"type": "string", "maxLength": MAX_FILE_BYTES}}, ["path", "content"], PermissionLevel.DESTRUCTIVE, 5, replace_file, "high"),
        ("directory.create", "Create a new approved directory.", {"path": {"type": "string", "maxLength": 2048}}, ["path"], PermissionLevel.WRITE_LOCAL, 3, mkdir, "moderate"),
        ("file.move", "Move a file to a new location within one approved root.", {"source": {"type": "string", "maxLength": 2048}, "target": {"type": "string", "maxLength": 2048}}, ["source", "target"], PermissionLevel.WRITE_LOCAL, 5, move, "moderate"),
        ("file.copy", "Copy a bounded file to a new location within one approved root.", {"source": {"type": "string", "maxLength": 2048}, "target": {"type": "string", "maxLength": 2048}}, ["source", "target"], PermissionLevel.WRITE_LOCAL, 5, copy_file, "moderate"),
        ("file.delete", "Delete one approved file; explicit confirmation is required.", {"path": {"type": "string", "maxLength": 2048}}, ["path"], PermissionLevel.DESTRUCTIVE, 3, delete_file, "high"),
    )
    for name, description, properties, required, permission, timeout, handler, risk in specs:
        schema = {**object_in, "properties": properties, "required": required}
        registry.register(ToolSpec(name, description, schema, {"type": "object"}, permission, timeout, handler,
                                   risk=risk, requires_confirmation=permission != PermissionLevel.READ_ONLY))


def _atomic_write(path: Path, root: Path, content: str) -> dict[str, Any]:
    if path != root and root not in path.parents:
        raise PermissionError("write path escaped approved root")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".aegis-write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except OSError: pass
        raise
    return {"path": str(path), "bytes_written": len(content.encode("utf-8")), "written": True}
