"""Reviewer and referee.

The reviewer is an agent, not a single prompt: it can search the codebase to check
a diff against existing conventions and call sites, and it must finish with a
structured `ReviewDecision`. A malformed or failed review is reported as a
*malfunction* cycle, which the graph does not charge to the task's budget.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelFallbackMiddleware, ModelRetryMiddleware
from langchain_core.messages import HumanMessage, SystemMessage

from agentflow.config import get_settings
from agentflow.models import chat_model, structured
from agentflow.rag.retriever import HybridRetriever
from agentflow.rag.tools import make_search_tools
from agentflow.schemas import FailureRuling, ReviewCycle, ReviewDecision, Spec, Task

log = logging.getLogger(__name__)

REVIEW_SYSTEM = """You are a senior code reviewer. Review the diff against the spec, the task's
Definition of Done and the acceptance test. The automated gate (types, tests, lint) has ALREADY passed.

Approve if the change does what the task asks, is consistent with existing code, and has no real bugs.
Request changes only for incorrect logic, missing required behaviour, or clear violations of the
spec or the repository's conventions — not for style preferences.
You may use search_codebase to check how the changed code fits existing modules.
Each change request must name the file and the exact fix needed."""

REFEREE_SYSTEM = """You settle a deadlock between a test and an implementation. The same failure has
repeated several times, so one of the two is wrong in a way the coder cannot fix by trying again.

Answer "test" when the test contradicts the task or spec — it calls a function the spec never defines,
uses a different name for an entity, or asserts out-of-scope behaviour. Such a test is unsatisfiable.
Answer "code" when the test faithfully reflects the task and the implementation simply falls short.
Prefer "code" unless the test clearly departs from the specification."""


def _middleware(primary: str, fallback: str) -> list[Any]:
    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=2),
    ]
    if fallback and fallback != primary:
        middleware.append(ModelFallbackMiddleware(chat_model(fallback)))
    return middleware


async def review_diff(
    task: Task, spec: Spec, test_content: str, diff: str, retriever: HybridRetriever | None
) -> ReviewCycle:
    s = get_settings()
    agent = create_agent(
        chat_model(s.reviewer_model),
        tools=make_search_tools(retriever) if retriever else [],
        system_prompt=REVIEW_SYSTEM,
        response_format=ReviewDecision,
        middleware=_middleware(s.reviewer_model, s.fallback_model),
        name="reviewer",
    )
    prompt = (
        f"## Spec: {spec.title}\n**Goal:** {spec.goal}\n\n"
        f"## Task: {task.title}\n{task.description}\n\n**Definition of Done:** {task.definition_of_done}\n\n"
        f"## Acceptance test\n```\n{test_content}\n```\n\n"
        f"## Diff\n```diff\n{diff or '(no changes staged)'}\n```"
    )
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"tags": ["reviewer", f"task:{task.id}"]},
        )
        decision: ReviewDecision | None = result.get("structured_response")
        if decision is None:
            raise ValueError("reviewer finished without a structured decision")
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


async def adjudicate(task: Task, spec: Spec, test_content: str, failure: str) -> FailureRuling:
    runnable = structured(get_settings().reviewer_model, FailureRuling)
    return await runnable.ainvoke(
        [
            SystemMessage(REFEREE_SYSTEM),
            HumanMessage(
                f"## Specification\n**Goal:** {spec.goal}\n**Technical notes:** {spec.technical_notes}\n\n"
                f"## Task\n**Title:** {task.title}\n**Description:** {task.description}\n"
                f"**Definition of Done:** {task.definition_of_done}\n\n"
                f"## Test\n```\n{test_content}\n```\n\n## Repeating failure\n```\n{failure[:2000]}\n```"
            ),
        ],
        config={"tags": ["referee", f"task:{task.id}"]},
    )
