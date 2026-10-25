"""uv run agentflow-chat  — start the Telegram bridge (long polling)."""

from __future__ import annotations

import asyncio
import logging

from agentflow_chat.telegram import TelegramBridge


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(TelegramBridge().run())


if __name__ == "__main__":
    main()
