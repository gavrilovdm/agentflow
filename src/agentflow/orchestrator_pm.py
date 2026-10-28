"""The orchestrator as a project manager: it runs the board, it does no task work.

Once a minute (an arq cron in the worker) it reads the board and decides, in code:

- which free cards get a stage agent now: by priority, within each column's WIP limit, one
  card per repo in In progress (two PRs into one repo conflict), and nothing new out of Backlog
  while more than three cards wait on a person;
- which cards are stuck: an agent's name in Owner but no heartbeat from its job. The card is
  freed with a comment; the second time it goes to Blocked;
- the Approval mode of a card that just left Backlog;
- whether to start anything at all: an LLM provider error that a retry will not fix (no
  credit, a bad key) opens a breaker, and nothing starts until it closes.

The orchestrator hands cards out itself: it writes the agent into Owner and only then queues
the job, so two agents can never take the same card (the old claim-and-read-back could race).

    uv run python -m agentflow.orchestrator_pm      # one tick, printed, without queueing jobs
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from agentflow import repos, telemetry
from agentflow.board import (
    BACKLOG,
    BLOCKED,
    DOING,
    MERGED,
    PLANNING,
    READY,
    TESTING,
    Board,
    Card,
    Report,
    Thread,
    reports,
)
from agentflow.config import get_settings
from agentflow.integrations import telegram
from agentflow.stages import developer, intake, planner, releaser, tester
from agentflow.stages.tester import dod_items

log = logging.getLogger(__name__)

STAGES = {
    BACKLOG: intake.AGENT,
    PLANNING: planner.AGENT,
    DOING: developer.AGENT,
    TESTING: tester.AGENT,
    READY: releaser.AGENT,
}
AGENTS = set(STAGES.values())
# Agents per column at once. Backlog and Ready to merge are cheap passes, the rest hold a model
# (and In progress a worker) for minutes to hours.
WIP = {BACKLOG: 4, PLANNING: 2, DOING: 4, TESTING: 2, READY: 4}
MAX_WAITING_ON_HUMAN = 3
# Columns whose agent may only wait for a person's words: re-running it before the issue
# changes reads the thread and decides "wait" again.
WAITS_FOR_WORDS = (BACKLOG, PLANNING)
PRIORITY = {"high": 0, "normal": 1, "low": 2}

CARD_PREFIX = "agentflow:card:"  # heartbeat of the job holding a card
SEEN_PREFIX = "agentflow:seen:"  # issue updatedAt when the card last ended in "wait"
BREAKER_KEY = "agentflow:breaker"
SUMMARY_KEY = "agentflow:summary:"
CARD_TTL_S = 120
QUEUED_TTL_S = 600  # a queued job may wait for a free worker slot before its first beat
BREAKER_TTL_S = 1800
STUCK_MARK = "agentflow:stuck"
# Errors no retry fixes: the next card would fail the same way and burn its attempts.
PROVIDER_FATAL = re.compile(
    r"credit balance|insufficient[_ ]quota|invalid[_ ]api[_ ]key|authentication|\b401\b|\b403\b|permission", re.I
)


class Redis(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...
    async def delete(self, key: str) -> Any: ...


@dataclass
class Decision:
    launch: list[tuple[str, Card]] = field(default_factory=list)  # (agent, card)
    stuck: list[Card] = field(default_factory=list)
    approval: list[tuple[Card, str]] = field(default_factory=list)
    held: list[str] = field(default_factory=list)  # why something did not start, for the log


def approval_mode(card: Card, dod_count: int) -> str:
    if card.type in ("research", "text"):
        return "none"
    if dod_count <= 2 and card.priority != "high":
        return "plan_only"
    return "full"


def decide(
    cards: list[Card],
    alive: set[str],
    seen: dict[str, str],
    waiting_on_human: int,
    breaker: str | None,
) -> Decision:
    """The tick, as a pure function of what the board and Redis say."""
    d = Decision()
    owned: dict[str, list[Card]] = {c: [] for c in STAGES}
    for card in cards:
        if card.owner in AGENTS and card.status in STAGES:
            if card.item_id in alive:
                owned[card.status].append(card)
            else:
                d.stuck.append(card)
    if breaker:
        d.held.append(f"breaker open: {breaker}")
        return d

    busy_repos = {c.repo for c in owned[DOING]}
    for column, agent in STAGES.items():
        if column == BACKLOG and waiting_on_human > MAX_WAITING_ON_HUMAN:
            d.held.append(f"{waiting_on_human} cards wait on a person: nothing new from {BACKLOG}")
            continue
        free = sorted(
            (c for c in cards if c.status == column and not c.owner),
            key=lambda c: (PRIORITY.get(c.priority or "normal", 1), c.number),
        )
        room = WIP[column] - len(owned[column])
        for card in free:
            if column in WAITS_FOR_WORDS and seen.get(card.item_id) == card.updated_at:
                continue
            if room <= 0:
                d.held.append(f"{column}: WIP {WIP[column]} reached, {card.url} waits")
                continue
            if column == DOING and card.repo in busy_repos:
                d.held.append(f"{card.url} waits: {card.repo} already has a card in {DOING}")
                continue
            d.launch.append((agent, card))
            room -= 1
            if column == DOING:
                busy_repos.add(card.repo)
    return d


def waiting_on_human(cards: list[Card], threads: dict[str, Thread]) -> int:
    """Cards a person has to act on: blocked, a plan to approve, a PR to merge."""
    n = sum(c.status == BLOCKED for c in cards)
    for c in cards:
        t = threads.get(c.item_id)
        if t is None or c.status not in (PLANNING, READY) or not t.comments:
            continue
        last = t.comments[-1].body
        n += (c.status == PLANNING and planner.PLAN_MARK in last) or (c.status == READY and releaser.ASK_MARK in last)
    return n


async def _alive(redis: Redis, cards: list[Card]) -> set[str]:
    return {c.item_id for c in cards if c.owner in AGENTS and await redis.get(CARD_PREFIX + c.item_id)}


async def _seen(redis: Redis, cards: list[Card]) -> dict[str, str]:
    out = {}
    for c in cards:
        if c.status in WAITS_FOR_WORDS and not c.owner and (v := await redis.get(SEEN_PREFIX + c.item_id)):
            out[c.item_id] = v.decode() if isinstance(v, bytes) else v
    return out


async def release_stuck(board: Board, card: Card) -> None:
    """Free a card whose agent died. The second time the card goes to a person instead."""
    thread = await board.thread(card)
    since = max((r.index for r in reports(thread)), default=-1)
    before = sum(STUCK_MARK in c.body for c in thread.comments[since + 1 :])
    if before >= 1:
        await board.move(
            card,
            BLOCKED,
            Report(agent="orchestrator", summary=f"agent {card.owner} went missing on this card for the second time"),
        )
        return
    await board.comment(
        card, f"Agent {card.owner} stopped responding (no heartbeat); the card was released. <!-- {STUCK_MARK} -->"
    )
    await board.set_field(card, "Owner", None)


async def tick(board: Board, redis: Redis, enqueue: Any) -> Decision:
    """One pass: free the stuck, set approval modes, hand free cards to stage jobs."""
    cards = await board.cards()
    planning_ready = [c for c in cards if c.status in (PLANNING, READY) and not c.owner]
    threads = {c.item_id: await board.thread(c) for c in planning_ready}
    raw = await redis.get(BREAKER_KEY)
    breaker = raw.decode() if isinstance(raw, bytes) else raw
    d = decide(cards, await _alive(redis, cards), await _seen(redis, cards), waiting_on_human(cards, threads), breaker)

    for card in d.stuck:
        await release_stuck(board, card)
    for card in cards:
        if card.status == PLANNING and card.approval is None and not card.owner:
            body = threads[card.item_id].body if card.item_id in threads else (await board.thread(card)).body
            mode = approval_mode(card, len(dod_items(body)))
            await board.set_field(card, "Approval", mode)
            d.approval.append((card, mode))
            if card in [c for _, c in d.launch]:
                # The planner reads Approval from the card it was handed.
                d.launch = [(a, c.model_copy(update={"approval": mode}) if c is card else c) for a, c in d.launch]
    for agent, card in d.launch:
        await board.set_field(card, "Owner", agent)
        await redis.set(CARD_PREFIX + card.item_id, "queued", ex=QUEUED_TTL_S)
        await enqueue(agent, card.item_id)
    for why in d.held:
        log.info("held: %s", why)
    return d


@contextlib.asynccontextmanager
async def heartbeat(redis: Redis, item_id: str) -> AsyncIterator[None]:
    async def beat() -> None:
        while True:
            with contextlib.suppress(Exception):
                await redis.set(CARD_PREFIX + item_id, "1", ex=CARD_TTL_S)
            await asyncio.sleep(CARD_TTL_S / 3)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await redis.delete(CARD_PREFIX + item_id)


async def open_breaker(redis: Redis, reason: str) -> None:
    if await redis.set(BREAKER_KEY, reason[:300], ex=BREAKER_TTL_S, nx=True):
        with contextlib.suppress(Exception):
            await telegram.send_message(
                f"⛔ Agents paused for {BREAKER_TTL_S // 60} min: an LLM provider error that a retry will not "
                f"fix.\n{reason[:500]}"
            )


async def work(
    board: Board, redis: Redis, agent: str, item_id: str, persistence: Any = None, stage_runs: str | None = None
) -> str:
    """A stage job: run one agent on the card the orchestrator handed it. `stage_runs` is the
    database that keeps the job's telemetry row; the worker passes its own, tests none."""
    card = next((c for c in await board.cards() if c.item_id == item_id), None)
    if card is None or card.owner != agent:
        return "gone"  # moved, closed or reassigned while queued
    outcome = "?"
    run = telemetry.StageRun(agent, card.repo, card.number, card.type or "?", card.status or "?", card.attempts)
    async with heartbeat(redis, item_id), telemetry.stage(run, stage_runs):
        try:
            if agent == intake.AGENT:
                wl = {e.full_name for e in repos.load_whitelist()}
                outcome = (await intake.process(board, card, await board.cards(), wl)).kind
            elif agent == planner.AGENT:
                outcome = (await planner.process(board, card)).kind
            elif agent == developer.AGENT:
                outcome = await developer.process(board, card, persistence)
            elif agent == tester.AGENT:
                outcome = (await tester.process(board, card)).column
            elif agent == releaser.AGENT:
                outcome = (await releaser.process(board, card)).kind
        except Exception as exc:
            if PROVIDER_FATAL.search(str(exc)):
                await open_breaker(redis, f"{type(exc).__name__}: {exc}")
            raise
        finally:
            run.outcome = str(outcome)
            # A move already cleared Owner; a pass that ended in waiting or crashed has not.
            with contextlib.suppress(Exception):
                await board.set_field(card, "Owner", None)
    if outcome in ("wait", "ask", "awaiting"):
        fresh = next((c for c in await board.cards() if c.item_id == item_id), None)
        if fresh is not None:
            await redis.set(SEEN_PREFIX + item_id, fresh.updated_at, ex=7 * 86400)
    return str(outcome)


def summary(cards: list[Card], now: datetime | None = None) -> str:
    """The daily note to a person: what is where, what waits on them."""
    now = now or datetime.now(UTC)
    by = {c: [x for x in cards if x.status == c] for c in (*STAGES, BLOCKED, MERGED)}
    lines = [f"📋 Board on {now:%d.%m}"]
    lines += [f"{col}: {len(items)}" for col, items in by.items() if items]
    if by[BLOCKED]:
        lines += ["", "Blocked:"] + [f"• {c.title} — {c.blocked_reason or '?'} {c.url}" for c in by[BLOCKED]]
    return "\n".join(lines)


async def main() -> None:
    from redis.asyncio import Redis as AsyncRedis

    board, redis = Board.from_settings(), AsyncRedis.from_url(get_settings().redis_url)

    async def dry(agent: str, item_id: str) -> None:
        print(f"would start {agent} on {item_id}")

    try:
        d = decide(
            cards := await board.cards(), await _alive(redis, cards), await _seen(redis, cards), 0,
            await redis.get(BREAKER_KEY),
        )  # fmt: skip
        for agent, card in d.launch:
            await dry(agent, card.item_id)
            print(f"  {card.status}: {card.url}")
        for card in d.stuck:
            print(f"stuck: {card.url} ({card.owner})")
        for why in d.held:
            print(f"held: {why}")
    finally:
        await board.aclose()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
