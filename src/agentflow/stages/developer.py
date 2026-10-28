"""Developer: the agent of the Делается column.

It takes the approved plan from the card (the planner's comment) and runs agentflow's task
loop on it — per task: acceptance test → code → gate → review, with the referee and
escalation — as the developer graph, checkpointed under the card's Run field. The branch
is pushed; the PR is a later column's job.

- every task done and the full suite green → Тестируется, with the branch and sub-issues closed;
- a task out of budget → Блокировка with its dossier. A person answers in a comment (a hint
  for a retry, «пропусти», «перепланируй» or «стоп») and moves the card back to Делается;
  the paused run then resumes with that answer instead of starting over;
- a failed task or a red full suite → Блокировка with the reason.

    uv run python -m agentflow.stages.developer     # process the free cards in Делается once
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from agentflow.board import (
    BLOCKED,
    DOING,
    READY,
    REPORT_LINE,
    TESTING,
    Board,
    Card,
    Report,
    Thread,
    branch_of,
    reports,
)
from agentflow.config import WorkflowConfig
from agentflow.graph.build import build_developer_graph
from agentflow.graph.escalation import dossier_text
from agentflow.runner import (
    FINISHED,
    Persistence,
    RunSnapshot,
    continue_run,
    open_persistence,
    resume_run,
    snapshot,
    start_from_plan,
)
from agentflow.schemas import Task
from agentflow.stages import research
from agentflow.stages.planner import Plan, is_agent_comment, latest_plan, subtask_id, target_of

log = logging.getLogger(__name__)

AGENT = "developer"
# First words of a person's answer to a blocked task; anything else is a hint for a retry.
ACTIONS = {
    "skip": re.compile(r"^\W*(пропус\w*|skip)", re.I),
    "replan": re.compile(r"^\W*(переплан\w*|replan)", re.I),
    "abort": re.compile(r"^\W*(стоп|stop|abort|отмен\w*)", re.I),
}


def config_for(card: Card) -> WorkflowConfig:
    # "ask": a task out of budget pauses the run, and the stage turns the pause into Блокировка.
    return WorkflowConfig(target_repo=target_of(card), on_task_failure="ask")


def decision_from(thread: Thread, options: list[str]) -> dict[str, Any]:
    """The person's answer below the developer's last block report."""
    start = max(
        (
            i
            for i, c in enumerate(thread.comments)
            if f"agentflow:report agent={AGENT}" in c.body and f"to={BLOCKED}" in c.body
        ),
        default=-1,
    )
    human = [c.body.strip() for c in thread.comments[start + 1 :] if not is_agent_comment(c)]
    hint = "\n\n".join(human) or None
    if human:
        for action, pattern in ACTIONS.items():
            if action in options and pattern.match(human[-1]):
                return {"action": action, "hint": hint}
    return {"action": "retry", "hint": hint}


def outcome(card: Card, snap: RunSnapshot) -> tuple[str, Report]:
    """Where the card goes after the run stopped, and what the next reader needs to know."""
    v = snap.values
    if snap.interrupt and snap.interrupt.get("type") == "task_failed":
        d = snap.interrupt["dossier"]
        return BLOCKED, Report(
            agent=AGENT,
            summary=f"задача `{d['task']['id']}` исчерпала попытки\n\n```\n{dossier_text(d)}\n```",
            next_step="ответьте комментарием — подсказка для повтора, «пропусти», «перепланируй» или «стоп» — "
            "и верните карточку в Делается",
        )
    if v.get("status") == "failed":
        return BLOCKED, Report(agent=AGENT, summary=f"прогон остановлен: {(v.get('error') or '?')[:1500]}")
    tasks = v.get("tasks", [])
    failed = [t for t in tasks if t.status == "failed"]
    if failed:
        reasons = v.get("failed_tasks", {})
        lines = "; ".join(f"`{t.id}` — {reasons.get(t.id, '?').splitlines()[0][:200]}" for t in failed)
        return BLOCKED, Report(agent=AGENT, summary=f"не сделаны: {lines}")
    branch = v.get("branch", "")
    gate = v.get("final_gate")
    if gate is not None and not gate.passed:
        return BLOCKED, Report(
            agent=AGENT,
            summary=f"полный прогон на ветке {branch} не прошёл:\n\n```\n{gate.summary[:1500]}\n```",
        )
    return TESTING, Report(
        agent=AGENT,
        summary=f"сделано задач: {len(tasks)}, полный прогон зелёный",
        next_step=f"проверить каждый пункт Definition of Done на ветке {branch}",
        artifacts=[f"https://github.com/{card.repo}/tree/{branch}"],
        branch=branch,
    )


async def _close_done(board: Board, card: Card, snap: RunSnapshot) -> None:
    done = {t.id for t in snap.values.get("tasks", []) if t.status == "completed"}
    for s in await board.sub_issues(card):
        if not s.closed and subtask_id(s) in done:
            await board.close_issue(s.issue_id)


def sent_back(thread: Thread) -> tuple[str, str] | None:
    """(branch, what the later stage found) when the newest report sent the card back here."""
    last = next(iter(reversed(reports(thread))), None)
    if last is None or last.dst != DOING or last.src not in (TESTING, READY):
        return None
    body = REPORT_LINE.sub("", thread.comments[last.index].body).strip()
    return branch_of(thread), body


async def _fix_run(
    board: Board, card: Card, p: Persistence, cfg: WorkflowConfig, plan: Plan, branch: str, findings: str
) -> RunSnapshot:
    """A card the tester or the PR's CI sent back: one task, «fix what they found», on the
    card's own branch. Re-running the whole plan from main would redo finished work and
    lose the commits the tester saw."""
    files = sorted({f for t in plan.tasks for f in t.target_files})
    fix = Task(
        id=f"task-fix-{card.attempts}",
        title="Исправить то, что нашла проверка",
        description=f"A later stage checked the branch and sent it back. Its report:\n\n{findings}",
        target_files=files,
        definition_of_done="Every item the report marks as failed holds; nothing that passed breaks.",
    )
    if cfg.target_repo is not None:
        cfg = cfg.model_copy(update={"target_repo": cfg.target_repo.model_copy(update={"base_branch": branch})})
    thread_id = f"card-{card.number}-fix{card.attempts}-{uuid.uuid4().hex[:8]}"
    await board.set_field(card, "Run", thread_id)
    return await start_from_plan(p, plan.spec, [fix], cfg, thread_id, branch=branch, issue_number=card.number)


async def process(
    board: Board, card: Card, p: Persistence, config: Callable[[Card], WorkflowConfig] = config_for
) -> str:
    """Drive the card's run until it finishes or pauses; returns the column the card went to."""
    thread = await board.thread(card)
    found = latest_plan(thread)
    if found is None:
        await board.move(card, BLOCKED, Report(agent=AGENT, summary="на карточке нет плана: её не прошёл planner"))
        return BLOCKED

    if card.type == "research":
        returned_report = (sent_back(thread) or ("", None))[1]
        to, report = await research.research(board, card, thread, found[0], returned_report)
        if to != BLOCKED:
            for s in await board.sub_issues(card):
                if not s.closed:
                    await board.close_issue(s.issue_id)
        await board.move(card, to, report)
        return to

    snap = await snapshot(p.graph, card.run) if card.run else None
    returned = sent_back(thread)
    if snap and snap.values and snap.interrupt:
        snap = await resume_run(p, snap.thread_id, decision_from(thread, snap.interrupt.get("options", [])))
    elif snap and snap.values and snap.status not in FINISHED:
        snap = await continue_run(p, snap.thread_id)  # the worker that drove it died
    elif returned and f"-fix{card.attempts}-" not in (card.run or ""):
        snap = await _fix_run(board, card, p, config(card), found[0], *returned)
    elif snap and snap.values and outcome(card, snap)[0] == TESTING:
        pass  # finished, but the move after it never happened
    else:
        # No run yet, or a finished one a person sent back: start over from the plan.
        plan, _, _ = found
        suffix = uuid.uuid4().hex[:8]
        thread_id = f"card-{card.number}-{suffix}"
        await board.set_field(card, "Run", thread_id)
        snap = await start_from_plan(
            p,
            plan.spec,
            plan.tasks,
            config(card),
            thread_id,
            branch=f"agentflow/issue-{card.number}-{suffix}",
            issue_number=card.number,
        )

    await _close_done(board, card, snap)
    to, report = outcome(card, snap)
    await board.move(card, to, report)
    return to


async def run(
    board: Board, p: Persistence, config: Callable[[Card], WorkflowConfig] = config_for
) -> list[tuple[Card, str]]:
    """Process every free card in Делается once, one after another."""
    done = []
    for card in await board.cards(DOING):
        if card.owner or not await board.claim(card, AGENT):
            continue
        card = card.model_copy(update={"owner": AGENT})
        try:
            done.append((card, await process(board, card, p, config)))
        except Exception:
            log.exception("developer failed on %s", card.url)
            await board.set_field(card, "Owner", None)
            raise
    return done


async def main() -> None:
    board = Board.from_settings()
    try:
        async with open_persistence(builder=build_developer_graph) as p:
            for card, column in await run(board, p):
                print(f"{column:12} {card.url}")
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
