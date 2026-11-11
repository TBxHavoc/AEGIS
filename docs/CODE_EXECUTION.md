# Controlled code and test execution

`code.syntax_check` parses a Python source file under an approved project root without executing it. `code.run_tests` is available only when `AEGIS_PROJECT_ROOTS` names at least one project and both Python 3 and bubblewrap are installed. No dependency is installed automatically.

The test tool accepts only `unittest` or `pytest` discovery, runs against a temporary snapshot, and mounts that snapshot read-only inside bubblewrap. Network namespaces are disabled; `/tmp` is isolated; CPU, address space, file size, descriptors, execution time, and output are bounded. `.git`, `.agents`, `.codex`, `.aws`, virtual environments, model/node directories, `.env*`, and common key/credential files are excluded from the snapshot. Source files in the user's project are never modified by the tool. Test output includes the real exit code and combined stdout/stderr. `pytest` works only if installed in the base Python environment.

`code.run_tests` is a high-risk capability: it must be allowlisted and the local caller must set `confirmed: true`. If bubblewrap is unavailable, blocked by the host, or cannot create a network namespace, the tool reports failure/unavailability rather than running tests unsandboxed. Arbitrary shell commands, arbitrary scripts, package installation, network-enabled test execution, and generated code execution are not exposed.

This is an OS namespace and read-only snapshot boundary, not a hardened VM. Kernel vulnerabilities remain outside this application-level guarantee.
