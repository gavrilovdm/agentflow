from langgraph.store.memory import InMemoryStore

from agentflow.memory.lessons import recall_lessons, remember_review_feedback
from agentflow.schemas import ReviewCycle, Task


def _task(title: str) -> Task:
    return Task(id="task-x", title=title, description="d", target_files=[], definition_of_done="done")


async def test_recall_returns_most_recent_lessons_without_an_embedding_index():
    # The Store no longer has a vector index; recall must still work and prefer new lessons.
    store = InMemoryStore()
    for n in range(4):
        cycle = ReviewCycle(verdict="changes_requested", comments="", change_requests=[f"lesson {n}"])
        await remember_review_feedback(store, "octo/shop", _task(f"t{n}"), [cycle])
    got = await recall_lessons(store, "octo/shop", limit=2)
    assert got == ["lesson 3 (from task: t3)", "lesson 2 (from task: t2)"]
    assert await recall_lessons(store, "other/repo") == []
    assert await recall_lessons(None, "octo/shop") == []
