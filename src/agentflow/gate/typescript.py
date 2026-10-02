"""Gate for TypeScript target repos: tsc + vitest + eslint (port of the original TS gate)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

from agentflow.gate.base import (
    ACCEPTANCE_TEST_DIR,
    CommandResult,
    run_command,
    summarize,
    tail,
    with_fallback,
)
from agentflow.schemas import GateResult

STAMP = "node_modules/.agentflow-install-stamp"
# The gate owns its vitest config. The coder repeatedly narrowed `include` in the
# project's vitest.config.ts to a directory it had invented, silently filtering out
# the acceptance tests — vitest then ran zero files and no source edit could clear it.
GATE_VITEST_CONFIG = "vitest.agentflow.config.ts"
ESLINT_CONFIGS = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml",
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
        if re.match(r"^\s*[A-Za-z]*Error\b", head) or "Unhandled error" in head or (
            "expected " in head and " to " in head
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

    async def run(self, workspace: Path, test_paths: list[str] | None, enable_lint: bool) -> GateResult:
        install_error = await self._ensure_deps(workspace)
        (workspace / GATE_VITEST_CONFIG).write_text(
            'import { defineConfig } from "vitest/config";\n\n'
            'export default defineConfig({\n'
            '  test: { include: ["tests/**/*.test.ts", "src/**/*.test.ts"] },\n'
            '});\n'
        )
        test_args = ["npx", "vitest", "run", "--config", GATE_VITEST_CONFIG, "--reporter=verbose"]
        test_args += [os.path.normpath(p) for p in (test_paths or [])]
        lint_on = enable_lint and _has_eslint(workspace)

        tsc, tests, lint = await asyncio.gather(
            run_command(["npx", "tsc", "--noEmit"], workspace),
            run_command(test_args, workspace),
            run_command(["npx", "eslint", "src", "--max-warnings", "0"], workspace) if lint_on else _done(),
        )
        # TS18003 "No inputs were found": a scaffolding task has no sources yet. That is a
        # project-shape condition, not a defect, and wedged every run's first task.
        if not tsc.ok and "error TS18003" in tsc.output:
            tsc = _OK
        # eslint without the TS parser reports "Parsing error" on every file — unfixable
        # by editing code, and tsc already covers syntax.
        if not lint.ok and re.search(r"Parsing error|Cannot find module '@typescript-eslint", lint.output):
            lint = _OK

        return summarize(
            {
                "Dependency install": [install_error] if install_error else [],
                "TypeScript errors": [] if tsc.ok else with_fallback(
                    [line for line in tsc.output.splitlines() if re.search(r"error TS\d+", line)], tsc.output
                ),
                "Test failures": [] if tests.ok else with_fallback(extract_vitest_failures(tests.output), tests.output),
                "Lint errors": [] if lint.ok else tail(lint.output),
            }
        )


async def _done() -> CommandResult:
    return _OK
