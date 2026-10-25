"""MCP server: lets any MCP client (Claude Desktop, Claude Code, IDEs) drive agentflow.

Tools: pick a repo from the whitelist (list/inspect/find), start a run, inspect it,
approve/reject a checkpoint, and decide on a task that ran out of budget.

    uv run agentflow-mcp                      # stdio
    uv run agentflow-mcp --http               # streamable HTTP on 127.0.0.1:8001, needs MCP_TOKEN
"""

from __future__ import annotations

import asyncio
import hmac
import sys
import uuid
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from mcp.server.mcpserver import MCPServer

from agentflow import repos
from agentflow.config import TargetRepo, WorkflowConfig, get_settings
from agentflow.runner import open_persistence, snapshot, validate_decision

mcp = MCPServer(
    "agentflow",
    instructions="Start and supervise autonomous coding runs.",
)


async def _queue():
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


@mcp.tool()
async def list_repos() -> list[dict[str, Any]]:
    """Repos runs may target, with a short description, responsible owner and tags. Only these are accepted
    by start_run. Use inspect_repo / find_repos to check stack, CI, activity, license and permissions."""
    return [e.model_dump() for e in repos.load_whitelist()]


@mcp.tool()
async def inspect_repo(repo: str, refresh: bool = False) -> dict[str, Any]:
    """Facts about one whitelisted repo (owner/name): README excerpt, topics, languages, dependencies from
    manifests, top-level layout, CI workflows and latest default-branch result, last commit, open PRs,
    license, CONTRIBUTING, push permission. `errors` lists facts that could not be fetched. Cached 10 min."""
    entry = repos.check_allowed(repo)
    return (await repos.inspect(entry, refresh=refresh)).model_dump(mode="json")


@mcp.tool()
async def find_repos(
    keywords: list[str] | None = None,
    language: str | None = None,
    framework: str | None = None,
    license: str | None = None,
    ci_passing: bool | None = None,
    has_ci: bool | None = None,
    active_within_days: int | None = None,
    has_paths: list[str] | None = None,
    can_push: bool | None = None,
    has_contributing: bool | None = None,
) -> list[dict[str, Any]]:
    """Check the whitelist against hard criteria; every given criterion must hold. Returns every repo with
    passed/failed/unknown criteria and its description + README excerpt, matches first. `unknown` means the
    fact could not be fetched, not that it fails. Keywords are a literal substring hint only: judge what a
    project is about from the description and README yourself."""
    c = repos.Criteria(
        keywords=keywords or [],
        language=language,
        framework=framework,
        license=license,
        ci_passing=ci_passing,
        has_ci=has_ci,
        active_within_days=active_within_days,
        has_paths=has_paths or [],
        can_push=can_push,
        has_contributing=has_contributing,
    )
    facts = await asyncio.gather(*(repos.inspect(e) for e in repos.load_whitelist()))
    out = [
        repos.evaluate(f, c).model_dump()
        | {"description": f.description or f.github_description, "readme_excerpt": f.readme_excerpt[:800]}
        for f in facts
    ]
    return sorted(out, key=lambda m: (not m["matched"], len(m["failed"])))


@mcp.tool()
async def start_run(prompt: str, repo: str | None = None, base_branch: str | None = None) -> dict[str, str]:
    """Start a coding run. `repo` is owner/name and must be in list_repos (a PR is opened there); omit it
    for a local in-place run. `base_branch` defaults to the whitelist entry's branch, else "main"."""
    target = None
    if repo:
        try:
            entry = repos.check_allowed(repo)
        except repos.RepoNotAllowed as exc:
            return {"status": "rejected", "error": str(exc)}
        target = TargetRepo.parse(entry.full_name, base_branch or entry.base_branch or "main")
    cfg = WorkflowConfig(target_repo=target)
    thread_id = f"mcp-{uuid.uuid4().hex[:12]}"
    q = await _queue()
    await q.enqueue_job("start_run_job", prompt, cfg.model_dump(), thread_id, None, "mcp")
    await q.aclose()
    return {"thread_id": thread_id, "status": "queued"}


@mcp.tool()
async def get_run(thread_id: str) -> dict[str, Any]:
    """Current status of a run: spec, tasks, pending approval, PR link."""
    async with open_persistence() as p:
        return (await snapshot(p.graph, thread_id)).to_json()


@mcp.tool()
async def review_checkpoint(thread_id: str, approved: bool, feedback: str | None = None) -> dict[str, str]:
    """Approve or reject the spec/plan a run is waiting on. Rejections should carry feedback."""
    q = await _queue()
    await q.enqueue_job("resume_run_job", thread_id, {"approved": approved, "feedback": feedback})
    await q.aclose()
    return {"thread_id": thread_id, "status": "resuming"}


@mcp.tool()
async def resolve_failed_task(thread_id: str, action: str, hint: str | None = None) -> dict[str, str]:
    """A run is paused because a task ran out of budget (get_run shows the dossier).
    action: "retry" (fresh budget; put what to do differently in `hint`), "replan" (rewrite the
    remaining plan; `hint` says how), "skip" (drop it and its dependents) or "abort" (stop the run)."""
    async with open_persistence() as p:
        snap = await snapshot(p.graph, thread_id)
    if snap.interrupt is None or snap.interrupt.get("type") != "task_failed":
        return {"thread_id": thread_id, "status": "not waiting on a failed task"}
    decision = validate_decision(snap.interrupt, {"action": action, "hint": hint})
    q = await _queue()
    await q.enqueue_job("resume_run_job", thread_id, decision, _job_id=f"resume:{thread_id}:{snap.checkpoint_id}")
    await q.aclose()
    return {"thread_id": thread_id, "status": "resuming"}


class BearerAuth:
    """ASGI guard for MCP over HTTP. These tools start runs that push to GitHub with our
    token, so the HTTP transport refuses anyone without MCP_TOKEN; stdio needs no guard
    because only the process that spawned the server can talk to it."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(auth, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b"invalid or missing bearer token"})
                return
        await self.app(scope, receive, send)


def http_app() -> Any:
    s = get_settings()
    app = mcp.streamable_http_app()
    if s.mcp_token:
        return BearerAuth(app, s.mcp_token)
    if s.insecure_dev:
        return app
    raise SystemExit("MCP_TOKEN is not set: refusing to serve MCP over HTTP without auth (or set INSECURE_DEV=true)")


def main() -> None:
    if "--http" in sys.argv:
        import uvicorn

        uvicorn.run(http_app(), host="127.0.0.1", port=8001)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
