"""Deterministic auto gate: typecheck + tests + lint, run before any LLM review.

A gate is a strategy per target-repo language. Everything it reports is fed back to
the coder verbatim, so each implementation works hard to (a) keep the *reason* a
check failed, not only its name, and (b) never fail on a condition no source edit
can clear — those wedge a task until its budget runs out.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentflow.schemas import GateResult

ACCEPTANCE_TEST_DIR = "tests/acceptance"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class CommandResult:
    ok: bool
    code: int
    output: str


async def run_command(args: list[str], cwd: Path, timeout: float = 600) -> CommandResult:
    """Run a command, merging stdout+stderr. Test runners and type checkers report
    diagnostics on stdout; reading only stderr once told the coder a task failed
    without saying why."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError as exc:
        return CommandResult(False, 127, f"{args[0]}: command not found ({exc})")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return CommandResult(False, -1, f"{' '.join(args)} timed out after {timeout}s")
    text = _ANSI.sub("", out.decode(errors="replace"))
    return CommandResult(proc.returncode == 0, proc.returncode or 0, text)


def tail(output: str, n: int = 20) -> list[str]:
    return [line for line in output.splitlines() if line.strip()][-n:]


def with_fallback(lines: list[str], raw: str) -> list[str]:
    """Never hand the coder an empty error list: if the filter matched nothing
    (e.g. a runner that failed to start), fall back to the raw tail."""
    return lines or tail(raw)


def summarize(errors: dict[str, list[str]]) -> GateResult:
    errors = {k: v for k, v in errors.items() if v}
    if not errors:
        return GateResult(passed=True)
    summary = "\n\n".join(f"{name} ({len(lines)}):\n" + "\n".join(lines[:5]) for name, lines in errors.items())
    return GateResult(passed=False, errors=errors, summary=summary)


class Gate(Protocol):
    language: str
    test_file_suffix: str

    def test_prompt_rules(self) -> str:
        """Framework, import-path and mocking rules injected into the test generator."""
        ...

    async def run(self, workspace: Path, test_paths: list[str] | None, enable_lint: bool) -> GateResult:
        """Run the checks. `test_paths` scopes the run to this task's acceptance test
        plus those of completed tasks (regression); None runs the whole suite."""
        ...
