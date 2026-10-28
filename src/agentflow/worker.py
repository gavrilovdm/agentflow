"""arq worker: runs graphs off the request path.

API requests, GitHub webhooks and Telegram button presses only enqueue jobs; the
worker owns the long-running graph execution. When a run pauses at an interrupt,
the worker asks for approval on Telegram.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from agentflow import orchestrator_pm
from agentflow.board import Board, BoardError
from agentflow.config import WorkflowConfig, get_settings
from agentflow.graph.build import build_developer_graph
from agentflow.graph.escalation import dossier_text
from agentflow.integrations import telegram
from agentflow.observability import setup_logging
from agentflow.progress import HEARTBEAT_TTL_S, LAST_THREAD_KEY, RUNNING_PREFIX, StatusCard, render
from agentflow.runner import CRASH_PREFIX, RunSnapshot, open_persistence, resume_run, snapshot, start_run

log = logging.getLogger("agentflow.worker")


async def _card(ctx: dict, thread_id: str, first: str) -> tuple[StatusCard, Any]:
    """Post the run's status card and return a step callback that keeps it current."""
    card = StatusCard(thread_id)
    await card.show(first, force=True)
    if redis := ctx.get("redis"):
        # `/status` with no thread answers about the run that moved last.
        with contextlib.suppress(Exception):
            await redis.set(LAST_THREAD_KEY, thread_id)

    async def on_step(node: str) -> None:
        if not card.due():  # skip the checkpoint read when the edit would be throttled anyway
            return
        try:
            snap = await snapshot(ctx["persistence"].graph, thread_id)
        except Exception:  # noqa: BLE001 — a raise here would be recorded as the run crashing
            log.warning("status snapshot failed for %s", thread_id, exc_info=True)
            return
        await card.show(render(snap, node))

    return card, on_step


@contextlib.asynccontextmanager
async def _heartbeat(ctx: dict, thread_id: str) -> AsyncIterator[None]:
    """Keep a TTL key alive while this job drives the run. A worker killed outright (OOM,
    SIGKILL, host reboot) records nothing; the key expiring is what marks the run stalled."""
    redis = ctx.get("redis")
    if redis is None:
        yield
        return
    key = RUNNING_PREFIX + thread_id

    async def beat() -> None:
        while True:
            with contextlib.suppress(Exception):
                await redis.set(key, "1", ex=HEARTBEAT_TTL_S)
            await asyncio.sleep(HEARTBEAT_TTL_S / 3)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await redis.delete(key)


async def _after(snap: RunSnapshot, card: StatusCard | None = None) -> dict[str, Any]:
    if card:
        await card.show(render(snap), force=True)
    error = snap.values.get("error") or ""
    if error.startswith(CRASH_PREFIX):
        spec = snap.values.get("spec")
        await telegram.send_message(
            f"❌ *Run failed* `{snap.thread_id}`\n*Feature:* {spec.title if spec else '?'}\n{telegram.verbatim(error)}"
        )
    if snap.interrupt:
        kind = snap.interrupt.get("type", "approval")
        if kind == "spec_approval":
            spec = snap.interrupt["spec"]
            summary = f"*{spec['title']}*\n{spec['goal']}\n\n" + "\n".join(
                f"• {c}" for c in spec["acceptance_criteria"]
            )
        else:
            summary = "\n".join(f"• `{t['id']}` {t['title']}" for t in snap.interrupt.get("tasks", []))
        if kind == "task_failed":
            await telegram.request_failure_decision(
                snap.thread_id, dossier_text(snap.interrupt["dossier"]), snap.interrupt["options"]
            )
        else:
            await telegram.request_approval(snap.thread_id, kind, summary)
    return snap.to_json()


async def start_run_job(
    ctx: dict, prompt: str, config: dict, thread_id: str, issue_number: int | None = None, trigger: str = "api"
) -> dict:
    log.info("run started", extra={"data": {"thread_id": thread_id, "trigger": trigger}})
    card, on_step = await _card(ctx, thread_id, f"⏳ *Started* ({trigger})\n\nthread: `{thread_id}`")
    async with _heartbeat(ctx, thread_id):
        snap = await start_run(
            ctx["persistence"],
            prompt,
            WorkflowConfig.model_validate(config),
            thread_id=thread_id,
            issue_number=issue_number,
            on_step=on_step,
        )
    return await _after(snap, card)


async def resume_run_job(ctx: dict, thread_id: str, decision: dict | bool, feedback: str | None = None) -> dict:
    # Jobs enqueued before decisions became dicts carry (approved, feedback).
    if isinstance(decision, bool):
        decision = {"approved": decision, "feedback": feedback}
    card, on_step = await _card(ctx, thread_id, f"⏳ *Picked up* — continuing\n\nthread: `{thread_id}`")
    async with _heartbeat(ctx, thread_id):
        snap = await resume_run(ctx["persistence"], thread_id, decision, on_step=on_step)
    return await _after(snap, card)


async def stage_job(ctx: dict, agent: str, item_id: str) -> str:
    """One stage agent on the card the orchestrator handed it (see orchestrator_pm)."""
    return await orchestrator_pm.work(ctx["board"], ctx["redis"], agent, item_id, ctx["developer_persistence"])


async def add_to_board_job(ctx: dict, issue_id: str) -> str:
    if ctx.get("board") is None:
        return "no board"
    return await ctx["board"].add_issue(issue_id)


async def orchestrate(ctx: dict) -> int:
    if ctx.get("board") is None:
        return 0

    d = await orchestrator_pm.tick(ctx["board"], ctx["redis"], stage_enqueuer(ctx["redis"]))
    return len(d.launch)


def stage_enqueuer(redis: Any) -> Callable[[str, str], Awaitable[None]]:
    async def enqueue(agent: str, item_id: str) -> None:
        # One job id per launch, not per card: arq keeps a finished job's result for
        # keep_result and silently refuses an id it still holds, so a card back in the same
        # column within a day (answers to intake, a tester's return) was never run again.
        # Owner and the queued heartbeat already keep a tick from launching a card twice.
        job_id = f"stage-{item_id}-{agent}-{uuid.uuid4().hex[:8]}"
        if await redis.enqueue_job("stage_job", agent, item_id, _job_id=job_id) is None:
            raise RuntimeError(f"arq refused job {job_id}")

    return enqueue


async def daily_summary(ctx: dict) -> None:
    if ctx.get("board") is None:
        return
    key = orchestrator_pm.SUMMARY_KEY + datetime.now(UTC).strftime("%Y-%m-%d")
    if await ctx["redis"].set(key, "1", ex=2 * 86400, nx=True):
        await telegram.send_message(orchestrator_pm.summary(await ctx["board"].cards(include_closed=False)))


async def startup(ctx: dict) -> None:
    setup_logging()
    ctx["_persistence_cm"] = open_persistence()
    ctx["persistence"] = await ctx["_persistence_cm"].__aenter__()
    ctx["_developer_cm"] = open_persistence(builder=build_developer_graph)
    ctx["developer_persistence"] = await ctx["_developer_cm"].__aenter__()
    try:
        ctx["board"] = Board.from_settings()
    except BoardError:
        ctx["board"] = None  # no board configured: runs still work, the orchestrator sits idle
        log.info("no task board configured; the orchestrator is off")


async def shutdown(ctx: dict) -> None:
    if ctx.get("board") is not None:
        await ctx["board"].aclose()
    await ctx["_developer_cm"].__aexit__(None, None, None)
    await ctx["_persistence_cm"].__aexit__(None, None, None)


class WorkerSettings:
    functions = [start_run_job, resume_run_job, stage_job, add_to_board_job]
    cron_jobs = [
        cron(orchestrate, second=0, run_at_startup=True, unique=True),
        cron(daily_summary, hour=6, minute=0),  # 09:00 Moscow
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 4 * 3600  # a full run can legitimately take hours
    # Stage agents come on top of standalone runs: 4 in Делается, plus planner, tester, releaser.
    max_jobs = 12
    keep_result = 24 * 3600
