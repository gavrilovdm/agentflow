"""Pluggable auto gate. `detect_gate` picks the strategy from the repo's manifests."""

from __future__ import annotations

from pathlib import Path

from agentflow.gate.base import ACCEPTANCE_TEST_DIR, Gate
from agentflow.gate.python import PythonGate
from agentflow.gate.typescript import TypeScriptGate

__all__ = ["ACCEPTANCE_TEST_DIR", "Gate", "detect_gate", "gate_for"]

_GATES: dict[str, type] = {"python": PythonGate, "typescript": TypeScriptGate}


def gate_for(language: str) -> Gate:
    return _GATES[language]()


def detect_gate(workspace: Path, hint_files: list[str] | None = None) -> Gate:
    """Manifests win; for an empty repo fall back to the extensions the plan targets."""
    if (workspace / "package.json").exists() or (workspace / "tsconfig.json").exists():
        return TypeScriptGate()
    if any((workspace / m).exists() for m in ("pyproject.toml", "requirements.txt", "setup.py")):
        return PythonGate()
    exts = {Path(f).suffix for f in hint_files or []}
    if exts & {".ts", ".tsx"} and not exts & {".py"}:
        return TypeScriptGate()
    return PythonGate()
