"""One Claude Agent SDK session per chat, with agentflow's MCP server as its only tools."""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
)

from agentflow_chat.config import Settings, get_settings
from agentflow_chat.policy import MCP_PREFIX, START_RUN, ChatState, needs_confirmation
from agentflow_chat.prompt import SYSTEM_PROMPT

log = logging.getLogger(__name__)

# Reads run without asking. Everything else that changes state goes through can_use_tool.
READ_ONLY = [MCP_PREFIX + t for t in ("list_repos", "inspect_repo", "find_repos", "get_run")]
# Driven by the user's own words about a run agentflow already showed them, so no extra tap.
USER_DRIVEN = {MCP_PREFIX + "review_checkpoint", MCP_PREFIX + "resolve_failed_task"}


class Confirmer(Protocol):
    async def confirm(self, chat_id: int, question: str) -> tuple[bool, str | None]:
        """Ask the user; returns (approved, what they said instead if they typed a reply)."""
        ...


Reply = Callable[[str], Awaitable[None]]


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _from_blocks(blocks: list[Any]) -> Any:
    parsed = [_loads(b["text"]) for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    # A list-returning MCP tool may send one text block per item instead of one JSON array.
    return parsed[0] if len(parsed) == 1 else parsed


def tool_json(response: Any) -> Any:
    """Hooks see MCP results in whatever shape the CLI forwards; normalise to the payload."""
    if isinstance(response, str):
        return _loads(response)
    if isinstance(response, dict):
        if "structuredContent" in response and isinstance(response["structuredContent"], dict):
            sc = response["structuredContent"]
            return sc.get("result", sc)
        if isinstance(response.get("content"), list):
            return _from_blocks(response["content"])
        return response
    if isinstance(response, list) and response and all(isinstance(b, dict) and "type" in b for b in response):
        return _from_blocks(response)
    return response


class ChatSession:
    def __init__(self, chat_id: int, confirmer: Confirmer, settings: Settings | None = None):
        self.chat_id = chat_id
        self.confirmer = confirmer
        self.settings = settings or get_settings()
        self.state = ChatState()
        self._client: ClaudeSDKClient | None = None

    def options(self) -> ClaudeAgentOptions:
        s = self.settings
        server: dict[str, Any] = {"type": "http", "url": s.mcp_url}
        if s.mcp_token:
            server["headers"] = {"Authorization": f"Bearer {s.mcp_token}"}
        env = {}
        if s.anthropic_base_url:
            # CLAUDE_PROXY_URL ends in /v1 for OpenAI clients; Claude Code appends /v1/messages itself.
            env["ANTHROPIC_BASE_URL"] = s.anthropic_base_url.rstrip("/").removesuffix("/v1")
        if s.anthropic_auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = s.anthropic_auth_token
        return ClaudeAgentOptions(
            env=env,
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"agentflow": server},  # type: ignore[dict-item]
            strict_mcp_config=True,
            tools=[],  # no file, shell or web tools: this layer only talks to agentflow
            allowed_tools=READ_ONLY,
            can_use_tool=self.can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher=MCP_PREFIX + "list_repos", hooks=[self._on_list_repos])]},  # type: ignore[list-item]
            setting_sources=[],  # don't pick up CLAUDE.md or user settings from wherever it runs
            cwd=tempfile.gettempdir(),
            model=s.model,
            effort=s.effort,  # type: ignore[arg-type]
            max_turns=s.max_turns,
        )

    async def can_use_tool(
        self, tool: str, tool_input: dict[str, Any], _ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool in READ_ONLY or tool in USER_DRIVEN:
            return PermissionResultAllow()
        if tool != START_RUN:
            return PermissionResultDeny(message=f"{tool} is not available here")
        repo = tool_input.get("repo")
        if not repo:
            # In-place mode edits the worker's own checkout; a chat request should never do that.
            return PermissionResultDeny(message="start_run needs a repo from list_repos")
        if not needs_confirmation(repo, self.state):
            return PermissionResultAllow()
        ok, said = await self.confirmer.confirm(self.chat_id, f"Запустить в {repo}?\n\n{tool_input.get('prompt', '')}")
        if ok:
            return PermissionResultAllow()
        reason = f"The user declined running in {repo}" + (f" and said: {said}" if said else "")
        return PermissionResultDeny(message=reason + ". Ask what to change; don't retry the same call.")

    async def _on_list_repos(self, data: Any, _tool_use_id: str | None, _ctx: Any) -> dict[str, Any]:
        payload = tool_json(data.get("tool_response"))
        if isinstance(payload, list):
            self.state.whitelist = [r["full_name"] for r in payload if isinstance(r, dict) and "full_name" in r]
        return {}

    async def send(self, text: str, reply: Reply) -> None:
        """Run one user turn, relaying each assistant text as it arrives (tool calls can take a while)."""
        self.state.last_user_text = text
        # Only a list_repos seen in this turn may waive the confirmation: the whitelist can
        # change between turns, and a stale "only one repo" would start a run unasked.
        self.state.whitelist = None
        if self._client is None:
            self._client = ClaudeSDKClient(self.options())
            await self._client.connect()
        await self._client.query(text)
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                out = "".join(b.text for b in msg.content if isinstance(b, TextBlock)).strip()
                if out:
                    await reply(out)
            elif isinstance(msg, ResultMessage) and msg.is_error:
                log.warning("turn ended with error: %s", msg.result)
                await reply("Не получилось ответить, попробуйте ещё раз.")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
        self.state = ChatState()
