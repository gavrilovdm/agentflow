from pathlib import Path

import pytest

from agentflow.agents.repo_tools import (
    MAX_GREP_MATCHES,
    glob_paths,
    grep_paths,
    make_repo_tools,
    read_lines,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/api.py").write_text("def handler(req):\n    return resolve(req)\n")
    (tmp_path / "src/pkg/core.py").write_text("def resolve(x):\n    return x\n")
    (tmp_path / "README.md").write_text("# demo\nresolve docs\n")
    (tmp_path / "node_modules/dep").mkdir(parents=True)
    (tmp_path / "node_modules/dep/index.py").write_text("def resolve(): ...\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\0\0resolve")
    return tmp_path


def test_glob_matches_nested_and_root_files_and_skips_vendor_dirs(repo: Path):
    assert glob_paths(repo, "**/*.py").splitlines() == ["src/pkg/api.py", "src/pkg/core.py"]
    assert glob_paths(repo, "*.md") == "README.md"
    assert "No files match" in glob_paths(repo, "**/*.rs")


def test_grep_reports_file_and_line_and_skips_binaries(repo: Path):
    out = grep_paths(repo, r"resolve\(")
    assert out.splitlines() == ["src/pkg/api.py:2:     return resolve(req)", "src/pkg/core.py:1: def resolve(x):"]
    assert grep_paths(repo, "resolve", glob="*.md") == "README.md:2: resolve docs"
    assert grep_paths(repo, "RESOLVE", path="src/pkg/core.py", ignore_case=True) == "src/pkg/core.py:1: def resolve(x):"


def test_grep_bad_regex_and_escapes_return_messages_not_errors(repo: Path):
    assert "Invalid regex" in grep_paths(repo, "resolve(")
    assert "Refusing to access" in grep_paths(repo, "x", path="../")


def test_grep_caps_output(repo: Path):
    (repo / "big.py").write_text("hit\n" * (MAX_GREP_MATCHES + 50))
    out = grep_paths(repo, "hit").splitlines()
    assert len(out) == MAX_GREP_MATCHES + 1 and "narrow" in out[-1]


def test_read_numbers_lines_and_pages(repo: Path):
    (repo / "long.py").write_text("".join(f"line{i}\n" for i in range(1, 11)))
    out = read_lines(repo, "long.py", offset=3, limit=2)
    assert out.splitlines() == [
        "    3  line3",
        "    4  line4",
        "[long.py: lines 3-4 of 10, call again with a later offset]",
    ]
    assert read_lines(repo, "long.py", offset=9).endswith("[long.py: lines 9-10 of 10]")


def test_read_refuses_escapes_dirs_and_binaries(repo: Path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("secret")
    assert "Refusing to access" in read_lines(repo, str(outside))
    assert "Refusing to access" in read_lines(repo, "../secret.txt")
    assert "is a directory" in read_lines(repo, "src")
    assert "binary" in read_lines(repo, "logo.png")
    assert "File not found" in read_lines(repo, "nope.py")


def test_tools_are_exposed_by_name(repo: Path):
    tools = {t.name: t for t in make_repo_tools(repo)}
    assert set(tools) == {"glob_files", "grep", "read_file"}
    assert tools["grep"].invoke({"pattern": "def handler"}) == "src/pkg/api.py:1: def handler(req):"
