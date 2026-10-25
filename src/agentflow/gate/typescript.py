"""Gate for TypeScript target repos: tsc + vitest + eslint (port of the original TS gate)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from agentflow.config import get_settings
from agentflow.gate.base import (
    ACCEPTANCE_TEST_DIR,
    CommandResult,
    lintable,
    run_command,
    summarize,
    tail,
    with_fallback,
)
from agentflow.schemas import GateResult

STAMP = "node_modules/.agentflow-install-stamp"
# The gate brings its own vitest, installed outside the target repo. It used to assume the
# repo had vitest: on a Vite site without tests every run failed "Cannot find package
# 'vitest'", whatever the code, until the coder added vitest to the repo's package.json —
# a dependency the task never asked for, shipped in the PR.
VITEST_VERSION = "3.2.4"
_runner_lock = asyncio.Lock()
ESLINT_CONFIGS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
)
_OK = CommandResult(True, 0, "")


def extract_vitest_failures(raw: str, per_failure: int = 6, max_line: int = 2000) -> list[str]:
    """Marker lines plus the diagnostics that follow them. Deliberately no alternation
    regex over whole lines: one with `expected .+ to ` backtracked quadratically and
    took seconds on a single 60k-char assertion diff."""
    lines = raw.splitlines()
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if "FAIL" in line or "✗" in line or "×" in line:
            keep.update(range(i, min(len(lines), i + 1 + per_failure)))
        head = line[:max_line]
        if (
            re.match(r"^\s*[A-Za-z]*Error\b", head)
            or "Unhandled error" in head
            or ("expected " in head and " to " in head)
        ):
            keep.add(i)
    return [lines[i] for i in sorted(keep) if lines[i].strip()]


def _has_eslint(ws: Path) -> bool:
    if any((ws / c).exists() for c in ESLINT_CONFIGS):
        return True
    pkg = ws / "package.json"
    return pkg.exists() and '"eslintConfig"' in pkg.read_text(errors="ignore")


class TypeScriptGate:
    language = "typescript"
    test_file_suffix = ".test.ts"

    def test_prompt_rules(self) -> str:
        return f"""- Use vitest: `import {{ describe, it, expect, vi }} from "vitest"`. ESM imports only.
- Mock ALL external dependencies (API calls, file I/O, DB) with `vi.mock()`.
- Test names describe the scenario: "returns 404 when user not found".
- The file is saved to `{ACCEPTANCE_TEST_DIR}/<name>.test.ts`, TWO directories below the root.
  Import sources with `../../src/...` — `../src/...` resolves to tests/src and every test fails to load.
- Cover: happy path, at least one error case, at least one edge case."""

    async def _ensure_deps(self, ws: Path) -> str | None:
        pkg = ws / "package.json"
        if not pkg.exists():
            return None
        digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
        stamp = ws / STAMP
        if stamp.exists() and stamp.read_text() == digest:
            return None
        result = await run_command(["npm", "install", "--no-audit", "--no-fund"], ws)
        if not result.ok:
            return "npm install failed:\n" + "\n".join(tail(result.output, 8))
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(digest)
        return None

    async def run(
        self, workspace: Path, test_paths: list[str] | None, enable_lint: bool, lint_paths: list[str] | None = None
    ) -> GateResult:
        install_error = await self._ensure_deps(workspace)
        vitest, runner_error = await _vitest_bin(workspace)
        test_args = [vitest, "run", "--config", str(_gate_config(workspace)), "--root", ".", "--reporter=verbose"]
        test_args += [os.path.normpath(p) for p in (test_paths or [])]
        targets = ["src"] if lint_paths is None else lintable(workspace, lint_paths, (".ts", ".tsx"))
        lint_on = enable_lint and bool(targets) and _has_eslint(workspace)

        tsc, tests, lint = await asyncio.gather(
            run_command(["npx", "tsc", "--noEmit"], workspace),
            run_command(test_args, workspace) if not runner_error else _failed(runner_error),
            run_command(["npx", "eslint", *targets, "--max-warnings", "0"], workspace) if lint_on else _done(),
        )
        # TS18003 "No inputs were found": a scaffolding task has no sources yet. That is a
        # project-shape condition, not a defect, and wedged every run's first task.
        if not tsc.ok and "error TS18003" in tsc.output:
            tsc = _OK
        tsc_errors = [
            line for line in tsc.output.splitlines() if re.search(r"error TS\d+", line) and not _gate_owned_import(line)
        ]
        if not tsc.ok and not tsc_errors and re.search(r"error TS\d+", tsc.output):
            tsc = _OK  # the only errors were about vitest, which the repo does not (and need not) have
        # eslint without the TS parser reports "Parsing error" on every file — unfixable
        # by editing code, and tsc already covers syntax.
        if not lint.ok and re.search(r"Parsing error|Cannot find module '@typescript-eslint", lint.output):
            lint = _OK

        return summarize(
            {
                "Dependency install": [install_error] if install_error else [],
                "TypeScript errors": [] if tsc.ok else with_fallback(tsc_errors, tsc.output),
                "Test failures": [] if tests.ok else with_fallback(extract_vitest_failures(tests.output), tests.output),
                "Lint errors": [] if lint.ok else tail(lint.output),
            }
        )


async def _done() -> CommandResult:
    return _OK


async def _failed(message: str) -> CommandResult:
    return CommandResult(False, 1, message)


def _gate_owned_import(tsc_line: str) -> bool:
    """tsc on a repo whose tsconfig includes tests/ cannot resolve the gate's vitest; that is
    not something the coder should fix by adding the package."""
    return tsc_line.startswith(ACCEPTANCE_TEST_DIR) and "'vitest'" in tsc_line and "TS2307" in tsc_line


def _gate_config(workspace: Path) -> Path:
    """The gate's vitest config, outside the repo. Kept in the repo, the coder repeatedly
    narrowed its `include` to a directory it had invented (vitest then ran zero files) or
    rewrote it; and it must never reach the PR. It imports nothing, so it loads with
    either vitest (the repo's or ours)."""
    key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    d = Path(tempfile.gettempdir()) / "agentflow-gate" / key
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "vitest.config.mjs"
    test = {"include": ["tests/**/*.test.ts", "src/**/*.test.ts"]}
    cfg.write_text(f"export default {json.dumps({'cacheDir': str(d / 'vite'), 'test': test})};\n")
    return cfg


async def _vitest_bin(workspace: Path) -> tuple[str, str | None]:
    """The repo's own vitest when it has one (the version its tests are written for),
    otherwise ours, installed once into JS_RUNNER_DIR (the Docker image ships it)."""
    own = workspace / "node_modules" / ".bin" / "vitest"
    if own.exists():
        return str(own), None
    runner = Path(get_settings().js_runner_dir).expanduser()
    bin_ = runner / "node_modules" / ".bin" / "vitest"
    async with _runner_lock:
        if not bin_.exists():
            runner.mkdir(parents=True, exist_ok=True)
            (runner / "package.json").write_text('{"private": true}\n')
            result = await run_command(
                ["npm", "install", "--no-audit", "--no-fund", f"vitest@{VITEST_VERSION}"], runner
            )
            if not result.ok:
                return str(bin_), "installing the gate's vitest failed:\n" + "\n".join(tail(result.output, 8))
    return str(bin_), None
