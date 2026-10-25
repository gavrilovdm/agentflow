import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo

from agentflow.config import TargetRepo, get_settings
from agentflow.integrations import github
from agentflow.integrations import workspace as ws

TOKEN = "github_pat_test_0123456789"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    get_settings.cache_clear()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True)


def _fake_github(tmp_path: Path, monkeypatch) -> Path:
    """A local bare repo that git reaches for https://github.com/o/r.git (insteadOf, no network)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "README.md").write_text("hi\n")
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("add", "-A", cwd=seed)
    _git("commit", "-qm", "init", cwd=seed)
    bare = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(bare), cwd=tmp_path)
    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(n + 1))
    monkeypatch.setenv(f"GIT_CONFIG_KEY_{n}", f"url.{bare.as_uri()}.insteadOf")
    monkeypatch.setenv(f"GIT_CONFIG_VALUE_{n}", "https://github.com/o/r.git")
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)  # they would override the workspace's identity
    return bare


OWNER = ("Octo Cat", "7+octo@users.noreply.github.com")


def test_clone_and_push_keep_the_token_out_of_the_workspace(tmp_path, monkeypatch, token):
    """The token sat in each workspace's .git/config, where the coder's read_file could open it."""
    bare = _fake_github(tmp_path, monkeypatch)
    monkeypatch.setattr(ws, "WORKSPACE_ROOT", tmp_path / "runs")

    dest = ws.prepare_workspace(TargetRepo(owner="o", repo="r"), "agentflow/feature", identity=OWNER)
    (dest / "a.txt").write_text("a\n")
    assert ws.commit(dest, ["a.txt"], "add a")
    ws.push(dest, "agentflow/feature")

    assert "agentflow/feature" in [h.name for h in Repo(bare).heads]  # the environment's own git config survived
    assert Repo(dest).remote().url == "https://github.com/o/r.git"
    leaked = [p for p in (dest / ".git").rglob("*") if p.is_file() and TOKEN.encode() in p.read_bytes()]
    assert leaked == []

    # Committed as agentflow@<container id>, Vercel refused to build the PR.
    pushed = Repo(bare).commit("agentflow/feature")
    assert (pushed.author.name, pushed.author.email) == OWNER
    assert (pushed.committer.name, pushed.committer.email) == OWNER


async def test_commit_identity_is_the_token_owner(monkeypatch, token):
    users = iter([
        SimpleNamespace(name="Octo Cat", login="octo", id=7),
        SimpleNamespace(name=None, login="octo", id=7),
    ])  # fmt: skip
    monkeypatch.setattr(github, "_client", lambda: SimpleNamespace(get_user=lambda: next(users)))
    assert await github.commit_identity() == OWNER
    assert await github.commit_identity() == ("octo", "7+octo@users.noreply.github.com")

    def broken():
        raise RuntimeError("401")

    monkeypatch.setattr(github, "_client", broken)
    assert await github.commit_identity() is None  # never fails the run; git falls back to user@host

    monkeypatch.setenv("GITHUB_TOKEN", "")
    get_settings.cache_clear()
    assert await github.commit_identity() is None


def test_the_token_goes_to_github_only(monkeypatch, token):
    env = {**os.environ, **ws.git_auth_env()}
    # Nowhere in git's config or on a command line, where GitPython's errors would print it.
    assert not any(TOKEN in v for k, v in env.items() if k.startswith("GIT_CONFIG_"))

    def ask(host: str) -> str:
        return subprocess.run(
            ["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n",
            capture_output=True, text=True, env=env,
        ).stdout  # fmt: skip

    assert f"username=x-access-token\npassword={TOKEN}" in ask("github.com")
    assert TOKEN not in ask("example.com")

    monkeypatch.setenv("GITHUB_TOKEN", "")
    get_settings.cache_clear()
    assert ws.git_auth_env() == {}  # no token: git falls back to whatever the machine has


def test_agents_cannot_open_git_internals(tmp_path):
    for path in (".git/config", "sub/.git/HEAD", ".GIT/config"):
        with pytest.raises(ws.PathEscapeError):
            ws.resolve_in_workspace(tmp_path, path)
    for path in (".gitignore", ".github/workflows/ci.yml"):
        assert ws.resolve_in_workspace(tmp_path, path)[1] == path
