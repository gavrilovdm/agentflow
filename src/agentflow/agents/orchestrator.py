"""Orchestrator: turns a request into a spec, then a dependency-ordered task plan.

Both calls are structured outputs (Pydantic schemas → forced tool call) with a
model fallback chain. Planning is grounded in retrieved repository context, which
replaces the old "LLM reads project-map.md and guesses which files matter" step.
"""

from __future__ import annotations

import json
import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from agentflow.config import get_settings
from agentflow.models import structured
from agentflow.schemas import Spec, SpecDraft, Task, TaskPlan

log = logging.getLogger(__name__)

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
- interface is mandatory and binding: the exact module path, function/class signatures with types,
  return values and exceptions this task exposes. Tests are written against it BEFORE the code,
  so reuse names from the spec and the repository and never leave a name for the coder to choose
- Do not create separate "write tests" tasks: every task automatically gets its own acceptance test,
  written before its code. Tests belong to the task whose behaviour they check
- ID format: task-<slug>, e.g. task-add-auth-middleware"""


SPEC_ATTEMPTS = 2
LIST_FIELDS_HINT = HumanMessage(
    "Your previous answer did not match the schema. constraints, acceptance_criteria and out_of_scope must be "
    "JSON arrays with one item per entry, and acceptance_criteria needs at least two separate testable statements."
)


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
    runnable = structured(s.orchestrator_model, SpecDraft)
    draft: SpecDraft | None = None
    for attempt in range(SPEC_ATTEMPTS):
        try:
            draft = await runnable.ainvoke(
                messages if attempt == 0 else [*messages, LIST_FIELDS_HINT],
                config={"tags": ["orchestrator", "spec"]},
            )
            break
        except Exception as exc:  # noqa: BLE001 — schema violations surface as several exception types
            if attempt == SPEC_ATTEMPTS - 1:
                raise
            log.warning("spec rejected by schema (attempt %s): %s", attempt + 1, str(exc)[:200])
    assert draft is not None
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
    plan: TaskPlan = await structured(s.orchestrator_model, TaskPlan).ainvoke(
        messages, config={"tags": ["orchestrator", "tasks"]}
    )
    return validate_plan([Task(**t.model_dump()) for t in plan.tasks])


REPLAN_SYSTEM = """You are a senior software architect revising a plan after a task failed.

You get the spec, the tasks already completed (their code is committed and must not be redone),
the task that failed with the evidence of why, any human guidance, and the tasks still pending.
Return ONLY the tasks still to do — a replacement for the failed and pending tasks.

Rules:
- Address the cause of the failure: split the failed task, change its approach or interface, add a
  missing prerequisite, or drop it if the spec can be met without it. Follow the human guidance.
- Every task follows the same rules as the original plan: small, independently testable, binding
  interface, specific definition_of_done, no separate "write tests" tasks.
- depends_on may reference completed task ids and ids in your new list only.
- Use fresh ids (task-<slug>) — never reuse the id of the failed task."""


async def replan_tasks(
    spec: Spec,
    completed: list[Task],
    failed: Task,
    remaining: list[Task],
    dossier: dict,
    hint: str | None,
    repo_context: str,
) -> list[Task]:
    messages = [
        SystemMessage(REPLAN_SYSTEM),
        HumanMessage(
            f"## Specification\n```json\n{spec.model_dump_json(indent=2)}\n```\n\n"
            f"## Completed tasks\n```json\n{json.dumps([t.model_dump() for t in completed], indent=2)}\n```\n\n"
            f"## Failed task\n```json\n{failed.model_dump_json(indent=2)}\n```\n\n"
            f"## Why it failed\n```json\n{json.dumps(dossier, indent=2, default=str)}\n```\n\n"
            f"## Still pending\n```json\n{json.dumps([t.model_dump() for t in remaining], indent=2)}\n```\n\n"
            + (f"## Human guidance\n{hint}\n\n" if hint else "")
            + (f"## Repository context\n{repo_context}" if repo_context else "")
        ),
    ]
    plan: TaskPlan = await structured(get_settings().orchestrator_model, TaskPlan).ainvoke(
        messages, config={"tags": ["orchestrator", "replan"]}
    )
    return [Task(**t.model_dump()) for t in plan.tasks]


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
