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
