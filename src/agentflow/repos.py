"""Repository whitelist and the facts a caller needs to pick a repo for a run.

The conversational layer chooses *which* repo a request is about; this module only
answers "which repos may it choose from" and "what is true about each one". Facts come
from the GitHub API (no clone), so inspecting the whole whitelist stays cheap.

Fuzzy criteria (what a project is *about*) are deliberately not scored here: we return
the README and description and let the LLM judge. Hard criteria (language, framework,
license, CI, activity, layout, permissions) are checked here so they are never guessed.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentflow.config import get_settings

MANIFESTS = ("pyproject.toml", "requirements.txt", "package.json", "pom.xml", "build.gradle", "go.mod", "Cargo.toml")
README_CHARS = 3000
CACHE_TTL_S = 600


class RepoEntry(BaseModel):
    full_name: str
    description: str = ""
    owner: str = ""  # the team or person responsible, not the GitHub owner
    tags: list[str] = Field(default_factory=list)
    base_branch: str | None = None  # None = the repo's default branch
    # The releaser merges a green, tested PR without waiting for a person's «merge».
    auto_merge: bool = False


class RepoNotAllowed(ValueError):
    pass


def load_whitelist(path: str | None = None) -> list[RepoEntry]:
    """`[[repo]]` tables from repos.toml. A missing file means an empty whitelist, not "anything goes"."""
    p = Path(path or get_settings().repos_file)
    if not p.exists():
        return []
    data = tomllib.loads(p.read_text())
    return [RepoEntry(**r) for r in data.get("repo", [])]


def check_allowed(full_name: str, whitelist: list[RepoEntry] | None = None) -> RepoEntry:
    """Runs push a branch and open a PR with our token, so a model that guessed a repo name
    could otherwise write to any repo the token reaches. Only listed repos are accepted."""
    entries = load_whitelist() if whitelist is None else whitelist
    for e in entries:
        if e.full_name.lower() == full_name.lower():
            return e
    if not entries and get_settings().insecure_dev:
        return RepoEntry(full_name=full_name)
    raise RepoNotAllowed(f"{full_name} is not in the repo whitelist ({get_settings().repos_file})")


# --- facts -----------------------------------------------------------------------------


class RepoFacts(BaseModel):
    full_name: str
    description: str = ""
    owner: str = ""
    tags: list[str] = Field(default_factory=list)
    github_description: str = ""
    topics: list[str] = Field(default_factory=list)
    readme_excerpt: str = ""
    language: str | None = None
    languages: list[str] = Field(default_factory=list)  # by bytes, largest first
    dependencies: list[str] = Field(default_factory=list)  # lower-cased package names from manifests
    manifests: list[str] = Field(default_factory=list)
    top_level: list[str] = Field(default_factory=list)  # dirs end with "/"
    workflows: list[str] = Field(default_factory=list)
    # Conclusion of the latest default-branch run (success, failure, ...), "none" if never run, None if unknown.
    ci_status: str | None = None
    last_commit_at: datetime | None = None
    open_prs: int | None = None
    license: str | None = None  # SPDX id, "none" if there is no license file, None if unknown
    has_contributing: bool = False
    can_push: bool | None = None
    archived: bool = False
    default_branch: str = "main"
    errors: list[str] = Field(default_factory=list)  # facts we could not fetch, so absence isn't misread


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def parse_dependencies(manifest: str, text: str) -> list[str]:
    deps: list[str] = []
    try:
        if manifest == "pyproject.toml":
            data = tomllib.loads(text)
            proj = data.get("project", {})
            specs = list(proj.get("dependencies", []))
            for group in proj.get("optional-dependencies", {}).values():
                specs += group
            specs += list(data.get("tool", {}).get("poetry", {}).get("dependencies", {}))
            deps = [m.group(1) for s in specs if (m := _REQ_NAME.match(s))]
        elif manifest == "requirements.txt":
            lines = (ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-")))
            deps = [m.group(1) for ln in lines if (m := _REQ_NAME.match(ln))]
        elif manifest == "package.json":
            data = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps += list(data.get(key, {}))
        elif manifest == "pom.xml":
            deps = re.findall(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", text)
        elif manifest == "build.gradle":
            deps = [g.split(":")[1] for g in re.findall(r"['\"]([\w.-]+:[\w.-]+)(?::[^'\"]*)?['\"]", text)]
        elif manifest == "go.mod":
            deps = re.findall(r"^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+v", text, re.M)
        elif manifest == "Cargo.toml":
            deps = list(tomllib.loads(text).get("dependencies", {}))
    except (ValueError, KeyError, IndexError, AttributeError):
        return []  # a malformed manifest shouldn't hide the rest of the repo's facts
    return sorted({_norm(d) for d in deps if d and d != "python"})


def _fetch(entry: RepoEntry) -> RepoFacts:
    """Blocking: one GitHub round-trip per fact. Each fact is fetched on its own so one
    missing permission (e.g. Actions read) doesn't blank the others."""
    from github import Auth, Github, GithubException

    gh = Github(auth=Auth.Token(get_settings().github_token)) if get_settings().github_token else Github()
    facts = RepoFacts(full_name=entry.full_name, description=entry.description, owner=entry.owner, tags=entry.tags)

    def attempt(label: str, fn: Any) -> None:
        try:
            fn()
        except GithubException as exc:
            facts.errors.append(f"{label}: {exc.status}")
        except Exception as exc:  # one odd payload must not hide every other fact
            facts.errors.append(f"{label}: {type(exc).__name__}")

    repo = gh.get_repo(entry.full_name)
    branch = entry.base_branch or repo.default_branch
    facts.default_branch = branch
    facts.github_description = repo.description or ""
    facts.language = repo.language
    facts.archived = repo.archived
    facts.can_push = bool(repo.permissions and repo.permissions.push)

    def _topics() -> None:
        facts.topics = repo.get_topics()

    def _languages() -> None:
        # PyGithub mixes a "url" key into the byte counts; keep only real languages.
        langs = {k: v for k, v in repo.get_languages().items() if isinstance(v, int)}
        facts.languages = sorted(langs, key=lambda k: -langs[k])

    def _readme() -> None:
        facts.readme_excerpt = repo.get_readme(ref=branch).decoded_content.decode(errors="replace")[:README_CHARS]

    def _tree() -> None:
        # The git tree, not contents("/"): some GitHub proxies reject a bare trailing-slash path.
        items = repo.get_git_tree(branch).tree
        facts.top_level = sorted(i.path + ("/" if i.type == "tree" else "") for i in items)
        names = {i.path for i in items}
        facts.has_contributing = any(n.lower().startswith("contributing") for n in names)
        deps: set[str] = set()
        for m in MANIFESTS:
            if m in names:
                facts.manifests.append(m)
                f = repo.get_contents(m, ref=branch)
                if not isinstance(f, list):
                    deps.update(parse_dependencies(m, f.decoded_content.decode(errors="replace")))
        facts.dependencies = sorted(deps)

    def _workflows() -> None:
        wf = repo.get_contents(".github/workflows", ref=branch)
        facts.workflows = sorted(w.name for w in (wf if isinstance(wf, list) else [wf]))

    def _ci() -> None:
        runs = repo.get_workflow_runs(branch=repo.get_branch(branch))
        recent: list[Any] = list(runs[:5])
        for run in recent:
            if run.status == "completed":
                facts.ci_status = run.conclusion
                return
        facts.ci_status = "running" if runs.totalCount else "none"

    def _activity() -> None:
        facts.last_commit_at = repo.get_commits(sha=branch)[0].commit.committer.date
        facts.open_prs = repo.get_pulls(state="open").totalCount

    def _license() -> None:
        # No LICENSE file is a definite answer ("none"), unlike a failed request, which stays unknown.
        try:
            facts.license = repo.get_license().license.spdx_id
        except GithubException as exc:
            if exc.status != 404:
                raise
            facts.license = "none"

    for label, fn in [
        ("topics", _topics),
        ("languages", _languages),
        ("readme", _readme),
        ("tree", _tree),
        ("workflows", _workflows),
        ("ci", _ci),
        ("activity", _activity),
        ("license", _license),
    ]:
        attempt(label, fn)
    # 404s on optional files are normal, not errors worth showing.
    facts.errors = [e for e in facts.errors if not e.endswith(": 404")]
    return facts


_cache: dict[str, tuple[float, RepoFacts]] = {}


async def inspect(entry: RepoEntry, *, refresh: bool = False) -> RepoFacts:
    key = entry.full_name.lower()
    hit = _cache.get(key)
    if hit and not refresh and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    facts = await asyncio.to_thread(_fetch, entry)
    _cache[key] = (time.monotonic(), facts)
    return facts


# --- matching --------------------------------------------------------------------------


class Criteria(BaseModel):
    """All optional; given ones must all hold. Strings match case-insensitively."""

    keywords: list[str] = Field(default_factory=list)  # any of them in name/description/topics/tags/README
    language: str | None = None
    framework: str | None = None  # a dependency name, e.g. "fastapi", "react", "spring-boot"
    license: str | None = None  # SPDX id prefix: "MIT", "Apache", "GPL"
    ci_passing: bool | None = None
    has_ci: bool | None = None
    active_within_days: int | None = None
    has_paths: list[str] = Field(default_factory=list)  # top-level entries, e.g. "src", "tests"
    can_push: bool | None = None
    has_contributing: bool | None = None


class Match(BaseModel):
    full_name: str
    matched: bool
    passed: list[str]
    failed: list[str]
    unknown: list[str]  # facts we couldn't fetch: the caller should not treat these as failures


def evaluate(facts: RepoFacts, c: Criteria, now: datetime | None = None) -> Match:
    passed: list[str] = []
    failed: list[str] = []
    unknown: list[str] = []

    def check(name: str, value: bool | None) -> None:
        (unknown if value is None else passed if value else failed).append(name)

    if c.keywords:
        hay = " ".join(
            [facts.full_name, facts.description, facts.github_description, *facts.topics, *facts.tags]
            + [facts.readme_excerpt]
        ).lower()
        hits = [k for k in c.keywords if k.lower() in hay]
        check(f"keywords {hits or c.keywords}", bool(hits))
    if c.language:
        langs = [x.lower() for x in ([facts.language] if facts.language else []) + facts.languages]
        check(f"language {c.language}", c.language.lower() in langs if langs else None)
    if c.framework:
        fw = _norm(c.framework)
        check(
            f"framework {c.framework}",
            any(d == fw or d.startswith(fw + "-") or d.endswith("/" + fw) for d in facts.dependencies)
            if facts.manifests
            else None,
        )
    if c.license:
        check(
            f"license {c.license}",
            facts.license.lower().startswith(c.license.lower()) if facts.license else None,
        )
    if c.has_ci is not None:
        check(f"has_ci={c.has_ci}", bool(facts.workflows) == c.has_ci)
    if c.ci_passing is not None:
        check(f"ci_passing={c.ci_passing}", (facts.ci_status == "success") == c.ci_passing if facts.ci_status else None)
    if c.active_within_days is not None:
        if facts.last_commit_at is None:
            check(f"active_within_days {c.active_within_days}", None)
        else:
            age = ((now or datetime.now(UTC)) - facts.last_commit_at).days
            check(f"active_within_days {c.active_within_days} (last commit {age}d ago)", age <= c.active_within_days)
    for path in c.has_paths:
        p = path.strip("/").lower()
        check(f"has {path}", any(t.rstrip("/").lower() == p for t in facts.top_level) if facts.top_level else None)
    if c.can_push is not None:
        check(f"can_push={c.can_push}", None if facts.can_push is None else facts.can_push == c.can_push)
    if c.has_contributing is not None:
        check(f"has_contributing={c.has_contributing}", facts.has_contributing == c.has_contributing)
    if facts.archived:
        failed.append("archived")
    return Match(full_name=facts.full_name, matched=not failed, passed=passed, failed=failed, unknown=unknown)
