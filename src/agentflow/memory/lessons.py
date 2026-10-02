"""Long-term memory: review feedback that cost a task a cycle, recalled for later tasks.

Per-run state lives in the checkpointer and dies with the thread. Lessons live in the
LangGraph Store, namespaced by repository, so a mistake the reviewer caught in one
run ("use the repo's `Result` type, don't raise") is in the coder's prompt next run.
Search is semantic: the Store is created with an embedding index.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from langgraph.store.base import BaseStore

from agentflow.schemas import ReviewCycle, Task

MAX_LESSONS_PER_TASK = 3


def _ns(repo: str) -> tuple[str, ...]:
    return ("lessons", repo.replace(".", "_"))


async def remember_review_feedback(store: BaseStore, repo: str, task: Task, cycles: list[ReviewCycle]) -> int:
    """Record reviewer judgements (not gate output, not reviewer malfunctions)."""
    saved = 0
    for cycle in cycles:
        if cycle.verdict != "changes_requested" or cycle.reviewer_malfunction:
            continue
        for request in cycle.change_requests[:MAX_LESSONS_PER_TASK]:
            key = hashlib.sha1(request.encode()).hexdigest()[:16]
            await store.aput(
                _ns(repo),
                key,
                {
                    "text": request,
                    "task": task.title,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            saved += 1
    return saved


async def recall_lessons(store: BaseStore | None, repo: str, query: str, limit: int = 5) -> list[str]:
    if store is None:
        return []
    items = await store.asearch(_ns(repo), query=query, limit=limit)
    return [f"{i.value['text']} (from task: {i.value.get('task', '?')})" for i in items]
