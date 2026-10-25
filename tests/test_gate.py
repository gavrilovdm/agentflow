import shutil
from pathlib import Path

import pytest

from agentflow.gate import detect_gate
from agentflow.gate.python import PythonGate, extract_pytest_failures
from agentflow.gate.typescript import TypeScriptGate, extract_vitest_failures


def test_detect_by_manifest(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    assert isinstance(detect_gate(tmp_path), TypeScriptGate)


def test_detect_empty_repo_uses_planned_extensions(tmp_path: Path):
    assert isinstance(detect_gate(tmp_path, ["src/a.ts"]), TypeScriptGate)
    assert isinstance(detect_gate(tmp_path, ["app/main.py"]), PythonGate)


def test_pytest_extractor_keeps_reason_not_just_name():
    raw = "tests/x.py F\n    def test_a():\n>       assert add(1, 2) == 4\nE       assert 3 == 4\nFAILED tests/x.py::test_a - assert 3 == 4\n"
    out = extract_pytest_failures(raw)
    assert any("assert 3 == 4" in line for line in out)
    assert any(line.startswith("FAILED") for line in out)


def test_vitest_extractor_survives_huge_line():
    raw = (
        " FAIL tests/a.test.ts > adds\nTypeError: x.addNote is not a function\n"
        + "expected "
        + "a" * 60_000
        + " to be b\n"
    )
    out = extract_vitest_failures(raw)
    assert any("addNote" in line for line in out)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
async def test_python_gate_end_to_end(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="calc"\nversion="0.1.0"\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
    )
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "__init__.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n")
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "test_add.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    gate = PythonGate()
    failed = await gate.run(tmp_path, ["tests/acceptance/test_add.py"], enable_lint=True)
    assert not failed.passed
    assert "Test failures" in failed.errors

    (tmp_path / "calc" / "__init__.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    passed = await gate.run(tmp_path, ["tests/acceptance/test_add.py"], enable_lint=True)
    assert passed.passed, passed.summary


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
async def test_python_gate_lints_only_task_files(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="calc"\nversion="0.1.0"\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
    )
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "__init__.py").write_text("")
    (tmp_path / "calc" / "legacy.py").write_text("import os\n")  # pre-existing violation, not ours
    (tmp_path / "calc" / "new.py").write_text("def f() -> int:\n    return 1\n")
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "test_new.py").write_text(
        "import sys\nfrom calc.new import f\n\n\ndef test_f():\n    assert f() == 1\n"  # unused import in generated test
    )
    gate = PythonGate()
    scoped = await gate.run(tmp_path, ["tests/acceptance/test_new.py"], True, lint_paths=["calc/new.py"])
    assert scoped.passed, scoped.summary
    (tmp_path / "calc" / "new.py").write_text("import json\n\n\ndef f() -> int:\n    return 1\n")
    dirty = await gate.run(tmp_path, ["tests/acceptance/test_new.py"], True, lint_paths=["calc/new.py"])
    assert "Lint errors" in dirty.errors


async def test_ts_gate_brings_its_own_vitest_and_leaves_the_repo_alone(tmp_path: Path, monkeypatch):
    """A Vite site without tests failed every run with "Cannot find package 'vitest'" until
    the coder added vitest to the repo's package.json, which then shipped in the PR."""
    from agentflow.config import get_settings
    from agentflow.gate import typescript as ts

    runner = tmp_path / "runner"
    monkeypatch.setenv("JS_RUNNER_DIR", str(runner))
    get_settings.cache_clear()
    calls = []

    async def fake_install(args, cwd, **kw):
        calls.append((args, cwd))
        (cwd / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
        (cwd / "node_modules" / ".bin" / "vitest").write_text("")
        return ts.CommandResult(True, 0, "")

    monkeypatch.setattr(ts, "run_command", fake_install)
    assert await ts._ensure_runner() is None
    assert calls[0][1] == runner and (runner / "package.json").read_text() == ts.RUNNER_MANIFEST.read_text()
    await ts._ensure_runner()
    assert len(calls) == 1  # installed once

    repo = tmp_path / "repo"
    repo.mkdir()
    assert ts._vitest_bin(repo) == str(runner / "node_modules" / ".bin" / "vitest")
    cfg = ts._gate_config(repo, ts._vitest_bin(repo), None)
    assert repo not in cfg.parents and "import(" not in cfg.read_text()
    assert '"environment": "jsdom"' in cfg.read_text() and "@testing-library/react" in cfg.read_text()

    (repo / "node_modules" / ".bin").mkdir(parents=True)
    (repo / "node_modules" / ".bin" / "vitest").write_text("")
    assert ts._vitest_bin(repo) == str(repo / "node_modules" / ".bin" / "vitest")
    # The repo's own vitest without jsdom: keep the project's node environment.
    assert '"environment": "node"' in ts._gate_config(repo, ts._vitest_bin(repo), None).read_text()

    owned = "tests/acceptance/a.test.ts(1,38): error TS2307: Cannot find module 'vitest' or its type declarations."
    assert ts._gate_owned_import(owned)
    assert ts._gate_owned_import(owned.replace("'vitest'", "'@testing-library/react'"))
    assert not ts._gate_owned_import("src/a.ts(1,1): error TS2307: Cannot find module 'react'.")


def test_component_tests_get_a_tsx_suffix():
    """A test rendering <Home /> was saved as .test.ts and never parsed, on every attempt."""
    gate = TypeScriptGate()
    assert gate.test_suffix_for('render(<Home />); expect(screen.getByText("x")).toBeTruthy()') == ".test.tsx"
    assert gate.test_suffix_for("vi.mock('./a', () => ({ default: () => <div data-testid=\"bg\"></div> }))") == (
        ".test.tsx"
    )
    assert gate.test_suffix_for("expect(add(1, 2) < 3).toBe(false); const x = a<b>(c);") == ".test.ts"


async def test_gate_installs_from_the_lockfile_without_rewriting_it(tmp_path: Path, monkeypatch):
    """`npm install` dropped fields from package-lock.json and that churn reached the PR."""
    from agentflow.gate import typescript as ts

    calls = []

    async def fake(args, cwd, **kw):
        calls.append(args[:2])
        return ts.CommandResult(True, 0, "")

    monkeypatch.setattr(ts, "run_command", fake)
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    await TypeScriptGate()._ensure_deps(tmp_path)
    assert calls == [["npm", "ci"]]
    calls.clear()
    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "package.json").write_text('{"name": "x"}')
    await TypeScriptGate()._ensure_deps(tmp_path)
    assert calls == [["npm", "install"]]


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
async def test_ts_gate_runs_acceptance_test_in_a_repo_without_vitest(tmp_path: Path, monkeypatch):
    from agentflow.config import get_settings

    monkeypatch.setenv("JS_RUNNER_DIR", str(tmp_path / "runner"))
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests" / "acceptance").mkdir(parents=True)
    (repo / "package.json").write_text('{"name": "site", "type": "module", "private": true}')
    (repo / "src" / "m.ts").write_text("export const add = (a: number, b: number) => a + b;\n")
    (repo / "tests" / "acceptance" / "m.test.ts").write_text(
        'import { describe, it, expect } from "vitest";\nimport { add } from "../../src/m";\n'
        'describe("m", () => { it("adds", () => { expect(add(1, 2)).toBe(3); }); });\n'
    )
    before = (repo / "package.json").read_text()
    result = await TypeScriptGate().run(repo, ["tests/acceptance/m.test.ts"], enable_lint=False)
    assert "Test failures" not in result.errors, result.summary
    assert (repo / "package.json").read_text() == before
    assert not list(repo.glob("vitest*"))


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
async def test_ts_gate_renders_components_with_the_projects_aliases(tmp_path: Path, monkeypatch):
    """A React site: the test renders <Home /> via an `@/` alias from the project's vite
    config, with Testing Library and jsdom the repo does not have."""
    from agentflow.config import get_settings
    from agentflow.gate.base import run_command

    monkeypatch.setenv("JS_RUNNER_DIR", str(tmp_path / "runner"))
    get_settings.cache_clear()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests" / "acceptance").mkdir(parents=True)
    (repo / "package.json").write_text(
        '{"name": "site", "type": "module", "private": true,'
        ' "dependencies": {"react": "19.1.0", "react-dom": "19.1.0"}, "devDependencies": {"vite": "6.3.5"}}'
    )
    assert (await run_command(["npm", "install", "--no-audit", "--no-fund"], repo)).ok
    lock = (repo / "package-lock.json").read_text()
    (repo / "vite.config.ts").write_text(
        'import { defineConfig } from "vite";\nimport path from "node:path";\n'
        'export default defineConfig(() => ({ resolve: { alias: { "@": path.resolve(__dirname, "src") } },'
        ' test: { include: ["nothing/**"] } }));\n'
    )
    (repo / "src" / "Home.tsx").write_text(
        'export function Home() { return <a href="https://linkedin.com/in/x">LinkedIn</a>; }\n'
    )
    (repo / "tests" / "acceptance" / "home.test.tsx").write_text(
        'import { describe, it, expect } from "vitest";\nimport { render, screen } from "@testing-library/react";\n'
        'import { Home } from "@/Home";\n'
        'describe("home", () => { it("links", () => { render(<Home />);\n'
        '  expect(screen.getByText("LinkedIn")).toHaveAttribute("href", "https://linkedin.com/in/x"); }); });\n'
    )
    result = await TypeScriptGate().run(repo, ["tests/acceptance/home.test.tsx"], enable_lint=False)
    assert "Test failures" not in result.errors, result.summary
    assert (repo / "package-lock.json").read_text() == lock
