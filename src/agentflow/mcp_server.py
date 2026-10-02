"""MCP server: lets any MCP client (Claude Desktop, Claude Code, IDEs) drive agentflow.

Tools: start a run, inspect it, approve/reject a checkpoint, and search an indexed
repository with the same hybrid retriever the agents use.

    uv run agentflow-mcp                      # stdio
    uv run agentflow-mcp --http               # streamable HTTP on :8001
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from mcp.server.mcpserver import MCPServer

from agentflow.config import TargetRepo, WorkflowConfig, get_settings
from agentflow.rag.embeddings import get_embeddings
from agentflow.rag.retriever import HybridRetriever, pack_context
from agentflow.rag.store import PgVectorStore
from agentflow.runner import open_persistence, snapshot, validate_decision

mcp = MCPServer(
    "agentflow",
    instructions="Start and supervise autonomous coding runs; search indexed repositories.",
)


async def _queue():
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


@mcp.tool()
async def start_run(prompt: str, repo: str | None = None, base_branch: str = "main") -> dict[str, str]:
    """Start a coding run. `repo` is owner/name (a PR is opened there); omit it for a local in-place run."""
    cfg = WorkflowConfig(target_repo=TargetRepo.parse(repo, base_branch) if repo else None)
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


@mcp.tool()
async def search_codebase(repo: str, query: str, k: int = 8) -> str:
    """Hybrid (vector + keyword) search over an indexed repository, e.g. repo="octo/shop"."""
    s = get_settings()
    retriever = HybridRetriever(PgVectorStore(s.database_url, s.embedding_dim), get_embeddings(), repo)
    return pack_context(await retriever.search(query, k=k), budget_tokens=6000) or "No results."


def main() -> None:
    if "--http" in sys.argv:
        mcp.run("streamable-http", port=8001)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
