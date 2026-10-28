"""Intake: the agent of the Бэклог column.

It turns whatever the author wrote into a standard card (goal, checkable Definition of
Done, type, priority) and lets it into Планируется only when that card is complete. The
model only reads the text and extracts fields; whether the card passes is decided here,
in code, so every refusal has a rule and a reason:

- the type has no workflow, or the card repeats an open one → Блокировка with the reason;
- something is missing or a DoD item can't be checked → questions to the author in a
  comment, and the card waits in Бэклог for the answer;
- three rounds of questions without a complete card → Блокировка, a person decides.

    uv run python -m agentflow.stages.intake        # process the free cards in Бэклог once
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agentflow import repos
from agentflow.board import (
    BACKLOG,
    BLOCKED,
    CARD_MARKER,
    MERGED,
    PLANNING,
    Board,
    Card,
    Report,
    Thread,
    render_body,
)
from agentflow.config import get_settings
from agentflow.models import structured
from agentflow.skills import role_section

log = logging.getLogger(__name__)

AGENT = "intake"
# A type the model is less sure of than this is asked about, not guessed: a research task
# routed to the coding workflow burns a full run before anyone notices.
MIN_CONFIDENCE = 0.8
MAX_ROUNDS = 3
QUESTIONS_MARK = "agentflow:questions"
QUOTES = "«»\"'“” "


class IntakeDraft(BaseModel):
    type: Literal["coding", "research", "text", "out_of_scope"] = Field(
        description="coding: change code in the repo; research: find and summarise material; "
        "text: write a post, letter or article; out_of_scope: none of these"
    )
    type_confidence: float = Field(ge=0, le=1)
    title: str = Field(description="Imperative, at most 80 characters, in the author's language")
    goal: str = Field(description="What should change, 1-3 sentences; empty if the author did not say")
    definition_of_done: list[str] = Field(description="Checkable conditions the author gave or clearly implied")
    untestable: list[str] = Field(
        default_factory=list, description="Items of definition_of_done that nobody could check as written"
    )
    priority: Literal["high", "normal", "low"] = "normal"
    context: str = Field(default="", description="Constraints, links and details worth keeping")
    questions: list[str] = Field(
        default_factory=list,
        description="Only what blocks understanding WHAT is wanted: which product or part, what result. "
        "Never how to build it (paths, status codes, edge cases, sizes, sources, wording): the next stages "
        "decide that from the repository. Empty for a card a planner could start from.",
    )


SYSTEM = """You read a task someone filed for an engineering team and extract a standard card.
Use only what the author wrote in the issue and in their answers below it; never invent
requirements, numbers or acceptance criteria they did not give or clearly imply.
Intake records what is wanted; how to build it is decided later by a planner who reads the
repository, so details the author left open are not a reason to ask.
Write the card in the author's language."""


@dataclass
class Verdict:
    kind: Literal["ready", "ask", "reject", "wait"]
    reasons: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    draft: IntakeDraft | None = None


def _norm(title: str) -> str:
    return re.sub(r"\W+", " ", title).strip().lower()


def judge(draft: IntakeDraft, card: Card, others: list[Card], whitelist: set[str]) -> Verdict:
    """The intake filter. Each check gives its own reason; asking beats guessing."""
    if draft.type == "out_of_scope":
        return Verdict("reject", ["нет воркфлоу для такой задачи: это не код, не ресёрч и не текст"], draft=draft)
    if card.repo not in whitelist:
        return Verdict("reject", [f"репо {card.repo} нет в whitelist (repos.toml)"], draft=draft)
    dup = next(
        (o for o in others if o.item_id != card.item_id and o.repo == card.repo
         and o.status != MERGED and _norm(o.title) == _norm(draft.title)),
        None,
    )  # fmt: skip
    if dup:
        return Verdict("reject", [f"повторяет открытую карточку {dup.url}"], draft=draft)

    questions = list(draft.questions)
    if draft.type_confidence < MIN_CONFIDENCE:
        questions.append("Это задача на код, на поиск материалов или на текст?")
    if not draft.goal.strip():
        questions.append("Что должно измениться в результате?")
    if not draft.definition_of_done:
        questions.append("По каким признакам понять, что задача сделана?")
    for item in draft.untestable:
        questions.append(f"Как проверить «{item.strip(QUOTES)}»? Нужен измеримый признак.")
    if questions:
        return Verdict("ask", questions=list(dict.fromkeys(questions)), draft=draft)
    return Verdict("ready", draft=draft)


def render_thread(t: Thread) -> str:
    parts = [f"# {t.title}\n\n{t.body.strip() or '(empty)'}"]
    for c in t.comments:
        if "agentflow:report" in c.body:
            continue
        who = "agentflow (questions)" if QUESTIONS_MARK in c.body else c.author
        parts.append(f"--- comment by {who}\n{c.body.strip()}")
    return "\n\n".join(parts)


Extractor = Callable[[Thread], Awaitable[IntakeDraft]]


async def extract(thread: Thread) -> IntakeDraft:
    runnable = structured(get_settings().orchestrator_model, IntakeDraft)
    messages = [SystemMessage(SYSTEM + role_section("intake")), HumanMessage(render_thread(thread))]
    draft = await runnable.ainvoke(messages)
    assert isinstance(draft, IntakeDraft)
    return draft


def _rounds(t: Thread) -> int:
    return sum(QUESTIONS_MARK in c.body for c in t.comments)


def _waiting(t: Thread) -> bool:
    """The last thing on the issue is our own question: the author has not answered."""
    last = next((c for c in reversed(t.comments) if "agentflow:report" not in c.body), None)
    return last is not None and QUESTIONS_MARK in last.body


async def process(
    board: Board, card: Card, others: list[Card], whitelist: set[str], extractor: Extractor = extract
) -> Verdict:
    thread = await board.thread(card)
    if _waiting(thread):
        return Verdict("wait")
    draft = await extractor(thread)
    verdict = judge(draft, card, others, whitelist)

    if verdict.kind == "ready":
        source, reply_to = "", ""
        if m := CARD_MARKER.search(thread.body):
            source, reply_to = m.group(1), m.group(2)
        await board.update_issue(
            card, draft.title, render_body(draft.goal, draft.definition_of_done, draft.context, source, reply_to)
        )
        await board.set_field(card, "Task type", draft.type)
        await board.set_field(card, "Priority", draft.priority)
        await board.move(
            card,
            PLANNING,
            Report(
                agent=AGENT,
                summary=f"карточка полная: {draft.type}, пунктов DoD: {len(draft.definition_of_done)}",
                next_step="исследовать репо, написать спеку и план",
            ),
        )
    elif verdict.kind == "reject":
        await board.move(card, BLOCKED, Report(agent=AGENT, summary="; ".join(verdict.reasons)))
    elif verdict.kind == "ask":
        rounds = _rounds(thread)
        if rounds >= MAX_ROUNDS:
            await board.move(
                card,
                BLOCKED,
                Report(agent=AGENT, summary=f"не удалось уточнить за {rounds} круга вопросов"),
            )
        else:
            lines = "\n".join(f"{i}. {q}" for i, q in enumerate(verdict.questions, 1))
            await board.comment(
                card,
                f"Чтобы взять задачу в работу, ответьте, пожалуйста, комментарием ниже:\n\n{lines}\n\n"
                f"<!-- {QUESTIONS_MARK} agent={AGENT} round={rounds + 1} -->",
            )
            await board.set_field(card, "Owner", None)
    return verdict


async def run(board: Board, extractor: Extractor = extract) -> list[tuple[Card, Verdict]]:
    """Process every free card in Бэклог once."""
    whitelist = {e.full_name for e in repos.load_whitelist()}
    cards = await board.cards()
    done = []
    for card in [c for c in cards if c.status == BACKLOG and not c.owner]:
        if not await board.claim(card, AGENT):
            continue
        card = card.model_copy(update={"owner": AGENT})
        try:
            verdict = await process(board, card, cards, whitelist, extractor)
        except Exception:
            log.exception("intake failed on %s", card.url)
            await board.set_field(card, "Owner", None)
            raise
        if verdict.kind == "wait":
            await board.set_field(card, "Owner", None)
        done.append((card, verdict))
    return done


async def main() -> None:
    board = Board.from_settings()
    try:
        for card, v in await run(board):
            print(f"{v.kind:6} {card.url}  {'; '.join(v.reasons or v.questions)}")
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
