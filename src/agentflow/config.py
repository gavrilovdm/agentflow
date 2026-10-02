"""Runtime configuration: environment-backed settings plus per-run workflow knobs."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TargetRepo(BaseModel):
    """The repo the workflow clones, codes in, and opens a PR against."""

    owner: str
    repo: str
    base_branch: str = "main"

    @classmethod
    def parse(cls, value: str, base_branch: str = "main") -> TargetRepo:
        owner, _, repo = value.partition("/")
        if not owner or not repo:
            raise ValueError(f'Invalid repo "{value}" — expected owner/repo')
        return cls(owner=owner, repo=repo, base_branch=base_branch)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class WorkflowConfig(BaseModel):
    """Per-run budgets. Gate failures and review rejections are counted apart:
    the gate is objective and cheap to re-check, the reviewer is a judgement call
    and the component most prone to rejecting correct work. Sharing one budget let
    a few mechanical failures use up the allowance before the code was ever judged."""

    max_coder_fix_attempts: int = 2
    max_review_cycles: int = 4
    max_gate_failures: int = 5
    # Reviewer outages are not the task's fault, so they don't consume review cycles —
    # but uncapped, an outage (or an exhausted API balance) loops coder→reviewer forever.
    max_reviewer_malfunctions: int = 3
    # When a task exhausts a budget: "ask" pauses the run and asks a human (retry with a
    # hint / replan / skip / abort); "skip" is the unattended behaviour — fail the task and
    # skip only the tasks that depend on it.
    on_task_failure: Literal["ask", "skip"] = "ask"
    max_escalations_per_task: int = 2  # beyond this a task is skipped without asking again
    max_replans: int = 2
    enable_lint: bool = True
    # None = in-place mode: operate on a local directory, no clone, no PR.
    target_repo: TargetRepo | None = None
    local_path: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    voyage_api_key: str = ""
    # OpenAI-compatible proxy to Claude (e.g. cursor-proxy): models named cpx* go through it.
    claude_proxy_url: str = "http://127.0.0.1:8787/v1"
    claude_proxy_key: str = ""

    orchestrator_model: str = "claude-opus-5"
    reviewer_model: str = "claude-opus-5"
    test_generator_model: str = "claude-opus-5"
    coder_model: str = "deepseek-v4-flash"
    fallback_model: str = "claude-sonnet-5"

    embedding_model: str = "voyage-code-3"
    embedding_dim: int = 1024

    database_url: str = "postgresql://agentflow:agentflow@localhost:5432/agentflow"
    redis_url: str = "redis://localhost:6379"

    github_token: str = ""
    github_webhook_secret: str = ""
    github_trigger_label: str = "agentflow"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"  # the guide app's dev server
    api_token: str = Field(default="", description="Bearer token for the REST API; empty disables auth")
    recursion_limit: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
