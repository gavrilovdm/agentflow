"""Releaser: the agent of the Готово к мержу column.

It opens the PR from the card's branch (Closes #N, with the tester's checklist), then
watches it on each pass:

- CI red → back to Делается with the failing checks (the developer fixes on the same branch);
- CI running → the card waits;
- CI green (or the repo runs none) → merged once a person writes «мержи» on the card, or
  at once for a repo with `auto_merge` in the whitelist; the card moves to Смержено;
- a conflict with the base branch, or a PR closed without merging → Блокировка.

    uv run python -m agentflow.stages.releaser      # process the free cards in Готово к мержу once
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from agentflow import repos
from agentflow.board import (
    BLOCKED,
    DOING,
    MERGED,
    READY,
    REPORT_LINE,
    Board,
    Card,
    PullRequest,
    Report,
    Thread,
    branch_of,
    reports,
)
from agentflow.stages.planner import is_agent_comment, target_of

log = logging.getLogger(__name__)

AGENT = "releaser"
MERGE_WORD = re.compile(r"^\W*(мержи|мёржи|merge|сливай)\W*$", re.I)
ASK_MARK = "agentflow:merge-ask"


@dataclass
class Outcome:
    kind: Literal["waiting", "merged", "back", "blocked"]
    detail: str = ""


def merge_approved(thread: Thread) -> bool:
    """A person wrote «мержи» after the card last arrived in Готово к мержу."""
    arrived = max((r.index for r in reports(thread) if r.dst == READY), default=-1)
    return any(MERGE_WORD.match(c.body.strip()) for c in thread.comments[arrived + 1 :] if not is_agent_comment(c))


def pr_body(thread: Thread, card: Card) -> str:
    """The tester's checklist with its evidence, without the card-only parts (the report's
    heading, the next stage's instructions), which read as noise on a PR."""
    tested = next((r for r in reversed(reports(thread)) if r.dst == READY), None)
    text = REPORT_LINE.sub("", thread.comments[tested.index].body) if tested else ""
    checklist = "\n".join(line for line in text.splitlines() if line.startswith("- ["))
    return f"## Definition of Done (проверено tester)\n{checklist or '—'}\n\nCloses #{card.number}"


def auto_merge(card: Card) -> bool:
    entry = next((e for e in repos.load_whitelist() if e.full_name == card.repo), None)
    return bool(entry and entry.auto_merge)


async def _merged(board: Board, card: Card, pr: PullRequest, how: str) -> Outcome:
    await board.move(card, MERGED, Report(agent=AGENT, summary=f"{pr.url} смержен ({how})", artifacts=[pr.url]))
    return Outcome("merged", pr.url)


async def process(board: Board, card: Card, may_merge: bool | None = None) -> Outcome:
    thread = await board.thread(card)
    branch = branch_of(thread)
    if not branch:
        await board.move(card, BLOCKED, Report(agent=AGENT, summary="в отчётах карточки нет ветки"))
        return Outcome("blocked", "no branch")

    pr = await board.pull_request(card, branch)
    if pr is None:
        base = target_of(card).base_branch
        pr = await board.open_pull_request(card, branch, base, card.title, pr_body(thread, card))
        await board.comment(card, f"Открыт {pr.url}. <!-- agentflow:pr url={pr.url} -->")
        # Checks start a moment after the PR opens; reading them now would see "none" and merge.
        await board.set_field(card, "Owner", None)
        return Outcome("waiting", f"opened {pr.url}")
    if pr.state == "MERGED":
        return await _merged(board, card, pr, "человеком")
    if pr.state == "CLOSED":
        await board.move(card, BLOCKED, Report(agent=AGENT, summary=f"{pr.url} закрыт без мержа"))
        return Outcome("blocked", "closed")
    if pr.mergeable == "CONFLICTING":
        await board.move(card, BLOCKED, Report(agent=AGENT, summary=f"{pr.url} конфликтует с базовой веткой"))
        return Outcome("blocked", "conflict")
    if pr.checks in ("FAILURE", "ERROR"):
        failing = "\n".join(f"- {f}" for f in pr.failing) or "- (подробностей нет)"
        moved = await board.move(
            card,
            DOING,
            Report(
                agent=AGENT,
                summary=f"CI на {pr.url} красный:\n{failing}",
                next_step="починить упавшие проверки на той же ветке",
                artifacts=[pr.url],
                branch=branch,
            ),
        )
        return Outcome("back" if moved.status == DOING else "blocked", ", ".join(pr.failing))
    if pr.checks in ("PENDING", "EXPECTED") or pr.mergeable == "UNKNOWN":
        await board.set_field(card, "Owner", None)
        return Outcome("waiting", f"checks {pr.checks}, mergeable {pr.mergeable}")

    allowed = auto_merge(card) if may_merge is None else may_merge
    if allowed or merge_approved(thread):
        await board.merge_pull_request(pr)
        return await _merged(board, card, pr, "автомержем" if allowed else "по «мержи»")
    if not any(ASK_MARK in c.body for c in thread.comments[max((r.index for r in reports(thread)), default=0) :]):
        await board.comment(
            card, f"{pr.url}: CI зелёный, DoD проверен. Напишите «мержи», чтобы смержить. <!-- {ASK_MARK} -->"
        )
    await board.set_field(card, "Owner", None)
    return Outcome("waiting", "merge approval")


async def run(board: Board) -> list[tuple[Card, Outcome]]:
    done = []
    for card in await board.cards(READY):
        if card.owner or not await board.claim(card, AGENT):
            continue
        card = card.model_copy(update={"owner": AGENT})
        try:
            outcome = await process(board, card)
        except Exception:
            log.exception("releaser failed on %s", card.url)
            await board.set_field(card, "Owner", None)
            raise
        if outcome.kind == "waiting":
            await board.set_field(card, "Owner", None)
        done.append((card, outcome))
    return done


async def main() -> None:
    board = Board.from_settings()
    try:
        for card, o in await run(board):
            print(f"{o.kind:8} {card.url}  {o.detail}")
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
