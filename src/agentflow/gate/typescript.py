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
# The gate brings its own test runner (vitest, jsdom, Testing Library), installed outside the
# target repo. It used to assume the repo had vitest: on a Vite site without tests every run
# failed "Cannot find package 'vitest'", whatever the code, until the coder added vitest to
# the repo's package.json — a dependency the task never asked for, shipped in the PR.
RUNNER_MANIFEST = Path(__file__).with_name("js_runner") / "package.json"
TESTING_LIBRARY = ("react", "dom", "jest-dom", "user-event")
REPO_CONFIGS = tuple(f"{n}.config.{e}" for n in ("vitest", "vite") for e in ("ts", "mts", "js", "mjs", "cjs"))
# A component test (`render(<Home />)`) is JSX; saved as .ts it never even parses.
_JSX = re.compile(r"<[A-Za-z][\w.]*(\s[^<>]*)?/>|</[A-Za-z][\w.]*>")
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

    def test_suffix_for(self, content: str) -> str:
        return ".test.tsx" if _JSX.search(content) else ".test.ts"

    def test_prompt_rules(self) -> str:
        return f"""- Use vitest: `import {{ describe, it, expect, vi }} from "vitest"`. ESM imports only.
- Tests run in jsdom. For UI behaviour, render the component with `@testing-library/react`
  (`render`, `screen`) and assert on what the user sees; jest-dom matchers (`toBeInTheDocument`,
  `toHaveAttribute`) are available. The gate provides these packages — the repo need not have them.
- Mock ALL external dependencies (API calls, file I/O, DB) with `vi.mock()`.
- Test names describe the scenario: "returns 404 when user not found".
- The file is saved to `{ACCEPTANCE_TEST_DIR}/<name>.test.ts` (`.test.tsx` when it contains JSX), TWO
  directories below the root. Import sources with `../../src/...` — `../src/...` resolves to tests/src
  and every test fails to load.
- Cover: happy path, at least one error case, at least one edge case."""

    async def _ensure_deps(self, ws: Path) -> str | None:
        pkg = ws / "package.json"
        if not pkg.exists():
            return None
        digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
        stamp = ws / STAMP
        if stamp.exists() and stamp.read_text() == digest:
            return None
        # `npm install` rewrote the repo's package-lock.json (another npm version drops fields),
        # and that churn reached the PR. `npm ci` installs exactly the lockfile; only a
        # package.json that no longer matches it (the task changed dependencies) updates it.
        flags = ["--no-audit", "--no-fund"]
        if (ws / "package-lock.json").exists():
            result = await run_command(["npm", "ci", *flags], ws)
            if not result.ok:
                result = await run_command(["npm", "install", *flags], ws)
        else:
            result = await run_command(["npm", "install", "--no-package-lock", *flags], ws)
        if not result.ok:
            return "npm install failed:\n" + "\n".join(tail(result.output, 8))
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(digest)
        return None

    async def run(
        self, workspace: Path, test_paths: list[str] | None, enable_lint: bool, lint_paths: list[str] | None = None
    ) -> GateResult:
        install_error = await self._ensure_deps(workspace)
        runner_error = await _ensure_runner()
        vitest = _vitest_bin(workspace)
        paths = [os.path.normpath(p) for p in (test_paths or [])]
        targets = ["src"] if lint_paths is None else lintable(workspace, lint_paths, (".ts", ".tsx"))
        lint_on = enable_lint and bool(targets) and _has_eslint(workspace)

        tsc, tests, lint = await asyncio.gather(
            run_command(["npx", "tsc", "--noEmit"], workspace),
            _run_tests(vitest, workspace, paths) if not runner_error else _failed(runner_error),
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


_GATE_MODULES = re.compile(r"'(vitest|@testing-library/[\w-]+)'")


def _gate_owned_import(tsc_line: str) -> bool:
    """tsc on a repo whose tsconfig includes tests/ cannot resolve the gate's packages; that
    is not something the coder should fix by adding them."""
    return tsc_line.startswith(ACCEPTANCE_TEST_DIR) and "TS2307" in tsc_line and bool(_GATE_MODULES.search(tsc_line))


def _runner_dir() -> Path:
    return Path(get_settings().js_runner_dir).expanduser()


async def _ensure_runner() -> str | None:
    """Install the gate's packages into JS_RUNNER_DIR once (the Docker image ships them)."""
    runner = _runner_dir()
    manifest = RUNNER_MANIFEST.read_text()
    async with _runner_lock:
        installed = runner / "package.json"
        bin_ = runner / "node_modules" / ".bin" / "vitest"
        if bin_.exists() and installed.exists() and installed.read_text() == manifest:
            return None
        runner.mkdir(parents=True, exist_ok=True)
        installed.write_text(manifest)
        result = await run_command(["npm", "install", "--no-audit", "--no-fund"], runner)
        if not result.ok:
            installed.unlink(missing_ok=True)  # retry on the next run instead of trusting a half install
            return "installing the gate's test runner failed:\n" + "\n".join(tail(result.output, 8))
    return None


def _vitest_bin(workspace: Path) -> str:
    """The repo's own vitest when it has one (the version its tests are written for)."""
    own = workspace / "node_modules" / ".bin" / "vitest"
    return str(own if own.exists() else _runner_dir() / "node_modules" / ".bin" / "vitest")


async def _run_tests(vitest: str, workspace: Path, paths: list[str]) -> CommandResult:
    repo_config = next((workspace / c for c in REPO_CONFIGS if (workspace / c).exists()), None)
    args = [vitest, "run", "--root", ".", "--reporter=verbose"]
    result = await run_command(
        [*args, "--config", str(_gate_config(workspace, vitest, repo_config)), *paths], workspace
    )
    if repo_config and not result.ok and "failed to load config from" in result.output:
        # The project's own vite config does not even bundle; run without it rather than
        # fail every attempt on something the task may not touch.
        result = await run_command([*args, "--config", str(_gate_config(workspace, vitest, None)), *paths], workspace)
    return result


def _gate_config(workspace: Path, vitest: str, repo_config: Path | None) -> Path:
    """The gate's vitest config, outside the repo, layered over the project's vite config.

    Kept in the repo, the coder repeatedly narrowed its `include` to a directory it had
    invented (vitest then ran zero files) or rewrote it; and it must never reach the PR.
    The project's config still applies (path aliases like `@/`, plugins); only its `test`
    section is replaced. Imports nothing itself, so it loads with either vitest.
    """
    key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    d = Path(tempfile.gettempdir()) / "agentflow-gate" / key
    d.mkdir(parents=True, exist_ok=True)
    runner_modules = _runner_dir() / "node_modules"
    repo_modules = workspace / "node_modules"

    def provided(pkg: str) -> Path:
        return repo_modules / pkg if (repo_modules / pkg).exists() else runner_modules / pkg

    aliases = [
        {"find": f"@testing-library/{name}", "replacement": str(runner_modules / "@testing-library" / name)}
        for name in TESTING_LIBRARY
        if not (repo_modules / "@testing-library" / name).exists()
    ]
    ours_vitest = not vitest.startswith(str(repo_modules))
    environment = "jsdom" if ours_vitest or (repo_modules / "jsdom").exists() else "node"
    jest_dom = provided("@testing-library/jest-dom") / "dist" / "vitest.mjs"
    test = {
        "include": ["tests/**/*.test.{ts,tsx}", "src/**/*.test.{ts,tsx}"],
        "environment": environment,
        "setupFiles": [str(jest_dom)] if environment == "jsdom" and jest_dom.exists() else [],
        # Inlined so their own `import "react"` resolves to the repo's React, not a copy.
        "server": {"deps": {"inline": ["@testing-library"]}},
    }
    load = ""
    if repo_config:
        load = f"""try {{
  const loaded = (await import({json.dumps(str(repo_config))})).default;
  base = (typeof loaded === "function" ? await loaded({{ command: "serve", mode: "test" }}) : loaded) ?? {{}};
}} catch (err) {{
  console.warn("agentflow gate: ignoring the project's vite config:", err?.message ?? err);
}}
"""
    allowed = json.dumps([str(workspace), str(_runner_dir())])
    cfg = d / "vitest.config.mjs"
    cfg.write_text(
        f"""let base = {{}};
{load}const toList = (a) =>
  !a ? [] : Array.isArray(a) ? a : Object.entries(a).map(([find, replacement]) => ({{ find, replacement }}));
const {{ test: _projectTest, ...rest }} = base;
export default {{
  ...rest,
  cacheDir: {json.dumps(str(d / "vite"))},
  // Vite refuses to serve files outside the project; ours live in JS_RUNNER_DIR.
  server: {{
    ...(rest.server ?? {{}}),
    fs: {{ ...(rest.server?.fs ?? {{}}), allow: [...(rest.server?.fs?.allow ?? []), ...{allowed}] }},
  }},
  esbuild: rest.esbuild ?? {{ jsx: "automatic" }},
  resolve: {{
    ...(rest.resolve ?? {{}}),
    alias: [...{json.dumps(aliases)}, ...toList(rest.resolve?.alias)],
    dedupe: [...(rest.resolve?.dedupe ?? []), "react", "react-dom"],
  }},
  test: {json.dumps(test)},
}};
"""
    )
    return cfg
