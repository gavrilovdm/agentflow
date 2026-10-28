"""The Делается stage over the real developer graph (git + Python gate), LLM agents faked."""

import shutil
from pathlib import Path

import pytest

from agentflow import board as b
from agentflow.agents import coder
from agentflow.board import Card, Comment, SubIssue, Thread
from agentflow.config import WorkflowConfig
from agentflow.graph.build import build_developer_graph
from agentflow.runner import open_persistence
from agentflow.schemas import CoderResult, Spec, Task
from agentflow.stages import developer, planner
from agentflow.stages.planner import Plan

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv needed for the Python gate")


def _plan():
    spec = Spec(id="s", title="Calculator", goal="add and mul", constraints=[], acceptance_criteria=["a", "b"],
                technical_notes="", out_of_scope=[])  # fmt: skip
    tasks = [
        Task(id="task-add", title="add()", description="", target_files=["calc/ops.py"],
             definition_of_done="add(a,b) returns a+b"),
        Task(id="task-mul", title="mul()", description="", target_files=["calc/mul.py"],
             definition_of_done="mul(a,b) returns a*b", depends_on=["task-add"]),
    ]  # fmt: skip
    return Plan(spec=spec, tasks=tasks)


class FakeBoard:
    """Keeps the issue thread so a report the stage posted is there on the next pass."""

    def __init__(self, plan):
        posted = Comment(author="bot", body=planner.render_plan(plan, 1, False), created_at="1")
        self.thread_ = Thread(title="Calculator", body="x", author="u", comments=[posted])
        self.subs = [
            SubIssue(issue_id=f"S-{t.id}", number=i, url=f"https://x/{i}", title=t.title,
                     body=planner.render_subtask(t))
            for i, t in enumerate(plan.tasks)
        ]  # fmt: skip
        self.calls = []

    async def thread(self, card):
        return self.thread_

    async def set_field(self, card, field, value):
        self.calls.append(("set_field", field, value))

    async def move(self, card, to, report):
        self.calls.append(("move", to, report))
        body = b.render_report(report, card.status, to, card.attempts)
        self.thread_.comments.append(Comment(author="bot", body=body, created_at="x"))

    async def sub_issues(self, card):
        return self.subs

    async def close_issue(self, issue_id):
        self.calls.append(("close", issue_id))
        self.subs = [s.model_copy(update={"closed": True}) if s.issue_id == issue_id else s for s in self.subs]


def _card(run=None):
    return Card(item_id="IT", issue_id="I", number=7, repo="o/calc", url="https://x/7", title="Calculator",
                status=b.DOING, owner=developer.AGENT, run=run)  # fmt: skip


async def test_card_with_an_approved_plan_reaches_testing_with_sub_issues_closed(repo: Path, fakes):
    fb = FakeBoard(_plan())
    async with open_persistence(in_memory=True, builder=build_developer_graph) as p:
        to = await developer.process(fb, _card(), p, lambda c: WorkflowConfig(local_path=str(repo)))
    assert to == b.TESTING
    run = next(v for k, f, v in (c for c in fb.calls if c[0] == "set_field") if f == "Run")
    assert run.startswith("card-7-")
    assert {c[1] for c in fb.calls if c[0] == "close"} == {"S-task-add", "S-task-mul"}
    report = fb.calls[-1][2]
    assert "полный прогон зелёный" in report.summary and "agentflow/issue-7-" in report.artifacts[0]
    assert fakes["spec"] == 0  # the plan came from the card, nobody re-planned


async def test_exhausted_task_blocks_then_resumes_with_the_persons_hint(repo: Path, fakes, monkeypatch):
    seen = []

    async def coder_needing_a_hint(ctx, feedback=None):
        seen.append((ctx.task.id, ctx.human_hint))
        name, fn = ("ops", "add") if ctx.task.id == "task-add" else ("mul", "mul")
        op = {"add": "+" if ctx.human_hint else "-", "mul": "*"}[fn]
        body = f"def {fn}(a: int, b: int) -> int:\n    return a {op} b\n"
        (ctx.workspace / f"calc/{name}.py").write_text(body)
        return CoderResult(task_id=ctx.task.id, success=True, written_files={f"calc/{name}.py": body})

    monkeypatch.setattr(coder, "run_coder", coder_needing_a_hint)
    fb = FakeBoard(_plan())
    tight = WorkflowConfig(local_path=str(repo), max_gate_failures=1, on_task_failure="ask")
    async with open_persistence(in_memory=True, builder=build_developer_graph) as p:
        assert await developer.process(fb, _card(), p, lambda c: tight) == b.BLOCKED
        report = fb.calls[-1][2]
        assert "task-add" in report.summary and "перепланируй" in report.next_step
        assert not any(c[0] == "close" for c in fb.calls)

        # a person answers under the report and moves the card back
        fb.thread_.comments.append(Comment(author="u", body="use + not -", created_at="y"))
        run = next(v for k, f, v in (c for c in fb.calls if c[0] == "set_field") if f == "Run")
        assert await developer.process(fb, _card(run=run), p, lambda c: tight) == b.TESTING
    # the paused run went on with the hint; a fresh start would have had none
    assert [h for tid, h in seen if tid == "task-add"] == [None, "use + not -"]


def test_a_persons_words_pick_the_action():
    report = b.render_report(b.Report(agent="developer", summary="s"), b.DOING, b.BLOCKED, 0)
    th = Thread(title="t", body="", author="u", comments=[
        Comment(author="u", body="old comment", created_at="0"),
        Comment(author="bot", body=report, created_at="1"),
        Comment(author="u", body="Пропусти её", created_at="2"),
    ])  # fmt: skip
    assert developer.decision_from(th, ["retry", "skip"]) == {"action": "skip", "hint": "Пропусти её"}
    # replan not offered (budget spent): the words become a hint for a retry
    th.comments[-1] = Comment(author="u", body="перепланируй", created_at="2")
    assert developer.decision_from(th, ["retry", "skip"])["action"] == "retry"


async def test_card_without_a_plan_is_blocked(repo: Path):
    fb = FakeBoard(_plan())
    fb.thread_.comments.clear()
    async with open_persistence(in_memory=True, builder=build_developer_graph) as p:
        assert await developer.process(fb, _card(), p) == b.BLOCKED
    assert "нет плана" in fb.calls[-1][2].summary
