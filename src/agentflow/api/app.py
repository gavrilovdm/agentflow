"""REST API + webhooks. Event-driven: every write endpoint only enqueues a job."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from agentflow.config import TargetRepo, WorkflowConfig, get_settings
from agentflow.integrations import github, telegram
from agentflow.observability import setup_logging
from agentflow.progress import LAST_THREAD_KEY, render, render_list, running_threads
from agentflow.runner import list_runs, mark_stalled, open_persistence, snapshot, validate_decision
from agentflow.schemas import FailureAction

log = logging.getLogger("agentflow.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    s = get_settings()
    app.state.queue = await create_pool(RedisSettings.from_dsn(s.redis_url))
    async with open_persistence() as p:
        app.state.persistence = p
        poller = None
        if s.telegram_mode == "polling" and s.telegram_bot_token:
            poller = asyncio.create_task(telegram.poll(lambda u: handle_telegram_update(app, u)))
        yield
        if poller:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
    await app.state.queue.aclose()


app = FastAPI(title="agentflow", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    s = get_settings()
    if not s.api_token:
        if s.insecure_dev:
            return
        raise HTTPException(503, "API_TOKEN is not configured")
    if not hmac.compare_digest((authorization or "").encode(), f"Bearer {s.api_token}".encode()):
        raise HTTPException(401, "invalid or missing bearer token")


Auth = Depends(require_token)


class StartRun(BaseModel):
    prompt: str = Field(min_length=3)
    repo: str | None = Field(default=None, description="owner/name; omit for in-place mode on local_path")
    base_branch: str = "main"
    local_path: str | None = None
    max_review_cycles: int = 4
    max_gate_failures: int = 5
    enable_lint: bool = True


class Approval(BaseModel):
    approved: bool
    feedback: str | None = None


class Decision(BaseModel):
    """Answer to a task that ran out of budget (the run is paused at `task_failed`)."""

    action: FailureAction
    hint: str | None = None


class ResumeRejected(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


async def _enqueue_resume(app: FastAPI, thread_id: str, decision: dict[str, Any], channel: str = "api") -> None:
    """Queue a resume for the checkpoint the run is paused at, after checking the decision
    fits the question being asked. The job id is derived from that checkpoint, so a
    double-clicked button (or API + Telegram racing) resumes once."""
    snap = await snapshot(app.state.persistence.graph, thread_id)
    if snap.interrupt is None:
        raise ResumeRejected(409, f"run is not waiting for a decision (status: {snap.status})")
    try:
        decision = validate_decision(snap.interrupt, decision)
    except (ValueError, ValidationError) as exc:
        raise ResumeRejected(422, str(exc)) from exc
    log.info("resume", extra={"data": {"thread_id": thread_id, "channel": channel, "decision": decision}})
    await app.state.queue.enqueue_job(
        "resume_run_job", thread_id, decision, _job_id=f"resume:{thread_id}:{snap.checkpoint_id}"
    )


@app.post("/runs", status_code=202, dependencies=[Auth])
async def create_run(body: StartRun, request: Request) -> dict[str, str]:
    # local_path points the agent (which runs shell commands) at any directory of the API
    # host; over the network that is remote code execution, so only local dev may use it.
    if body.local_path and not get_settings().insecure_dev:
        raise HTTPException(403, "local_path is only accepted with INSECURE_DEV=true")
    cfg = WorkflowConfig(
        target_repo=TargetRepo.parse(body.repo, body.base_branch) if body.repo else None,
        local_path=body.local_path,
        max_review_cycles=body.max_review_cycles,
        max_gate_failures=body.max_gate_failures,
        enable_lint=body.enable_lint,
    )
    thread_id = f"run-{uuid.uuid4().hex[:12]}"
    await request.app.state.queue.enqueue_job("start_run_job", body.prompt, cfg.model_dump(), thread_id)
    return {"thread_id": thread_id, "status": "queued"}


@app.get("/runs", dependencies=[Auth])
async def get_runs(request: Request, limit: int = 20, include_finished: bool = False) -> list[dict[str, Any]]:
    """Recent runs: waiting for a decision first, then stalled ones with their cause."""
    running = await running_threads(request.app.state.queue)
    snaps = await list_runs(request.app.state.persistence.graph, limit, include_finished, running)
    return [s.summary() for s in snaps]


@app.get("/runs/{thread_id}", dependencies=[Auth])
async def get_run(thread_id: str, request: Request) -> dict[str, Any]:
    snap = await snapshot(request.app.state.persistence.graph, thread_id)
    if not snap.values:
        raise HTTPException(404, "unknown run (or not started yet)")
    mark_stalled([snap], await running_threads(request.app.state.queue))
    return snap.to_json()


@app.post("/runs/{thread_id}/approval", status_code=202, dependencies=[Auth])
async def approve(thread_id: str, body: Approval, request: Request) -> dict[str, str]:
    """Approve or reject the spec / plan a run is paused on."""
    try:
        await _enqueue_resume(request.app, thread_id, body.model_dump())
    except ResumeRejected as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"thread_id": thread_id, "status": "resuming"}


@app.post("/runs/{thread_id}/decision", status_code=202, dependencies=[Auth])
async def decide(thread_id: str, body: Decision, request: Request) -> dict[str, str]:
    """Resolve a task that ran out of budget: retry (with a hint), replan, skip or abort."""
    try:
        await _enqueue_resume(request.app, thread_id, body.model_dump())
    except ResumeRejected as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"thread_id": thread_id, "status": "resuming"}


@app.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    s = get_settings()
    raw = await request.body()
    if not github.verify_signature(s.github_webhook_secret, raw, x_hub_signature_256, insecure_dev=s.insecure_dev):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    # Trigger: an issue gets the configured label → the issue becomes the prompt.
    if x_github_event != "issues" or payload.get("action") != "labeled":
        return {"status": "ignored"}
    if payload.get("label", {}).get("name") != s.github_trigger_label:
        return {"status": "ignored"}
    issue, repo = payload["issue"], payload["repository"]
    if s.github_board_token and s.board_org and s.board_project and issue.get("node_id"):
        # With a task board the label files the issue as a card; stage agents take it from
        # Backlog like any other. Without one it still starts a run, as it always did.
        await request.app.state.queue.enqueue_job("add_to_board_job", issue["node_id"])
        return {"status": "queued", "board": "Backlog"}
    cfg = WorkflowConfig(
        target_repo=TargetRepo.parse(repo["full_name"], repo.get("default_branch", "main")),
    )
    thread_id = f"gh-{repo['name']}-{issue['number']}-{uuid.uuid4().hex[:6]}"
    prompt = f"{issue['title']}\n\n{issue.get('body') or ''}"
    await request.app.state.queue.enqueue_job(
        "start_run_job", prompt, cfg.model_dump(), thread_id, issue["number"], "github", _job_id=thread_id
    )
    return {"status": "queued", "thread_id": thread_id}


TELEGRAM_ACTIONS: dict[str, dict[str, Any]] = {
    "approve": {"approved": True},
    "reject": {"approved": False},
    **{a: {"action": a} for a in ("retry", "replan", "skip", "abort")},
}
TELEGRAM_REPLIES = {
    "approve": "✅ Approved",
    "reject": "❌ Rejected",
    "retry": "🔁 Retrying",
    "replan": "🗺 Re-planning",
    "skip": "⏭ Skipped",
    "abort": "🛑 Stopped",
}
TEXT_COMMANDS = {
    "/reject": lambda g: {"approved": False, "feedback": g},
    "/hint": lambda g: {"action": "retry", "hint": g},
    "/replan": lambda g: {"action": "replan", "hint": g},
}


def _from_allowed_chat(update: dict[str, Any], chat_id: str, insecure_dev: bool) -> bool:
    if not chat_id:
        return insecure_dev
    message = (update.get("callback_query") or {}).get("message") or update.get("message") or {}
    return str((message.get("chat") or {}).get("id", "")) == chat_id


@app.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    s = get_settings()
    if s.telegram_webhook_secret:
        if not hmac.compare_digest(
            (x_telegram_bot_api_secret_token or "").encode(), s.telegram_webhook_secret.encode()
        ):
            raise HTTPException(401, "bad secret")
    elif not s.insecure_dev:
        raise HTTPException(503, "TELEGRAM_WEBHOOK_SECRET is not configured")
    return {"status": await handle_telegram_update(request.app, await request.json())}


async def handle_telegram_update(app: FastAPI, update: dict[str, Any]) -> str:
    """One update from the bot, whether pushed to the webhook or pulled by polling."""
    s = get_settings()
    # Anyone can message the bot; only the configured chat may steer runs (a /hint is
    # injected into the coder's prompt).
    if not _from_allowed_chat(update, s.telegram_chat_id, s.insecure_dev):
        return "ignored"

    # Inline buttons: "<action>|<thread>" — approve/reject for spec & plan,
    # retry/replan/skip/abort for a task that ran out of budget.
    if cb := update.get("callback_query"):
        action, _, thread_id = (cb.get("data") or "").partition("|")
        if thread_id and action in TELEGRAM_ACTIONS:
            note = None
            try:
                await _enqueue_resume(app, thread_id, TELEGRAM_ACTIONS[action], "telegram")
                reply = TELEGRAM_REPLIES[action]
                who = (cb.get("from") or {}).get("first_name")
                note = f"{reply}{f' by {who}' if who else ''} — queued; progress follows below"
            except ResumeRejected as exc:
                reply = "Nothing to answer" if exc.status == 409 else exc.detail[:150]
                if exc.status == 409:  # already answered (or the run moved on): stale buttons
                    note = "☑️ Already answered"
            await telegram.answer_callback(cb["id"], reply)
            if note:
                await telegram.mark_answered(cb.get("message") or {}, note)
        return "ok"

    # Text commands carrying guidance:
    #   /reject <thread> <what to change>      (spec / plan)
    #   /hint   <thread> <what to do instead>  (retry a failed task)
    #   /replan <thread> <how to restructure>  (re-plan around a failed task)
    text = (update.get("message") or {}).get("text", "")
    command, *rest = text.split(maxsplit=2) if text.startswith("/") else [""]
    command = command.split("@", 1)[0]  # "/status@agentflow_bot" in group chats
    if command == "/status":
        await telegram.send_message(await _status_text(app, rest[0] if rest else None))
        return "ok"
    if command == "/runs":
        include_finished = bool(rest) and rest[0] == "all"
        running = await running_threads(app.state.queue)
        snaps = await list_runs(app.state.persistence.graph, 15, include_finished, running)
        await telegram.send_message(render_list(snaps))
        return "ok"
    if command in TEXT_COMMANDS and rest:
        thread_id, guidance = rest[0], rest[1] if len(rest) > 1 else None
        with contextlib.suppress(ResumeRejected):
            await _enqueue_resume(app, thread_id, TEXT_COMMANDS[command](guidance), "telegram")
    return "ok"


async def _status_text(app: FastAPI, thread_id: str | None) -> str:
    if not thread_id:
        with contextlib.suppress(Exception):
            last = await app.state.queue.get(LAST_THREAD_KEY)
            thread_id = last.decode() if isinstance(last, bytes) else last
    if not thread_id:
        return "No run yet. Use `/status <thread>`."
    snap = await snapshot(app.state.persistence.graph, thread_id)
    if not snap.values:
        return f"No run `{thread_id}`."
    mark_stalled([snap], await running_threads(app.state.queue))
    return render(snap)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
