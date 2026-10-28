"""Planner: the agent of the Planning column.

It reads the card (goal, Definition of Done, context) and writes a spec and a task plan
with binding interfaces, exploring a fresh clone of the repo the way the orchestrator
always has. The plan goes onto the card as a comment: readable for a person, with the
spec and tasks encoded in a marker so the developer stage works from the card alone.

- Approval ≠ none → the plan waits for a person: a comment "ok" (or the legacy "ок") approves it, anything
  else is feedback and the plan is rewritten with it (at most three versions, then
  Blocked);
- approved → each task becomes a sub-issue of the card and the card moves to In progress.

    uv run python -m agentflow.stages.planner       # process the free cards in Planning once
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agentflow import repos
from agentflow.agents import orchestrator
from agentflow.board import BLOCKED, CARD_MARKER, DOING, PLANNING, Board, Card, Comment, Report, SubIssue, Thread
from agentflow.config import TargetRepo
from agentflow.integrations import telegram
from agentflow.integrations import workspace as ws
from agentflow.schemas import Spec, Task
from agentflow.stages import research

log = logging.getLogger(__name__)

AGENT = "planner"
PLAN_MARK = "agentflow:plan"
TASK_MARK = re.compile(r"<!-- agentflow:subtask id=(\S+) -->")
PLAN_RE = re.compile(r"<!-- agentflow:plan version=(\d+) data=([A-Za-z0-9+/=]+) -->")
# Short answers a person types to say "go". Anything else under the plan is feedback.
APPROVE = re.compile(r"^\W*(ок|ok|окей|okay|да|yes|го|go|lgtm|approved?|одобр\w*|поехали|давай)\W*$", re.I)
MAX_VERSIONS = 3


class Plan(BaseModel):
    spec: Spec
    tasks: list[Task]


@dataclass
class Outcome:
    kind: Literal["awaiting", "wait", "approved", "blocked"]
    detail: str = ""


def render_plan(plan: Plan, version: int, needs_approval: bool) -> str:
    s = plan.spec
    lines = [f"### Plan v{version}: {s.title}", s.goal, "", "**Acceptance criteria:**"]
    lines += [f"- {c}" for c in s.acceptance_criteria]
    if s.out_of_scope:
        lines += ["", "**Out of scope:** " + "; ".join(s.out_of_scope)]
    lines += ["", "**Tasks:**"]
    for i, t in enumerate(plan.tasks, 1):
        after = f" (after {', '.join(t.depends_on)})" if t.depends_on else ""
        lines.append(f"{i}. `{t.id}` {t.title}{after} — {', '.join(t.target_files)}")
    if needs_approval:
        lines += ["", "Reply «ok» to start work on this plan, or write what to change."]
    # base64: JSON in an HTML comment would end it at the first "--" inside a string.
    data = base64.b64encode(plan.model_dump_json().encode()).decode()
    lines.append(f"<!-- {PLAN_MARK} version={version} data={data} -->")
    return "\n".join(lines)


def latest_plan(thread: Thread) -> tuple[Plan, int, list[Comment]] | None:
    """The newest plan on the card, its version, and the comments that came after it."""
    for i in range(len(thread.comments) - 1, -1, -1):
        if m := PLAN_RE.search(thread.comments[i].body):
            plan = Plan.model_validate_json(base64.b64decode(m.group(2)))
            return plan, int(m.group(1)), thread.comments[i + 1 :]
    return None


def is_agent_comment(c: Comment) -> bool:
    return "agentflow:" in c.body


def card_prompt(thread: Thread) -> str:
    """What the author asked for, as intake left it: the card is the whole input."""
    return f"# {thread.title}\n\n{CARD_MARKER.sub('', thread.body).strip()}"


def needs_approval(card: Card) -> bool:
    # Unset means the orchestrator has not decided yet; a person looking costs less than a wrong plan.
    return card.approval != "none"


def render_subtask(t: Task) -> str:
    deps = ", ".join(t.depends_on) or "—"
    return (
        f"{t.description}\n\n**Files:** {', '.join(t.target_files) or '—'}\n\n"
        f"**Interface:**\n```\n{t.interface or '—'}\n```\n\n**Done when:** {t.definition_of_done}\n\n"
        f"**After:** {deps}\n\n<!-- agentflow:subtask id={t.id} -->"
    )


def subtask_id(s: SubIssue) -> str | None:
    m = TASK_MARK.search(s.body)
    return m.group(1) if m else None


Planner = Callable[[str, Plan | None, str | None, Path], Awaitable[Plan]]
Checkout = Callable[[Card], AbstractAsyncContextManager[Path]]


async def make_plan(prompt: str, previous: Plan | None, feedback: str | None, workspace: Path) -> Plan:
    spec = await orchestrator.generate_spec(
        prompt, previous=previous.spec if previous else None, feedback=feedback, workspace=workspace
    )
    tasks = await orchestrator.generate_tasks(
        spec, previous=previous.tasks if previous else None, feedback=feedback, workspace=workspace
    )
    return Plan(spec=spec, tasks=tasks)


def target_of(card: Card) -> TargetRepo:
    entry = next((e for e in repos.load_whitelist() if e.full_name == card.repo), None)
    return TargetRepo.parse(card.repo, (entry.base_branch if entry else None) or "main")


@contextlib.asynccontextmanager
async def checkout(card: Card) -> AsyncIterator[Path]:
    """A throwaway clone to explore: the planner reads the repo, it never writes to it."""
    target = target_of(card)
    path = await asyncio.to_thread(ws.prepare_workspace, target, f"plan/{uuid.uuid4().hex[:8]}")
    try:
        yield path
    finally:
        ws.cleanup_workspace(path, target)


@contextlib.asynccontextmanager
async def _nowhere(card: Card) -> AsyncIterator[Path]:
    yield Path(".")


async def _approve(board: Board, card: Card, plan: Plan, version: int) -> Outcome:
    """Tasks → sub-issues, then the move. Sub-issues already there (a pass that died before
    the move) are kept, so a retry never files a task twice."""
    have = {subtask_id(s): s for s in await board.sub_issues(card)}
    subs = []
    for t in plan.tasks:
        s = have.get(t.id) or await board.add_sub_issue(card, f"{t.title} ({t.id})", render_subtask(t))
        subs.append(s)
    await board.move(
        card,
        DOING,
        Report(
            agent=AGENT,
            summary=f"plan v{version} approved: {len(plan.tasks)} task(s)",
            next_step="for each sub-task: test → code → gate → review; on the card's branch, no PR",
            artifacts=[s.url for s in subs],
        ),
    )
    return Outcome("approved", f"{len(subs)} sub-issues")


async def process(board: Board, card: Card, planner: Planner = make_plan, checkout: Checkout = checkout) -> Outcome:
    if card.type == "research" and planner is make_plan:
        # A search plan from the card; there is no repo to explore.
        planner, checkout = research.make_research_plan, _nowhere
    thread = await board.thread(card)
    found = latest_plan(thread)
    previous, version, feedback = None, 0, None
    if found:
        previous, version, after = found
        human = [c for c in after if not is_agent_comment(c)]
        if not human:
            if needs_approval(card):
                return Outcome("wait")
            return await _approve(board, card, previous, version)  # a pass died between the plan and the move
        if APPROVE.match(human[-1].body.strip()):
            return await _approve(board, card, previous, version)
        if version >= MAX_VERSIONS:
            await board.move(
                card,
                BLOCKED,
                Report(agent=AGENT, summary=f"plan not agreed after {version} versions: {human[-1].body[:300]}"),
            )
            return Outcome("blocked", "too many versions")
        feedback = "\n\n".join(c.body.strip() for c in human)

    async with checkout(card) as root:
        plan = await planner(card_prompt(thread), previous, feedback, root)
    if not plan.tasks:
        await board.move(card, BLOCKED, Report(agent=AGENT, summary="the plan is empty: nothing to do"))
        return Outcome("blocked", "empty plan")
    version += 1
    ask = needs_approval(card)
    await board.comment(card, render_plan(plan, version, ask))
    if not ask:
        return await _approve(board, card, plan, version)
    await board.set_field(card, "Owner", None)
    with contextlib.suppress(Exception):  # a notification must never lose the plan
        await telegram.send_message(f"📝 Plan v{version} awaits approval: {card.title}\n{card.url}")
    return Outcome("awaiting", f"v{version}")


async def run(board: Board, planner: Planner = make_plan, checkout: Checkout = checkout) -> list[tuple[Card, Outcome]]:
    """Process every free card in Planning once."""
    done = []
    for card in await board.cards(PLANNING):
        if card.owner or not await board.claim(card, AGENT):
            continue
        card = card.model_copy(update={"owner": AGENT})
        try:
            outcome = await process(board, card, planner, checkout)
        except Exception:
            log.exception("planner failed on %s", card.url)
            await board.set_field(card, "Owner", None)
            raise
        if outcome.kind == "wait":
            await board.set_field(card, "Owner", None)
        done.append((card, outcome))
    return done


async def main() -> None:
    board = Board.from_settings()
    try:
        for card, o in await run(board):
            print(f"{o.kind:9} {card.url}  {o.detail}")
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
