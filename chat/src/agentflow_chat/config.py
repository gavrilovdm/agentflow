"""Settings for the chat layer. Prefixed CHAT_ so they can share a .env with agentflow."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHAT_", extra="ignore")

    # A second bot: agentflow's own bot owns its webhook (approval buttons), and a token
    # can have either a webhook or long polling, not both.
    telegram_bot_token: str = ""
    # Comma-separated chat ids allowed to talk to the layer. Empty refuses everyone: this
    # bot can start runs that push to GitHub.
    allowed_chat_ids: str = ""

    mcp_url: str = "http://127.0.0.1:8001/mcp"
    mcp_token: str = Field(default="", description="Sent as a Bearer token if agentflow's MCP is behind auth")

    # Point the SDK at an Anthropic-format gateway (e.g. a local proxy) instead of api.anthropic.com.
    # Empty = the SDK's own credentials: ANTHROPIC_API_KEY, or Bedrock / Vertex via CLAUDE_CODE_USE_*.
    anthropic_base_url: str = ""
    anthropic_auth_token: str = ""

    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_turns: int = 30
    confirm_timeout_s: int = 600

    @property
    def allowed_chats(self) -> set[int]:
        return {int(x) for x in self.allowed_chat_ids.replace(" ", "").split(",") if x}


@lru_cache
def get_settings() -> Settings:
    return Settings()
