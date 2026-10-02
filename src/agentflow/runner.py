"""One entry point for starting, resuming and inspecting runs — shared by the CLI,
REST API, queue worker and MCP server so they cannot drift apart."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agentflow.config import WorkflowConfig, get_settings
from agentflow.graph.build import SERDE, build_graph
from agentflow.graph.context import Deps
from agentflow.rag.embeddings import get_embeddings
from agentflow.rag.store import ChunkStore, PgVectorStore
from agentflow.rag.store import InMemoryStore as InMemoryChunkStore


@dataclass
class Persistence:
    graph: CompiledStateGraph
    chunk_store: ChunkStore


@asynccontextmanager
async def open_persistence(in_memory: bool = False) -> AsyncIterator[Persistence]:
    """Postgres for checkpoints, long-term memory and vectors — one database, three
    roles. `in_memory` swaps all three for process-local versions (tests, demos)."""
    s = get_settings()
    embeddings = get_embeddings()
    index = {"dims": s.embedding_dim, "embed": embeddings, "fields": ["text"]}
    if in_memory:
        yield Persistence(
            build_graph(InMemorySaver(serde=SERDE), InMemoryStore(index=index)),  # type: ignore[arg-type]
            InMemoryChunkStore(),
        )
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    async with (
        AsyncPostgresSaver.from_conn_string(s.database_url, serde=SERDE) as saver,
        AsyncPostgresStore.from_conn_string(s.database_url, index=index) as store,  # type: ignore[arg-type]
    ):
        await saver.setup()
        await store.setup()
        chunks = PgVectorStore(s.database_url, dim=s.embedding_dim)
        await chunks.setup()
        yield Persistence(build_graph(saver, store), chunks)


@dataclass
class RunSnapshot:
    thread_id: str
    status: str
    interrupt: dict[str, Any] | None
    values: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        v = self.values
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "pending_approval": self.interrupt,
            "spec": v["spec"].model_dump() if v.get("spec") else None,
            "tasks": [
                {"id": t.id, "title": t.title, "status": t.status, "depends_on": t.depends_on}
                for t in v.get("tasks", [])
            ],
            "failed_tasks": v.get("failed_tasks", {}),
            "pull_request": v["pull_request"].model_dump() if v.get("pull_request") else None,
            "error": v.get("error"),
        }


def _config(thread_id: str) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": get_settings().recursion_limit,
        "tags": ["agentflow"],
        "metadata": {"thread_id": thread_id},
    }


def make_deps(p: Persistence, config: WorkflowConfig) -> Deps:
    return Deps(chunk_store=p.chunk_store, embeddings=get_embeddings(), config=config)


async def snapshot(graph: CompiledStateGraph, thread_id: str) -> RunSnapshot:
    st = await graph.aget_state(_config(thread_id))
    interrupts = [i.value for task in st.tasks for i in task.interrupts]
    values = st.values or {}
    status = "awaiting_approval" if interrupts else values.get("status", "unknown")
    return RunSnapshot(thread_id, status, interrupts[0] if interrupts else None, values)


async def start_run(
    p: Persistence,
    prompt: str,
    config: WorkflowConfig,
    thread_id: str | None = None,
    issue_number: int | None = None,
    on_event: Any = None,
) -> RunSnapshot:
    thread_id = thread_id or f"run-{uuid.uuid4().hex[:12]}"
    inp = {"user_prompt": prompt, "issue_number": issue_number, "run_config": config.model_dump()}
    await _drive(p, inp, config, thread_id, on_event)
    return await snapshot(p.graph, thread_id)


async def resume_run(
    p: Persistence,
    thread_id: str,
    approved: bool,
    feedback: str | None = None,
    on_event: Any = None,
) -> RunSnapshot:
    # The run's config was fixed when it started; a resume must not change targets mid-run.
    current = await snapshot(p.graph, thread_id)
    if current.interrupt is None:
        raise ValueError(f"run {thread_id} is not waiting for approval (status: {current.status})")
    config = WorkflowConfig.model_validate(current.values["run_config"])
    await _drive(p, Command(resume={"approved": approved, "feedback": feedback}), config, thread_id, on_event)
    return await snapshot(p.graph, thread_id)


async def _drive(p: Persistence, inp: Any, config: WorkflowConfig, thread_id: str, on_event: Any) -> None:
    """Run until the graph finishes or pauses at an interrupt, forwarding progress events."""
    async for chunk in p.graph.astream(  # type: ignore[call-overload]
        inp, _config(thread_id), context=make_deps(p, config), stream_mode="custom"
    ):
        if on_event:
            await on_event(chunk)
