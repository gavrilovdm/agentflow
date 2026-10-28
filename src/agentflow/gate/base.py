"""Deterministic auto gate: typecheck + tests + lint, run before any LLM review.

A gate is a strategy per target-repo language. Everything it reports is fed back to
the coder verbatim, so each implementation works hard to (a) keep the *reason* a
check failed, not only its name, and (b) never fail on a condition no source edit
can clear — those wedge a task until its budget runs out.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentflow.schemas import GateResult

ACCEPTANCE_TEST_DIR = "tests/acceptance"


def lintable(workspace: Path, paths: list[str], suffixes: tuple[str, ...]) -> list[str]:
    return [
        p for p in paths if p.endswith(suffixes) and not p.startswith(ACCEPTANCE_TEST_DIR) and (workspace / p).exists()
    ]


# The gate runs code the agent just wrote, in the target repo. Inheriting the worker's
# environment leaked agentflow's own DATABASE_URL into a Django project's pytest (every
# test errored against agentflow's Postgres; card #2 burned 10 attempts) and handed
# GitHub tokens and LLM/LangSmith keys to that code. Only what toolchains need to
# find themselves and their caches passes through; anything else the gate sets explicitly.
GATE_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "LANG", "LANGUAGE", "TZ"}
    | {"VIRTUAL_ENV", "CI", "NO_COLOR", "FORCE_COLOR", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}
    | {"NODE_EXTRA_CA_CERTS", "NODE_PATH", "NODE_OPTIONS", "COREPACK_HOME", "NVM_DIR", "VOLTA_HOME", "PNPM_HOME"}
    | {"GOPATH", "GOROOT", "GOCACHE", "GOMODCACHE", "GOFLAGS", "GOPROXY", "CARGO_HOME", "RUSTUP_HOME"}
    | {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"}
)
GATE_ENV_PREFIXES: tuple[str, ...] = ("LC_", "UV_", "PIP_", "PYTHON", "NPM_CONFIG_", "XDG_")


def gate_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for a command run in the target repo: allowlisted parent vars plus `extra`."""
    base = {k: v for k, v in os.environ.items() if k in GATE_ENV_ALLOWLIST or k.startswith(GATE_ENV_PREFIXES)}
    return {**base, **(extra or {})}


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class CommandResult:
    ok: bool
    code: int
    output: str


async def run_command(
    args: list[str], cwd: Path, timeout: float = 600, env: dict[str, str] | None = None
) -> CommandResult:
    """Run a command, merging stdout+stderr. Test runners and type checkers report
    diagnostics on stdout; reading only stderr once told the coder a task failed
    without saying why."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=gate_env(env),
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

    def has_test_suite(self, workspace: Path) -> bool:
        """Whether the project tests itself; without a suite no target test is written and
        the project's own checks are the gate."""
        ...

    def test_suffix_for(self, content: str) -> str:
        """The acceptance test's file suffix, which may depend on what the test contains."""
        ...

    def test_prompt_rules(self) -> str:
        """Framework, import-path and mocking rules injected into the test generator."""
        ...

    async def run(
        self, workspace: Path, test_paths: list[str] | None, enable_lint: bool, lint_paths: list[str] | None = None
    ) -> GateResult:
        """Run the checks. `test_paths` scopes the run to this task's acceptance test
        plus those of completed tasks (regression); None runs the whole suite.

        `lint_paths` scopes lint to the files the task wrote. Linting the whole repo failed
        tasks on pre-existing violations and on the generated acceptance test, neither of
        which the coder is allowed to touch — the task could never pass."""
        ...
