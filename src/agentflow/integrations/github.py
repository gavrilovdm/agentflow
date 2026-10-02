"""GitHub: pull requests, issue comments, webhook signature verification."""

from __future__ import annotations

import asyncio
import hashlib
import hmac

from github import Auth, Github

from agentflow.config import TargetRepo, get_settings
from agentflow.schemas import PullRequest


def _client() -> Github:
    return Github(auth=Auth.Token(get_settings().github_token))


async def open_pull_request(target: TargetRepo, head: str, title: str, body: str) -> PullRequest:
    def _create() -> PullRequest:
        pr = (
            _client().get_repo(target.full_name).create_pull(title=title, body=body, head=head, base=target.base_branch)
        )
        return PullRequest(number=pr.number, url=pr.html_url, title=pr.title, branch=head)

    return await asyncio.to_thread(_create)


async def comment_on_issue(target: TargetRepo, issue_number: int, body: str) -> None:
    def _comment() -> None:
        _client().get_repo(target.full_name).get_issue(issue_number).create_comment(body)

    await asyncio.to_thread(_comment)


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret:
        return True  # verification disabled (local dev)
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
