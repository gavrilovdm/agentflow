"""Settings for the chat layer. Prefixed CHAT_ so they can share a .env with agentflow."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ../.env is agentflow's: the proxy URL and key are shared with its orchestrator.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_prefix="CHAT_", extra="ignore", populate_by_name=True
    )

    # A second bot: agentflow's own bot owns its webhook (approval buttons), and a token
    # can have either a webhook or long polling, not both.
    telegram_bot_token: str = ""
    # Comma-separated chat ids allowed to talk to the layer. Empty refuses everyone: this
    # bot can start runs that push to GitHub.
    allowed_chat_ids: str = ""

    mcp_url: str = "http://127.0.0.1:8001/mcp"
    mcp_token: str = Field(default="", description="Sent as a Bearer token if agentflow's MCP is behind auth")

    # Same cursor-proxy as agentflow's orchestrator by default (CLAUDE_PROXY_URL / CLAUDE_PROXY_KEY).
    # It passes Anthropic-format /v1/messages through on the Claude Code OAuth session. Set
    # CHAT_ANTHROPIC_BASE_URL="" to use ANTHROPIC_API_KEY (or Bedrock / Vertex) instead.
    anthropic_base_url: str = Field(
        default="http://127.0.0.1:8787",
        validation_alias=AliasChoices("CHAT_ANTHROPIC_BASE_URL", "CLAUDE_PROXY_URL"),
    )
    anthropic_auth_token: str = Field(
        default="", validation_alias=AliasChoices("CHAT_ANTHROPIC_AUTH_TOKEN", "CLAUDE_PROXY_KEY")
    )

    # What the orchestrator's cpxopus resolves to. The proxy maps cpx* aliases only on its
    # OpenAI route; /v1/messages is passed through, so it needs the real model id.
    model: str = "claude-opus-5"
    effort: str = "medium"
    max_turns: int = 30
    confirm_timeout_s: int = 600

    @property
    def allowed_chats(self) -> set[int]:
        return {int(x) for x in self.allowed_chat_ids.replace(" ", "").split(",") if x}


@lru_cache
def get_settings() -> Settings:
    return Settings()
