"""Rules the chat layer enforces in code rather than trusting the prompt with.

A wrong repo costs a whole run (and a PR in the wrong place), while asking costs one tap,
so create_task is confirmed unless the choice could not have been a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MCP_PREFIX = "mcp__agentflow__"
# The bot files cards on the board; stage agents take them from there. It no longer starts runs.
CREATE_TASK = MCP_PREFIX + "create_task"
# agentflow's own Telegram bot ends every notification with this line.
THREAD_RE = re.compile(r"thread:\s*`?([\w.:-]+)`?")
YES = {"да", "ок", "ok", "okay", "yes", "y", "го", "давай", "запускай", "подтверждаю", "+", "👍"}


@dataclass
class ChatState:
    """What the layer has learned in one conversation, from tool results it observed."""

    whitelist: list[str] | None = None  # full names from the last list_repos
    last_user_text: str = ""


def repo_named(repo: str, text: str) -> bool:
    """The user wrote the repo themselves: "owner/name", or the bare name as a whole word."""
    t = text.lower()
    full = repo.lower()
    name = full.rsplit("/", 1)[-1]
    return full in t or re.search(rf"(?<![\w/-]){re.escape(name)}(?![\w/-])", t) is not None


def needs_confirmation(repo: str | None, state: ChatState) -> bool:
    if not repo:
        return True
    if state.whitelist is not None and [r.lower() for r in state.whitelist] == [repo.lower()]:
        return False  # nothing to choose between
    return not repo_named(repo, state.last_user_text)


def is_yes(text: str) -> bool:
    return text.strip().lower().rstrip("!.") in YES


def with_reply_context(text: str, replied_to: str | None) -> str:
    """Notifications come from agentflow's bot, not from this layer, so the model never saw
    them. A reply to one ("убери пункт 3") is meaningless without the run it refers to."""
    if not replied_to:
        return text
    m = THREAD_RE.search(replied_to)
    ref = f"прогон {m.group(1)}" if m else "сообщение"
    return f"[Ответ на уведомление agentflow, {ref}:\n{replied_to[:1500]}]\n\n{text}"
