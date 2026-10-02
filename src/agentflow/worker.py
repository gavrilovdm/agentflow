"""arq worker: runs graphs off the request path.

API requests, GitHub webhooks and Telegram button presses only enqueue jobs; the
worker owns the long-running graph execution. When a run pauses at an interrupt,
the worker asks for approval on Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from arq.connections import RedisSettings
from prometheus_client import start_http_server

from agentflow.config import WorkflowConfig, get_settings
from agentflow.integrations import telegram
from agentflow.observability import JOB_SECONDS, RUNS_FINISHED, RUNS_STARTED, setup_logging
from agentflow.runner import RunSnapshot, open_persistence, resume_run, start_run

log = logging.getLogger("agentflow.worker")


async def _after(snap: RunSnapshot) -> dict[str, Any]:
    RUNS_FINISHED.labels(snap.status).inc()
    if snap.interrupt:
        kind = snap.interrupt.get("type", "approval")
        if kind == "spec_approval":
            spec = snap.interrupt["spec"]
            summary = f"*{spec['title']}*\n{spec['goal']}\n\n" + "\n".join(
                f"• {c}" for c in spec["acceptance_criteria"]
            )
        else:
            summary = "\n".join(f"• `{t['id']}` {t['title']}" for t in snap.interrupt.get("tasks", []))
        await telegram.request_approval(snap.thread_id, kind, summary)
    return snap.to_json()


async def start_run_job(
    ctx: dict, prompt: str, config: dict, thread_id: str, issue_number: int | None = None, trigger: str = "api"
) -> dict:
    RUNS_STARTED.labels(trigger).inc()
    with JOB_SECONDS.labels("start").time():
        snap = await start_run(
            ctx["persistence"],
            prompt,
            WorkflowConfig.model_validate(config),
            thread_id=thread_id,
            issue_number=issue_number,
        )
    return await _after(snap)


async def resume_run_job(ctx: dict, thread_id: str, approved: bool, feedback: str | None = None) -> dict:
    with JOB_SECONDS.labels("resume").time():
        snap = await resume_run(ctx["persistence"], thread_id, approved, feedback)
    return await _after(snap)


async def startup(ctx: dict) -> None:
    setup_logging()
    start_http_server(9100)  # Prometheus scrape target for the worker
    ctx["_persistence_cm"] = open_persistence()
    ctx["persistence"] = await ctx["_persistence_cm"].__aenter__()


async def shutdown(ctx: dict) -> None:
    await ctx["_persistence_cm"].__aexit__(None, None, None)


class WorkerSettings:
    functions = [start_run_job, resume_run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 4 * 3600  # a full run can legitimately take hours
    max_jobs = 4
    keep_result = 24 * 3600
