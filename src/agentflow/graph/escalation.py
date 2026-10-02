"""Escalation: what a human sees, and what each decision does, when a task runs out of budget.

Silently skipping a failed task (and everything depending on it) hid the most important
moment of a run: the point where the agent had reached the limit of what it could do on
its own. Production coding harnesses treat budget exhaustion as stop-and-escalate, handing
a person the evidence. The dossier is that evidence.
"""

from __future__ import annotations

from typing import Any

from agentflow.graph.state import WorkflowState, task_by_id

OPTIONS = {
    "retry": "Retry the task with your hint (budgets reset)",
    "replan": "Re-plan the remaining work around this failure",
    "skip": "Skip this task and the tasks that depend on it",
    "abort": "Stop the run",
}


def build_dossier(state: WorkflowState, task_id: str) -> dict[str, Any]:
    task = task_by_id(state, task_id)
    stall = state.get("stalls", {}).get(task_id)
    review = state.get("review_results", {}).get(task_id)
    coder = state.get("task_results", {}).get(task_id)
    history = state.get("review_history", {}).get(task_id, [])
    change_requests = [r for c in history[-3:] for r in c.change_requests][-5:]
    failures = list(dict.fromkeys(stall.history)) if stall and stall.history else []
    if coder and not coder.success and coder.error:
        failures.append(f"coder error: {coder.error}")
    if not failures and review and review.cycles:
        failures.append(review.cycles[-1].comments)
    return {
        "task": {
            "id": task.id,
            "title": task.title,
            "definition_of_done": task.definition_of_done,
            "interface": task.interface,
        },
        "attempts": {
            "coder_crashes": task.coder_fix_attempts,
            "gate_failures": task.gate_failures,
            "review_rejections": task.review_cycles,
            "reviewer_malfunctions": task.reviewer_malfunctions,
            "referee_consulted": bool(state.get("adjudicated", {}).get(task_id)),
        },
        "recent_failures": [f[:600] for f in failures[-4:]],
        "open_change_requests": [r[:300] for r in change_requests],
        "files_written": sorted(coder.written_files) if coder else [],
        "acceptance_test": state.get("tests", {}).get(task_id),
        "completed_tasks": [t.id for t in state.get("tasks", []) if t.status == "completed"],
        "dependents": [t.id for t in state.get("tasks", []) if task_id in t.depends_on],
        "branch": state.get("branch"),
        "workspace": state.get("workspace"),
    }


def options_for(state: WorkflowState, max_replans: int) -> list[str]:
    opts = list(OPTIONS)
    if state.get("replans", 0) >= max_replans:
        opts.remove("replan")
    return opts


def dossier_text(d: dict[str, Any]) -> str:
    """Compact human-readable form (Telegram, CLI)."""
    a = d["attempts"]
    lines = [
        f"Task `{d['task']['id']}` — {d['task']['title']}",
        f"Attempts: {a['gate_failures']} gate failure(s), {a['review_rejections']} review rejection(s), "
        f"{a['coder_crashes']} coder crash(es), {a['reviewer_malfunctions']} reviewer malfunction(s)"
        + (", referee consulted" if a["referee_consulted"] else ""),
        "",
        "Recent failures:",
        *[f"• {f.splitlines()[0][:200]}" for f in d["recent_failures"]],
    ]
    if d["open_change_requests"]:
        lines += ["", "Reviewer asked for:", *[f"• {r[:200]}" for r in d["open_change_requests"][:3]]]
    if d["dependents"]:
        lines += ["", f"Blocked if skipped: {', '.join(d['dependents'])}"]
    lines += ["", f"Partial work: {', '.join(d['files_written']) or 'none'} on branch {d['branch']}"]
    return "\n".join(lines)
