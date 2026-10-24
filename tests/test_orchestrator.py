import pytest
from pydantic import ValidationError

from agentflow.agents import orchestrator
from agentflow.schemas import Spec, SpecDraft, TaskDraft, TaskPlan

BASE = dict(title="t", goal="g", constraints=[], technical_notes="", out_of_scope=[])


def test_single_string_criterion_is_rejected_but_bullets_are_rescued():
    # Seen in a live run: the model returned one sentence instead of a list.
    with pytest.raises(ValidationError):
        SpecDraft(**BASE, acceptance_criteria="Database gains get and update methods")
    ok = SpecDraft(**BASE, acceptance_criteria="- returns the user\n- raises EmailTaken on duplicates")
    assert ok.acceptance_criteria == ["returns the user", "raises EmailTaken on duplicates"]


async def test_generate_spec_retries_with_schema_hint(monkeypatch):
    seen: list[int] = []

    class Flaky:
        async def ainvoke(self, messages, config=None):
            seen.append(len(messages))
            if len(seen) == 1:
                return SpecDraft(**BASE, acceptance_criteria="only one")  # raises ValidationError
            return SpecDraft(**BASE, acceptance_criteria=["a", "b"])

    monkeypatch.setattr(orchestrator, "structured", lambda *a, **k: Flaky())
    spec = await orchestrator.generate_spec("p", "")
    assert spec.acceptance_criteria == ["a", "b"]
    assert seen[1] == seen[0] + 1  # second attempt carried the hint


SPEC = dict(
    id="s",
    version=1,
    title="t",
    goal="g",
    constraints=[],
    acceptance_criteria=["a", "b"],
    technical_notes="",
    out_of_scope=[],
)
PLANNED = {
    "id": "task-a",
    "title": "A",
    "description": "d",
    "target_files": ["a.py"],
    "depends_on": [],
    "definition_of_done": "done",
    "interface": "def a() -> None",
}


def _patch_agent(monkeypatch, result, seen):
    class Agent:
        async def ainvoke(self, payload, config=None):
            if isinstance(result, Exception):
                raise result
            return result

    def fake_create_agent(model, tools, **kw):
        seen["tools"] = sorted(t.name for t in tools)
        return Agent()

    monkeypatch.setattr(orchestrator, "create_agent", fake_create_agent)
    monkeypatch.setattr(orchestrator, "chat_model", lambda *a, **k: object())


class OneShot:
    def __init__(self, seen):
        self.seen = seen

    async def ainvoke(self, messages, config=None):
        self.seen["one_shot"] = True
        return TaskPlan(tasks=[TaskDraft(**{**PLANNED, "id": "task-fallback"})])


async def test_planner_with_workspace_explores_and_returns_agent_plan(monkeypatch, tmp_path):
    seen: dict = {}
    _patch_agent(monkeypatch, {"structured_response": TaskPlan(tasks=[TaskDraft(**PLANNED)])}, seen)
    monkeypatch.setattr(orchestrator, "structured", lambda *a, **k: OneShot(seen))
    tasks = await orchestrator.generate_tasks(Spec(**SPEC), "", workspace=tmp_path)
    assert [t.id for t in tasks] == ["task-a"]
    assert seen["tools"] == ["glob_files", "grep", "read_file"] and "one_shot" not in seen


@pytest.mark.parametrize("result", [{"messages": []}, RuntimeError("tool loop blew up")])
async def test_planner_falls_back_to_one_shot_when_exploration_yields_no_plan(monkeypatch, tmp_path, result):
    # Hitting the call limit ends the agent without a structured response; that must
    # still produce a plan rather than fail the run at the planning step.
    seen: dict = {}
    _patch_agent(monkeypatch, result, seen)
    monkeypatch.setattr(orchestrator, "structured", lambda *a, **k: OneShot(seen))
    tasks = await orchestrator.generate_tasks(Spec(**SPEC), "", workspace=tmp_path)
    assert [t.id for t in tasks] == ["task-fallback"] and seen["one_shot"]


async def test_planner_without_workspace_stays_one_shot(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(orchestrator, "create_agent", lambda *a, **k: pytest.fail("no agent without a workspace"))
    monkeypatch.setattr(orchestrator, "structured", lambda *a, **k: OneShot(seen))
    tasks = await orchestrator.generate_tasks(Spec(**SPEC), "")
    assert [t.id for t in tasks] == ["task-fallback"]
