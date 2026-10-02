"""REST API + webhooks. Event-driven: every write endpoint only enqueues a job."""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, ValidationError

from agentflow.config import TargetRepo, WorkflowConfig, get_settings
from agentflow.integrations import github, telegram
from agentflow.observability import APPROVALS, HTTP_REQUESTS, setup_logging
from agentflow.rag.embeddings import get_embeddings
from agentflow.rag.retriever import rrf_merge
from agentflow.rag.store import Hit
from agentflow.runner import open_persistence, snapshot, validate_decision
from agentflow.schemas import FailureAction

log = logging.getLogger("agentflow.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    s = get_settings()
    app.state.queue = await create_pool(RedisSettings.from_dsn(s.redis_url))
    async with open_persistence() as p:
        app.state.persistence = p
        yield
    await app.state.queue.aclose()


app = FastAPI(title="agentflow", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    HTTP_REQUESTS.labels(getattr(route, "path", "unmatched"), response.status_code).inc()
    return response


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().api_token
    if token and authorization != f"Bearer {token}":
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


async def _enqueue_resume(request: Request, thread_id: str, decision: dict[str, Any], channel: str = "api") -> None:
    """Queue a resume for the checkpoint the run is paused at, after checking the decision
    fits the question being asked. The job id is derived from that checkpoint, so a
    double-clicked button (or API + Telegram racing) resumes once."""
    snap = await snapshot(request.app.state.persistence.graph, thread_id)
    if snap.interrupt is None:
        raise ResumeRejected(409, f"run is not waiting for a decision (status: {snap.status})")
    try:
        decision = validate_decision(snap.interrupt, decision)
    except (ValueError, ValidationError) as exc:
        raise ResumeRejected(422, str(exc)) from exc
    label = decision.get("action") or ("approve" if decision.get("approved") else "reject")
    APPROVALS.labels(label, channel).inc()
    await request.app.state.queue.enqueue_job(
        "resume_run_job", thread_id, decision, _job_id=f"resume:{thread_id}:{snap.checkpoint_id}"
    )


@app.post("/runs", status_code=202, dependencies=[Auth])
async def create_run(body: StartRun, request: Request) -> dict[str, str]:
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


@app.get("/runs/{thread_id}", dependencies=[Auth])
async def get_run(thread_id: str, request: Request) -> dict[str, Any]:
    snap = await snapshot(request.app.state.persistence.graph, thread_id)
    if not snap.values:
        raise HTTPException(404, "unknown run (or not started yet)")
    return snap.to_json()


@app.post("/runs/{thread_id}/approval", status_code=202, dependencies=[Auth])
async def approve(thread_id: str, body: Approval, request: Request) -> dict[str, str]:
    """Approve or reject the spec / plan a run is paused on."""
    try:
        await _enqueue_resume(request, thread_id, body.model_dump())
    except ResumeRejected as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    return {"thread_id": thread_id, "status": "resuming"}


@app.post("/runs/{thread_id}/decision", status_code=202, dependencies=[Auth])
async def decide(thread_id: str, body: Decision, request: Request) -> dict[str, str]:
    """Resolve a task that ran out of budget: retry (with a hint), replan, skip or abort."""
    try:
        await _enqueue_resume(request, thread_id, body.model_dump())
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
    if not github.verify_signature(s.github_webhook_secret, raw, x_hub_signature_256):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    # Trigger: an issue gets the configured label → the issue becomes the prompt.
    if x_github_event != "issues" or payload.get("action") != "labeled":
        return {"status": "ignored"}
    if payload.get("label", {}).get("name") != s.github_trigger_label:
        return {"status": "ignored"}
    issue, repo = payload["issue"], payload["repository"]
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
    "approve": "Approved ✅",
    "reject": "Rejected ❌",
    "retry": "Retrying 🔁",
    "replan": "Re-planning 🗺",
    "skip": "Skipped ⏭",
    "abort": "Stopped 🛑",
}
TEXT_COMMANDS = {
    "/reject": lambda g: {"approved": False, "feedback": g},
    "/hint": lambda g: {"action": "retry", "hint": g},
    "/replan": lambda g: {"action": "replan", "hint": g},
}


@app.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    s = get_settings()
    if s.telegram_webhook_secret and x_telegram_bot_api_secret_token != s.telegram_webhook_secret:
        raise HTTPException(401, "bad secret")
    update = await request.json()

    # Inline buttons: "<action>|<thread>" — approve/reject for spec & plan,
    # retry/replan/skip/abort for a task that ran out of budget.
    if cb := update.get("callback_query"):
        action, _, thread_id = (cb.get("data") or "").partition("|")
        if thread_id and action in TELEGRAM_ACTIONS:
            try:
                await _enqueue_resume(request, thread_id, TELEGRAM_ACTIONS[action], "telegram")
                reply = TELEGRAM_REPLIES[action]
            except ResumeRejected as exc:
                reply = "Nothing to answer" if exc.status == 409 else exc.detail[:150]
            await telegram.answer_callback(cb["id"], reply)
        return {"status": "ok"}

    # Text commands carrying guidance:
    #   /reject <thread> <what to change>      (spec / plan)
    #   /hint   <thread> <what to do instead>  (retry a failed task)
    #   /replan <thread> <how to restructure>  (re-plan around a failed task)
    text = (update.get("message") or {}).get("text", "")
    command, *rest = text.split(maxsplit=2) if text.startswith("/") else [""]
    if command in TEXT_COMMANDS and rest:
        thread_id, guidance = rest[0], rest[1] if len(rest) > 1 else None
        with contextlib.suppress(ResumeRejected):
            await _enqueue_resume(request, thread_id, TEXT_COMMANDS[command](guidance), "telegram")
    return {"status": "ok"}


def _hit_json(h: Hit) -> dict[str, Any]:
    return {
        "path": h.path,
        "start_line": h.start_line,
        "end_line": h.end_line,
        "kind": h.kind,
        "score": round(h.score, 5),
        "snippet": h.content[:600],
    }


@app.get("/repos", dependencies=[Auth])
async def list_repos(request: Request) -> dict[str, list[str]]:
    """Repositories that have a vector index (one per target repo the workflow has touched)."""
    return {"repos": await request.app.state.persistence.chunk_store.repos()}


@app.get("/search", dependencies=[Auth])
async def search(
    request: Request,
    repo: str,
    q: Annotated[str, Query(min_length=2)],
    k: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """The retrieval the agents use, with its two halves exposed: dense (embeddings),
    lexical (full-text) and their Reciprocal Rank Fusion — handy for seeing why a chunk won."""
    store = request.app.state.persistence.chunk_store
    embeddings = get_embeddings()
    dense = await store.search_vector(repo, await embeddings.aembed_query(q), 20, None)
    lexical = await store.search_text(repo, q, 20, None)
    dense_view, lexical_view = [_hit_json(h) for h in dense[:k]], [_hit_json(h) for h in lexical[:k]]
    hybrid = rrf_merge([dense, lexical], k)  # mutates scores, so render the halves first
    return {
        "repo": repo,
        "query": q,
        "embeddings": type(embeddings).__name__,
        "dense": dense_view,
        "lexical": lexical_view,
        "hybrid": [_hit_json(h) for h in hybrid],
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
