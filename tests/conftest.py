from pathlib import Path

import pytest
from git import Repo

from agentflow.agents import coder, orchestrator, reviewer, test_generator
from agentflow.config import get_settings
from agentflow.schemas import CoderResult, ReviewCycle, Spec, Task


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Tests never talk to real providers, Telegram or GitHub."""
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "VOYAGE_API_KEY",
        "API_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "TELEGRAM_WEBHOOK_SECRET",
    ):
        monkeypatch.setenv(var, "")
    # Most tests exercise the flow, not auth; test_api.py turns this off to check fail-closed.
    monkeypatch.setenv("INSECURE_DEV", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# A tiny Python repo and faked LLM agents: the whole graph runs with real git and a real gate.
@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="calc"\nversion="0.1.0"\n'
        '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
    )
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "__init__.py").write_text("")
    (tmp_path / ".gitignore").write_text(".venv/\n__pycache__/\n")
    r = Repo.init(tmp_path)
    r.index.add(["pyproject.toml", "calc/__init__.py", ".gitignore"])
    r.index.commit("init")
    return tmp_path


@pytest.fixture
def fakes(monkeypatch):
    calls = {"spec": 0, "coder": [], "review": 0}

    async def fake_spec(prompt, previous=None, feedback=None, *, workspace=None):
        calls["spec"] += 1
        return Spec(
            id="spec-1",
            version=(previous.version + 1) if previous else 1,
            title="Calculator",
            goal=prompt,
            constraints=[],
            acceptance_criteria=["add works", "mul works"],
            technical_notes="",
            out_of_scope=[],
        )

    async def fake_tasks(spec, previous=None, feedback=None, *, workspace=None):
        assert workspace is not None, "the planner must get the workspace to explore"
        return [
            Task(
                id="task-add",
                title="add()",
                description="",
                target_files=["calc/ops.py"],
                definition_of_done="add(a,b) returns a+b",
            ),
            Task(
                id="task-mul",
                title="mul()",
                description="",
                target_files=["calc/mul.py"],
                definition_of_done="mul(a,b) returns a*b",
                depends_on=["task-add"],
            ),
        ]

    async def fake_coder(ctx, feedback=None):
        calls["coder"].append((ctx.task.id, feedback))
        calls.setdefault("saw_test", []).append(bool(ctx.test_content))
        if ctx.task.id == "task-add":
            # First attempt is buggy; the gate failure must reach the retry as feedback.
            op = "-" if feedback is None else "+"
            body = f"def add(a: int, b: int) -> int:\n    return a {op} b\n"
            (ctx.workspace / "calc/ops.py").write_text(body)
            return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/ops.py": body})
        body = "def mul(a: int, b: int) -> int:\n    return a * b\n"
        (ctx.workspace / "calc/mul.py").write_text(body)
        return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/mul.py": body})

    async def fake_test(
        task, spec, gate, workspace, *, existing_files=None, previous_test=None, ruling=None, attempted_code=None
    ):
        calls.setdefault("test_context", []).append(existing_files)
        name = task.id.replace("-", "_")
        fn, expr = ("add", "add(2, 3) == 5") if task.id == "task-add" else ("mul", "mul(2, 3) == 6")
        mod = "ops" if fn == "add" else "mul"
        rel = f"tests/acceptance/test_{name}.py"
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(f"from calc.{mod} import {fn}\n\n\ndef test_it():\n    assert {expr}\n")
        return rel

    async def fake_review(task, spec, test_content, diff, workspace):
        calls["review"] += 1
        assert diff, "reviewer must see a non-empty diff"
        return ReviewCycle(verdict="approved", comments="LGTM", diff=diff)

    monkeypatch.setattr(orchestrator, "generate_spec", fake_spec)
    monkeypatch.setattr(orchestrator, "generate_tasks", fake_tasks)
    monkeypatch.setattr(coder, "run_coder", fake_coder)
    monkeypatch.setattr(test_generator, "generate_test", fake_test)
    monkeypatch.setattr(reviewer, "review_diff", fake_review)
    return calls
