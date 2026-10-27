"""One entry point for starting, resuming and inspecting runs — shared by the CLI,
REST API, queue worker and MCP server so they cannot drift apart."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentflow.config import WorkflowConfig, get_settings
from agentflow.graph.build import SERDE, build_graph
from agentflow.graph.context import Deps
from agentflow.schemas import ApprovalDecision, FailureDecision

log = logging.getLogger("agentflow.runner")


@dataclass
class Persistence:
    graph: CompiledStateGraph


@asynccontextmanager
async def open_persistence(in_memory: bool = False) -> AsyncIterator[Persistence]:
    """Postgres checkpoints make runs resumable across worker restarts.
    `in_memory` swaps them for a process-local saver (tests, demos)."""
    s = get_settings()
    if in_memory:
        yield Persistence(build_graph(InMemorySaver(serde=SERDE)))
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(s.database_url, serde=SERDE) as saver:
        await saver.setup()
        yield Persistence(build_graph(saver))


@dataclass
class RunSnapshot:
    thread_id: str
    status: str
    interrupt: dict[str, Any] | None
    values: dict[str, Any]
    checkpoint_id: str | None = None
    updated_at: str | None = None  # time of the newest checkpoint
    next_nodes: tuple[str, ...] = ()
    node_errors: list[str] = field(default_factory=list)  # exceptions LangGraph kept for the pending nodes
    stalled: bool = False  # set by mark_stalled: unfinished, not paused, and no worker is on it

    def cause(self) -> str | None:
        """Why a run that is not running stopped, as far as the checkpoint can tell."""
        if self.values.get("error"):
            return str(self.values["error"])
        if self.node_errors:
            return "; ".join(self.node_errors)
        if self.stalled:
            return f"worker stopped without recording an error (killed?) before {', '.join(self.next_nodes) or '?'}"
        return None

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
            "updated_at": self.updated_at,
            "stalled": self.stalled,
            "cause": self.cause(),
        }

    def summary(self) -> dict[str, Any]:
        """One line's worth of a run, for listing many at once."""
        v = self.values
        target = (v.get("run_config") or {}).get("target_repo")
        tasks = v.get("tasks", [])
        spec, pr = v.get("spec"), v.get("pull_request")
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "waiting_for": self.interrupt.get("type") if self.interrupt else None,
            "repo": f"{target['owner']}/{target['repo']}" if target else None,
            "title": spec.title if spec else (v.get("user_prompt") or "")[:80],
            "tasks_done": sum(t.status == "completed" for t in tasks),
            "tasks_total": len(tasks),
            "pull_request": pr.url if pr else None,
            "updated_at": self.updated_at,
            "stalled": self.stalled,
            "cause": (self.cause() or "")[:300] or None,
        }


FINISHED = ("completed", "failed")


def _config(thread_id: str) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": get_settings().recursion_limit,
        "tags": ["agentflow"],
        "metadata": {"thread_id": thread_id},
    }


def make_deps(p: Persistence, config: WorkflowConfig) -> Deps:
    return Deps(config=config)


def _describe(error: object) -> str:
    text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return text[:300]


async def snapshot(graph: CompiledStateGraph, thread_id: str) -> RunSnapshot:
    st = await graph.aget_state(_config(thread_id))
    interrupts = [i.value for task in st.tasks for i in task.interrupts]
    values = st.values or {}
    status = "awaiting_approval" if interrupts else values.get("status", "unknown")
    checkpoint_id = (st.config or {}).get("configurable", {}).get("checkpoint_id")
    # A node that raised and was never recorded as a failure (the process died, or the
    # exception bypassed _drive) still has its exception in the checkpoint's pending writes.
    # Read back from a checkpoint it is the exception's repr string, not the exception.
    errors = [f"{t.name}: {_describe(t.error)}" for t in st.tasks if t.error]
    return RunSnapshot(
        thread_id,
        status,
        interrupts[0] if interrupts else None,
        values,
        checkpoint_id,
        st.created_at,
        tuple(st.next),
        errors,
    )


def mark_stalled(snaps: list[RunSnapshot], running: set[str] | None) -> list[RunSnapshot]:
    """Flag runs that are neither finished nor paused, yet no worker holds them.

    `running` is the set of threads with a live worker heartbeat; None means it is unknown
    (no Redis), and then nothing is flagged rather than everything.
    """
    if running is not None:
        for s in snaps:
            s.stalled = not s.interrupt and s.status not in FINISHED and s.thread_id not in running
    return snaps


async def _recent_thread_ids(graph: CompiledStateGraph, limit: int) -> list[str]:
    """Threads by their newest checkpoint, newest first. Checkpoint ids are uuid6, so they
    sort by time as strings."""
    saver = graph.checkpointer
    if isinstance(saver, InMemorySaver):
        newest = {t: max(c for ns in nss.values() for c in ns) for t, nss in saver.storage.items() if any(nss.values())}
        return sorted(newest, key=newest.__getitem__, reverse=True)[:limit]

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    assert isinstance(saver, AsyncPostgresSaver)
    # The saver has no "list threads" call; its alist() would load every checkpoint's
    # blobs of every run just to read the thread ids.
    async with saver._cursor() as cur:
        await cur.execute(
            "SELECT thread_id, max(checkpoint_id) AS newest FROM checkpoints WHERE checkpoint_ns = '' "
            "GROUP BY thread_id ORDER BY newest DESC LIMIT %s",
            (limit,),
        )
        return [row["thread_id"] for row in await cur.fetchall()]


async def list_runs(
    graph: CompiledStateGraph, limit: int = 20, include_finished: bool = False, running: set[str] | None = None
) -> list[RunSnapshot]:
    """Recent runs: waiting on a person first, then stalled, then by recency.

    Runs are started from several places (Telegram, MCP, REST, issue labels) and each
    knows only its own thread id, so without this nobody could see what is in flight.
    A queued run that has not reached its first checkpoint is not listed yet.
    """
    # Over-fetch so finished runs filtered out below don't starve the list.
    ids = await _recent_thread_ids(graph, limit if include_finished else limit * 3)
    snaps = [await snapshot(graph, t) for t in ids]
    if not include_finished:
        snaps = [s for s in snaps if s.interrupt or s.status not in FINISHED]
    mark_stalled(snaps, running)
    # stable sort: recency is kept within each group
    return sorted(snaps[:limit], key=lambda s: (s.interrupt is None, not s.stalled))


async def start_run(
    p: Persistence,
    prompt: str,
    config: WorkflowConfig,
    thread_id: str | None = None,
    issue_number: int | None = None,
    on_event: Any = None,
    on_step: Any = None,
) -> RunSnapshot:
    thread_id = thread_id or f"run-{uuid.uuid4().hex[:12]}"
    inp = {"user_prompt": prompt, "issue_number": issue_number, "run_config": config.model_dump()}
    await _drive(p, inp, config, thread_id, on_event, on_step)
    return await snapshot(p.graph, thread_id)


def validate_decision(pending: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Check a human's answer fits the question the run is paused on.

    spec/plan approvals take {"approved", "feedback"}; a failed task takes {"action", "hint"}
    with action among the options the pause offered.
    """
    if pending.get("type") == "task_failed":
        d = FailureDecision.model_validate(decision)
        if d.action not in pending.get("options", []):
            raise ValueError(f"'{d.action}' is not an option here; choose one of {pending.get('options')}")
        return d.model_dump(exclude={"auto"})
    return ApprovalDecision.model_validate(decision).model_dump()


async def resume_run(
    p: Persistence,
    thread_id: str,
    decision: dict[str, Any] | None = None,
    *,
    approved: bool | None = None,
    feedback: str | None = None,
    on_event: Any = None,
    on_step: Any = None,
) -> RunSnapshot:
    """Answer the question a paused run is waiting on and continue it.

    `approved`/`feedback` is shorthand for a spec/plan approval decision.
    """
    if decision is None:
        if approved is None:
            raise ValueError("pass a decision, or approved=…")
        decision = {"approved": approved, "feedback": feedback}
    # The run's config was fixed when it started; a resume must not change targets mid-run.
    current = await snapshot(p.graph, thread_id)
    if current.interrupt is None:
        raise ValueError(f"run {thread_id} is not waiting for a decision (status: {current.status})")
    resume = validate_decision(current.interrupt, decision)
    config = WorkflowConfig.model_validate(current.values["run_config"])
    await _drive(p, Command(resume=resume), config, thread_id, on_event, on_step)
    return await snapshot(p.graph, thread_id)


CRASH_PREFIX = "Run crashed: "
CANCEL_PREFIX = "Run cancelled: "


async def _drive(
    p: Persistence, inp: Any, config: WorkflowConfig, thread_id: str, on_event: Any, on_step: Any = None
) -> None:
    """Run until the graph finishes or pauses at an interrupt, forwarding progress events
    (`on_event`: what a node emits) and finished node names (`on_step`).

    A node that still fails after its retries and fallbacks would otherwise leave the run
    with no terminal status — pollers wait forever and nobody is told. Record it as failed
    (routed through handle_failure's edge, which ends the graph) instead of raising.
    """
    try:
        async for chunk in p.graph.astream(  # type: ignore[call-overload]
            inp, _config(thread_id), context=make_deps(p, config), stream_mode=["custom", "updates"]
        ):
            mode, data = chunk
            if mode == "custom" and on_event:
                await on_event(data)
            elif mode == "updates" and on_step:
                for node in data:
                    if not node.startswith("__"):
                        await on_step(node)
    except Exception as exc:  # noqa: BLE001
        log.exception("run %s crashed", thread_id)
        error = f"{CRASH_PREFIX}{type(exc).__name__}: {str(exc)[:1000]}"
        await p.graph.aupdate_state(_config(thread_id), {"status": "failed", "error": error}, as_node="handle_failure")
    except asyncio.CancelledError:
        # A worker restart or arq's job timeout cancels the job. CancelledError is not an
        # Exception, so the handler above missed it and the run stayed "coding" forever,
        # with nothing saying why. Record it (shielded: we are being cancelled) and let the
        # cancellation proceed.
        log.warning("run %s cancelled", thread_id)
        error = f"{CANCEL_PREFIX}worker stopped or job timed out"
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(
                p.graph.aupdate_state(
                    _config(thread_id), {"status": "failed", "error": error}, as_node="handle_failure"
                )
            )
        raise
