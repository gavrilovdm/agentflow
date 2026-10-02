"""Conditional edges. Pure functions of state + config, unit-tested in isolation."""

from __future__ import annotations

from langgraph.graph import END

from agentflow.config import WorkflowConfig
from agentflow.graph.state import WorkflowState, task_by_id
from agentflow.schemas import Stall, Task

STALL_REPEATS = 2  # the same failure for the third time within the window
STALL_WINDOW = 6


def next_stall(prior: Stall | None, signature: str, approved: bool) -> Stall:
    """Track recurring failures over a window, not only consecutive ones.

    A live run oscillated: the test failed (A), the coder changed an API to satisfy it, the
    reviewer rejected that change (B), the coder reverted (A again)… A, B, A, B never repeats
    back-to-back, so a consecutive-only check never sent the task to the referee and the
    budgets ran out. Counting occurrences in a window catches both shapes.
    """
    if approved:
        return Stall()
    history = [*(prior.history if prior else []), signature][-STALL_WINDOW:]
    return Stall(signature=signature, repeats=history[:-1].count(signature), history=history)


def after_spec_approval(state: WorkflowState) -> str:
    return "generate_tasks" if state.get("spec_approved") else "generate_spec"


def after_task_approval(state: WorkflowState) -> str:
    return "select_next_task" if state.get("tasks_approved") else "generate_tasks"


def after_task_selection(state: WorkflowState, config: WorkflowConfig) -> str:
    """test_first: the acceptance test is written before any code for the task."""
    if not state.get("current_task_id"):
        return "create_pr"
    return "generate_task_test" if config.test_strategy == "test_first" else "run_coder"


def after_test(state: WorkflowState, config: WorkflowConfig) -> str:
    return "run_coder" if config.test_strategy == "test_first" else "run_review"


def after_coder(state: WorkflowState, config: WorkflowConfig) -> str:
    """test_first: the test already exists, go straight to the gate.
    test_after: code first, then the test that judges it. On retries the test exists and
    stays fixed while the coder works against it."""
    task_id = state["current_task_id"]
    assert task_id
    result = state.get("task_results", {}).get(task_id)
    if result and result.success:
        return "run_review" if config.test_strategy == "test_first" else "generate_task_test"
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
    if (
        task.review_cycles >= config.max_review_cycles
        or task.gate_failures >= config.max_gate_failures
        or task.reviewer_malfunctions >= config.max_reviewer_malfunctions
    ):
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
