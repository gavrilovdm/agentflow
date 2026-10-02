"""Graph state. Per-task maps merge on update so a node returns only the keys it changed."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from agentflow.schemas import CoderResult, PullRequest, ReviewCycle, ReviewResult, Spec, Stall, Task

Status = Literal[
    "speccing", "spec_review", "planning", "task_review", "coding", "pr", "notifying", "completed", "failed"
]


def merge(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


def append_per_key(left: dict | None, right: dict | None) -> dict:
    """task_id → list, concatenated. A None value clears that task's list."""
    out = dict(left or {})
    for key, items in (right or {}).items():
        out[key] = [] if items is None else [*out.get(key, []), *items]
    return out


class WorkflowState(TypedDict, total=False):
    # input
    user_prompt: str
    issue_number: int | None  # set when the run was triggered from a GitHub issue
    run_config: dict[str, Any]  # WorkflowConfig at start; resumes rebuild Deps from it

    # workspace
    workspace: str
    branch: str
    repo_id: str
    index_stats: dict[str, Any]

    # spec / plan lifecycle (human-in-the-loop)
    spec: Spec | None
    spec_approved: bool
    spec_feedback: str | None
    tasks: list[Task]
    tasks_approved: bool
    tasks_feedback: str | None

    # execution
    current_task_id: str | None
    tests: Annotated[dict[str, str], merge]  # task_id → repo-relative acceptance test path
    task_results: Annotated[dict[str, CoderResult], merge]
    review_results: Annotated[dict[str, ReviewResult], merge]
    review_history: Annotated[dict[str, list[ReviewCycle]], append_per_key]
    stalls: Annotated[dict[str, Stall], merge]
    adjudicated: Annotated[dict[str, bool], merge]  # at most one referee ruling per task
    failed_tasks: Annotated[dict[str, str], merge]  # task_id → reason (for PR body + notification)

    # output
    pull_request: PullRequest | None
    status: Status
    error: str | None


def task_by_id(state: WorkflowState, task_id: str) -> Task:
    return next(t for t in state.get("tasks", []) if t.id == task_id)


def replace_task(tasks: list[Task], task_id: str, **changes: Any) -> list[Task]:
    return [t.model_copy(update=changes) if t.id == task_id else t for t in tasks]
