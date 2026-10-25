"""uv run agentflow-chat  — start the Telegram bridge (long polling)."""

from __future__ import annotations

import asyncio
import logging
import warnings

from agentflow_chat.telegram import TelegramBridge


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Telegram URLs carry the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Read-only tools are auto-approved on purpose; only start_run goes through can_use_tool.
    warnings.filterwarnings("ignore", message="can_use_tool will not be invoked")
    asyncio.run(TelegramBridge().run())


if __name__ == "__main__":
    main()
