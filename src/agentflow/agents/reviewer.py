"""Reviewer and referee.

The reviewer is an agent, not a single prompt: it can grep and read the workspace to check
a diff against existing conventions and call sites, and it must finish with a
structured `ReviewDecision`. A malformed or failed review is reported as a
*malfunction* cycle, which the graph does not charge to the task's budget.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelFallbackMiddleware, ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agentflow.agents.repo_tools import make_repo_tools
from agentflow.config import get_settings
from agentflow.models import chat_model, structured
from agentflow.schemas import FailureRuling, ReviewCycle, ReviewDecision, Spec, Task
from agentflow.skills import role_section

log = logging.getLogger(__name__)

REVIEW_SYSTEM = """You are a senior code reviewer. Review the diff against the spec, the task's
Definition of Done and the acceptance test. The automated gate (types, tests, lint) has ALREADY passed.

Approve if the change does what the task asks, is consistent with existing code, and has no real bugs.
Request changes only for incorrect logic, missing required behaviour, or clear violations of the
spec or the repository's conventions — not for style preferences.
You may use glob_files, grep and read_file to check how the changed code fits existing modules.
Each change request must name the file and the exact fix needed."""
REVIEW_SYSTEM += role_section("reviewer")

REFEREE_SYSTEM = """You settle a deadlock between a test and an implementation. The same failure has
repeated several times, so one of the two is wrong in a way the coder cannot fix by trying again.

Answer "test" when the test contradicts the task or spec — it calls a function the spec never defines,
uses a different name for an entity, or asserts out-of-scope behaviour. Such a test is unsatisfiable.
Answer "code" when the test faithfully reflects the task and the implementation simply falls short.
If the failures alternate — the test fails, and the change that makes it pass is rejected by the
reviewer for breaking the task's scope or an existing API — the test demands something the task
forbids: answer "test" and say what it should assert instead.
Prefer "code" unless the test clearly departs from the specification."""


def reviewer_chat_model(name: str) -> BaseChatModel:
    """The reviewer must end with a structured ReviewDecision, which the agent obtains by
    forcing a tool call. DeepSeek's thinking mode rejects a forced tool_choice, so it is
    disabled here — for the primary model *and* the fallback. Building the fallback without
    it made every review fail (400) the moment the Opus proxy got rate-limited."""
    return chat_model(name, forces_tool_choice=True)


# Model calls the reviewer may spend exploring before it must answer, scaled by how many
# files the diff touches (the diff itself is in the prompt; calls go to surrounding code).
# A flat 8 ended a live review with no verdict three times in a row. These numbers are a
# starting point: tune them from the reviewer's call counts in traces.
REVIEW_CALLS_BASE = 8
REVIEW_CALLS_PER_FILE = 3
REVIEW_CALLS_MAX = 40
READ_NOTES_CHARS = 24_000


def call_budget(diff: str) -> int:
    files = sum(line.startswith("diff --git ") for line in diff.splitlines())
    return min(REVIEW_CALLS_BASE + REVIEW_CALLS_PER_FILE * max(files, 1), REVIEW_CALLS_MAX)


def _middleware(primary: str, fallback: str, call_limit: int = REVIEW_CALLS_BASE) -> list[Any]:
    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=call_limit, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=2),
    ]
    if fallback and fallback != primary:
        middleware.append(ModelFallbackMiddleware(reviewer_chat_model(fallback)))
    return middleware


async def review_diff(task: Task, spec: Spec, test_content: str, diff: str, workspace: Path | None) -> ReviewCycle:
    s = get_settings()
    agent = create_agent(
        reviewer_chat_model(s.reviewer_model),
        tools=make_repo_tools(workspace) if workspace else [],
        system_prompt=REVIEW_SYSTEM,
        response_format=ReviewDecision,
        middleware=_middleware(s.reviewer_model, s.fallback_model, call_budget(diff)),
        name="reviewer",
    )
    prompt = (
        f"## Spec: {spec.title}\n**Goal:** {spec.goal}\n\n"
        f"## Task: {task.title}\n{task.description}\n\n**Definition of Done:** {task.definition_of_done}\n\n"
        + (
            f"## Acceptance test\n```\n{test_content}\n```\n\n"
            if test_content
            else "## Acceptance test\n(none: the repository has no test suite; the gate ran its type check, build "
            "and lint. Judge the change against the Definition of Done yourself.)\n\n"
        )
        + f"## Diff\n```diff\n{diff or '(no changes staged)'}\n```"
    )
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"tags": ["reviewer", f"task:{task.id}"]},
        )
        decision: ReviewDecision | None = result.get("structured_response")
        if decision is None:
            decision = await _forced_verdict(prompt, result.get("messages", []), task.id)
    except Exception as exc:  # noqa: BLE001
        # One reviewer's bad response is not a reason to destroy a run holding committed work.
        reason = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
        log.warning("reviewer malfunction on %s: %s", task.id, reason)
        return ReviewCycle(
            verdict="changes_requested",
            comments=f"The reviewer could not produce a usable verdict: {reason}",
            diff=diff,
            reviewer_malfunction=True,
        )
    return ReviewCycle(
        verdict=decision.verdict, comments=decision.summary, change_requests=decision.change_requests, diff=diff
    )


async def _forced_verdict(prompt: str, messages: list[Any], task_id: str) -> ReviewDecision:
    """The agent ran out of calls while still reading: ask once more, with no tools, for a
    verdict on the diff plus what it had read. Cutting it off used to count as a reviewer
    malfunction, and three of those escalate the task."""
    notes = "\n\n".join(f"### {m.name or 'tool'}\n{m.text}" for m in messages if isinstance(m, ToolMessage))
    log.info("reviewer hit its call limit on %s; forcing a verdict", task_id)
    runnable = structured(get_settings().reviewer_model, ReviewDecision)
    decision = await runnable.ainvoke(
        [
            SystemMessage(REVIEW_SYSTEM + "\n\nYour time for exploring is over. Give your verdict now."),
            HumanMessage(f"{prompt}\n\n## What you read while reviewing\n{notes[-READ_NOTES_CHARS:] or '(nothing)'}"),
        ],
        config={"tags": ["reviewer", "forced-verdict", f"task:{task_id}"]},
    )
    if not isinstance(decision, ReviewDecision):
        raise ValueError("reviewer finished without a structured decision")
    return decision


async def adjudicate(task: Task, spec: Spec, test_content: str, failure: str) -> FailureRuling:
    runnable = structured(get_settings().reviewer_model, FailureRuling)
    return await runnable.ainvoke(
        [
            SystemMessage(REFEREE_SYSTEM),
            HumanMessage(
                f"## Specification\n**Goal:** {spec.goal}\n**Technical notes:** {spec.technical_notes}\n\n"
                f"## Task\n**Title:** {task.title}\n**Description:** {task.description}\n"
                f"**Definition of Done:** {task.definition_of_done}\n\n"
                f"## Test\n```\n{test_content}\n```\n\n## Recent failures (oldest first)\n```\n{failure[:2000]}\n```"
            ),
        ],
        config={"tags": ["referee", f"task:{task.id}"]},
    )
