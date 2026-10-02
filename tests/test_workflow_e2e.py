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
            acceptance_criteria=["add works", "mul works"],
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
    # Tests come first: the coder saw its acceptance test on every attempt, including the first.
    assert all(fakes["saw_test"])


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
    cfg = WorkflowConfig(local_path=str(repo), max_gate_failures=5, on_task_failure="skip")
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


async def test_vacuous_test_is_regenerated_once(repo: Path, fakes, monkeypatch):
    """Red check: a test that passes before any code exists asserts nothing; regenerate it."""
    written: list[str] = []
    real_fake = test_generator.generate_test

    async def vacuous_then_real(
        task, spec, gate, workspace, *, existing_files=None, previous_test=None, ruling=None, attempted_code=None
    ):
        written.append(task.id)
        if task.id == "task-add" and ruling is None:
            rel = "tests/acceptance/test_task_add.py"
            (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
            (workspace / rel).write_text("def test_nothing():\n    assert True\n")
            return rel
        return await real_fake(
            task, spec, gate, workspace, existing_files=existing_files, previous_test=previous_test, ruling=ruling
        )

    monkeypatch.setattr(test_generator, "generate_test", vacuous_then_real)
    async with open_persistence(in_memory=True) as p:
        await start_run(p, "calc", WorkflowConfig(local_path=str(repo)), thread_id="t5")
        await resume_run(p, "t5", approved=True)
        snap = await resume_run(p, "t5", approved=True)
    assert snap.status == "completed"
    assert written.count("task-add") == 2  # vacuous first draft, then the real one


# ─── Escalation ─────────────────────────────────────────────────────────────


@pytest.fixture
def stubborn(fakes, monkeypatch):
    """task-add fails the gate until a human hint arrives; the referee blames the code."""
    seen: dict[str, list] = {"hints": []}

    async def coder_needing_hint(ctx, retriever, feedback=None):
        seen["hints"].append(ctx.human_hint)
        if "mul" in ctx.task.id:
            body = "def mul(a: int, b: int) -> int:\n    return a * b\n"
            (ctx.workspace / "calc/mul.py").write_text(body)
            return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/mul.py": body})
        op = "+" if ctx.human_hint else "-"
        body = f"def add(a: int, b: int) -> int:\n    return a {op} b\n"
        (ctx.workspace / "calc/ops.py").write_text(body)
        return CoderResult(task_id=ctx.task.id, success=True, written_files={"calc/ops.py": body})

    async def referee_blames_code(*a, **k):
        from agentflow.schemas import FailureRuling

        return FailureRuling(culprit="code", reasoning="returns a - b")

    monkeypatch.setattr(coder, "run_coder", coder_needing_hint)
    monkeypatch.setattr(reviewer, "adjudicate", referee_blames_code)
    return seen


async def _until_task_failed(p, thread: str, repo: Path):
    await start_run(p, "calc", WorkflowConfig(local_path=str(repo)), thread_id=thread)
    await resume_run(p, thread, approved=True)
    return await resume_run(p, thread, approved=True)


async def test_exhausted_task_pauses_with_dossier_then_retry_with_hint(repo: Path, stubborn):
    async with open_persistence(in_memory=True) as p:
        snap = await _until_task_failed(p, "e1", repo)
        assert snap.status == "awaiting_approval"
        q = snap.interrupt
        assert q["type"] == "task_failed" and q["task_id"] == "task-add"
        assert set(q["options"]) == {"retry", "replan", "skip", "abort"}
        d = q["dossier"]
        assert d["attempts"]["referee_consulted"] is True
        assert d["dependents"] == ["task-mul"] and d["files_written"] == ["calc/ops.py"]
        assert any("assert" in f for f in d["recent_failures"])

        with pytest.raises(ValueError):  # an approval is not an answer to this question
            await resume_run(p, "e1", approved=True)

        snap = await resume_run(p, "e1", {"action": "retry", "hint": "use + not -"})
        assert snap.status == "completed", snap.to_json()
        assert stubborn["hints"][-2] == "use + not -"  # task-add's retry carried the hint
        task_add = next(t for t in snap.values["tasks"] if t.id == "task-add")
        assert task_add.status == "completed" and snap.values["escalations"] == {"task-add": 1}


async def test_replan_replaces_failed_and_pending_tasks(repo: Path, stubborn, monkeypatch):
    from agentflow.schemas import Task as T

    async def replanner(spec, completed, failed, remaining, dossier, hint, context):
        assert failed.id == "task-add" and [t.id for t in remaining] == ["task-mul"] and hint == "split it"
        return [
            T(
                id="task-mul",
                title="mul()",
                description="",
                target_files=["calc/mul.py"],
                definition_of_done="mul works",
                interface="calc.mul.mul(a, b) -> int",
            ),
        ]

    monkeypatch.setattr(orchestrator, "replan_tasks", replanner)
    async with open_persistence(in_memory=True) as p:
        await _until_task_failed(p, "e2", repo)
        snap = await resume_run(p, "e2", {"action": "replan", "hint": "split it"})
    assert snap.status == "completed", snap.to_json()
    ids = [t.id for t in snap.values["tasks"]]
    assert ids == ["task-mul-r"]  # reused id renamed so no stale per-task state leaks in
    assert snap.values["replans"] == 1
    assert "Replaced by re-plan" in snap.values["failed_tasks"]["task-add"]


async def test_abort_stops_the_run(repo: Path, stubborn):
    async with open_persistence(in_memory=True) as p:
        await _until_task_failed(p, "e3", repo)
        snap = await resume_run(p, "e3", {"action": "abort"})
    assert snap.status == "failed"
    assert snap.values["failed_tasks"]["task-mul"].startswith("Not started")
    assert snap.values["error"].startswith("Stopped by a human")


async def test_escalation_cap_falls_back_to_skip(repo: Path, stubborn):
    cfg = WorkflowConfig(local_path=str(repo), max_escalations_per_task=1)
    async with open_persistence(in_memory=True) as p:
        await start_run(p, "calc", cfg, thread_id="e4")
        await resume_run(p, "e4", approved=True)
        snap = await resume_run(p, "e4", approved=True)
        # A retry without a hint fails the same way; the cap means no second question.
        snap = await resume_run(p, "e4", {"action": "retry"})
    assert snap.status == "failed"
    assert snap.values["failure_decisions"]["task-add"].auto is True
