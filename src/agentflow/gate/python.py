"""Gate for Python target repos: compile/mypy + pytest + ruff."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path

from agentflow.gate.base import (
    ACCEPTANCE_TEST_DIR,
    lintable,
    run_command,
    summarize,
    tail,
    with_fallback,
)
from agentflow.schemas import GateResult

VENV = ".venv"
STAMP = ".agentflow-install-stamp"
NO_TESTS_COLLECTED = 5  # pytest exit code


def fresh_bytecode_env() -> dict[str, str]:
    """Bytecode caches are validated by source mtime (1 s resolution) and size. The coder
    often rewrites a file within the same second at the same size (`a - b` → `a + b`), and
    Python then runs the stale .pyc — the gate judges code that no longer exists. A fresh
    cache prefix per gate run means no cached bytecode is ever reused."""
    return {"PYTHONPYCACHEPREFIX": tempfile.mkdtemp(prefix="agentflow-pyc-")}


def extract_pytest_failures(raw: str, per_failure: int = 6) -> list[str]:
    """Keep the *reason* each test failed: the FAILED/ERROR marker, the `E ` assertion
    lines, and exception lines. Test titles alone made the coder re-guess the same fix."""
    lines = raw.splitlines()
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if line.startswith(("FAILED", "ERROR")) or line.lstrip().startswith("E "):
            keep.add(i)
        elif re.match(r"^\s*[A-Za-z]*(Error|Exception)\b", line[:2000]):
            keep.update(range(i, min(len(lines), i + per_failure)))
    return [lines[i] for i in sorted(keep) if lines[i].strip()]


def _has_mypy_config(ws: Path) -> bool:
    if (ws / "mypy.ini").exists() or (ws / ".mypy.ini").exists():
        return True
    pyproject = ws / "pyproject.toml"
    return pyproject.exists() and "[tool.mypy]" in pyproject.read_text(errors="ignore")


class PythonGate:
    language = "python"
    test_file_suffix = ".py"

    def test_prompt_rules(self) -> str:
        return f"""- Use pytest. Plain `assert` statements, fixtures over setup methods.
- Mock ALL external I/O (HTTP, DB, filesystem outside tmp_path) with unittest.mock / monkeypatch.
- Each test is deterministic and named for the scenario: test_returns_404_when_user_missing.
- The file is saved to `{ACCEPTANCE_TEST_DIR}/`. Import the code under test by its package path
  as installed from the repo root (e.g. `from mypkg.users import get_user`), never relative imports.
- Cover: happy path, at least one error case, at least one edge case."""

    async def _ensure_env(self, ws: Path) -> str | None:
        """Install the project once per dependency-manifest revision. A fresh project has a
        manifest but no environment, and every check would fail on a missing module
        instead of on a real defect."""
        manifests = [p for p in ("pyproject.toml", "requirements.txt", "requirements-dev.txt") if (ws / p).exists()]
        digest = hashlib.sha256(b"".join((ws / p).read_bytes() for p in manifests)).hexdigest()
        stamp = ws / VENV / STAMP
        if stamp.exists() and stamp.read_text() == digest:
            return None
        if not (ws / VENV).exists():
            await run_command(["uv", "venv", VENV, "-q"], ws)
        py = str(ws / VENV / "bin" / "python")
        install = ["uv", "pip", "install", "-q", "--python", py, "pytest", "ruff", "mypy"]
        if (ws / "pyproject.toml").exists():
            install += ["-e", "."]
        for req in ("requirements.txt", "requirements-dev.txt"):
            if (ws / req).exists():
                install += ["-r", req]
        result = await run_command(install, ws)
        if not result.ok:
            # Don't raise: a bad dependency list is something the coder can fix, so report it.
            return "Dependency install failed:\n" + "\n".join(tail(result.output, 8))
        stamp.write_text(digest)
        return None

    async def run(
        self, workspace: Path, test_paths: list[str] | None, enable_lint: bool, lint_paths: list[str] | None = None
    ) -> GateResult:
        install_error = await self._ensure_env(workspace)
        bin_ = workspace / VENV / "bin"
        env = fresh_bytecode_env()
        try:
            return await self._checks(workspace, bin_, env, install_error, test_paths, enable_lint, lint_paths)
        finally:
            shutil.rmtree(env["PYTHONPYCACHEPREFIX"], ignore_errors=True)

    async def _checks(
        self,
        workspace: Path,
        bin_: Path,
        env: dict[str, str],
        install_error: str | None,
        test_paths: list[str] | None,
        enable_lint: bool,
        lint_paths: list[str] | None,
    ) -> GateResult:

        if _has_mypy_config(workspace):
            types = await run_command([str(bin_ / "mypy"), ".", "--exclude", ACCEPTANCE_TEST_DIR], workspace, env=env)
            type_errors = (
                []
                if types.ok
                else with_fallback([line for line in types.output.splitlines() if ": error:" in line], types.output)
            )
        else:
            # No type-checker configured: still catch syntax errors, the analogue of tsc.
            types = await run_command(
                [str(bin_ / "python"), "-m", "compileall", "-q", "-x", r"\.venv", "."], workspace, env=env
            )
            type_errors = [] if types.ok else tail(types.output)

        test_args = [str(bin_ / "pytest"), "-q", "-rfE", "--no-header", "-p", "no:cacheprovider"]
        test_args += [p for p in (test_paths or []) if (workspace / p).exists()]
        tests = await run_command(test_args, workspace, env=env)
        # Exit 5 = nothing collected. Normal for a scaffolding task with no tests yet;
        # a failure only when this task's own acceptance test was supposed to run.
        tests_ok = tests.ok or (tests.code == NO_TESTS_COLLECTED and not test_paths)
        test_errors = [] if tests_ok else with_fallback(extract_pytest_failures(tests.output), tests.output)

        lint_errors: list[str] = []
        targets = ["."] if lint_paths is None else lintable(workspace, lint_paths, (".py",))
        if enable_lint and targets:
            lint = await run_command(
                [
                    str(bin_ / "ruff"),
                    "check",
                    "--output-format=concise",
                    "--extend-exclude",
                    ACCEPTANCE_TEST_DIR,
                    *targets,
                ],
                workspace,
            )
            lint_errors = [] if lint.ok else tail(lint.output)

        return summarize(
            {
                "Dependency install": [install_error] if install_error else [],
                "Type/compile errors": type_errors,
                "Test failures": test_errors,
                "Lint errors": lint_errors,
            }
        )
