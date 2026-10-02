"""Coder agent: a fresh tool-using agent per task attempt.

Built with `create_agent` plus middleware for the production concerns the old
hand-rolled loop handled ad hoc or not at all: a model-call ceiling (runaway tool
loops), retries with backoff on transient API errors, and provider fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
)
from langchain_core.tools import BaseTool, tool

from agentflow.config import get_settings
from agentflow.gate import ACCEPTANCE_TEST_DIR
from agentflow.integrations.workspace import PathEscapeError, resolve_in_workspace
from agentflow.models import chat_model
from agentflow.rag.retriever import HybridRetriever
from agentflow.rag.tools import make_search_tools
from agentflow.schemas import CoderResult, Spec, Task

MAX_MODEL_CALLS = 25

# Scratch scripts the coder writes to poke at the project. Everything written is
# committed, so these reached PRs and the reviewer burned cycles rejecting them.
# Prompting against it did not hold; the tool refuses them and says why.
SCRATCH_PATTERNS = [
    re.compile(r"\.(sh|bash|zsh|bat|ps1)$", re.I),
    re.compile(r"(^|/)(run|debug|check|scratch|tmp|temp)[-_][^/]*\.(js|mjs|cjs|py)$", re.I),
    re.compile(r"(^|/)(debug|scratch|tmp|temp)\.[^/]+$", re.I),
]


def is_scratch_file(rel: str) -> bool:
    return any(p.search(rel) for p in SCRATCH_PATTERNS)


@dataclass
class CoderContext:
    task: Task
    spec: Spec
    workspace: Path
    language: str
    test_content: str = ""
    retrieved_context: str = ""
    lessons: list[str] = field(default_factory=list)


def build_file_tools(workspace: Path, written: dict[str, str]) -> list[BaseTool]:
    @tool
    def write_file(file_path: str, content: str) -> str:
        """Write full content to a file (relative to the repo root). Creates parent directories."""
        try:
            abs_path, rel = resolve_in_workspace(workspace, file_path)
        except PathEscapeError as exc:
            return str(exc)
        # The acceptance test grades this task; letting the coder rewrite it would make
        # the gate measure nothing. Unit tests elsewhere are fine.
        if rel.startswith(ACCEPTANCE_TEST_DIR):
            return (
                f'Refusing to write "{rel}": {ACCEPTANCE_TEST_DIR} holds the acceptance tests this task is '
                "graded against. Put unit tests elsewhere under tests/."
            )
        if is_scratch_file(rel):
            return (
                f'Refusing to write "{rel}": helper/debug scripts get committed with everything else. '
                "Write only the source and test files the task calls for."
            )
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)
        written[rel] = content
        return f"Written: {rel}"

    @tool
    def read_file(file_path: str) -> str:
        """Read an existing file (relative to the repo root) before modifying it."""
        try:
            abs_path, _ = resolve_in_workspace(workspace, file_path)
            return abs_path.read_text()
        except (PathEscapeError, OSError):
            return f"File not found: {file_path}"

    @tool
    def list_dir(directory: str = ".") -> str:
        """List files in a directory (relative to the repo root)."""
        try:
            abs_path, _ = resolve_in_workspace(workspace, directory) if directory not in ("", ".") else (workspace, ".")
        except PathEscapeError as exc:
            return str(exc)
        if not abs_path.is_dir():
            return f"Not a directory: {directory}"
        skip = {".git", ".venv", "node_modules", "__pycache__"}
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in abs_path.iterdir() if p.name not in skip))

    return [write_file, read_file, list_dir]


def system_prompt(ctx: CoderContext) -> str:
    sections = [
        f"You are an expert {ctx.language} developer implementing one task of a larger plan.",
        f"## Specification (read-only)\n**Goal:** {ctx.spec.goal}\n**Technical notes:** {ctx.spec.technical_notes}",
        f"## Your task\n**Title:** {ctx.task.title}\n**Description:** {ctx.task.description}\n"
        f"**Target files:** {', '.join(ctx.task.target_files)}\n**Definition of Done:** {ctx.task.definition_of_done}",
    ]
    if ctx.test_content:
        sections.append(
            f"## Acceptance test for this task\n```\n{ctx.test_content}\n```\n"
            "It was written against the Definition of Done. Make it pass."
        )
    if ctx.lessons:
        sections.append("## Lessons from past reviews of this repository\n" + "\n".join(f"- {x}" for x in ctx.lessons))
    if ctx.retrieved_context:
        sections.append(f"## Retrieved repository context\n{ctx.retrieved_context}")
    sections.append(
        "## Instructions\n"
        "1. Use search_codebase / search_docs to find existing code and conventions before writing new code\n"
        "2. Reuse the names and signatures that existing code and the task already use — later tasks depend on them\n"
        "3. Write each required file with write_file (full content)\n"
        "4. Do NOT modify acceptance tests; do NOT add features beyond the task\n"
        "5. No scratch, debug or helper scripts — everything you write is committed\n"
        "6. When done, reply with the list of files you wrote"
    )
    return "\n\n".join(sections)


async def run_coder(ctx: CoderContext, retriever: HybridRetriever | None, feedback: str | None = None) -> CoderResult:
    s = get_settings()
    written: dict[str, str] = {}
    tools = build_file_tools(ctx.workspace, written)
    if retriever is not None:
        tools += make_search_tools(retriever)

    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=2),
    ]
    if s.fallback_model and s.fallback_model != s.coder_model:
        middleware.append(ModelFallbackMiddleware(chat_model(s.fallback_model, temperature=0.1)))

    agent = create_agent(
        chat_model(s.coder_model, temperature=0.1),
        tools=tools,
        system_prompt=system_prompt(ctx),
        middleware=middleware,
        name="coder",
    )
    user = (
        f"The previous attempt was rejected. Fix these problems and rewrite the affected files:\n\n{feedback}"
        if feedback
        else "Implement the task. Start with the main implementation file."
    )
    try:
        await agent.ainvoke(
            {"messages": [{"role": "user", "content": user}]},
            config={"tags": ["coder", f"task:{ctx.task.id}"], "recursion_limit": MAX_MODEL_CALLS * 3},
        )
    except Exception as exc:  # noqa: BLE001 — an agent crash is a failed attempt, not a dead run
        return CoderResult(task_id=ctx.task.id, success=False, written_files=written, error=str(exc)[:2000])
    if not written:
        return CoderResult(task_id=ctx.task.id, success=False, error="Coder wrote no files")
    return CoderResult(task_id=ctx.task.id, success=True, written_files=written)
