"""Whole graph, real git + real Python gate + real RAG, with the LLM agents faked.

Covers: spec rejection → revision, plan approval, a gate failure fed back to the coder,
regression tests across tasks, per-task commits, and lessons written to the Store.
"""

import shutil
from pathlib import Path

import pytest
from git import Repo

from agentflow.agents import coder, orchestrator, reviewer, test_generator
from agentflow.config import WorkflowConfig
from agentflow.runner import open_persistence, resume_run, start_run
from agentflow.schemas import CoderResult, ReviewCycle, Spec, Task

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv needed for the Python gate")


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

    async def fake_spec(prompt, context, previous=None, feedback=None):
        calls["spec"] += 1
        return Spec(
            id="spec-1",
            version=(previous.version + 1) if previous else 1,
            title="Calculator",
            goal=prompt,
            constraints=[],
            acceptance_criteria=["add works"],
            technical_notes="",
            out_of_scope=[],
        )

    async def fake_tasks(spec, context, previous=None, feedback=None):
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

    async def fake_coder(ctx, retriever, feedback=None):
        calls["coder"].append((ctx.task.id, feedback))
        if ctx.task.id == "task-add":
            # First attempt is buggy; the gate failure must reach the retry as feedback.
            op = "-" if feedback is None else "+"
            body = f"def add(a: int, b: int) -> int:\n    return a {op} b\n"
            (ctx.workspace / "calc/ops.py").write_text(body)
            return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/ops.py": body})
        body = "def mul(a: int, b: int) -> int:\n    return a * b\n"
        (ctx.workspace / "calc/mul.py").write_text(body)
        return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/mul.py": body})

    async def fake_test(task, spec, gate, workspace, written, previous_test=None, ruling=None):
        name = task.id.replace("-", "_")
        fn, expr = ("add", "add(2, 3) == 5") if task.id == "task-add" else ("mul", "mul(2, 3) == 6")
        mod = "ops" if fn == "add" else "mul"
        rel = f"tests/acceptance/test_{name}.py"
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(f"from calc.{mod} import {fn}\n\n\ndef test_it():\n    assert {expr}\n")
        return rel

    async def fake_review(task, spec, test_content, diff, retriever):
        calls["review"] += 1
        assert diff, "reviewer must see a non-empty diff"
        return ReviewCycle(verdict="approved", comments="LGTM", diff=diff)

    monkeypatch.setattr(orchestrator, "generate_spec", fake_spec)
    monkeypatch.setattr(orchestrator, "generate_tasks", fake_tasks)
    monkeypatch.setattr(coder, "run_coder", fake_coder)
    monkeypatch.setattr(test_generator, "generate_test", fake_test)
    monkeypatch.setattr(reviewer, "review_diff", fake_review)
    return calls


async def test_full_run_in_place(repo: Path, fakes):
    cfg = WorkflowConfig(local_path=str(repo), enable_lint=True)
    events: list[dict] = []

    async def on_event(e):
        events.append(e)

    async with open_persistence(in_memory=True) as p:
        snap = await start_run(p, "build a calculator", cfg, thread_id="t1", on_event=on_event)
        assert snap.status == "awaiting_approval"
        assert snap.interrupt["type"] == "spec_approval"

        snap = await resume_run(p, "t1", approved=False, feedback="name it Calculator")
        assert snap.interrupt["type"] == "spec_approval"
        assert snap.values["spec"].version == 2

        snap = await resume_run(p, "t1", approved=True)
        assert snap.interrupt["type"] == "tasks_approval"

        snap = await resume_run(p, "t1", approved=True, on_event=on_event)
        assert snap.status == "completed", snap.to_json()

    assert [t.status for t in snap.values["tasks"]] == ["completed", "completed"], snap.values["failed_tasks"]
    # task-add: buggy attempt, then a retry that received the gate output
    add_calls = [f for tid, f in fakes["coder"] if tid == "task-add"]
    assert add_calls[0] is None and "Test failures" in add_calls[1]
    log = [c.message for c in Repo(repo).iter_commits()]
    assert log[:2] == ["feat(task-mul): mul()", "feat(task-add): add()"]
    assert "tests/acceptance/test_task_add.py" in Repo(repo).git.ls_files()
    assert any("indexed" in e.get("message", "") for e in events)


async def test_gate_budget_exhaustion_skips_dependents(repo: Path, fakes, monkeypatch):
    async def always_buggy(ctx, retriever, feedback=None):
        body = "def add(a: int, b: int) -> int:\n    return 0\n"
        (ctx.workspace / "calc/ops.py").write_text(body)
        return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/ops.py": body})

    async def no_referee(*a, **k):
        from agentflow.schemas import FailureRuling

        return FailureRuling(culprit="code", reasoning="implementation returns 0")

    monkeypatch.setattr(coder, "run_coder", always_buggy)
    monkeypatch.setattr(reviewer, "adjudicate", no_referee)
    cfg = WorkflowConfig(local_path=str(repo), max_gate_failures=5)
    async with open_persistence(in_memory=True) as p:
        await start_run(p, "calc", cfg, thread_id="t2")
        await resume_run(p, "t2", approved=True)
        snap = await resume_run(p, "t2", approved=True)

    # Same failure three times → referee rules "code" → task fails before the gate budget is spent
    assert snap.status == "failed"
    assert set(snap.values["failed_tasks"]) == {"task-add", "task-mul"}
    assert "Skipped" in snap.values["failed_tasks"]["task-mul"]
    assert snap.values["adjudicated"] == {"task-add": True}


async def test_review_feedback_becomes_lesson(repo: Path, fakes, monkeypatch):
    seen = {"n": 0}

    async def picky_review(task, spec, test_content, diff, retriever):
        seen["n"] += 1
        if task.id == "task-add" and seen["n"] == 1:
            return ReviewCycle(
                verdict="changes_requested",
                comments="needs docstring",
                change_requests=["calc/ops.py: public functions need a docstring"],
                diff=diff,
            )
        return ReviewCycle(verdict="approved", comments="ok", diff=diff)

    monkeypatch.setattr(reviewer, "review_diff", picky_review)
    async with open_persistence(in_memory=True) as p:
        await start_run(p, "calc", WorkflowConfig(local_path=str(repo)), thread_id="t3")
        await resume_run(p, "t3", approved=True)
        snap = await resume_run(p, "t3", approved=True)
        assert snap.status == "completed"
        task_add = next(t for t in snap.values["tasks"] if t.id == "task-add")
        assert task_add.review_cycles == 1
        items = await p.graph.store.asearch(("lessons", f"local:{repo.resolve()}".replace(".", "_")), query="docstring")
        assert any("docstring" in i.value["text"] for i in items)


async def test_node_crash_marks_run_failed(repo: Path, fakes, monkeypatch):
    async def broken_spec(*a, **k):
        raise ValueError("provider exploded")  # ValueError is not retried by the node RetryPolicy

    monkeypatch.setattr(orchestrator, "generate_spec", broken_spec)
    async with open_persistence(in_memory=True) as p:
        snap = await start_run(p, "calc", WorkflowConfig(local_path=str(repo)), thread_id="t4")
    assert snap.status == "failed"
    assert snap.values["error"].startswith("Run crashed: ValueError: provider exploded")
