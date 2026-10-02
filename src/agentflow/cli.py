"""Command-line interface. Runs the graph in-process with interactive approvals.

agentflow run "Add JWT login" --path ../myrepo          # in-place, local commits only
agentflow run "Add JWT login" --repo octo/shop --postgres
agentflow search ../myrepo "where are passwords hashed"
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agentflow.config import TargetRepo, WorkflowConfig
from agentflow.observability import setup_logging
from agentflow.runner import RunSnapshot, open_persistence, resume_run, snapshot, start_run

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


async def _print_event(event: dict[str, Any]) -> None:
    console.print(f"[dim]•[/dim] {event.get('message', event)}")


def _show_interrupt(snap: RunSnapshot) -> None:
    assert snap.interrupt
    if snap.interrupt["type"] == "spec_approval":
        s = snap.interrupt["spec"]
        body = (
            f"## {s['title']} (v{s['version']})\n\n**Goal:** {s['goal']}\n\n**Acceptance criteria**\n"
            + "\n".join(f"- {c}" for c in s["acceptance_criteria"])
            + f"\n\n**Technical notes:** {s['technical_notes']}\n\n**Out of scope**\n"
            + "\n".join(f"- {c}" for c in s["out_of_scope"])
        )
        console.print(Panel(Markdown(body), title="Spec"))
    else:
        table = Table(title="Task plan")
        for col in ("id", "title", "after", "definition of done"):
            table.add_column(col)
        for t in snap.interrupt["tasks"]:
            table.add_row(t["id"], t["title"], ", ".join(t["depends_on"]), t["definition_of_done"])
        console.print(table)


async def _loop(p, snap: RunSnapshot) -> RunSnapshot:
    while snap.interrupt:
        _show_interrupt(snap)
        answer = typer.prompt("Approve? [y / feedback text]", default="y")
        approved = answer.strip().lower() in ("y", "yes")
        snap = await resume_run(p, snap.thread_id, approved, None if approved else answer, on_event=_print_event)
    return snap


def _summary(snap: RunSnapshot) -> None:
    data = snap.to_json()
    color = "green" if snap.status == "completed" else "red"
    console.print(f"\n[bold {color}]{snap.status}[/] — thread {snap.thread_id}")
    for t in data["tasks"]:
        console.print(f"  {'✅' if t['status'] == 'completed' else '❌'} {t['id']}: {t['title']}")
    if data["pull_request"]:
        console.print(f"  PR: {data['pull_request']['url']}")


@app.command()
def run(
    prompt: str,
    repo: Annotated[str | None, typer.Option(help="owner/name — clone, code, open a PR")] = None,
    path: Annotated[Path, typer.Option(help="local repo for in-place mode")] = Path("."),
    base_branch: str = "main",
    postgres: Annotated[bool, typer.Option(help="persist to Postgres (resumable) instead of memory")] = False,
) -> None:
    """Run the full workflow, approving the spec and plan interactively."""
    setup_logging("WARNING", json_logs=False)
    cfg = WorkflowConfig(
        target_repo=TargetRepo.parse(repo, base_branch) if repo else None, local_path=str(path.resolve())
    )

    async def go() -> None:
        async with open_persistence(in_memory=not postgres) as p:
            snap = await start_run(p, prompt, cfg, on_event=_print_event)
            _summary(await _loop(p, snap))

    asyncio.run(go())


@app.command()
def resume(thread_id: str) -> None:
    """Continue a Postgres-backed run that is waiting for approval."""
    setup_logging("WARNING", json_logs=False)

    async def go() -> None:
        async with open_persistence() as p:
            _summary(await _loop(p, await snapshot(p.graph, thread_id)))

    asyncio.run(go())


@app.command()
def search(path: Path, query: str, k: int = 5) -> None:
    """Index a local repo in memory and run a hybrid search — a quick look at what agents retrieve."""
    from agentflow.rag.embeddings import get_embeddings
    from agentflow.rag.indexer import index_repo
    from agentflow.rag.retriever import HybridRetriever
    from agentflow.rag.store import InMemoryStore

    async def go() -> None:
        store, emb = InMemoryStore(), get_embeddings()
        stats = await index_repo(path.resolve(), "cli", store, emb)
        console.print(f"[dim]indexed {stats.files_seen} files / {stats.chunks_embedded} chunks[/dim]")
        for h in await HybridRetriever(store, emb, "cli").search(query, k=k):
            console.print(f"[bold]{h.path}[/bold]:{h.start_line}-{h.end_line}  [dim]rrf={h.score:.4f}[/dim]")

    asyncio.run(go())


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the REST API (the worker runs separately: `arq agentflow.worker.WorkerSettings`)."""
    import uvicorn

    uvicorn.run("agentflow.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
