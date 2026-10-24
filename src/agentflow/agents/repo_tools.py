"""Read-only code navigation tools (glob, grep, read) over the run's workspace.

RAG returns the chunks most similar to one query, chosen before the model has seen
anything. A planner that has to name exact target files and reuse existing
signatures needs to follow references instead: find a file by name, grep for every
call site, read the module around it. These tools let it do that.

Pure Python on purpose: the runtime image has no ripgrep, and the workspaces are
small enough that a walk with hard output caps stays fast.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from langchain_core.tools import BaseTool, tool

from agentflow.integrations.workspace import PathEscapeError, resolve_in_workspace

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
}
MAX_GLOB_RESULTS = 200
MAX_GREP_MATCHES = 100
MAX_LINE_CHARS = 240
MAX_READ_LINES = 400
# Lockfiles and bundles match nearly every grep and drown the real hits.
MAX_FILE_BYTES = 512_000


def _resolve_dir(workspace: Path, path: str) -> tuple[Path, str]:
    if path in ("", ".", "./"):
        return workspace.resolve(), ""
    return resolve_in_workspace(workspace, path)


def _walk(root: Path, start: Path) -> Iterator[tuple[Path, str]]:
    """Files under `start` as (absolute, repo-relative posix) pairs, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            abs_path = Path(dirpath) / name
            yield abs_path, PurePosixPath(abs_path.relative_to(root)).as_posix()


def _is_text(path: Path) -> bool:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        with path.open("rb") as fh:
            return b"\0" not in fh.read(8192)
    except OSError:
        return False


def _clip(line: str) -> str:
    line = line.rstrip("\n")
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"


def glob_paths(workspace: Path, pattern: str) -> str:
    root = workspace.resolve()
    pattern = pattern.strip().lstrip("/")
    # fnmatch's `*` already crosses `/`, so `**/` only needs to also match zero dirs.
    variants = {pattern, pattern.replace("**/", "")}
    hits = [rel for _, rel in _walk(root, root) if any(fnmatch.fnmatchcase(rel, v) for v in variants)]
    if not hits:
        return f"No files match {pattern!r}."
    more = len(hits) - MAX_GLOB_RESULTS
    out = "\n".join(hits[:MAX_GLOB_RESULTS])
    return out + (f"\n… {more} more, narrow the pattern" if more > 0 else "")


def grep_paths(workspace: Path, pattern: str, path: str = ".", glob: str = "", ignore_case: bool = False) -> str:
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return f"Invalid regex {pattern!r}: {exc}. Escape special characters such as ( [ . *"
    try:
        start, _ = _resolve_dir(workspace, path)
    except PathEscapeError as exc:
        return str(exc)
    root = workspace.resolve()
    files = [(start, PurePosixPath(start.relative_to(root)).as_posix())] if start.is_file() else _walk(root, start)
    matches: list[str] = []
    for abs_path, rel in files:
        if glob and not (fnmatch.fnmatchcase(rel, glob) or fnmatch.fnmatchcase(abs_path.name, glob)):
            continue
        if not _is_text(abs_path):
            continue
        try:
            with abs_path.open(encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if regex.search(line):
                        matches.append(f"{rel}:{n}: {_clip(line)}")
                        if len(matches) > MAX_GREP_MATCHES:
                            return (
                                "\n".join(matches[:MAX_GREP_MATCHES]) + "\n… more matches, narrow the pattern or path"
                            )
        except OSError:
            continue
    return "\n".join(matches) if matches else f"No matches for {pattern!r}."


def read_lines(workspace: Path, file_path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
    try:
        abs_path, rel = resolve_in_workspace(workspace, file_path)
    except PathEscapeError as exc:
        return str(exc)
    if abs_path.is_dir():
        return f"{rel} is a directory; use glob_files to list it."
    if not abs_path.is_file():
        return f"File not found: {file_path}"
    if not _is_text(abs_path):
        return f"{rel} is binary or larger than {MAX_FILE_BYTES} bytes."
    lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(offset, 1)
    end = min(start - 1 + max(1, min(limit, MAX_READ_LINES)), len(lines))
    body = "\n".join(f"{n:>5}  {_clip(lines[n - 1])}" for n in range(start, end + 1))
    tail = f"\n[{rel}: lines {start}-{end} of {len(lines)}" + (
        ", call again with a later offset]" if end < len(lines) else "]"
    )
    return (body + tail) if body else f"{rel} has {len(lines)} lines; offset {offset} is past the end."


def make_repo_tools(workspace: Path) -> list[BaseTool]:
    @tool
    def glob_files(pattern: str) -> str:
        """List repository files matching a glob, relative to the repo root.
        Examples: "src/**/*.py", "**/test_*.py", "*.md", "src/agentflow/api/*"."""
        return glob_paths(workspace, pattern)

    @tool
    def grep(pattern: str, path: str = ".", glob: str = "", ignore_case: bool = False) -> str:
        """Search file contents with a Python regex. Returns file:line: text for each match.
        Narrow with `path` (a directory or file) and `glob` (e.g. "*.py"). Use it to find
        where a name is defined ("def resolve_in_workspace") and every place it is used."""
        return grep_paths(workspace, pattern, path, glob, ignore_case)

    @tool
    def read_file(file_path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        """Read a file with line numbers. For long files, page with `offset` (1-based) and `limit`."""
        return read_lines(workspace, file_path, offset, limit)

    return [glob_files, grep, read_file]
