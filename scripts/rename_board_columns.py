"""Rename the board's Status options from the legacy Russian names to the English ones,
keeping every card in its column.

GitHub keeps a card's Status by option id. `updateProjectV2Field` takes the option's `id`
in its input ("include this to preserve the option's identity during updates, preventing
item field values from being cleared"), so each existing option is sent back with its id
and a new name. Anything that still comes out empty is re-applied from a snapshot taken
before the change, as a safety net. The code reads both names, so this can run at any time.

    uv run python scripts/rename_board_columns.py --dry-run
    uv run python scripts/rename_board_columns.py
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from agentflow.board import FIELDS, Board, canonical


def plan_options(current: list[dict[str, str]]) -> list[dict[str, str]]:
    """The Status options after the rename: the board's columns in order, each existing option
    (legacy or English name) keeping its id, colour and description; a missing column is
    created; an option the board does not know is kept as it is."""
    wanted = FIELDS["Status"][1]
    have = {canonical(o["name"]): o for o in current}
    out: list[dict[str, str]] = []
    for name, color in wanted:
        old = have.pop(name, None)
        if old is None:
            out.append({"name": name, "color": color, "description": ""})
        else:
            out.append(
                {"id": old["id"], "name": name, "color": old["color"], "description": old.get("description", "")}
            )
    out += [o for o in current if canonical(o["name"]) in have]  # unknown options stay
    return out


async def rename(board: Any, dry_run: bool = False) -> list[str]:
    """Snapshot, rename, re-apply. Returns the summary lines to print."""
    current = await board.status_options()
    options = plan_options(current)
    renames = [
        f"{o['name']}<-{old['name']}"
        for o in options
        for old in current
        if old.get("id") == o.get("id") and old["name"] != o["name"]
    ]
    created = [o["name"] for o in options if "id" not in o]
    cards = await board.cards(include_closed=True)
    snapshot = {c.item_id: c.status for c in cards if c.status}
    lines = [f"cards with a status: {len(snapshot)}; renamed: {len(renames)}; created: {len(created)}"]
    if renames:
        lines.append("rename: " + ", ".join(renames))
    if not renames and not created:
        return lines + ["nothing to do: the Status options are already English"]
    if dry_run:
        return lines + ["dry run: nothing changed"]
    await board.replace_status_options(options)
    lost = 0
    for card in await board.cards(include_closed=True):
        was = snapshot.get(card.item_id)
        if was and card.status != was:
            await board.set_field(card, "Status", was)
            lost += 1
    return lines + [f"status re-applied from the snapshot: {lost} (0 means the option ids kept every card)"]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = ap.parse_args()
    board = Board.from_settings()
    try:
        print("\n".join(await rename(board, args.dry_run)))
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
