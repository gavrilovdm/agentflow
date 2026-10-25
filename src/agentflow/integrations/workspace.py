"""Workspace isolation and git operations.

With a target repo, the run clones it into a fresh temp dir on a new branch; the
coder, gate and reviewer all operate there, never in this tool's own checkout.
In-place mode uses a local directory (no clone, no push).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from git import Repo

from agentflow.config import TargetRepo, get_settings

WORKSPACE_ROOT = Path(tempfile.gettempdir()) / "agentflow"


class PathEscapeError(ValueError):
    pass


def resolve_in_workspace(workspace: Path, file_path: str) -> tuple[Path, str]:
    """Map a model-supplied path onto the workspace, refusing anything outside it.

    Joining blindly is unsafe twice over: an absolute path gets concatenated (mirroring
    /var/folders/... inside the repo) and `../` escapes the workspace. This writes
    model-generated content to disk, so escapes are refused with a message the model
    can act on rather than silently remapped.
    """
    root = workspace.resolve()
    candidate = Path(file_path)
    candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        rel = None
    if rel is None or str(rel) in ("", "."):
        raise PathEscapeError(
            f'Refusing to access "{file_path}": path must be relative to the project root and stay '
            f'inside it (e.g. "src/config.py")'
        )
    # .git is git's own state, not the project: a write there can rewrite the remote or add
    # hooks that the worker's own commit and push then run. lower(): macOS paths ignore case.
    if any(part.lower() == ".git" for part in rel.parts):
        raise PathEscapeError(f'Refusing to access "{file_path}": .git holds git internals, not project files')
    return candidate, PurePosixPath(rel).as_posix()


def repo_id(target: TargetRepo | None, workspace: Path) -> str:
    """Stable name for the repository in progress events and run state."""
    return target.full_name if target else f"local:{workspace.resolve()}"


def git_auth_env() -> dict[str, str]:
    """GitHub credentials for one git command, never written to disk.

    A token in the clone URL was saved to the workspace's .git/config, where the coder's
    read_file could open it and carry it into the model's context and traces. Here a
    credential helper, set through GIT_CONFIG_* (git >= 2.31), hands the token to github.com
    only and only for this command. The empty helper before it drops user-level helpers
    (a keychain) that would otherwise answer first. Entries are appended after any
    GIT_CONFIG_* the environment already has, so those keep working.
    """
    token = get_settings().github_token
    if not token:
        return {}
    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    key = "credential.https://github.com.helper"
    helper = '!f() { test "$1" = get && echo username=x-access-token && echo "password=$AGENTFLOW_GIT_TOKEN"; }; f'
    return {
        "GIT_CONFIG_COUNT": str(n + 2),
        f"GIT_CONFIG_KEY_{n}": key,
        f"GIT_CONFIG_VALUE_{n}": "",
        f"GIT_CONFIG_KEY_{n + 1}": key,
        f"GIT_CONFIG_VALUE_{n + 1}": helper,
        "AGENTFLOW_GIT_TOKEN": token,
        "GIT_TERMINAL_PROMPT": "0",  # a rejected token fails the command instead of waiting on a prompt
    }


def prepare_workspace(target: TargetRepo | None, branch: str, local_path: str | None = None) -> Path:
    if target is None:
        return Path(local_path or ".").resolve()
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="run-", dir=WORKSPACE_ROOT))
    url = f"https://github.com/{target.full_name}.git"
    repo = Repo.clone_from(url, dest, env=git_auth_env(), branch=target.base_branch, depth=1, single_branch=True)
    repo.git.checkout("-b", branch)
    return dest


def cleanup_workspace(workspace: Path, target: TargetRepo | None) -> None:
    if target is not None:  # never delete the caller's own directory in in-place mode
        shutil.rmtree(workspace, ignore_errors=True)


def stage_and_diff(workspace: Path, paths: list[str]) -> str:
    """New files are untracked and `git diff` ignores them, so the reviewer once saw an
    empty diff and rejected every task. Stage exactly the task's files (never `add -A`,
    which sweeps in .venv/node_modules) and diff the index."""
    repo = Repo(workspace)
    existing = [p for p in paths if (workspace / p).exists()]
    if existing:
        repo.index.add(existing)
    try:
        return repo.git.diff("--cached", "HEAD")
    except Exception:  # no HEAD yet in a brand-new repo
        return repo.git.diff("--cached")


def commit(workspace: Path, paths: list[str], message: str) -> bool:
    repo = Repo(workspace)
    existing = [p for p in paths if (workspace / p).exists()]
    if not existing:
        return False
    repo.index.add(existing)
    if repo.head.is_valid() and not repo.index.diff("HEAD"):
        return False  # nothing changed
    repo.index.commit(message)
    return True


def push(workspace: Path, branch: str) -> None:
    Repo(workspace).git.push("-u", "origin", branch, env=git_auth_env())
