"""GitHub: pull requests, issue comments, webhook signature verification."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging

from github import Auth, Github

from agentflow.config import TargetRepo, token_for
from agentflow.schemas import PullRequest

log = logging.getLogger(__name__)


def _client(full_name: str | None = None) -> Github:
    return Github(auth=Auth.Token(token_for(full_name)))


async def commit_identity(full_name: str | None = None) -> tuple[str, str] | None:
    """The token's owner as a git author: (name, GitHub noreply address).

    Without one the worker committed as `agentflow@<container id>`. Vercel could not tie
    that address to any account and refused to build the PR. GIT_AUTHOR_* / GIT_COMMITTER_*
    in the environment still win. None (with a warning) when the token has no user, e.g.
    an app installation token.
    """
    if not token_for(full_name):
        return None

    def _me() -> tuple[str, str]:
        user = _client(full_name).get_user()
        return user.name or user.login, f"{user.id}+{user.login}@users.noreply.github.com"

    try:
        return await asyncio.to_thread(_me)
    except Exception as exc:  # noqa: BLE001 — a missing identity must not stop the run
        log.warning("no commit identity from the GitHub token (%s); git falls back to user@host", type(exc).__name__)
        return None


async def open_pull_request(target: TargetRepo, head: str, title: str, body: str) -> PullRequest:
    def _create() -> PullRequest:
        pr = (
            _client(target.full_name)
            .get_repo(target.full_name)
            .create_pull(title=title, body=body, head=head, base=target.base_branch)
        )
        return PullRequest(number=pr.number, url=pr.html_url, title=pr.title, branch=head)

    return await asyncio.to_thread(_create)


async def comment_on_issue(target: TargetRepo, issue_number: int, body: str) -> None:
    def _comment() -> None:
        _client(target.full_name).get_repo(target.full_name).get_issue(issue_number).create_comment(body)

    await asyncio.to_thread(_comment)


def verify_signature(secret: str, body: bytes, signature_header: str | None, *, insecure_dev: bool = False) -> bool:
    if not secret:
        return insecure_dev  # an unset secret must not mean "anyone may start a run"
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
