"""Tester: the agent of the Тестируется column.

It checks out the branch the developer pushed and checks the card's Definition of Done
item by item, without the developer's history: the run's reviews and retries are not on
the card, only the code is. Behaviour gets a test the tester writes itself and runs; an
item about the repository itself (a doc, a file) gets an inspection with a quote as
evidence. Then the whole suite runs.

- every item proven and the suite green → Готово к мержу, with the checklist and evidence;
- anything else → back to Делается with what failed (the board counts the return; the
  third one blocks the card).

The tester's own tests are evidence, not product: they are never committed or pushed.

    uv run python -m agentflow.stages.tester        # process the free cards in Тестируется once
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agentflow.agents import orchestrator
from agentflow.board import DOING, MERGED, READY, TESTING, Board, Card, Report, Thread, branch_of
from agentflow.config import TargetRepo
from agentflow.gate import Gate, detect_gate
from agentflow.integrations import workspace as ws
from agentflow.skills import role_section
from agentflow.stages import research

log = logging.getLogger(__name__)

AGENT = "tester"
DOD_DIR = "tests/dod"


class DodCheck(BaseModel):
    item: str = Field(description="The Definition of Done item, verbatim")
    method: Literal["test", "inspection"] = Field(
        description="test: behaviour, checked by running code; inspection: a fact about the repository itself"
    )
    test_code: str = Field(
        default="", description="method=test: a complete test file that passes only if the item holds"
    )
    passed: bool = Field(default=False, description="method=inspection: whether the item holds")
    evidence: str = Field(
        description="test: what the test calls and expects; inspection: file:line and the quoted text"
    )


class DodChecks(BaseModel):
    checks: list[DodCheck]


SYSTEM = """You are the tester of a finished branch. You did not write this code. For every
Definition of Done item of the card, make one check that would fail if the item did not hold.
Return one check per item, in the card's order, with the item text verbatim."""


@dataclass
class Checked:
    item: str
    passed: bool
    evidence: str


def dod_items(body: str) -> list[str]:
    """The card's checklist, as intake wrote it under «Definition of Done»."""
    section = body.split("## Definition of Done", 1)[-1].split("\n## ", 1)[0]
    return [m.group(1).strip() for m in re.finditer(r"^- \[[ xX]\] (.+)$", section, re.M)]


Checker = Callable[[str, list[str], Gate, Path], Awaitable[DodChecks]]
Checkout = Callable[[Card, str], AbstractAsyncContextManager[Path]]


async def make_checks(goal: str, items: list[str], gate: Gate, root: Path) -> DodChecks:
    numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))
    prompt = (
        f"## Card\n{goal}\n\n## Definition of Done\n{numbered}\n\n## Test rules ({gate.language})\n"
        f"{gate.test_prompt_rules().replace('tests/acceptance', DOD_DIR)}"
    )
    out = await orchestrator.explore(SYSTEM + role_section("tester"), [HumanMessage(prompt)], root, DodChecks, "tester")
    if out is None:
        raise RuntimeError("the tester returned no checks")
    return out


async def verify(items: list[str], checks: DodChecks, gate: Gate, root: Path) -> list[Checked]:
    """Run the tester's tests one item at a time, so each item has its own verdict."""
    by_item = {c.item.strip(): c for c in checks.checks}
    out = []
    for i, item in enumerate(items, 1):
        check = by_item.get(item.strip()) or (checks.checks[i - 1] if i <= len(checks.checks) else None)
        if check is None:
            out.append(Checked(item, False, "нет проверки для пункта"))
        elif check.method == "inspection":
            out.append(Checked(item, check.passed, check.evidence))
        elif not check.test_code.strip():
            out.append(Checked(item, False, "тест не написан"))
        else:
            rel = f"{DOD_DIR}/test_dod_{i}{gate.test_suffix_for(check.test_code)}"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(check.test_code)
            result = await gate.run(root, [rel], enable_lint=False)
            detail = "" if result.passed else f": {result.summary[:600]}"
            out.append(Checked(item, result.passed, f"{check.evidence} (`{rel}`{detail})"))
    return out


def render(checked: list[Checked]) -> str:
    return "\n".join(f"- [{'x' if c.passed else ' '}] {c.item} — {c.evidence}" for c in checked)


@contextlib.asynccontextmanager
async def checkout(card: Card, branch: str) -> AsyncIterator[Path]:
    """A fresh clone of the card's branch: what the developer pushed, nothing from its run."""
    target = TargetRepo.parse(card.repo, branch)
    path = await asyncio.to_thread(ws.prepare_workspace, target, branch)
    try:
        yield path
    finally:
        ws.cleanup_workspace(path, target)


@dataclass
class Outcome:
    column: str
    checked: list[Checked]


async def process(board: Board, card: Card, checker: Checker = make_checks, checkout: Checkout = checkout) -> Outcome:
    thread: Thread = await board.thread(card)
    if card.type == "research":
        to, report = await research.check(card, thread, dod_items(thread.body))
        moved = await board.move(card, to, report)
        if to == MERGED:
            # No PR will close it with "Closes #N": a live research card sat done with its issue open.
            await board.close_issue(card.issue_id)
        return Outcome(moved.status or to, [])
    branch = branch_of(thread)
    items = dod_items(thread.body)
    if not branch or not items:
        why = "в отчёте developer нет ветки" if not branch else "у карточки нет пунктов Definition of Done"
        await board.move(card, DOING, Report(agent=AGENT, summary=f"нечего проверять: {why}"))
        return Outcome(DOING, [])

    async with checkout(card, branch) as root:
        gate = detect_gate(root, [])
        goal = thread.body.split("## Definition of Done", 1)[0].strip()
        # The suite first, on the branch as pushed: run after the tester's own tests were written,
        # its lint failed a live card on the tester's own file while every DoD item had passed.
        suite = await gate.run(root, None, enable_lint=True)
        checked = await verify(items, await checker(goal, items, gate, root), gate, root)

    ok = all(c.passed for c in checked)
    lines = render(checked)
    suite_line = (
        "полный прогон зелёный" if suite.passed else f"полный прогон красный:\n```\n{suite.summary[:1200]}\n```"
    )
    if ok and suite.passed:
        report = Report(
            agent=AGENT,
            summary=f"все {len(checked)} пунктов DoD подтверждены, {suite_line}\n\n{lines}",
            next_step=f"открыть PR из {branch}",
            artifacts=[f"https://github.com/{card.repo}/tree/{branch}"],
            branch=branch,
        )
        await board.move(card, READY, report)
        return Outcome(READY, checked)
    failed = sum(not c.passed for c in checked)
    report = Report(
        agent=AGENT,
        summary=f"не подтверждено пунктов DoD: {failed} из {len(checked)}; {suite_line}\n\n{lines}",
        next_step="исправить неотмеченные пункты на той же ветке",
        artifacts=[f"https://github.com/{card.repo}/tree/{branch}"],
        branch=branch,
    )
    moved = await board.move(card, DOING, report)
    return Outcome(moved.status or DOING, checked)


async def run(
    board: Board, checker: Checker = make_checks, checkout: Checkout = checkout
) -> list[tuple[Card, Outcome]]:
    done = []
    for card in await board.cards(TESTING):
        if card.owner or not await board.claim(card, AGENT):
            continue
        card = card.model_copy(update={"owner": AGENT})
        try:
            done.append((card, await process(board, card, checker, checkout)))
        except Exception:
            log.exception("tester failed on %s", card.url)
            await board.set_field(card, "Owner", None)
            raise
    return done


async def main() -> None:
    board = Board.from_settings()
    try:
        for card, o in await run(board):
            print(f"{o.column:14} {card.url}  {sum(c.passed for c in o.checked)}/{len(o.checked)}")
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
