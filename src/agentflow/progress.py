"""A live status card per run in Telegram: one message, edited as the graph moves.

Between "Approve" and the next question the chat used to stay silent for an hour of
coding, so there was no way to tell whether the run had picked the work up or died.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agentflow.integrations import telegram

log = logging.getLogger(__name__)

# Set by the worker whenever a job starts, so `/status` without a thread has an answer.
LAST_THREAD_KEY = "agentflow:last_thread"

NODE_LABELS = {
    "prepare_workspace": "Preparing the workspace",
    "generate_spec": "Writing the spec",
    "await_spec_approval": "Spec approved",
    "generate_tasks": "Planning tasks",
    "await_task_approval": "Plan approved",
    "select_next_task": "Picking the next task",
    "generate_task_test": "Writing the acceptance test",
    "run_coder": "Coding",
    "run_review": "Reviewing",
    "adjudicate": "Referee ruling on the review",
    "complete_task": "Task done",
    "escalate": "Task out of budget",
    "replan": "Re-planning",
    "handle_failure": "Handling a failure",
    "create_pr": "Opening the PR",
    "notify": "Sending the summary",
    "finalize": "Cleaning up",
}
WAITING = {
    "spec_approval": "your spec approval",
    "tasks_approval": "your plan approval",
    "task_failed": "your decision",
}
PHASES = ("spec", "plan", "code", "PR")
PHASE_OF = {
    "speccing": 0, "spec_review": 0, "planning": 1, "task_review": 1,
    "coding": 2, "pr": 3, "notifying": 3, "completed": 4,
}  # fmt: skip
TASK_MARK = {"completed": "✅", "coding": "▶️", "failed": "❌", "pending": "▫️"}


def render(snap: Any, node: str | None = None) -> str:
    """Status text from a RunSnapshot; `node` is the step that just finished."""
    v = snap.values
    status = v.get("status", "")
    spec = v.get("spec")
    tasks = v.get("tasks") or []
    pr = v.get("pull_request")

    if snap.interrupt:
        head = f"⏸ *Waiting for {WAITING.get(snap.interrupt.get('type'), 'you')}*"
    elif snap.status == "completed":
        head = "✅ *Done*" + (f" — PR #{pr.number}" if pr else "")
    elif snap.status == "failed":
        head = "❌ *Failed*"
    else:
        head = f"⏳ *{NODE_LABELS.get(node or '', 'Working')}…*"

    lines = [head]
    if spec:
        lines.append(f"*Feature:* {spec.title}")

    current = PHASE_OF.get(status, 2 if v.get("tasks_approved") else 1 if v.get("spec_approved") else 0)
    marks = []
    for i, name in enumerate(PHASES):
        if i < current:
            mark = "✅"
        elif i == current:
            mark = "❌" if snap.status == "failed" else "⏸" if snap.interrupt else "⏳"
        else:
            mark = "⬜"
        marks.append(f"{mark} {name}")
    lines.append(" · ".join(marks))

    if tasks and current >= 2:
        done = sum(t.status == "completed" for t in tasks)
        lines.append(f"\n*Tasks* {done}/{len(tasks)}")
        lines += [f"{TASK_MARK.get(t.status, '▫️')} {t.title}" for t in tasks[:12]]
        if len(tasks) > 12:
            lines.append(f"… and {len(tasks) - 12} more")
    if pr:
        lines.append(f"\n{pr.url}")
    if snap.status == "failed" and v.get("error"):
        lines.append("\n" + telegram.verbatim(str(v["error"]), 500))
    lines.append(f"\nthread: `{snap.thread_id}`")
    return "\n".join(lines)


def render_list(snaps: list[Any]) -> str:
    """`/runs`: one line per run, the ones waiting on you first (list_runs' order)."""
    if not snaps:
        return "No active runs."
    lines = []
    for s in snaps:
        m = s.summary()
        if s.interrupt:
            head = f"⏸ {WAITING.get(m['waiting_for'], 'you')}"
        else:
            head = {"completed": "✅ done", "failed": "❌ failed"}.get(s.status, f"⏳ {s.status}")
        tasks = f" · {m['tasks_done']}/{m['tasks_total']}" if m["tasks_total"] else ""
        pr = f" · {m['pull_request']}" if m["pull_request"] else ""
        lines.append(f"{head} · *{m['repo'] or 'local'}* · {m['title']}{tasks}{pr}\n`{s.thread_id}`")
    return "\n\n".join(lines)


class StatusCard:
    """One silent message per worker job, edited in place.

    Edits are spaced out: Telegram throttles a chat that is edited every second, and a
    reviewer loop can finish several nodes that fast. The final state is always written.
    Errors are swallowed: a status line must never fail the run it describes.
    """

    MIN_EDIT_INTERVAL_S = 4.0

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.message_id: int | None = None
        self._text = ""
        self._last = 0.0

    def due(self) -> bool:
        return self.message_id is None or time.monotonic() - self._last >= self.MIN_EDIT_INTERVAL_S

    async def show(self, text: str, *, force: bool = False) -> None:
        if text == self._text or not (force or self.due()):
            return
        try:
            if self.message_id is None:
                self.message_id = await telegram.send_message(text, silent=True)
            else:
                await telegram.edit_message(self.message_id, text)
            self._text, self._last = text, time.monotonic()
        except Exception:  # noqa: BLE001
            log.warning("status card update failed for %s", self.thread_id, exc_info=True)
