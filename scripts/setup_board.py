"""Create the board's columns and fields in the GitHub project (BOARD_ORG / BOARD_PROJECT).

Safe to run again: it only adds what is missing and replaces Status options that are not
the board's columns.

    uv run python scripts/setup_board.py
"""

from __future__ import annotations

import asyncio

from agentflow.board import Board


async def main() -> None:
    board = Board.from_settings()
    try:
        changed = await board.setup()
        print("\n".join(changed) or "board already set up")
        fields = (await board.project())["fields"]
        for name in ("Status", "Task type", "Priority", "Approval", "Owner", "Attempts", "Blocked reason", "Run"):
            f = fields[name]
            print(f"{name}: {f['type']}" + (f" {list(f['options'])}" if f["options"] else ""))
    finally:
        await board.aclose()


if __name__ == "__main__":
    asyncio.run(main())
