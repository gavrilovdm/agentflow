"""Conditional edges. Pure functions of state + config, unit-tested in isolation."""

from __future__ import annotations

from langgraph.graph import END

from agentflow.config import WorkflowConfig
from agentflow.graph.state import WorkflowState, task_by_id
from agentflow.schemas import Task

STALL_REPEATS = 2  # three identical failures in a row


def after_spec_approval(state: WorkflowState) -> str:
    return "generate_tasks" if state.get("spec_approved") else "generate_spec"


def after_task_approval(state: WorkflowState) -> str:
    return "select_next_task" if state.get("tasks_approved") else "generate_tasks"


def after_task_selection(state: WorkflowState) -> str:
    return "run_coder" if state.get("current_task_id") else "create_pr"


def after_coder(state: WorkflowState, config: WorkflowConfig) -> str:
    """Code first, then the test that judges it (a no-op on retries, where the test
    already exists and must stay fixed while the coder works against it)."""
    task_id = state["current_task_id"]
    assert task_id
    result = state.get("task_results", {}).get(task_id)
    if result and result.success:
        return "generate_task_test"
    if task_by_id(state, task_id).coder_fix_attempts >= config.max_coder_fix_attempts:
        return "handle_failure"
    return "run_coder"


def after_review(state: WorkflowState, config: WorkflowConfig) -> str:
    task_id = state["current_task_id"]
    assert task_id
    task = task_by_id(state, task_id)
    review = state.get("review_results", {}).get(task_id)
    if review and review.approved:
        return "complete_task"
    if task.review_cycles >= config.max_review_cycles or task.gate_failures >= config.max_gate_failures:
        return "handle_failure"
    # Repeating the same failure: remaining attempts would go the same way. Give the
    # referee one chance to spot an unsatisfiable test before writing the task off.
    stall = state.get("stalls", {}).get(task_id)
    if stall and stall.repeats >= STALL_REPEATS:
        return "handle_failure" if state.get("adjudicated", {}).get(task_id) else "adjudicate"
    return "run_coder"


def after_adjudication(state: WorkflowState) -> str:
    """The referee clears the stall only when it rewrote a faulty test."""
    stall = state.get("stalls", {}).get(state["current_task_id"] or "")
    return "handle_failure" if stall and stall.repeats >= STALL_REPEATS else "run_coder"


def after_failure(state: WorkflowState) -> str:
    return END if state.get("status") == "failed" else "select_next_task"


def next_runnable(tasks: list[Task]) -> Task | None:
    done = {t.id for t in tasks if t.status == "completed"}
    return next((t for t in tasks if t.status == "pending" and all(d in done for d in t.depends_on)), None)


def dependents_of(task_id: str, tasks: list[Task]) -> list[str]:
    """Every open task that needs `task_id`, directly or transitively."""
    blocked: set[str] = set()
    grew = True
    while grew:
        grew = False
        for t in tasks:
            if t.id in blocked or t.id == task_id or t.status in ("completed", "failed"):
                continue
            if any(d == task_id or d in blocked for d in t.depends_on):
                blocked.add(t.id)
                grew = True
    return sorted(blocked)
