"""Allowlisted test runner requiring bubblewrap mount/network isolation."""
from __future__ import annotations

import ast
import os
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.permissions.policy import PermissionLevel
from backend.tools.registry import ToolRegistry, ToolSpec

MAX_OUTPUT = 64_000


def register_code_tools(registry: ToolRegistry, allowed_roots: tuple[str | Path, ...]) -> None:
    roots = tuple(Path(item).resolve() for item in allowed_roots)
    bwrap = shutil.which("bwrap")
    python = shutil.which("python3")

    def project_path(raw: Any) -> Path:
        if not isinstance(raw, str) or len(raw) > 2048:
            raise ValueError("project path is invalid")
        candidate = Path(os.path.abspath(Path(raw).expanduser()))
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            if current.is_symlink(): raise PermissionError("project paths cannot traverse symlinks")
        path = candidate.resolve(strict=True)
        if not path.is_dir() or not any(path == root or root in path.parents for root in roots):
            raise PermissionError("project must be an approved directory")
        if any(part.casefold() in {".git", ".agents", ".codex", ".aws"} for part in path.parts):
            raise PermissionError("protected project metadata cannot be executed")
        return path

    def syntax(args: dict[str, Any]) -> dict[str, Any]:
        root = project_path(args["project"])
        relative = Path(args["file"])
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
            raise ValueError("syntax check accepts only a relative Python source path")
        source = (root / relative).resolve(strict=True)
        if root not in source.parents or source.is_symlink() or source.stat().st_size > 1_000_000:
            raise PermissionError("source file is outside the approved project or exceeds its limit")
        ast.parse(source.read_text(encoding="utf-8"), filename=str(relative))
        return {"file": str(relative), "syntax_valid": True, "execution_performed": False}

    def tests(args: dict[str, Any], cancellation=None) -> dict[str, Any]:
        if not bwrap or not python:
            raise RuntimeError("sandbox_runtime_unavailable: bubblewrap and python3 are required")
        root = project_path(args["project"])
        runner = args.get("runner", "unittest")
        target = args.get("target", "tests")
        if runner not in {"unittest", "pytest"}:
            raise ValueError("only unittest or pytest discovery is allowed")
        if not isinstance(target, str) or len(target) > 256 or target.startswith("/") or ".." in Path(target).parts:
            raise ValueError("test target must be a safe relative path")
        target_path = (root / target).resolve(strict=True)
        if root not in target_path.parents and target_path != root:
            raise PermissionError("test target is outside approved project")
        with tempfile.TemporaryDirectory(prefix="aegis-test-snapshot-") as temporary:
            snapshot = Path(temporary) / "project"
            _copy_safe_snapshot(root, snapshot)
            relative_target = target_path.relative_to(root).as_posix() if target_path != root else "."
            python_code = (
                "import sys,unittest; sys.path.insert(0,'/workspace'); "
                f"suite=unittest.defaultTestLoader.discover('/workspace/' + {relative_target!r}, pattern='test*.py'); "
                "result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(0 if result.wasSuccessful() else 1)"
            ) if runner == "unittest" else ""
            command = [bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--unshare-net",
                       "--ro-bind", str(snapshot), "/workspace", "--proc", "/proc", "--dev", "/dev",
                       "--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr"]
            for system_path in ("/lib", "/lib64", "/bin"):
                if Path(system_path).exists(): command.extend(["--ro-bind", system_path, system_path])
            command += ["--chdir", "/workspace", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp"]
            if runner == "unittest": command += [python, "-I", "-c", python_code]
            else: command += [python, "-I", "-m", "pytest", "-q", relative_target]
            env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
            def limits() -> None:
                resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
                resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            with tempfile.TemporaryFile() as output_file:
                process = subprocess.Popen(command, cwd="/", env=env, stdin=subprocess.DEVNULL,
                                           stdout=output_file, stderr=subprocess.STDOUT, preexec_fn=limits)
                deadline = __import__("time").monotonic() + 35
                cancelled=False
                while process.poll() is None:
                    if cancellation is not None and cancellation.is_set():
                        cancelled=True; process.kill(); break
                    if __import__("time").monotonic() >= deadline:
                        process.kill(); process.wait(); output_file.seek(0)
                        return {"exit_code": None, "timed_out": True,
                                "output": output_file.read(MAX_OUTPUT).decode("utf-8", "replace")}
                    __import__("time").sleep(.05)
                code=process.wait(); output_file.seek(0); raw=output_file.read(MAX_OUTPUT+1)
                return {"exit_code": code, "timed_out": False, "cancelled": cancelled,
                        "output": raw[:MAX_OUTPUT].decode("utf-8", "replace"), "output_truncated": len(raw)>MAX_OUTPUT,
                        "sandbox": "bubblewrap_readonly_snapshot_network_namespace", "network_enabled": False,
                        "credential_files_excluded": True}

    schema = {"type": "object", "properties": {"project": {"type": "string", "maxLength": 2048},
              "file": {"type": "string", "maxLength": 256}}, "required": ["project", "file"], "additionalProperties": False}
    registry.register(ToolSpec("code.syntax_check", "Parse an approved Python source file without executing it.",
        schema, {"type": "object"}, PermissionLevel.READ_ONLY, 5, syntax, risk="low",
        availability="available" if roots else "unavailable"))
    test_schema = {"type": "object", "properties": {"project": {"type": "string", "maxLength": 2048},
        "runner": {"type": "string", "enum": ["unittest", "pytest"]}, "target": {"type": "string", "maxLength": 256}},
        "required": ["project"], "additionalProperties": False}
    registry.register(ToolSpec("code.run_tests", "Run allowlisted test discovery in a read-only, network-isolated bubblewrap sandbox.",
        test_schema, {"type": "object"}, PermissionLevel.DESTRUCTIVE, 40, tests, risk="high",
        availability="available" if bwrap and python and roots else "unavailable", requires_confirmation=True,
        cancellable_handler=tests))


def _copy_safe_snapshot(source: Path, destination: Path) -> None:
    excluded_dirs={".git", ".agents", ".codex", ".aws", ".ssh", ".gnupg", ".venv", "venv", "node_modules", "__pycache__", "models", "data"}
    excluded_files={".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials"}
    total=0; count=0
    for folder, dirs, files in os.walk(source, followlinks=False):
        dirs[:]=[name for name in dirs if name.casefold() not in excluded_dirs and not (Path(folder)/name).is_symlink()]
        for name in files:
            path=Path(folder)/name
            if path.is_symlink() or _sensitive_name(name, excluded_files) or name.startswith(".env") or path.suffix.lower() in {".pem",".key",".p12",".pfx"}:
                continue
            count+=1; total+=path.stat().st_size
            if count>10_000 or total>50_000_000:
                raise ValueError("project snapshot exceeds the 10,000 file or 50 MB limit")
    def ignore(folder: str, names: list[str]) -> set[str]:
        ignored=set()
        for name in names:
            path=Path(folder)/name
            if name.casefold() in excluded_dirs or path.is_symlink() or _sensitive_name(name, excluded_files) or name.startswith(".env") or path.suffix.lower() in {".pem",".key",".p12",".pfx"}:
                ignored.add(name)
        return ignored
    shutil.copytree(source,destination,ignore=ignore,symlinks=False)


def _sensitive_name(name: str, exact: set[str]) -> bool:
    lowered=name.casefold()
    return (lowered in exact or lowered.startswith(("secret_", "credentials_", "token_"))
            or lowered.endswith((".secret", ".credentials", ".token", ".db", ".sqlite", ".sqlite3", ".log")))
