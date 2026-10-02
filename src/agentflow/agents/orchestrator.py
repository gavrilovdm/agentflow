"""Orchestrator: turns a request into a spec, then a dependency-ordered task plan.

Both calls are structured outputs (Pydantic schemas → forced tool call) with a
model fallback chain. Planning is grounded in retrieved repository context, which
replaces the old "LLM reads project-map.md and guesses which files matter" step.
"""

from __future__ import annotations

import json
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from agentflow.config import get_settings
from agentflow.models import structured
from agentflow.schemas import Spec, SpecDraft, Task, TaskPlan

SPEC_SYSTEM = """You are a senior software architect. Create a precise, unambiguous development
specification from the user's request.

Rules:
- Acceptance criteria must be testable ("responds in <200ms", not "should be fast")
- Constraints name the actual tech stack, matching what the repository already uses
- technical_notes reference existing modules, functions and patterns from the repository context
- out_of_scope lists anything a developer might reasonably assume is included but isn't
- A developer must be able to start coding with no follow-up questions."""

TASKS_SYSTEM = """You are a senior software architect breaking an approved spec into an ordered task list.

Rules:
- Each task is small enough for one focused coding session and independently testable
- depends_on lists only IDs from THIS list that must finish first
- target_files lists the exact files to create or modify, consistent with the repository layout
- definition_of_done is specific: "function X returns Y given Z", not "feature works"
- Foundational types/interfaces come before their consumers
- ID format: task-<slug>, e.g. task-add-auth-middleware"""


def _revision_message(label: str, previous: object, feedback: str) -> HumanMessage:
    return HumanMessage(
        f"## Previous {label} (rejected by reviewer)\n\n```json\n{json.dumps(previous, indent=2)}\n```\n\n"
        f"## Reviewer feedback\n\n{feedback}\n\n"
        f"Revise the {label} to address this feedback. Keep everything the feedback doesn't ask to change."
    )


async def generate_spec(
    prompt: str, repo_context: str, previous: Spec | None = None, feedback: str | None = None
) -> Spec:
    messages = [
        SystemMessage(SPEC_SYSTEM),
        HumanMessage(
            (f"## Relevant repository context\n\n{repo_context}" if repo_context else "## Repository\n\nEmpty.")
            + f"\n\n## User request\n\n{prompt}"
        ),
    ]
    if previous and feedback:
        messages.append(_revision_message("spec", previous.model_dump(), feedback))
    s = get_settings()
    draft: SpecDraft = await structured(s.orchestrator_model, SpecDraft, tool_name="create_spec").ainvoke(
        messages, config={"tags": ["orchestrator", "spec"]}
    )
    return Spec(
        id=previous.id if previous else f"spec-{uuid.uuid4().hex[:8]}",
        version=(previous.version + 1) if previous else 1,
        **draft.model_dump(),
    )


async def generate_tasks(
    spec: Spec, repo_context: str, previous: list[Task] | None = None, feedback: str | None = None
) -> list[Task]:
    messages = [
        SystemMessage(TASKS_SYSTEM),
        HumanMessage(
            f"## Approved specification\n\n```json\n{spec.model_dump_json(indent=2)}\n```\n\n"
            + (f"## Repository context\n\n{repo_context}" if repo_context else "")
        ),
    ]
    if previous and feedback:
        messages.append(_revision_message("task list", [t.model_dump() for t in previous], feedback))
    s = get_settings()
    plan: TaskPlan = await structured(s.orchestrator_model, TaskPlan, tool_name="create_task_list").ainvoke(
        messages, config={"tags": ["orchestrator", "tasks"]}
    )
    return validate_plan([Task(**t.model_dump()) for t in plan.tasks])


def validate_plan(tasks: list[Task]) -> list[Task]:
    """Drop dangling dependencies and break cycles, so the scheduler can never deadlock
    on a plan the model got slightly wrong. Cycles are broken by dropping the in-cycle
    edges of the earliest stuck task in plan order; downstream edges are kept."""
    ids = {t.id for t in tasks}
    for t in tasks:
        t.depends_on = list(dict.fromkeys(d for d in t.depends_on if d in ids and d != t.id))
    done: set[str] = set()
    while len(done) < len(tasks):
        ready = [t for t in tasks if t.id not in done and all(d in done for d in t.depends_on)]
        if ready:
            done.update(t.id for t in ready)
            continue
        stuck = next(t for t in tasks if t.id not in done)
        stuck.depends_on = [d for d in stuck.depends_on if d in done]
    return tasks
