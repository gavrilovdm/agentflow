"""Runs against a real Postgres: `docker compose up -d postgres` then
`uv run pytest -m integration`."""

import uuid

import pytest

from agentflow.memory.lessons import recall_lessons, remember_review_feedback
from agentflow.runner import open_persistence
from agentflow.schemas import ReviewCycle, Task

pytestmark = pytest.mark.integration


async def test_lessons_roundtrip_through_postgres_store():
    # The Store runs without an embedding index now; recall must still work on Postgres.
    repo = f"test/lessons-{uuid.uuid4().hex[:8]}"
    task = Task(id="task-x", title="t", description="d", target_files=[], definition_of_done="done")
    async with open_persistence() as p:
        store = p.graph.store
        assert store is not None
        for n in range(3):
            cycle = ReviewCycle(verdict="changes_requested", comments="", change_requests=[f"lesson {n}"])
            await remember_review_feedback(store, repo, task, [cycle])
        assert await recall_lessons(store, repo, limit=2) == ["lesson 2 (from task: t)", "lesson 1 (from task: t)"]
