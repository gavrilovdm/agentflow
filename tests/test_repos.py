from datetime import UTC, datetime, timedelta

import pytest

from agentflow import mcp_server, repos
from agentflow.config import get_settings
from agentflow.repos import Criteria, RepoFacts, evaluate, parse_dependencies

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def _facts(**kw) -> RepoFacts:
    base = dict(
        full_name="octo/api",
        description="Mobile backend: login, payments",
        language="Python",
        languages=["Python", "Shell"],
        manifests=["pyproject.toml"],
        dependencies=["fastapi", "pydantic", "uvicorn"],
        top_level=["src/", "tests/", "README.md"],
        workflows=["ci.yml"],
        ci_status="success",
        last_commit_at=NOW - timedelta(days=3),
        license="MIT",
        can_push=True,
    )
    return RepoFacts(**(base | kw))


def test_parse_pyproject_and_requirements():
    pyproject = '[project]\ndependencies = ["FastAPI>=0.115", "pydantic_settings"]\n'
    assert parse_dependencies("pyproject.toml", pyproject) == ["fastapi", "pydantic-settings"]
    reqs = "# pinned\nDjango==5.0\n-r base.txt\nrequests[socks]>=2\n"
    assert parse_dependencies("requirements.txt", reqs) == ["django", "requests"]


def test_parse_js_java_go():
    assert parse_dependencies(
        "package.json", '{"dependencies": {"react": "^18"}, "devDependencies": {"vite": "5"}}'
    ) == [
        "react",
        "vite",
    ]
    pom = "<dependency><artifactId>spring-boot-starter-web</artifactId></dependency>"
    assert parse_dependencies("pom.xml", pom) == ["spring-boot-starter-web"]
    gomod = "module x\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
    assert parse_dependencies("go.mod", gomod) == ["github.com/gin-gonic/gin"]


def test_malformed_manifest_yields_nothing_instead_of_raising():
    assert parse_dependencies("package.json", "{not json") == []


def test_example_request_python_fastapi_active_ci_mit():
    c = Criteria(language="python", framework="fastapi", ci_passing=True, license="MIT", active_within_days=30)
    m = evaluate(_facts(), c, now=NOW)
    assert m.matched and not m.failed and not m.unknown


def test_framework_prefix_and_go_module_path():
    assert evaluate(_facts(dependencies=["spring-boot-starter-web"]), Criteria(framework="spring-boot")).matched
    assert evaluate(_facts(dependencies=["github.com/gin-gonic/gin"]), Criteria(framework="gin")).matched
    assert not evaluate(_facts(dependencies=["flask"]), Criteria(framework="fastapi")).matched


def test_failing_ci_stale_repo_and_wrong_license_fail():
    c = Criteria(ci_passing=True, active_within_days=30, license="Apache")
    m = evaluate(_facts(ci_status="failure", last_commit_at=NOW - timedelta(days=90)), c, now=NOW)
    assert not m.matched and len(m.failed) == 3


def test_missing_facts_are_unknown_not_failed():
    """A token without Actions access must not make every repo look like it has red CI."""
    m = evaluate(_facts(ci_status=None, license=None), Criteria(ci_passing=True, license="MIT"))
    assert m.matched and len(m.unknown) == 2


def test_layout_keywords_and_archived():
    m = evaluate(_facts(), Criteria(has_paths=["src", "tests/"], keywords=["login", "nothing"]))
    assert m.matched
    assert not evaluate(_facts(), Criteria(has_paths=["docs"])).matched
    assert not evaluate(_facts(archived=True), Criteria()).matched


@pytest.fixture
def whitelist(tmp_path, monkeypatch):
    f = tmp_path / "repos.toml"
    f.write_text('[[repo]]\nfull_name = "octo/api"\ndescription = "Mobile backend"\nbase_branch = "develop"\n')
    monkeypatch.setenv("REPOS_FILE", str(f))
    get_settings.cache_clear()
    return f


def test_check_allowed_is_case_insensitive_and_rejects_others(whitelist):
    assert repos.check_allowed("Octo/API").full_name == "octo/api"
    with pytest.raises(repos.RepoNotAllowed):
        repos.check_allowed("evil/repo")


def test_empty_whitelist_fails_closed_outside_dev(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOS_FILE", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("INSECURE_DEV", "false")
    get_settings.cache_clear()
    with pytest.raises(repos.RepoNotAllowed):
        repos.check_allowed("octo/api")


class FakeQueue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, fn, *args, **kwargs):
        self.jobs.append((fn, args))

    async def aclose(self):
        pass


async def test_start_run_rejects_repo_outside_whitelist(whitelist, monkeypatch):
    q = FakeQueue()

    async def fake_queue():
        return q

    monkeypatch.setattr(mcp_server, "_queue", fake_queue)
    out = await mcp_server.start_run("add login", repo="evil/repo")
    assert out["status"] == "rejected" and not q.jobs
    out = await mcp_server.start_run("add login", repo="octo/api")
    assert out["status"] == "queued"
    assert q.jobs[0][1][1]["target_repo"] == {"owner": "octo", "repo": "api", "base_branch": "develop"}


async def test_find_repos_sorts_matches_first(whitelist, monkeypatch):
    whitelist.write_text(whitelist.read_text() + '[[repo]]\nfull_name = "octo/web"\n')

    async def fake_inspect(entry, refresh=False):
        lang = "Python" if entry.full_name == "octo/api" else "TypeScript"
        return _facts(full_name=entry.full_name, language=lang, languages=[lang])

    monkeypatch.setattr(repos, "inspect", fake_inspect)
    out = await mcp_server.find_repos(language="python")
    assert [m["full_name"] for m in out] == ["octo/api", "octo/web"]
    assert out[0]["matched"] and not out[1]["matched"]


def test_no_license_file_or_never_run_ci_is_a_failure_not_unknown():
    m = evaluate(_facts(license="none", ci_status="none"), Criteria(license="MIT", ci_passing=True))
    assert not m.matched and len(m.failed) == 2
